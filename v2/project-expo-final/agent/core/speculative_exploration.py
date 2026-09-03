"""
Speculative Exploration Engine — Dynamic Bayesian knowledge gap discovery.

NOT rigid templates. NOT hardcoded permutations.

Core principle: "Users don't ask about unknown unknowns because they don't
know what they don't know. Our project fills that gap."

How it works:
  1. Build a DIMENSION MAP of all known properties across all entities
  2. Compute COVERAGE: which dimensions have we already explored?
  3. Compute ENTROPY: which unexplored dimensions have highest information gain?
  4. Generate ALL possible query combinations dynamically
  5. Score by expected information gain (Bayesian posterior)
  6. Select top-k queries that MAXIMIZE total coverage

This is NOT about fixed permutations like "entity×location×topic".
This is about DYNAMIC exploration where the system discovers which
combinations are most informative based on what it already knows.

Example — "Tom Holland vs Zendaya blockbusters":
  - Dimension map: {filmography, box_office, awards, relationships, upcoming_projects, ...}
  - After round 1: filmography=explored, box_office=partial
  - Entropy says: awards (unexplored, high info gain) > relationships (partially explored)
  - System generates: "Tom Holland award nominations", "Zendaya box office total revenue"
  - NOT rigid "entity×location" — it DISCOVERED that awards is the gap
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Dimension:
    """One knowledge dimension for an entity."""
    name: str                  # e.g. "location", "filmography", "awards"
    values: list[str] = field(default_factory=list)  # Known values
    coverage: float = 0.0     # 0=unexplored, 1=fully explored
    query_count: int = 0      # How many queries targeted this dimension
    result_count: int = 0     # How many learnings came from this dimension
    source: str = ""          # "kg_property" | "learning" | "inferred"

    @property
    def entropy(self) -> float:
        """Information entropy — higher = more uncertainty = more to learn.
        
        Adjusted Shannon: unexplored (p≈0) gets HIGH score because
        it represents unknown unknowns with high potential.
        """
        if self.coverage >= 0.95:
            return 0.01  # Fully explored, nothing to gain
        if self.coverage <= 0.05:
            return 0.95  # Completely unexplored = highest potential
        p = self.coverage
        h = -(p * math.log2(p) + (1 - p) * math.log2(1 - p))
        return h

    @property
    def expected_gain(self) -> float:
        """Expected information gain — combines entropy with success history."""
        base = self.entropy
        if self.query_count > 0 and self.result_count == 0:
            base *= 0.4  # Queries tried but returned nothing
        elif self.query_count > 0 and self.result_count > 0:
            base *= 1.1  # Queries worked — productive dimension
        return min(1.0, base)


@dataclass 
class SpeculativeQuery:
    """One dynamically generated query with entropy metadata."""
    query: str
    dimensions_covered: list[str]
    expected_gain: float
    reasoning: str
    entity_origin: str = ""


class SpeculativeExplorationEngine:
    """Dynamic Bayesian exploration engine.
    
    Discovers ALL dimensions from KG properties + learnings.
    Tracks which dimensions have been explored.
    Dynamically generates queries targeting UNEXPLORED dimensions.
    Maximizes total information coverage per query budget.
    """

    def __init__(self, query: str, entities: Optional[list[str]] = None):
        self.query = query
        self.entities = entities or []
        self.dimensions: dict[str, Dimension] = {}
        self._entity_dimensions: dict[str, set[str]] = defaultdict(set)
        self._explored_queries: set[str] = set()
        self._topic = self._extract_topic(query)

    def ingest_kg_properties(self, entity: str, properties: dict[str, str]):
        """Ingest KG properties and DYNAMICALLY discover dimensions."""
        for prop_key, prop_value in properties.items():
            dim_name = self._normalize_dimension(prop_key)
            if dim_name in self.dimensions:
                dim = self.dimensions[dim_name]
                if prop_value not in dim.values:
                    dim.values.append(prop_value)
            else:
                self.dimensions[dim_name] = Dimension(
                    name=dim_name, values=[prop_value], source="kg_property",
                )
            self._entity_dimensions[entity].add(dim_name)

    def ingest_aliases(self, entity: str, aliases: list[str]):
        """Aliases create an 'identity' dimension."""
        dim = self.dimensions.setdefault(
            "identity", Dimension(name="identity", source="kg_property")
        )
        dim.values.extend(aliases)
        self._entity_dimensions[entity].add("identity")

    def ingest_type_hierarchy(self, entity: str, types: list[str]):
        """Type hierarchy creates a 'category' dimension."""
        dim = self.dimensions.setdefault(
            "category", Dimension(name="category", source="kg_property")
        )
        dim.values.extend(types)
        self._entity_dimensions[entity].add("category")

    def ingest_learnings(self, learnings: list, query_used: str = ""):
        """Feed retrieval results back — update dimension coverage dynamically."""
        for learning in learnings:
            text = getattr(learning, 'text', str(learning))
            discovered = self._discover_dimensions_from_text(text)
            for dim_name, values in discovered.items():
                if dim_name in self.dimensions:
                    self.dimensions[dim_name].coverage = min(
                        1.0, self.dimensions[dim_name].coverage + 0.3
                    )
                    self.dimensions[dim_name].result_count += 1
                    for v in values:
                        if v not in self.dimensions[dim_name].values:
                            self.dimensions[dim_name].values.append(v)
                else:
                    self.dimensions[dim_name] = Dimension(
                        name=dim_name, values=values, coverage=0.3,
                        result_count=1, source="learning",
                    )

    def mark_explored(self, dimension: str, coverage_delta: float = 0.4):
        """Mark a dimension as partially/fully explored."""
        dim_name = self._normalize_dimension(dimension)
        if dim_name in self.dimensions:
            self.dimensions[dim_name].coverage = min(
                1.0, self.dimensions[dim_name].coverage + coverage_delta
            )
            self.dimensions[dim_name].query_count += 1

    def generate_speculative_queries(
        self,
        budget: int = 6,
        min_gain: float = 0.15,
    ) -> list[SpeculativeQuery]:
        """Generate queries that MAXIMIZE total information coverage.
        
        Dynamic algorithm — no templates:
        1. Rank dimensions by expected_gain (entropy × success_history)
        2. For each high-entropy dimension: generate query combinations
        3. Cross-pollinate: combine dimensions from different entities
        4. Greedy select: pick highest gain → mark dims → reprioritize
        """
        if not self.dimensions:
            return []

        candidates: list[SpeculativeQuery] = []
        ranked_dims = sorted(
            self.dimensions.values(),
            key=lambda d: d.expected_gain,
            reverse=True,
        )

        # Single-dimension queries
        for dim in ranked_dims:
            if dim.expected_gain < min_gain:
                continue
            for entity in self.entities:
                if dim.values:
                    best_value = dim.values[0]
                    q = f"{entity} {best_value} {self._topic}".strip()
                else:
                    q = f"{entity} {dim.name} {self._topic}".strip()
                candidates.append(SpeculativeQuery(
                    query=q,
                    dimensions_covered=[dim.name],
                    expected_gain=dim.expected_gain,
                    reasoning=f"Explore {dim.name} (entropy={dim.entropy:.2f})",
                    entity_origin=entity,
                ))

        # Cross-dimension queries (2 high-entropy dims combined)
        high_entropy = [d for d in ranked_dims if d.expected_gain > 0.3]
        for i, dim_a in enumerate(high_entropy[:5]):
            for dim_b in high_entropy[i+1:5]:
                val_a = dim_a.values[0] if dim_a.values else dim_a.name
                val_b = dim_b.values[0] if dim_b.values else dim_b.name
                combined_gain = (dim_a.expected_gain + dim_b.expected_gain) / 2
                for entity in self.entities[:2]:
                    q = f"{entity} {val_a} {val_b}".strip()
                    candidates.append(SpeculativeQuery(
                        query=q,
                        dimensions_covered=[dim_a.name, dim_b.name],
                        expected_gain=combined_gain,
                        reasoning=f"Cross: {dim_a.name}×{dim_b.name}",
                        entity_origin=entity,
                    ))

        # Entity comparison queries (for "A vs B" queries)
        if len(self.entities) >= 2:
            elist = list(self._entity_dimensions.keys())[:2]
            if len(elist) >= 2:
                shared = self._entity_dimensions[elist[0]] & self._entity_dimensions[elist[1]]
                for dim_name in shared:
                    dim = self.dimensions.get(dim_name)
                    if dim and dim.expected_gain > min_gain:
                        q = f"{elist[0]} vs {elist[1]} {dim_name}"
                        candidates.append(SpeculativeQuery(
                            query=q,
                            dimensions_covered=[dim_name],
                            expected_gain=dim.expected_gain * 1.2,
                            reasoning=f"Compare on {dim_name}",
                            entity_origin=f"{elist[0]} vs {elist[1]}",
                        ))

        # Greedy selection maximizing dimension coverage
        candidates.sort(key=lambda sq: sq.expected_gain, reverse=True)
        selected = self._greedy_select(candidates, budget)

        for sq in selected:
            for dn in sq.dimensions_covered:
                if dn in self.dimensions:
                    self.dimensions[dn].query_count += 1
            self._explored_queries.add(sq.query.lower())

        if selected:
            logger.info(
                "Speculative engine: %d queries from %d dims (top: %s)",
                len(selected), len(self.dimensions),
                [(sq.dimensions_covered[0], f"{sq.expected_gain:.2f}") for sq in selected[:3]],
            )

        return selected

    @property
    def coverage_summary(self) -> dict[str, float]:
        return {d.name: d.coverage for d in sorted(self.dimensions.values(), key=lambda d: d.coverage)}

    @property
    def total_entropy(self) -> float:
        if not self.dimensions:
            return 0.0
        return sum(d.entropy for d in self.dimensions.values()) / len(self.dimensions)

    # ── Private helpers ──

    @staticmethod
    def _normalize_dimension(prop_key: str) -> str:
        key = prop_key.lower().strip()
        for prefix in ("located in the administrative territorial entity",
                       "located in", "headquarters"):
            if key.startswith(prefix):
                key = "location"
        for prefix in ("date of", "start", "end"):
            if key.startswith(prefix):
                key = key.replace("date of ", "").replace(" ", "_")
        return key.replace(" ", "_")

    def _extract_topic(self, query: str) -> str:
        topic = query.lower()
        for entity in self.entities:
            topic = topic.replace(entity.lower(), "").strip()
        topic = re.sub(r'\s+', ' ', topic).strip()
        return topic if len(topic) > 2 else ""

    def _discover_dimensions_from_text(self, text: str) -> dict[str, list[str]]:
        """Dynamically discover dimensions from learning text."""
        discovered: dict[str, list[str]] = {}
        dates = re.findall(r'\b((?:19|20)\d{2})\b', text)
        if dates:
            discovered["temporal"] = list(set(dates))
        money = re.findall(r'\$[\d,.]+\s*(?:million|billion|M|B)?', text)
        if money:
            discovered["financial"] = money[:3]
        percents = re.findall(r'[\d.]+\s*%', text)
        if percents:
            discovered["metrics"] = percents[:3]
        orgs = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b', text)
        if orgs:
            discovered["related_entities"] = list(set(orgs))[:3]
        return discovered

    def _greedy_select(
        self, candidates: list[SpeculativeQuery], budget: int,
    ) -> list[SpeculativeQuery]:
        """Greedy selection maximizing dimension coverage diversity."""
        selected: list[SpeculativeQuery] = []
        covered_dims: set[str] = set()
        used_words: list[set[str]] = []

        for c in candidates:
            if len(selected) >= budget:
                break
            c_words = set(c.query.lower().split())
            is_dup = any(
                len(c_words & ew) / max(len(c_words | ew), 1) > 0.65
                for ew in used_words
            ) if used_words else False
            if is_dup or c.query.lower() in self._explored_queries:
                continue
            new_dims = set(c.dimensions_covered) - covered_dims
            if not new_dims and covered_dims:
                c.expected_gain *= 0.5
            selected.append(c)
            covered_dims.update(c.dimensions_covered)
            used_words.append(c_words)

        return selected
