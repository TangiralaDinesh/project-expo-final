"""
LLM Reasoning Engine — the BRAIN inside the retrieval loop.

Problem (before): LLM was only used at entry (gate) and exit (synthesis).
The entire retrieval loop was heuristic-only: text overlap for satisfaction,
regex for entity extraction, no reasoning about WHAT to search next.

Solution: This module puts the LLM IN the loop:
  1. Entity Question Generation: Given extracted entities, LLM generates
     specific research questions per entity (not just "search entity name")
  2. Learning Evaluation: LLM reads learnings and judges if they actually
     answer the original question (not just text overlap)
  3. Gap Identification: LLM identifies what's MISSING from current learnings
  4. Next Action Decision: LLM decides stop/continue/pivot/dig-deeper
  5. Connected-Dot Exploration: Uses KG context to suggest related searches

Key design: All calls use chat_fast (low-latency) or chat_worker (Groq),
NOT the main chat model. Each call is <5s with tight token limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..llm.client import NIMClient, get_client

logger = logging.getLogger(__name__)


@dataclass
class EntityResearchPlan:
    """LLM-generated research plan for a single entity."""
    entity: str
    questions: list[str]          # Specific questions to research
    search_queries: list[str]     # Actual search queries to execute
    priority: float               # 0-1, how important this entity is
    reasoning: str                # Why these questions matter


@dataclass
class LearningEvaluation:
    """LLM's judgment on retrieved learnings."""
    sufficient: bool              # Does this answer the question?
    confidence: float             # 0-1
    missing_aspects: list[str]    # What's still missing
    quality_score: float          # 0-1, overall quality
    follow_up_queries: list[str]  # What to search next
    reasoning: str                # Chain-of-thought


@dataclass
class ReasoningDecision:
    """LLM's decision on what to do next."""
    action: str                   # "stop" | "continue" | "pivot" | "dig_deeper"
    target: str                   # What to focus on next
    queries: list[str]            # Specific queries to execute
    confidence: float
    reasoning: str


# ── Entity-Specific Question Generation ──

_ENTITY_QUESTIONS_PROMPT = """I need to conduct targeted research to answer this query: "{query}"

Key entities to investigate: {entities}

For EACH entity, generate 2 specific web search queries that will retrieve the exact factual, numerical, or comparative data needed to answer the query. Do NOT just search the entity name alone — search for the specific metrics, statistics, and records required.

Research Guidelines:
1. Target Concrete Facts: Focus on empirical data, verified numbers, career statistics, chronological records, or primary sources.
2. Avoid Generic Searches: Search for specific discriminating keywords (e.g. "Tom Holland worldwide box office gross numbers" rather than "Tom Holland").
3. Comparative Symmetry: Formulate parallel search queries to capture comparable metrics across all entities.

Example: If question is "who sold more albums, Drake or Kanye" and entities are ["Drake", "Kanye West"]:
{{"plans":[{{"entity":"Drake","search_queries":["Drake total album sales worldwide certified units","Drake discography Billboard chart records"],"priority":0.95,"reasoning":"Need Drake certified album sales data"}},{{"entity":"Kanye West","search_queries":["Kanye West total album sales worldwide certified","Kanye West discography sales records"],"priority":0.95,"reasoning":"Need Kanye sales data for comparison"}}]}}

Now generate research plans for MY question: "{query}".
Return valid JSON only:"""


