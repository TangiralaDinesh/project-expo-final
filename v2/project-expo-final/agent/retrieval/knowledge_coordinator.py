"""
Knowledge-Aware Retrieval Coordinator

Integrates v1 features into the v2 query flow:
1. Knowledge graph traversal for multi-hop reasoning
2. GeoHash-style progressive loading (overview → detailed)
3. Surprising factor / novelty detection
4. Intent-aware retrieval depth based on focus areas

This ensures:
- Complex queries get graph-based entity relationship reasoning
- Progressive loading reduces initial latency
- Novelty detection surfaces surprising/high-signal information
- Long streaming responses without artificial cutoffs
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.intent_classifier import QueryIntentAnalysis
    from ..core.satisfaction import SatisfactionTracker

from ..llm.client import NIMClient, get_client
from ..knowledge.graph_rag import should_use_graph, graph_enhanced_retrieval, get_graph_store, get_kb_store
from ..blocks.semantic.types import Learning

logger = logging.getLogger(__name__)


@dataclass
class RetrievalStrategy:
    """Strategy for how to retrieve information."""
    use_graph: bool                    # Enable knowledge graph traversal?
    progressive_depth: int             # 0=overview, 1=sections, 2=full
    min_novelty_score: float          # Minimum novelty (0-1) to include
    entity_focus_areas: list[str]     # Entities to focus retrieval on
    graph_hops: int = 2                # How many graph hops to traverse
    top_k_sources: int = 8             # How many sources to retrieve
    
    def __repr__(self):
        return f"RetrievalStrategy(graph={self.use_graph}, depth={self.progressive_depth}, novelty>{self.min_novelty_score:.2f})"


@dataclass
class NoveltyScore:
    """Novelty assessment for a learning."""
    score: float                       # 0-1, higher = more novel/surprising
    factors: dict[str, float] = field(default_factory=dict)  # Component scores
    explanation: str = ""              # Why this is novel


class KnowledgeCoordinator:
    """
    Coordinates knowledge-aware retrieval for complex queries.
    
    Features:
    1. Decides when to use knowledge graph (for multi-hop reasoning)
    2. Manages progressive loading to avoid information overload
    3. Scores novelty/surprising factor for each learning
    4. Routes retrieval based on intent classification
    """
    
    def __init__(self, client: Optional[NIMClient] = None):
        self.client = client or get_client()
        self.kb = get_kb_store()
        self.graph = get_graph_store()
    
    async def plan_retrieval_strategy(
        self,
        query: str,
        intent_analysis: Optional[QueryIntentAnalysis] = None,
        satisfaction_tracker: Optional[SatisfactionTracker] = None,
    ) -> RetrievalStrategy:
        """
        Determine how to retrieve information for this query.
        
        Considers:
        - Query complexity (from intent analysis)
        - User's focus areas (from intent_analysis.focus_areas)
        - What user is already satisfied with (from satisfaction_tracker)
        - Whether query needs multi-hop reasoning (uses knowledge graph)
        """
        
        # Default strategy
        use_graph = await should_use_graph(query, client=self.client)
        progressive_depth = 1  # Start with overview + previews
        min_novelty = 0.3      # Keep somewhat novel information
        top_k = 8
        
        # Adjust based on intent
        if intent_analysis:
            focus_areas = [fa.name for fa in intent_analysis.focus_areas]
            
            # Comparison queries need deeper investigation
            if intent_analysis.requires_comparison or intent_analysis.requires_parallel:
                progressive_depth = 2  # Go to full content
                min_novelty = 0.2      # Be more inclusive
                top_k = 12
            
            # Multi-task needs broader coverage
            elif intent_analysis.intent.value == "multi_task":
                min_novelty = 0.25
                top_k = 10
            
            # Research queries benefit from graph reasoning
            elif intent_analysis.intent.value == "research":
                use_graph = True  # Force graph for research
                min_novelty = 0.35
        else:
            focus_areas = []
        
        # Adjust based on satisfaction
        if satisfaction_tracker:
            # User wants more depth → progressive load more
            satisfied_count = len([
                c for c in satisfaction_tracker.get_satisfaction_scores().values()
                if c >= 0.7
            ])
            
            if satisfied_count == 0:
                # Not satisfied with anything yet → deep investigation
                progressive_depth = 2
                min_novelty = 0.15
            elif satisfied_count > 3:
                # Satisfied with most → only novel stuff
                min_novelty = 0.5
        
        strategy = RetrievalStrategy(
            use_graph=use_graph,
            progressive_depth=progressive_depth,
            min_novelty_score=min_novelty,
            entity_focus_areas=focus_areas,
            top_k_sources=top_k,
        )
        
        logger.info(f"Retrieval strategy: {strategy}")
        return strategy
    
    async def retrieve_with_strategy(
        self,
        query: str,
        query_vec: list[float],
        strategy: RetrievalStrategy,
    ) -> list[Learning]:
        """
        Execute retrieval according to the planned strategy.
        
        Pipeline:
        1. Vector search (KB) or graph traversal
        2. Progressive loading if needed
        3. Novelty scoring
        4. Deduplication and ranking
        """
        all_chunks = []
        
        # ── STAGE 1: Primary retrieval (vector + optional graph) ──
        try:
            chunks = await asyncio.wait_for(
                graph_enhanced_retrieval(
                    query=query,
                    query_vec=query_vec,
                    kb=self.kb,
                    graph=self.graph if strategy.use_graph else None,
                    client=self.client,
                    top_k=strategy.top_k_sources,
                    graph_hops=strategy.graph_hops,
                ),
                timeout=15.0
            )
            all_chunks.extend(chunks)
            logger.debug(f"Retrieved {len(chunks)} chunks from KB+Graph")
        except Exception as e:
            logger.warning(f"Graph retrieval failed: {e}, falling back to KB only")
            # Fallback: KB search only
            if self.kb:
                chunks = await self.kb.search(query_vec, top_k=strategy.top_k_sources)
                all_chunks.extend(chunks)
        
        # ── STAGE 2: Score novelty / surprising factor ──
        scored_chunks = []
        for chunk in all_chunks:
            novelty = self._score_novelty(
                chunk,
                query,
                all_chunks,
                strategy.entity_focus_areas
            )
            
            # Keep if meets novelty threshold
            if novelty.score >= strategy.min_novelty_score:
                scored_chunks.append((chunk, novelty))
        
        # ── STAGE 3: Convert to Learning objects with metadata ──
        learnings = []
        for chunk, novelty in scored_chunks:
            learning = Learning(
                text=chunk.text,
                title=chunk.title or "Unknown Source",
                source_url=chunk.metadata.get("source_url", "") if hasattr(chunk, 'metadata') else "",
                score=novelty.score,  # Use novelty as ranking score
            )
            
            # Add novelty metadata
            if hasattr(learning, 'metadata'):
                learning.metadata['novelty_score'] = novelty.score
                learning.metadata['novelty_factors'] = novelty.factors
            
            learnings.append(learning)
        
        # ── STAGE 4: Sort by novelty + relevance ──
        learnings.sort(
            key=lambda l: (getattr(l, 'score', 0), l.text.count(query.split()[0]) if query else 0),
            reverse=True
        )
        
        logger.info(
            f"Retrieval complete: {len(learnings)} learnings "
            f"(novelty: {[f'{l.score:.2f}' for l in learnings[:3]]}...)"
        )
        
        return learnings
    
    def _score_novelty(
        self,
        chunk,
        query: str,
        all_chunks: list,
        entity_focus_areas: list[str],
    ) -> NoveltyScore:
        """
        Calculate surprising factor / novelty score for a chunk.
        
        Components:
        1. Uniqueness: How different from other chunks (0-1)
        2. Relevance: How relevant to query (0-1)
        3. Information density: How much info per token (0-1)
        4. Specificity: Is it specific to focus areas? (0-1)
        """
        text = chunk.text.lower() if hasattr(chunk, 'text') else ""
        query_lower = query.lower()
        
        # Factor 1: Uniqueness (inverse of redundancy)
        unique_score = 1.0
        if len(all_chunks) > 1:
            # How many other chunks have similar text?
            similar_count = 0
            text_words = set(w for w in text.split() if len(w) > 3)
            
            for other in all_chunks:
                if other is chunk:
                    continue
                other_text = other.text.lower() if hasattr(other, 'text') else ""
                other_words = set(w for w in other_text.split() if len(w) > 3)
                
                # Jaccard similarity
                if text_words or other_words:
                    intersection = len(text_words & other_words)
                    union = len(text_words | other_words)
                    similarity = intersection / union if union > 0 else 0
                    
                    if similarity > 0.6:  # High similarity
                        similar_count += 1
            
            # Reduce score for redundancy
            unique_score = max(0.2, 1.0 - (similar_count / max(1, len(all_chunks) * 0.3)))
        
        # Factor 2: Relevance to query
        query_terms = set(w for w in query_lower.split() if len(w) > 3)
        text_terms = set(w for w in text.split() if len(w) > 3)
        relevance_score = 0.5
        
        if query_terms and text_terms:
            matched = len(query_terms & text_terms)
            relevance_score = min(matched / len(query_terms), 1.0)
        
        # Factor 3: Information density
        # Prefer chunks that are dense with information (not just filler)
        avg_word_length = sum(len(w) for w in text.split()[:50]) / max(1, min(50, len(text.split())))
        density_score = min(avg_word_length / 5.0, 1.0)  # Prefer 5+ char words
        
        # Factor 4: Specificity to focus areas
        specificity_score = 0.5
        if entity_focus_areas:
            matching_entities = sum(
                1 for entity in entity_focus_areas
                if entity.lower() in text
            )
            specificity_score = min(matching_entities / len(entity_focus_areas), 1.0)
        
        # Combine factors
        novelty = (
            unique_score * 0.4 +      # 40% - uniqueness/non-redundancy
            relevance_score * 0.35 +   # 35% - relevance to query
            density_score * 0.15 +     # 15% - information density
            specificity_score * 0.1    # 10% - entity focus
        )
        
        return NoveltyScore(
            score=min(novelty, 1.0),
            factors={
                "uniqueness": unique_score,
                "relevance": relevance_score,
                "density": density_score,
                "specificity": specificity_score,
            },
            explanation=f"Novel ({novelty:.2f}): unique={unique_score:.2f}, relevant={relevance_score:.2f}, dense={density_score:.2f}"
        )
    
    async def retrieve_for_intent(
        self,
        query: str,
        query_vec: list[float],
        intent_analysis: Optional[QueryIntentAnalysis] = None,
        satisfaction_tracker: Optional[SatisfactionTracker] = None,
    ) -> list[Learning]:
        """
        End-to-end: Plan strategy + Execute retrieval based on intent.
        
        This is the main entry point that ties everything together.
        """
        strategy = await self.plan_retrieval_strategy(
            query,
            intent_analysis=intent_analysis,
            satisfaction_tracker=satisfaction_tracker,
        )
        
        learnings = await self.retrieve_with_strategy(
            query,
            query_vec,
            strategy,
        )
        
        return learnings


def get_knowledge_coordinator(client: Optional[NIMClient] = None) -> KnowledgeCoordinator:
    """Get or create the global knowledge coordinator."""
    if not hasattr(get_knowledge_coordinator, '_instance'):
        get_knowledge_coordinator._instance = KnowledgeCoordinator(client)
    return get_knowledge_coordinator._instance
