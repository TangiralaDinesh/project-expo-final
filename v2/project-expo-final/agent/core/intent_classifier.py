"""
Dynamic intent classification for multi-entity and multi-task queries.

REPLACES rigid comparison detection with flexible, attention-aware query understanding:

1. QUERY INTENT: Classify what the user actually wants
   - NARRATIVE: "X went to Y's house" → storytelling, information gathering
   - COMPARISON: "X vs Y" → decision-making, evaluation
   - RESEARCH: "Who is X?" → fact-finding
   - DECISION: "Should I use X or Y?" → choice making
   - ANALYSIS: "How does X work?" → deep understanding
   - MULTI_TASK: "X and Y in context of Z" → multiple independent tasks

2. FOCUS AREAS: Extract multiple concepts/entities (not just binary pairs)
   - Each entity has relevance, relationship type, and required retrieval depth
   - Satisfaction layer can guide which to prioritize

3. DECOMPOSITION STRATEGY: Based on intent, decide HOW to decompose
   - Narrative → single retriever with multi-concept focus
   - Comparison → parallel retrievers per entity + synthesis
   - Research → targeted fact-finding
   - Multi-task → parallel independent tasks per concept
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .satisfaction import SatisfactionTracker

from ..llm.client import NIMClient, get_client
import logging

logger = logging.getLogger(__name__)


class QueryIntent(Enum):
    """Classification of what the user wants to do."""
    NARRATIVE = "narrative"          # Story, sequence, information gathering
    COMPARISON = "comparison"        # X vs Y (decision)
    RESEARCH = "research"            # Who/what/when/where/why questions
    DECISION = "decision"            # Should I choose X or Y?
    ANALYSIS = "analysis"            # Deep dive: how does X work?
    CREATIVE = "creative"            # Generate, create, design
    MULTI_TASK = "multi_task"        # Multiple independent concepts
    TROUBLESHOOTING = "troubleshooting"  # Fix problem with X
    CALCULATION = "calculation"      # Compute, calculate, quantify


@dataclass
class FocusArea:
    """A concept/entity the query focuses on."""
    name: str
    entity_type: str              # "person", "concept", "tool", "decision_option", etc.
    relevance: float              # 0-1, importance in query
    relationship_type: str        # "primary", "secondary", "context", "comparison"
    retrieval_depth: str          # "summary", "details", "comprehensive"
    keywords: list[str] = field(default_factory=list)
    
    def __repr__(self):
        return f"FocusArea({self.name}, type={self.entity_type}, rel={self.relevance:.2f})"


@dataclass
class QueryIntentAnalysis:
    """Result of intent classification."""
    intent: QueryIntent
    confidence: float              # 0-1, how confident in classification
    focus_areas: list[FocusArea]  # Multiple entities/concepts
    requires_comparison: bool      # True if actually comparing
    requires_parallel: bool        # True if tasks are independent
    suggested_decomposition: str   # "single", "parallel", "sequential", "hierarchical"
    reasoning: str                 # Explanation of classification
    metadata: dict = field(default_factory=dict)


class IntentClassifier:
    """
    Dynamically classify query intent and extract focus areas.
    
    Replaces rigid comparison detection with flexible understanding that:
    - Handles narrative queries with multiple entities (no false positive comparisons)
    - Supports multi-task decomposition
    - Uses satisfaction layer to guide retrieval depth
    - Applies attention mechanism to track all focus areas
    """
    
    # Intent detection keywords
    COMPARISON_KEYWORDS = {
        "vs", "versus", "vs.", "better", "best", "worse", "compare", 
        "comparison", "difference", "distinguish", "choose between",
        "which is", "which one", "pick", "or", "either"
    }
    
    DECISION_KEYWORDS = {
        "should", "choose", "buy", "use", "go with", "pick",
        "recommend", "suggest", "better", "best", "prefer"
    }
    
    RESEARCH_KEYWORDS = {
        "who", "what", "when", "where", "why", "how", "which",
        "what is", "tell me", "explain", "describe", "information about"
    }
    
    ANALYSIS_KEYWORDS = {
        "how does", "explain", "mechanism", "process", "work", "system",
        "architecture", "design", "implementation", "structure", "algorithm"
    }
    
    TROUBLESHOOTING_KEYWORDS = {
        "fix", "error", "bug", "problem", "issue", "broken", "not working",
        "debug", "crash", "fail", "why is", "trouble", "wrong"
    }
    
    def __init__(self, client: Optional[NIMClient] = None):
        self.client = client or get_client()
    
    async def analyze(
        self, 
        query: str,
        satisfaction_tracker: Optional[SatisfactionTracker] = None,
        use_llm: bool = True,
    ) -> QueryIntentAnalysis:
        """
        Analyze query intent and extract focus areas.
        
        Pipeline:
        1. Fast heuristic pass (sync, <1ms)
        2. LLM entity extraction (async, ~2s) — corrects typos, extracts clean names
        3. If LLM returns entities, override heuristic focus areas
        """
        # Fast heuristic pass
        heuristic_result = self._analyze_heuristic(query)
        
        if not use_llm:
            return heuristic_result
        
        # Run LLM entity extraction + optional LLM intent analysis IN PARALLEL
        tasks = [self._llm_entity_extract(query)]
        if heuristic_result.confidence < 0.7:
            tasks.append(self._analyze_llm(query, satisfaction_tracker))
        
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=8.0,
            )
        except asyncio.TimeoutError:
            logger.warning("LLM intent/entity extraction timed out, using heuristic")
            return heuristic_result
        
        # Process LLM entity extraction result
        llm_entities = results[0] if isinstance(results[0], list) else []
        
        if llm_entities:
            # LLM returned corrected entities — rebuild focus areas
            logger.info("Using LLM-corrected entities: %s (heuristic had: %s)",
                       llm_entities, [fa.name for fa in heuristic_result.focus_areas])
            
            new_focus_areas = []
            for i, entity in enumerate(llm_entities):
                if i == 0:
                    rel_type = "primary"
                elif len(llm_entities) >= 2 and heuristic_result.intent == QueryIntent.COMPARISON:
                    rel_type = "comparison"
                else:
                    rel_type = "secondary"
                
                new_focus_areas.append(FocusArea(
                    name=entity,
                    entity_type=self._infer_entity_type(entity),
                    relevance=max(0, 1.0 - (i * 0.15)),
                    relationship_type=rel_type,
                    retrieval_depth="comprehensive" if rel_type == "primary" else "detailed",
                    keywords=[entity.lower()],
                ))
            
            heuristic_result.focus_areas = new_focus_areas
            # Boost confidence since LLM confirmed the entities
            heuristic_result.confidence = max(heuristic_result.confidence, 0.75)
        
        # Process LLM intent analysis result (if it ran)
        if len(results) > 1 and not isinstance(results[1], Exception):
            try:
                return self._blend_results(heuristic_result, results[1], query)
            except Exception as e:
                logger.debug("LLM intent blend failed: %s", e)
        
        return heuristic_result
    
    def _analyze_heuristic(self, query: str) -> QueryIntentAnalysis:
        """Fast heuristic analysis using keywords and patterns."""
        query_lower = query.lower()
        word_count = len(query.split())
        
        # NEW: Detect narrative markers (past tense, storytelling)
        narrative_markers = [
            "went", "was", "were", "happened", "occurred", "had", "came",
            "story", "history", "timeline", "sequence", "happened then",
            "afterwards", "before that", "next", "then", "subsequently"
        ]
        narrative_score = sum(1 for marker in narrative_markers if marker in query_lower)
        
        # Count keyword matches
        comparison_score = sum(1 for kw in self.COMPARISON_KEYWORDS if kw in query_lower)
        decision_score = sum(1 for kw in self.DECISION_KEYWORDS if kw in query_lower)
        research_score = sum(1 for kw in self.RESEARCH_KEYWORDS if kw in query_lower)
        analysis_score = sum(1 for kw in self.ANALYSIS_KEYWORDS if kw in query_lower)
        troubleshooting_score = sum(1 for kw in self.TROUBLESHOOTING_KEYWORDS if kw in query_lower)
        
        # NEW: Penalize comparison if narrative markers present (fixes false positives)
        if narrative_score > 0:
            comparison_score = max(0, comparison_score - narrative_score)
        
        # Determine primary intent
        scores = {
            QueryIntent.COMPARISON: comparison_score + decision_score * 0.5,
            QueryIntent.DECISION: decision_score + comparison_score * 0.3,
            QueryIntent.RESEARCH: research_score,
            QueryIntent.ANALYSIS: analysis_score,
            QueryIntent.TROUBLESHOOTING: troubleshooting_score,
        }
        
        if max(scores.values()) == 0:
            # Default to narrative/research
            intent = QueryIntent.NARRATIVE if narrative_score > 0 else QueryIntent.RESEARCH
            confidence = 0.5 if narrative_score > 0 else 0.4
        else:
            intent = max(scores.items(), key=lambda x: x[1])[0]
            confidence = min(max(scores.values()) / max(1, word_count), 1.0)
        
        # Extract focus areas
        focus_areas = self._extract_focus_areas(query, intent)
        
        # NEW: Progressive retrieval strategy for multi-entity queries
        # Instead of just "parallel" vs "single", now supports stages
        requires_comparison = (
            intent == QueryIntent.COMPARISON or 
            (intent == QueryIntent.DECISION and len(focus_areas) >= 2)
        )
        requires_parallel = len(focus_areas) > 1 and intent in (
            QueryIntent.MULTI_TASK, QueryIntent.COMPARISON, QueryIntent.DECISION
        )
        
        # NEW: Decomposition strategy with progressive retrieval awareness
        if requires_comparison and len(focus_areas) >= 2:
            suggested_decomposition = "parallel_with_synthesis"  # NEW: explicit synthesis stage
        elif len(focus_areas) > 2:
            # Multi-entity progressive: retrieve primary first, then secondary with context
            suggested_decomposition = "progressive_multi_entity"  # NEW: staged retrieval
        elif len(focus_areas) > 1:
            suggested_decomposition = "hierarchical"
        else:
            suggested_decomposition = "single"
        
        analysis = QueryIntentAnalysis(
            intent=intent,
            confidence=confidence,
            focus_areas=focus_areas,
            requires_comparison=requires_comparison,
            requires_parallel=requires_parallel,
            suggested_decomposition=suggested_decomposition,
            reasoning=f"Heuristic classification: {intent.value} with {len(focus_areas)} focus areas, "
                      f"strategy={suggested_decomposition}"
        )
        
        # NEW: Add metadata for progressive retrieval
        analysis.metadata = {
            "narrative_score": narrative_score,
            "entity_count": len(focus_areas),
            "can_use_progressive_retrieval": len(focus_areas) > 1,
            "retrieval_stages": self._compute_retrieval_stages(focus_areas, intent)
        }
        
        return analysis
    
    def _extract_focus_areas(self, query: str, intent: QueryIntent) -> list[FocusArea]:
        """Extract multiple focus areas (entities/concepts) from query.
        
        Uses heuristic extraction first (fast, sync), then the async
        LLM-based extraction corrects/enhances results (handles typos, 
        disambiguates entities). The LLM path is called separately in
        analyze() when confidence is low.
        """
        focus_areas = []
        
        # ── Heuristic extraction (fast, sync) ──
        entities = self._heuristic_entity_extract(query, intent)
        
        if not entities:
            focus_areas.append(FocusArea(
                name="query_topic",
                entity_type="concept",
                relevance=1.0,
                relationship_type="primary",
                retrieval_depth="comprehensive",
                keywords=[]
            ))
        else:
            entity_list = list(entities)
            for i, entity in enumerate(entity_list):
                frequency = query.lower().count(entity.lower())
                position_relevance = max(0, 1.0 - (i * 0.2))
                frequency_relevance = min(frequency / 3.0, 1.0)
                relevance = (position_relevance + frequency_relevance) / 2.0
                
                if i == 0:
                    relationship_type = "primary"
                elif len(entity_list) == 2 and intent == QueryIntent.COMPARISON:
                    relationship_type = "comparison"
                else:
                    relationship_type = "secondary"
                
                if relationship_type == "primary":
                    retrieval_depth = "comprehensive"
                elif relationship_type == "comparison":
                    retrieval_depth = "detailed"
                else:
                    retrieval_depth = "summary"
                
                focus_areas.append(FocusArea(
                    name=entity,
                    entity_type=self._infer_entity_type(entity),
                    relevance=relevance,
                    relationship_type=relationship_type,
                    retrieval_depth=retrieval_depth,
                    keywords=[entity.lower()]
                ))
        
        # Sort by relevance
        focus_areas.sort(key=lambda x: x.relevance, reverse=True)
        return focus_areas
    
    def _heuristic_entity_extract(self, query: str, intent: QueryIntent) -> set[str]:
        """Fast sync heuristic: regex + stop-word cleaning for entity extraction."""
        words = query.split()
        proper_nouns = [w.rstrip("'\".,;:!?") for w in words if len(w) > 1 and w[0].isupper()]
        common = {"The", "A", "An", "I", "We", "You", "They", "Is", "Are", "Was", "Do", "Does"}
        proper_nouns = [n for n in proper_nouns if n not in common]
        
        # VS pattern
        vs_pattern = r"(.+?)\s+(?:vs\.?|versus)\s+(.+?)(?:\s+(?:who|what|which|how|more|less|had|have|has)\b.*)?$"
        vs_match = re.match(vs_pattern, query, re.IGNORECASE)
        
        stop_words = {
            "who", "what", "which", "where", "when", "how", "why",
            "have", "has", "had", "more", "less", "most", "least",
            "the", "a", "an", "is", "are", "was", "were", "do", "does",
            "better", "worse", "bigger", "smaller", "than",
            "many", "much", "few", "some", "all", "any", "block",
            "busters", "blockbusters", "movies", "films",
        }
        
        def _clean(raw: str) -> str:
            parts = raw.strip().split()
            while parts and parts[-1].lower() in stop_words:
                parts.pop()
            while parts and parts[0].lower() in stop_words:
                parts.pop(0)
            return " ".join(parts).strip()
        
        entities = set()
        
        if vs_match:
            left = _clean(vs_match.group(1))
            right = _clean(vs_match.group(2))
            if left and len(left) > 1:
                entities.add(left)
            if right and len(right) > 1:
                entities.add(right)
        
        # Proper nouns as fallback
        for noun in proper_nouns[:3]:
            cleaned = _clean(noun)
            if cleaned and len(cleaned) > 1 and cleaned not in entities:
                entities.add(cleaned)
        
        return entities
    
    async def _llm_entity_extract(self, query: str) -> list[str]:
        """LLM-based entity extraction with typo correction.
        
        Uses chat_worker (fast path) to:
        1. Correct typos ("zndaya" → "Zendaya")
        2. Extract clean entity names
        3. Identify comparison metric
        
        Returns list of corrected entity names, or empty list on failure.
        """
        prompt = (
            f'Extract the key entity names from this query. Fix any typos in the names.\n'
            f'Query: "{query}"\n\n'
            f'Example: "tom hollandd vs zndaya who had more blokbusters"\n'
            f'Answer: ["Tom Holland", "Zendaya"]\n\n'
            f'Return ONLY a JSON array of corrected entity names (max 4 entities):'
        )
        
        try:
            raw = await self.client.chat_worker(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=80,
            )
            
            import json
            text = raw.strip()
            # Extract JSON array
            match = re.search(r'\[.*?\]', text, re.DOTALL)
            if match:
                entities = json.loads(match.group())
                if isinstance(entities, list):
                    # Validate — reject if template placeholders were echoed
                    clean = [str(e).strip() for e in entities if str(e).strip() and len(str(e)) > 1]
                    template_words = {"entity", "name", "entity1", "entity2", "example"}
                    clean = [e for e in clean if e.lower() not in template_words]
                    if clean:
                        logger.info("LLM entity extraction: %s", clean)
                        return clean[:4]
        except Exception as e:
            logger.debug("LLM entity extraction failed: %s", e)
        
        return []

    def _infer_entity_type(self, entity: str) -> str:
        """Infer what type of entity this is (person, place, tool, concept, etc.)."""
        entity_lower = entity.lower()
        
        # Simple heuristics
        if any(title in entity for title in ["Mr.", "Mrs.", "Dr.", "Prof."]):
            return "person"
        if any(place in entity_lower for place in ["city", "country", "region", "state", "street"]):
            return "place"
        if any(lang in entity_lower for lang in ["python", "javascript", "go", "rust", "java", "c++"]):
            return "programming_language"
        if any(tool in entity_lower for tool in ["react", "django", "flask", "node", "express", "docker"]):
            return "tool"
        
        # Default
        return "entity"
    
    def _compute_retrieval_stages(self, focus_areas: list[FocusArea], intent: QueryIntent) -> list[dict]:
        """Compute progressive retrieval stages for multi-entity queries.
        
        Returns list of stages, each with:
        - stage_id: "wave_1", "wave_2_synthesis", etc.
        - entities: focus areas to retrieve in this stage
        - focus_type: "primary", "comparison", "context"
        - expected_quality: "high", "medium", "low"
        """
        stages = []
        
        if len(focus_areas) == 0:
            return stages
        
        if len(focus_areas) == 1:
            # Single entity: one stage
            return [{
                "stage_id": "single_entity",
                "entities": [focus_areas[0].name],
                "focus_type": "primary",
                "expected_quality": "high"
            }]
        
        # Multi-entity progressive retrieval
        primary_entities = [e for e in focus_areas if e.relationship_type == "primary"]
        comparison_entities = [e for e in focus_areas if e.relationship_type == "comparison"]
        secondary_entities = [e for e in focus_areas if e.relationship_type == "secondary"]
        
        # Stage 1: Primary entity with highest retrieval depth
        if primary_entities:
            stages.append({
                "stage_id": "wave_1_primary",
                "entities": [e.name for e in primary_entities],
                "focus_type": "primary",
                "expected_quality": "high",
                "description": "Retrieve comprehensive information on primary focus areas"
            })
        
        # Stage 2: Comparison entities (if any)
        if comparison_entities and intent == QueryIntent.COMPARISON:
            stages.append({
                "stage_id": "wave_1_comparison",
                "entities": [e.name for e in comparison_entities],
                "focus_type": "comparison",
                "expected_quality": "high",
                "description": "Retrieve comparable information on secondary entities"
            })
        
        # Stage 3: Synthesis/secondary refinement
        if len(focus_areas) > 2:
            stages.append({
                "stage_id": "wave_2_synthesis",
                "entities": [e.name for e in focus_areas],
                "focus_type": "context",
                "expected_quality": "medium",
                "description": "Synthesize and fill gaps across all entities"
            })
        
        return stages
    
    async def _analyze_llm(
        self,
        query: str,
        satisfaction_tracker: Optional[SatisfactionTracker] = None,
    ) -> QueryIntentAnalysis:
        """Use LLM for sophisticated intent analysis."""
        
        guidance = ""
        if satisfaction_tracker:
            concepts = satisfaction_tracker.extract_concepts(query)
            guidance = f"\nBased on user history, key concepts of interest: {', '.join(concepts[:5])}"
        
        prompt = f"""Analyze this query to determine user intent and focus areas:

Query: "{query}"{guidance}

Classify the intent as ONE of:
- "narrative": storytelling or information gathering about a sequence/relationship
- "comparison": evaluating/comparing multiple options
- "research": factual questions (who/what/when/where/why)
- "decision": choosing between options
- "analysis": deep understanding of how something works
- "multi_task": multiple independent concepts to explore
- "troubleshooting": fixing a problem
- "creative": generating/creating content
- "calculation": computing numeric results

Respond with JSON:
{{
  "intent": "one_of_above",
  "confidence": 0.0_to_1.0,
  "focus_areas": [
    {{"name": "entity_name", "type": "person|tool|concept|etc", "relevance": 0.0_to_1.0}},
    ...
  ],
  "is_comparison": true_or_false,
  "is_parallel": true_or_false,
  "decomposition": "single|parallel|sequential|hierarchical",
  "reasoning": "brief explanation"
}}"""
        
        try:
            response = await self.client.chat_fast(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format_json=True
            )
            
            data = json.loads(response)
            
            # Convert to QueryIntentAnalysis
            intent = QueryIntent(data.get("intent", "narrative"))
            
            focus_areas = [
                FocusArea(
                    name=fa["name"],
                    entity_type=fa.get("type", "entity"),
                    relevance=fa.get("relevance", 0.5),
                    relationship_type="primary" if fa.get("relevance", 0) > 0.7 else "secondary",
                    retrieval_depth="comprehensive" if fa.get("relevance", 0) > 0.7 else "summary",
                )
                for fa in data.get("focus_areas", [])
            ]
            
            return QueryIntentAnalysis(
                intent=intent,
                confidence=data.get("confidence", 0.5),
                focus_areas=focus_areas,
                requires_comparison=data.get("is_comparison", False),
                requires_parallel=data.get("is_parallel", False),
                suggested_decomposition=data.get("decomposition", "single"),
                reasoning=data.get("reasoning", "LLM analysis"),
            )
        except Exception as e:
            logger.error(f"LLM intent analysis failed: {e}")
            raise
    
    def _blend_results(
        self,
        heuristic: QueryIntentAnalysis,
        llm: QueryIntentAnalysis,
        query: str,
    ) -> QueryIntentAnalysis:
        """Blend heuristic and LLM results, preferring LLM for intent but heuristic for focus areas."""
        # Use LLM intent (higher confidence than heuristics)
        # Use merged focus areas (combine both)
        
        merged_focus_areas = {}
        
        # Add heuristic focus areas
        for fa in heuristic.focus_areas:
            merged_focus_areas[fa.name] = fa
        
        # Update with LLM focus areas (may override relevance scores)
        for fa in llm.focus_areas:
            if fa.name in merged_focus_areas:
                # Update relevance from LLM
                merged_focus_areas[fa.name].relevance = fa.relevance
                merged_focus_areas[fa.name].relationship_type = fa.relationship_type
            else:
                merged_focus_areas[fa.name] = fa
        
        combined_areas = list(merged_focus_areas.values())
        combined_areas.sort(key=lambda x: x.relevance, reverse=True)
        
        return QueryIntentAnalysis(
            intent=llm.intent,  # Trust LLM intent
            confidence=max(heuristic.confidence, llm.confidence),
            focus_areas=combined_areas,
            requires_comparison=llm.requires_comparison or heuristic.requires_comparison,
            requires_parallel=llm.requires_parallel or heuristic.requires_parallel,
            suggested_decomposition=llm.suggested_decomposition,
            reasoning=f"Blended analysis: LLM intent={llm.intent.value}, focus_areas={len(combined_areas)}"
        )