async def generate_entity_research_plans(
    query: str,
    entities: list[str],
    *,
    client: Optional[NIMClient] = None,
    existing_learnings: list = None,
) -> list[EntityResearchPlan]:
    """Use LLM to generate targeted research questions per entity.
    
    Instead of searching "Zendaya" → searches "Zendaya filmography box office grosses"
    Instead of searching "Tom Holland" → searches "Tom Holland movies worldwide revenue"
    """
    client = client or get_client()
    
    if not entities:
        return []
    
    context = ""
    if existing_learnings:
        # Tell LLM what we already know so it doesn't repeat
        known = [getattr(l, 'text', str(l))[:100] for l in existing_learnings[:5]]
        context = f"\n\nWe already know:\n" + "\n".join(f"- {k}" for k in known)
    
    prompt = _ENTITY_QUESTIONS_PROMPT.format(
        query=query,
        entities=json.dumps(entities),
    ) + context

    try:
        response = await client.chat_worker(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an automated query planning engine. Output strictly raw JSON. "
                        "CRITICAL: Do NOT output 'Here\\'s a thinking process', chain of thought, or preamble. "
                        "Do NOT use markdown code blocks. Start your response IMMEDIATELY with '{' and end with '}'."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=512,  # Was 256 — truncated JSON caused fallback to garbage queries
            response_format_json=True,
        )
        
        parsed = _parse_json(response)
        if not parsed or "plans" not in parsed:
            logger.warning("LLM returned invalid JSON, using fallback")
            return _fallback_entity_plans(query, entities)
        
        plans = []
        for p in parsed["plans"]:
            entity_name = p.get("entity", "")
            search_qs = p.get("search_queries", [])
            
            # VALIDATION: Reject if LLM echoed template placeholders
            template_words = {"query1", "query2", "q1", "q2", "name", "entity name"}
            if entity_name.lower() in template_words:
                logger.warning("LLM echoed template (entity='%s'), using fallback", entity_name)
                return _fallback_entity_plans(query, entities)
            if any(sq.lower().strip() in template_words for sq in search_qs):
                logger.warning("LLM echoed template queries %s, using fallback", search_qs)
                return _fallback_entity_plans(query, entities)
            
            plans.append(EntityResearchPlan(
                entity=entity_name,
                questions=p.get("questions", []),
                search_queries=search_qs if search_qs else [f"{entity_name} {query}"],
                priority=float(p.get("priority", 0.5)),
                reasoning=p.get("reasoning", ""),
            ))
        
        if not plans:
            return _fallback_entity_plans(query, entities)
        
        logger.info(
            "Entity research plans: %s",
            [(p.entity, p.search_queries[:2]) for p in plans],
        )
        return plans
        
    except Exception as e:
        logger.warning("Entity research plan generation failed: %s, using fallback", e)
        return _fallback_entity_plans(query, entities)


def _fallback_entity_plans(query: str, entities: list[str]) -> list[EntityResearchPlan]:
    """Rule-based fallback when LLM fails — generates targeted queries from query context."""
    # Extract what's being compared from the query
    query_lower = query.lower()
    # Normalize common misspellings/spacing for fuzzy matching
    query_normalized = re.sub(r'\s+', '', query_lower)  # "block busters" → "blockbusters"
    comparison_aspects = []
    
    # Common comparison keywords → search terms
    # Keys are checked both as-is (with spaces) AND normalized (no spaces)
    aspect_keywords = {
        "blockbuster": "blockbuster movies box office gross",
        "box office": "box office gross worldwide revenue",
        "album": "albums total sales certified units",
        "movie": "movies filmography box office",
        "film": "filmography movies box office gross",
        "net worth": "net worth earnings salary",
        "award": "awards nominations wins",
        "hit": "hit songs movies chart performance",
        "record": "records achievements milestones",
        "salary": "salary earnings income",
        "height": "height physical stats",
        "age": "age birthday born",
        "popular": "popularity social media followers fans",
        "revenue": "total revenue earnings gross",
        "earning": "earnings revenue income gross",
        "rich": "net worth earnings wealth",
        "song": "songs discography chart performance",
        "goal": "goals scored career statistics",
        "score": "scores statistics career performance",
    }
    
    for keyword, aspect in aspect_keywords.items():
        # Match both exact ("blockbuster" in query) AND normalized ("blockbusters" in no-space query)
        keyword_normalized = re.sub(r'\s+', '', keyword)
        if keyword in query_lower or keyword_normalized in query_normalized:
            comparison_aspects.append(aspect)
    
    if not comparison_aspects:
        # Last resort: extract noun phrases from query as search aspects
        # instead of useless "key facts statistics data"
        words = [w for w in query_lower.split() if len(w) > 3 and w not in {
            'more', 'most', 'less', 'than', 'with', 'have', 'does', 'which',
            'what', 'that', 'this', 'from', 'they', 'them', 'their', 'about',
            'who', 'whom', 'whose',
        }]
        if words:
            comparison_aspects = [' '.join(words[:3]) + ' statistics data comparison']
        else:
            comparison_aspects = ["career achievements statistics comparison"]
    
    plans = []
    for entity in entities:
        search_queries = []
        for aspect in comparison_aspects[:2]:
            search_queries.append(f"{entity} {aspect}")
        # Always add a combined query
        search_queries.append(f"{entity} {query}")
        
        plans.append(EntityResearchPlan(
            entity=entity,
            questions=[f"What are {entity}'s key metrics for: {query}?"],
            search_queries=search_queries[:3],
            priority=0.7,
            reasoning=f"Fallback: targeted search for {entity}",
        ))
    return plans


