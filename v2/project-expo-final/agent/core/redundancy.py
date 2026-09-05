"""
Query Redundancy Tracker — prevents duplicate/near-duplicate search queries.

Problem: Fan-out + KG investigation + reasoning loop gap queries can generate
overlapping queries. Each duplicate wastes a search API call (latency + cost).

Solution: Track ALL fired queries. Before firing a new one, check if it's
semantically similar to any already-fired query (word-overlap Jaccard > 0.65).

Also provides information entropy scoring: prioritize queries that are most
DIFFERENT from what we already know (maximize information gain per query).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


class RedundancyTracker:
    """Tracks fired queries and prevents near-duplicates.
    
    Usage:
        tracker = RedundancyTracker()
        
        queries = ["SCSVMV news", "SCSVMV latest news", "Kanchi University news"]
        deduped = tracker.filter_and_track(queries)
        # → ["SCSVMV news", "Kanchi University news"]
        # "SCSVMV latest news" dropped (Jaccard 0.75 with "SCSVMV news")
    """

    def __init__(self, similarity_threshold: float = 0.65):
        self._threshold = similarity_threshold
        self._fired: list[set[str]] = []  # Word sets of all fired queries
        self._fired_raw: list[str] = []   # Original query strings
        self._result_counts: dict[str, int] = {}  # query → learnings received

    def is_redundant(
        self,
        query: str,
        threshold: Optional[float] = None,
    ) -> bool:
        """Check if a single query is redundant against previously fired queries.
        
        Returns True if:
          - query is empty, None, or too short (< 5 characters)
          - query contains no meaningful tokens after stopword removal
          - query has word-overlap Jaccard similarity > threshold with ANY
            previously fired query.
        Returns False if the query is sufficiently novel.
        """
        if hasattr(query, "query"):
            query = getattr(query, "query")
        elif not isinstance(query, str):
            query = str(query) if query is not None else ""

        q_clean = query.strip()
        if not q_clean or len(q_clean) < 5:
            return True

        q_words = self._tokenize(q_clean)
        if not q_words:
            return True

        thresh = threshold if threshold is not None else self._threshold
        for fired_words in self._fired:
            if self._jaccard(q_words, fired_words) > thresh:
                return True

        return False

    def track(self, query: str) -> bool:
        """Track a single fired query in the history.
        
        Returns True if successfully tracked, False if empty or too short.
        """
        if hasattr(query, "query"):
            query = getattr(query, "query")
        elif not isinstance(query, str):
            query = str(query) if query is not None else ""

        q_clean = query.strip()
        if not q_clean or len(q_clean) < 5:
            return False

        q_words = self._tokenize(q_clean)
        if not q_words:
            return False

        self._fired.append(q_words)
        self._fired_raw.append(q_clean)
        return True

    def filter_and_track(
        self,
        queries: list[str],
        *,
        source: str = "unknown",
    ) -> list[str]:
        """Filter out near-duplicates and track what we fire.
        
        Returns only queries that are sufficiently different from ALL
        previously fired queries and other queries in this batch.
        """
        kept = []
        for q in queries:
            if hasattr(q, "query"):
                q_clean = getattr(q, "query", "").strip()
            elif isinstance(q, str):
                q_clean = q.strip()
            else:
                q_clean = str(q).strip() if q is not None else ""

            if not q_clean or len(q_clean) < 5:
                continue

            # Check against previously fired queries
            if self.is_redundant(q_clean):
                continue

            q_words = self._tokenize(q_clean)

            # Check against queries we're keeping in THIS batch
            is_dup_in_batch = False
            for kept_q in kept:
                kept_words = self._tokenize(kept_q)
                if self._jaccard(q_words, kept_words) > self._threshold:
                    is_dup_in_batch = True
                    break

            if not is_dup_in_batch:
                kept.append(q_clean)
                self._fired.append(q_words)
                self._fired_raw.append(q_clean)

        dropped = len(queries) - len(kept)
        if dropped > 0:
            logger.info(
                "Redundancy filter (%s): kept %d, dropped %d duplicates",
                source, len(kept), dropped,
            )

        return kept

    def record_result(self, query: str, learning_count: int):
        """Track how many learnings a query produced.
        Used for information entropy scoring of future queries.
        """
        self._result_counts[query.lower().strip()] = learning_count

    def score_information_entropy(self, candidate_queries: list[str]) -> list[tuple[str, float]]:
        """Score candidate queries by expected information gain.
        
        Higher score = more different from what we already searched for.
        
        Scoring formula:
          entropy = 1.0 - max(jaccard(candidate, fired_query) for all fired_queries)
          
        Queries that overlap heavily with already-fired queries get LOW scores
        (we probably already have that info). Novel queries get HIGH scores.
        
        Also penalizes queries similar to LOW-RESULT queries (they're unlikely
        to produce results either).
        """
        scored = []
        for q in candidate_queries:
            q_words = self._tokenize(q)
            
            if not q_words:
                scored.append((q, 0.0))
                continue

            # Base entropy: how different from all fired queries
            if self._fired:
                max_overlap = max(
                    self._jaccard(q_words, fired) for fired in self._fired
                )
                base_entropy = 1.0 - max_overlap
            else:
                base_entropy = 1.0  # First query always has max entropy

            # Penalty: if similar to queries that produced 0 results
            penalty = 0.0
            for fired_raw, fired_words in zip(self._fired_raw, self._fired):
                overlap = self._jaccard(q_words, fired_words)
                if overlap > 0.4:  # Somewhat similar
                    result_count = self._result_counts.get(fired_raw.lower().strip(), -1)
                    if result_count == 0:
                        penalty += overlap * 0.3  # Penalize queries similar to failures

            final_score = max(0.0, base_entropy - penalty)
            scored.append((q, final_score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def select_top_by_entropy(
        self,
        candidates: list[str],
        max_queries: int = 4,
        min_entropy: float = 0.15,
    ) -> list[str]:
        """Select top queries by information entropy, then filter redundancy.
        
        Combined pipeline: entropy scoring → top-k → redundancy filter.
        """
        if not candidates:
            return []

        scored = self.score_information_entropy(candidates)
        
        # Filter by minimum entropy threshold
        good = [q for q, score in scored if score >= min_entropy]
        
        # Take top-k
        top = good[:max_queries * 2]  # Over-select then dedup
        
        # Final redundancy filter
        return self.filter_and_track(top, source="entropy_selected")

    @property
    def total_fired(self) -> int:
        return len(self._fired)

    @property
    def total_results(self) -> int:
        return sum(self._result_counts.values())

    # ── Private helpers ──

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Tokenize to lowercase word set, stripping noise."""
        words = re.findall(r'\b[a-z0-9]+\b', text.lower())
        # Remove common stop words that inflate Jaccard
        stops = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
                 "to", "for", "of", "and", "or", "not", "with", "from", "by", "about"}
        return {w for w in words if w not in stops and len(w) > 1}

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)
