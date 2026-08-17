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
        
        Args:
            query: User query
            satisfaction_tracker: Optional satisfaction tracker for guidance
            use_llm: If True, use LLM for complex analysis; else use heuristics
        
        Returns:
            QueryIntentAnalysis with intent, focus areas, and decomposition strategy
        """
        # Fast heuristic pass
        heuristic_result = self._analyze_heuristic(query)
        
        # If low confidence or complex query, use LLM for verification
        if use_llm and heuristic_result.confidence < 0.7:
            try:
                llm_result = await self._analyze_llm(query, satisfaction_tracker)
                # Blend heuristic and LLM results
                return self._blend_results(heuristic_result, llm_result, query)
            except Exception as e:
                logger.debug(f"LLM intent analysis failed: {e}, using heuristic")
                return heuristic_result
        
        return heuristic_result
    
    def _analyze_heuristic(self, query: str) -> QueryIntentAnalysis:
        """Fast heuristic analysis using keywords and patterns."""
        query_lower = query.lower()
        word_count = len(query.split())
        
        # Count keyword matches
        comparison_score = sum(1 for kw in self.COMPARISON_KEYWORDS if kw in query_lower)
        decision_score = sum(1 for kw in self.DECISION_KEYWORDS if kw in query_lower)
        research_score = sum(1 for kw in self.RESEARCH_KEYWORDS if kw in query_lower)
        analysis_score = sum(1 for kw in self.ANALYSIS_KEYWORDS if kw in query_lower)
        troubleshooting_score = sum(1 for kw in self.TROUBLESHOOTING_KEYWORDS if kw in query_lower)
        
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
            intent = QueryIntent.NARRATIVE
            confidence = 0.5
        else:
            intent = max(scores.items(), key=lambda x: x[1])[0]
            confidence = min(max(scores.values()) / max(1, word_count), 1.0)
        
        # Extract focus areas
        focus_areas = self._extract_focus_areas(query, intent)
        
        # Decide decomposition strategy based on intent and focus area count
        # IMPORTANT FIX: Treat DECISION with 2+ entities as comparison too
        requires_comparison = (
            intent == QueryIntent.COMPARISON or 
            (intent == QueryIntent.DECISION and len(focus_areas) >= 2)
        )
        requires_parallel = len(focus_areas) > 1 and intent in (
            QueryIntent.MULTI_TASK, QueryIntent.COMPARISON, QueryIntent.DECISION
        )
        
        if requires_comparison and len(focus_areas) >= 2:
            suggested_decomposition = "parallel"
        elif len(focus_areas) > 1:
            suggested_decomposition = "hierarchical"
        else:
            suggested_decomposition = "single"
        
        return QueryIntentAnalysis(
            intent=intent,
            confidence=confidence,
            focus_areas=focus_areas,
            requires_comparison=requires_comparison,
            requires_parallel=requires_parallel,
            suggested_decomposition=suggested_decomposition,
            reasoning=f"Heuristic classification: {intent.value} with {len(focus_areas)} focus areas"
        )
    
    def _extract_focus_areas(self, query: str, intent: QueryIntent) -> list[FocusArea]:
        """Extract multiple focus areas (entities/concepts) from query."""
        focus_areas = []
        
        # Extract proper nouns (likely entities)
        # Simple heuristic: capitalized words
        words = query.split()
        proper_nouns = [w.rstrip("'\".,;:!?") for w in words if w[0].isupper() and len(w) > 1]
        
        # Remove common words
        common = {"The", "A", "An", "I", "We", "You", "They"}
        proper_nouns = [n for n in proper_nouns if n not in common]
        
        # Extract concepts from query patterns
        # "X and Y" patterns
        and_pattern = r"(\w+(?:\s+\w+)?)\s+and\s+(\w+(?:\s+\w+)?)"
        and_matches = re.findall(and_pattern, query, re.IGNORECASE)
        
        # "X vs Y" patterns
        vs_pattern = r"(\w+(?:\s+\w+)?)\s+(?:vs\.?|versus)\s+(\w+(?:\s+\w+)?)"
        vs_matches = re.findall(vs_pattern, query, re.IGNORECASE)
        
        # Collect unique entities
        entities = set()
        
        # From proper nouns
        for noun in proper_nouns[:3]:  # Limit to top 3
            entities.add(noun)
        
        # From patterns
        for match in and_matches[:2]:
            entities.add(match[0].strip())
            entities.add(match[1].strip())
        
        for match in vs_matches[:2]:
            entities.add(match[0].strip())
            entities.add(match[1].strip())
        
        # Determine focus area properties
        if not entities:
            # No specific entities, treat whole query as single focus
            focus_areas.append(FocusArea(
                name="query_topic",
                entity_type="concept",
                relevance=1.0,
                relationship_type="primary",
                retrieval_depth="comprehensive",
                keywords=[]
            ))
        else:
            # Rank entities by relevance (position in query, mention count)
            entity_list = list(entities)
            for i, entity in enumerate(entity_list):
                # Earlier entities and more frequent ones are more relevant
                frequency = query.lower().count(entity.lower())
                position_relevance = max(0, 1.0 - (i * 0.2))
                frequency_relevance = min(frequency / 3.0, 1.0)
                relevance = (position_relevance + frequency_relevance) / 2.0
                
                # Determine relationship type
                if i == 0:
                    relationship_type = "primary"
                elif len(entity_list) == 2 and intent == QueryIntent.COMPARISON:
                    relationship_type = "comparison"
                else:
                    relationship_type = "secondary"
                
                # Determine retrieval depth based on position
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