# ── LLM Learning Evaluation ──

_EVALUATE_PROMPT = """Does the following retrieved data actually answer this question: "{query}"?

Retrieved data:
{learnings_text}

Judge honestly using these criteria:
- "sufficient": true if this data answers the question thoroughly with concrete metrics; false if critical info is missing
- "quality_score": 0.0 (completely irrelevant) to 1.0 (perfect comprehensive answer)
- "missing_aspects": list of what specific facts, figures, or aspects are still missing
- "follow_up_queries": targeted web search queries that would find the missing data

Example: If question is "who has more blockbusters, Tom Holland or Zendaya":
{{"sufficient": false, "quality_score": 0.35, "confidence": 0.85, "missing_aspects": ["box office revenue data for Tom Holland movies", "box office earnings for Zendaya filmography"], "follow_up_queries": ["Tom Holland movies worldwide box office gross", "Zendaya filmography box office totals"], "reasoning": "Retrieved data mentions their popularity but lacks specific box office numbers needed to compare"}}

Now evaluate the retrieved data for MY question. Return valid JSON only:"""


async def evaluate_learnings(
    query: str,
    learnings: list,
    *,
    client: Optional[NIMClient] = None,
) -> LearningEvaluation:
    """LLM evaluates if current learnings actually answer the query.
    
    This replaces heuristic satisfaction checking with actual reasoning.
    Uses chat_worker (Groq/fast) for low latency (~1-2s).
    """
    client = client or get_client()
    
    if not learnings:
        return LearningEvaluation(
            sufficient=False,
            confidence=1.0,
            missing_aspects=["No learnings retrieved"],
            quality_score=0.0,
            follow_up_queries=[query],
            reasoning="No data retrieved",
        )
    
    # Format learnings for LLM (truncate to keep prompt small)
    learning_texts = []
    for i, l in enumerate(learnings[:10]):  # Cap at 10 learnings
        text = getattr(l, 'text', str(l))[:180]
        learning_texts.append(f"[{i+1}] {text}")
    
    prompt = _EVALUATE_PROMPT.format(
        query=query,
        learnings_text="\n".join(learning_texts),
    )

    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an automated API backend that outputs raw machine-readable JSON. "
                    "CRITICAL: Do NOT output 'Here\\'s a thinking process', chain of thought, or preamble. "
                    "Do NOT use markdown code blocks. Start your response IMMEDIATELY with '{' and end with '}'. "
                    "Keep reasoning under 10 words."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = await client.chat_worker(
            messages,
            temperature=0.0,
            max_tokens=512,
            response_format_json=True,
        )
        
        parsed = _parse_json(response)
        if not parsed:
            logger.warning("LLM evaluation returned invalid JSON, falling back to heuristic. Raw snippet: %s", repr(response)[:120])
            return _heuristic_evaluation(query, learnings)
        
        # Filter out template placeholder queries
        raw_followup = parsed.get("follow_up_queries", [])
        template_indicators = {"query1", "query2", "q1", "q2", "search query 1", "search query 2"}
        clean_followup = [
            q for q in raw_followup
            if q.strip() and q.lower().strip() not in template_indicators and len(q) > 5
        ]
        
        # CRITICAL: Clamp quality_score to 0.0-1.0 range
        # LLMs frequently return quality on a 1-10 scale despite the prompt saying 0-1.
        # Without clamping, 8.4 > 0.85 → instant stop → kills the entire progressive loop.
        raw_quality = float(parsed.get("quality_score", 0.5))
        if raw_quality > 1.0:
            raw_quality = raw_quality / 10.0  # Normalize 1-10 → 0.0-1.0
            logger.debug("Quality score normalized from %.1f to %.2f (LLM used 1-10 scale)", raw_quality * 10, raw_quality)
        quality_clamped = max(0.0, min(1.0, raw_quality))
        
        return LearningEvaluation(
            sufficient=parsed.get("sufficient", False),
            confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.5)))),
            missing_aspects=parsed.get("missing_aspects", []),
            quality_score=quality_clamped,
            follow_up_queries=clean_followup,
            reasoning=parsed.get("reasoning", ""),
        )
        
    except Exception as e:
        logger.warning("LLM evaluation failed: %s, using heuristic", e)
        return _heuristic_evaluation(query, learnings)


