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

_ENTITY_QUESTIONS_PROMPT = """Generate targeted search queries per entity to answer: "{query}"
Entities: {entities}

DON'T search entity names. Generate SPECIFIC queries for the data needed.
Be concise. JSON only:
{{"plans":[{{"entity":"name","search_queries":["query1","query2"],"priority":0.9,"reasoning":"why"}}]}}"""


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
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
            response_format_json=True,
        )
        
        parsed = _parse_json(response)
        if not parsed or "plans" not in parsed:
            return _fallback_entity_plans(query, entities)
        
        plans = []
        for p in parsed["plans"]:
            plans.append(EntityResearchPlan(
                entity=p.get("entity", ""),
                questions=p.get("questions", []),
                search_queries=p.get("search_queries", [f"{p.get('entity', '')} {query}"]),
                priority=float(p.get("priority", 0.5)),
                reasoning=p.get("reasoning", ""),
            ))
        
        logger.info(
            "Entity research plans: %s",
            [(p.entity, len(p.search_queries)) for p in plans],
        )
        return plans
        
    except Exception as e:
        logger.warning("Entity research plan generation failed: %s, using fallback", e)
        return _fallback_entity_plans(query, entities)


def _fallback_entity_plans(query: str, entities: list[str]) -> list[EntityResearchPlan]:
    """Rule-based fallback when LLM fails."""
    plans = []
    for entity in entities:
        plans.append(EntityResearchPlan(
            entity=entity,
            questions=[f"What are the key facts about {entity}?"],
            search_queries=[
                f"{entity} {query}",
                f"{entity} detailed facts data statistics",
            ],
            priority=0.7,
            reasoning="Fallback: generic entity search",
        ))
    return plans


# ── LLM Learning Evaluation ──

_EVALUATE_PROMPT = """Does this data answer "{query}"? Be brutally honest.

Learnings:
{learnings_text}

JSON only:
{{"sufficient":bool,"confidence":0.8,"quality_score":0.6,"missing_aspects":["gap1"],"follow_up_queries":["query1"],"reasoning":"why"}}"""


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
    for i, l in enumerate(learnings[:12]):  # Cap at 12 learnings
        text = getattr(l, 'text', str(l))[:200]
        learning_texts.append(f"[{i+1}] {text}")
    
    prompt = _EVALUATE_PROMPT.format(
        query=query,
        learnings_text="\n".join(learning_texts),
    )

    try:
        response = await client.chat_worker(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=200,
            response_format_json=True,
        )
        
        parsed = _parse_json(response)
        if not parsed:
            return _heuristic_evaluation(query, learnings)
        
        return LearningEvaluation(
            sufficient=parsed.get("sufficient", False),
            confidence=float(parsed.get("confidence", 0.5)),
            missing_aspects=parsed.get("missing_aspects", []),
            quality_score=float(parsed.get("quality_score", 0.5)),
            follow_up_queries=parsed.get("follow_up_queries", []),
            reasoning=parsed.get("reasoning", ""),
        )
        
    except Exception as e:
        logger.warning("LLM evaluation failed: %s, using heuristic", e)
        return _heuristic_evaluation(query, learnings)


def _heuristic_evaluation(query: str, learnings: list) -> LearningEvaluation:
    """Fallback heuristic when LLM eval fails."""
    query_terms = set(w.lower() for w in query.split() if len(w) > 3)
    covered = 0
    for l in learnings:
        text = getattr(l, 'text', str(l)).lower()
        for term in query_terms:
            if term in text:
                covered += 1
                break
    
    coverage = covered / max(len(query_terms), 1)
    return LearningEvaluation(
        sufficient=coverage > 0.7 and len(learnings) >= 3,
        confidence=0.4,
        missing_aspects=[],
        quality_score=coverage,
        follow_up_queries=[],
        reasoning=f"Heuristic: {coverage:.0%} term coverage",
    )


# ── Reasoning Decision (Stop/Continue/Pivot) ──

_DECISION_PROMPT = """Query: "{query}" | Learnings: {num_learnings} | Quality: {quality_score:.1f} | Round: {round_num} | Time: {elapsed_s:.0f}s
Missing: {missing}
{kg_context}
Action? stop/continue/pivot/dig_deeper. If continue, provide 2 search queries.
JSON: {{"action":"continue","queries":["q1","q2"],"confidence":0.7,"reasoning":"why"}}"""


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
) -> ReasoningDecision:
    """LLM decides what to do next in the retrieval loop.
    
    This is the core "reasoning in the loop" — the LLM actively guides
    the retrieval process instead of just consuming results at the end.
    """
    client = client or get_client()
    
    # Fast path: if quality is very high and we have enough data, stop
    if quality_score > 0.85 and len(learnings) >= 5:
        return ReasoningDecision(
            action="stop",
            target="",
            queries=[],
            confidence=0.9,
            reasoning=f"High quality ({quality_score:.2f}) with {len(learnings)} learnings",
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
    
    prompt = _DECISION_PROMPT.format(
        query=query,
        num_learnings=len(learnings),
        quality_score=quality_score,
        round_num=round_num,
        missing=json.dumps(missing_aspects or []),
        elapsed_s=elapsed_s,
        kg_context=kg_text,
    )

    try:
        response = await client.chat_worker(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=150,
            response_format_json=True,
        )
        
        parsed = _parse_json(response)
        if not parsed:
            return ReasoningDecision(
                action="stop" if quality_score > 0.6 else "continue",
                target="",
                queries=[query + " more details"] if quality_score <= 0.6 else [],
                confidence=0.4,
                reasoning="JSON parse failed, using heuristic",
            )
        
        return ReasoningDecision(
            action=parsed.get("action", "stop"),
            target=parsed.get("target", ""),
            queries=parsed.get("queries", []),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
        )
        
    except Exception as e:
        logger.warning("LLM decision failed: %s", e)
        return ReasoningDecision(
            action="stop" if quality_score > 0.6 else "continue",
            target="",
            queries=[],
            confidence=0.3,
            reasoning=f"LLM decision failed: {e}",
        )


# ── Connected-Dot Exploration (KG-aware) ──

async def explore_connected_dots(
    query: str,
    entities: list[str],
    learnings: list,
    *,
    client: Optional[NIMClient] = None,
) -> list[str]:
    """Use KG + LLM to find connected concepts worth exploring.
    
    This is where KG becomes useful — not as standalone search,
    but as CONTEXT for the LLM to reason about what else to explore.
    """
    client = client or get_client()
    
    # Try to get KG connections
    kg_connections = []
    try:
        from ..knowledge.graph_rag import GraphRAG
        graph = GraphRAG()
        for entity in entities[:3]:
            neighbors = graph.find_similar_concepts(entity.lower(), top_k=3)
            kg_connections.extend(neighbors)
        kg_connections = list(set(kg_connections))[:8]
    except Exception:
        pass  # KG not available, that's fine
    
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
    """Extract JSON from LLM response (handles markdown code blocks)."""
    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Try extracting from code block
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Try extracting JSON object
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, TypeError):
            pass
    
    return None