def _heuristic_evaluation(query: str, learnings: list) -> LearningEvaluation:
    """Fallback heuristic when LLM eval fails."""
    query_terms = set(w.lower() for w in query.split() if len(w) > 3)
    if not query_terms:
        query_terms = set(w.lower() for w in query.split() if len(w) > 1)
    
    # Calculate unique terms covered across ANY learnings
    all_text = " ".join(getattr(l, 'text', str(l)).lower() for l in learnings)
    covered_terms = sum(1 for term in query_terms if term in all_text)
    
    coverage = covered_terms / max(len(query_terms), 1)
    coverage = max(0.0, min(1.0, coverage))
    return LearningEvaluation(
        sufficient=coverage >= 0.8 and len(learnings) >= 5,
        confidence=0.4,
        missing_aspects=[],
        quality_score=coverage,
        follow_up_queries=[],
        reasoning=f"Heuristic: {covered_terms}/{len(query_terms)} query terms covered ({coverage:.0%})",
    )


# ── Reasoning Decision (Stop/Continue/Pivot) ──

_DECISION_PROMPT = """I am directing an adaptive research agent for this objective: "{query}"
Status: {num_learnings} facts collected | Quality: {quality_score:.2f}/1.0 | Round: {round_num} | Elapsed: {elapsed_s:.0f}s
Missing info: {missing}
{kg_context}
{detective_context}

Action Strategy Guidelines:
- "continue": Critical aspects or factual data are still missing. Formulate targeted search queries to capture the missing metrics.
- "pivot": Current search angle has plateaued or missed the mark; pivot to alternative perspectives or fresh entity leads.
- "stop": Sufficient high-quality information has been collected to comprehensively answer the query.

Example: If researching "who sold more albums, Drake or Kanye":
{{"action": "continue", "queries": ["Drake total album sales worldwide certified units RIAA", "Kanye West total album sales worldwide certified"], "confidence": 0.85, "target": "certified sales comparison", "reasoning": "Need verified worldwide sales numbers to compare"}}

Now decide the best next action for MY research trajectory.
Return valid JSON only:"""


async def decide_next_action(
    query: str,
    learnings: list,
    *,
    client: Optional[NIMClient] = None,
    quality_score: float = 0.5,
    round_num: int = 0,
    elapsed_s: float = 0,
    missing_aspects: list[str] = None,
    kg_context: list[str] = None,
    detective_log: list[str] = None,
) -> ReasoningDecision:
    """LLM decides what to do next in the retrieval loop.
    
    This is the core "reasoning in the loop" — the LLM actively guides
    the retrieval process instead of just consuming results at the end.
    """
    client = client or get_client()
    
    # Fast path: only auto-stop if quality is near-perfect AND
    # we have substantial learnings across at least one progressive round.
    # Otherwise, let the LLM make the reasoning decision dynamically.
    if quality_score > 0.95 and len(learnings) >= 12 and round_num >= 1:
        return ReasoningDecision(
            action="stop",
            target="",
            queries=[],
            confidence=0.9,
            reasoning=f"High quality ({quality_score:.2f}) with {len(learnings)} learnings across progressive waves",
        )
    
    # Fast path: if too many rounds, force stop
    if round_num >= 3:
        return ReasoningDecision(
            action="stop",
            target="",
            queries=[],
            confidence=0.7,
            reasoning="Max retrieval rounds reached",
        )
    
    kg_text = ""
    if kg_context:
        kg_text = f"Knowledge Graph Connections:\n" + "\n".join(f"- {c}" for c in kg_context[:5])
        kg_text += "\n(Consider exploring these connected concepts)"
    
    detective_text = ""
    if detective_log:
        detective_text = "Detective Investigation Findings:\n" + "\n".join(f"- {d}" for d in detective_log[-5:])
    
    prompt = _DECISION_PROMPT.format(
        query=query,
        num_learnings=len(learnings),
        quality_score=quality_score,
        round_num=round_num,
        missing=json.dumps(missing_aspects or []),
        elapsed_s=elapsed_s,
        kg_context=kg_text,
        detective_context=detective_text,
    )

    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an automated research decision engine. Output strictly raw JSON. "
                    "CRITICAL: Do NOT output 'Here\\'s a thinking process', chain of thought, or preamble. "
                    "Do NOT use markdown code blocks. Start your response IMMEDIATELY with '{' and end with '}'. "
                    "Keep reasoning under 10 words."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = await client.chat_worker(
            messages,
            temperature=0.0,
            max_tokens=512,
            response_format_json=True,
        )
        
        parsed = _parse_json(response)
        if not parsed:
            logger.warning("decide_next_action: JSON parse failed, using fallback. Raw snippet: %s", repr(response)[:120])
            # Resilience: On Round 1, NEVER abort retrieval simply because an LLM JSON call had a transient error
            if round_num <= 1 and len(learnings) > 0:
                should_stop = False
            else:
                should_stop = (quality_score > 0.85 and len(learnings) >= 6) or (len(learnings) == 0 and round_num >= 2)
            
            return ReasoningDecision(
                action="stop" if should_stop else "continue",
                target="",
                queries=[query + " detailed facts data"] if not should_stop else [],
                confidence=0.4,
                reasoning="JSON parse failed, using safe fallback" + (" (continue retrieval)" if not should_stop else " (stop)"),
            )
        
        # Filter out any queries that look like template examples
        raw_queries = parsed.get("queries", [])
        template_indicators = {"q1", "q2", "query1", "query2", "search query", "specific search"}
        clean_queries = [
            q for q in raw_queries
            if q.strip() and q.lower().strip() not in template_indicators
            and len(q) > 5  # Reject very short placeholder text
        ]
        
        raw_action = str(parsed.get("action", "continue")).lower().strip()
        valid_actions = {"stop", "continue", "pivot", "dig_deeper"}
        action = raw_action if raw_action in valid_actions else "continue"

        return ReasoningDecision(
            action=action,
            target=parsed.get("target", ""),
            queries=clean_queries,
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
        )
        
    except Exception as e:
        logger.warning("LLM decision failed: %s", e)
        if round_num <= 1 and len(learnings) > 0:
            should_stop = False
        else:
            should_stop = (quality_score > 0.85 and len(learnings) >= 6) or (len(learnings) == 0 and round_num >= 2)
        return ReasoningDecision(
            action="stop" if should_stop else "continue",
            target="",
            queries=[query + " detailed facts data"] if not should_stop else [],
            confidence=0.3,
            reasoning=f"LLM decision failed ({type(e).__name__}): {e}" + (" (continue retrieval)" if not should_stop else " (stop)"),
        )


# ── Connected-Dot Exploration (KG-aware) ──

async def explore_connected_dots(
    query: str,
    entities: list[str],
    learnings: list,
    *,
    client: Optional[NIMClient] = None,
    ephemeral_kg: Optional[Any] = None,
) -> list[str]:
    """Use KG + LLM to find connected concepts worth exploring.
    
    This is where KG becomes useful — not as standalone search,
    but as CONTEXT for the LLM to reason about what else to explore.
    """
    client = client or get_client()
    
    # Try to get KG connections from EphemeralKG (Detective KG) and graph store
    kg_connections = []

    # Priority 1: Pull rich entity properties and cross-entity relations from EphemeralKG
    if ephemeral_kg and getattr(ephemeral_kg, "entities", None):
        for ename, profile in ephemeral_kg.entities.items():
            for prop_name, prop_val in list(profile.properties.items())[:4]:
                kg_connections.append(f"{ename} {prop_name}: {prop_val}")
            if profile.aliases:
                kg_connections.append(f"{ename} aliases: {', '.join(profile.aliases[:3])}")

    # Priority 2: Ingested triples from graph store
    try:
        from ..knowledge.graph_store import get_graph_store
        graph = get_graph_store()
        if graph.entity_count > 0:
            for entity in entities[:3]:
                triples = graph.query_entity(entity.lower(), hops=2)
                for t in triples:
                    # Collect connected entity names (not the query entity itself)
                    if t.object.lower() != entity.lower():
                        kg_connections.append(f"{t.subject} -> {t.predicate} -> {t.object}")
                    elif t.subject.lower() != entity.lower():
                        kg_connections.append(f"{t.object} -> {t.predicate} -> {t.subject}")
    except Exception:
        pass  # KG not available, that's fine
    
    kg_connections = list(dict.fromkeys(kg_connections))[:8]
    
    if not kg_connections and not learnings:
        return []
    
    # Compact prompt for speed
    learning_summary = " | ".join(
        getattr(l, 'text', str(l))[:60] for l in learnings[:4]
    )
    
    prompt = (
        f'Research: "{query}" | KG concepts: {json.dumps(kg_connections[:5])} | '
        f'Known: {learning_summary[:300]}\n'
        f'2 search queries for missing connected concepts. JSON array only: ["q1","q2"]'
    )

    try:
        response = await client.chat_worker(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=100,
        )
        
        match = re.search(r'\[.*?\]', response, re.DOTALL)
        if match:
            queries = json.loads(match.group())
            return [str(q).strip() for q in queries if q][:3]
    except Exception as e:
        logger.debug("Connected dots exploration failed: %s", e)
    
    return []


# ── Utility ──

def _parse_json(text: str) -> Optional[dict]:
    """Extract JSON from LLM response (handles thinking tags, markdown blocks, trailing commas, truncations)."""
    if not text:
        return None

    # Strip thinking tags <think>...</think>
    clean = re.sub(r'<think>.*?</think>', '', str(text), flags=re.DOTALL).strip()

    # 1. Try direct parse
    try:
        return json.loads(clean)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Try extracting from code block (handles cases with or without closing ```)
    match = re.search(r'```(?:json)?\s*([\s\S]*?)(?:```|$)', clean)
    if match:
        block = match.group(1).strip()
        try:
            return json.loads(block)
        except (json.JSONDecodeError, TypeError):
            # Clean trailing commas
            cleaned_block = re.sub(r',\s*([\]}])', r'\1', block)
            try:
                return json.loads(cleaned_block)
            except (json.JSONDecodeError, TypeError):
                clean = block

    # 3. Try extracting JSON object between first '{' and last '}'
    start = clean.find('{')
    end = clean.rfind('}')
    if start != -1 and end != -1 and end > start:
        snippet = clean[start:end+1]
        try:
            return json.loads(snippet)
        except (json.JSONDecodeError, TypeError):
            # Clean trailing commas
            cleaned_snippet = re.sub(r',\s*([\]}])', r'\1', snippet)
            try:
                return json.loads(cleaned_snippet)
            except (json.JSONDecodeError, TypeError):
                pass

    # 4. Fallback field extraction if JSON was truncated by max_tokens
    extracted: dict = {}
    action_match = re.search(r'"action"\s*:\s*"(\w+)"', clean)
    if action_match:
        extracted["action"] = action_match.group(1)

    suff_match = re.search(r'"sufficient"\s*:\s*(true|false)', clean, re.IGNORECASE)
    if suff_match:
        extracted["sufficient"] = suff_match.group(1).lower() == "true"

    qual_match = re.search(r'"quality_score"\s*:\s*([0-9.]+)', clean)
    if qual_match:
        try:
            extracted["quality_score"] = float(qual_match.group(1))
        except ValueError:
            pass

    conf_match = re.search(r'"confidence"\s*:\s*([0-9.]+)', clean)
    if conf_match:
        try:
            extracted["confidence"] = float(conf_match.group(1))
        except ValueError:
            pass

    target_match = re.search(r'"target"\s*:\s*"([^"]*)"', clean)
    if target_match:
        extracted["target"] = target_match.group(1)

    reason_match = re.search(r'"reasoning"\s*:\s*"([^"]*)"', clean)
    if reason_match:
        extracted["reasoning"] = reason_match.group(1)

    queries_match = re.search(r'"queries"\s*:\s*\[(.*?)\]', clean, re.DOTALL)
    if queries_match:
        raw_qs = re.findall(r'"([^"]+)"', queries_match.group(1))
        extracted["queries"] = [q for q in raw_qs if q.strip()]

    missing_match = re.search(r'"missing_aspects"\s*:\s*\[(.*?)\]', clean, re.DOTALL)
    if missing_match:
        raw_ms = re.findall(r'"([^"]+)"', missing_match.group(1))
        extracted["missing_aspects"] = [m for m in raw_ms if m.strip()]

    followup_match = re.search(r'"follow_up_queries"\s*:\s*\[(.*?)\]', clean, re.DOTALL)
    if followup_match:
        raw_fq = re.findall(r'"([^"]+)"', followup_match.group(1))
        extracted["follow_up_queries"] = [f for f in raw_fq if f.strip()]

    plans_match = re.search(r'"plans"\s*:\s*\[(.*?)\]', clean, re.DOTALL)
    if plans_match:
        # If plans array was generated but truncated, attempt minimal recovery
        pass

    if "action" in extracted or "sufficient" in extracted or "quality_score" in extracted:
        return extracted

    return None

