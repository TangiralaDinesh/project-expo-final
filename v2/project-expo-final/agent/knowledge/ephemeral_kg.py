"""
Topic-Scoped Ephemeral Knowledge Graph — Detective-Style Entity Exploration.

Created fresh per query. Lives in-memory (dict). No persistence needed.

Architecture inspired by:
  - STORM: multi-perspective entity exploration
  - GraphRAG: local+global search with community detection
  - Perplexity: pre-retrieval entity resolution
  - Detective methodology: pull every string from every node

Key difference from graph_store.py:
  - graph_store = persistent session KG (extracted from learnings)
  - ephemeral_kg = per-query investigation map (pre-populated from external KGs)

Flow:
  1. Extract entities from query (fast regex, no LLM)
  2. Parallel Wikidata investigation (100-200ms per entity)
  3. Generate investigation queries from EVERY property
  4. Feed queries into fan-out for parallel retrieval
  5. After retrieval: feed learnings BACK into KG (iterative enrichment)

Detective's Rule: Every property is a string to pull.
  "SCSVMV" → aliases, type, location, dates, people, accreditation = 8+ search leads
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .entity_profile import EntityProfile, InvestigationQuery
from .graph_store import Triple

logger = logging.getLogger(__name__)

# Properties that generate HIGH-VALUE search queries
_LOCATION_PROPS = frozenset({
    "country", "located in", "headquarters location", "location",
    "administrative territorial entity", "located in the administrative territorial entity",
    "city", "state", "continent",
})
_TEMPORAL_PROPS = frozenset({
    "inception", "founded", "start date", "publication date",
    "date of birth", "dissolved, abolished or demolished date",
    "point in time", "start time", "end time",
})
_PERSON_PROPS = frozenset({
    "founder", "ceo", "chancellor", "head of state", "chairperson",
    "chief executive officer", "director", "president",
    "named after", "creator", "author",
})
_ACCREDITATION_PROPS = frozenset({
    "award received", "certification", "accreditation",
    "member of", "affiliation", "approved by",
})
_IDENTITY_PROPS = frozenset({
    "official name", "short name", "native label", "motto",
})


class EphemeralKG:
    """Per-query investigation map. Fast, in-memory, disposable.
    
    Usage:
        ekg = EphemeralKG("scsvmv live news")
        await ekg.investigate_entity("SCSVMV", external_kg_bridge)
        queries = ekg.generate_investigation_queries(max_queries=5)
        # → ["Kanchi University news", "Kanchipuram university education", ...]
    """

    def __init__(self, query: str):
        self.query = query
        self.entities: dict[str, EntityProfile] = {}
        self._adjacency: dict[str, list[Triple]] = defaultdict(list)
        self._investigation_log: list[str] = []  # Detective's notebook
        self._attempted_queries: set[str] = set()
        self._created_at = time.time()

    async def investigate_entity(
        self,
        entity_name: str,
        external_kg,
        *,
        timeout_s: float = 3.0,
    ) -> EntityProfile:
        """Pull ALL strings from one entity node.

        1. Wikidata → aliases, type, properties, relationships
        2. Hierarchy → instance_of, subclass_of
        3. Disambiguation → alternative meanings
        
        All run in parallel with a timeout budget.
        """
        profile = EntityProfile(name=entity_name)
        t0 = time.time()

        try:
            # Parallel external lookup
            enrich_task = external_kg.enrich_entity(entity_name)
            hierarchy_task = external_kg.get_concept_hierarchy(entity_name)
            disambig_task = external_kg.disambiguate_entity(entity_name)

            results = await asyncio.wait_for(
                asyncio.gather(
                    enrich_task, hierarchy_task, disambig_task,
                    return_exceptions=True,
                ),
                timeout=timeout_s,
            )

            # Process Wikidata properties (enrich_entity returns list[Triple])
            if isinstance(results[0], list):
                for triple in results[0]:
                    profile.add_property(triple.predicate, triple.object)
                    self._adjacency[entity_name.lower()].append(triple)

            # Process hierarchy (instance_of, subclass_of)
            if isinstance(results[1], list):
                for triple in results[1]:
                    if triple.predicate == "instance_of":
                        if triple.object not in profile.type_hierarchy:
                            profile.type_hierarchy.append(triple.object)
                    self._adjacency[entity_name.lower()].append(triple)

            # Process aliases from disambiguation
            if isinstance(results[2], list):
                for option in results[2]:
                    label = option.label if hasattr(option, 'label') else str(option)
                    if label.lower() != entity_name.lower() and label not in profile.aliases:
                        profile.aliases.append(label)
                    # Store wikidata_id from first match
                    if not profile.wikidata_id and hasattr(option, 'wikidata_id'):
                        profile.wikidata_id = option.wikidata_id
                    if not profile.description and hasattr(option, 'description'):
                        profile.description = option.description

        except asyncio.TimeoutError:
            logger.warning("EphemeralKG: investigation timed out for '%s' (%.1fs)", entity_name, timeout_s)
        except Exception as e:
            logger.warning("EphemeralKG: investigation failed for '%s': %s", entity_name, e)

        elapsed = (time.time() - t0) * 1000
        self.entities[entity_name] = profile
        self._investigation_log.append(
            f"Investigated '{entity_name}': {len(profile.aliases)} aliases, "
            f"{len(profile.properties)} props, {len(profile.type_hierarchy)} types "
            f"({elapsed:.0f}ms)"
        )

        logger.info(
            "Detective KG: '%s' → %d aliases, %d properties, %d types (%.0fms)",
            entity_name, len(profile.aliases), len(profile.properties),
            len(profile.type_hierarchy), elapsed,
        )

        return profile

    def generate_investigation_queries(
        self,
        max_queries: int = 8,
        exclude_attempted: Optional[set[str]] = None,
    ) -> list[InvestigationQuery]:
        """Detective-style: derive search queries from EVERY KG property.

        Not just aliases — every property becomes a potential lead:
          - "location: Kanchipuram" → "Kanchipuram university news"
          - "type: private university" → "private university Tamil Nadu updates"
          - "accreditation: NAAC" → "NAAC accreditation results 2026"
          - "alias: Kanchi University" → "Kanchi University latest news"

        Prioritized by source reliability and expected information gain.
        """
        exclude = exclude_attempted or self._attempted_queries
        queries: list[InvestigationQuery] = []
        topic = self._extract_topic_words()

        for entity_name, profile in self.entities.items():
            if not profile.is_rich:
                # Discovered from learnings: generate a direct lead for this new entity
                if profile.investigation_depth == 0:
                    lead_q = f"{entity_name} {topic}"
                    if lead_q.lower() not in exclude:
                        queries.append(InvestigationQuery(
                            query=lead_q,
                            source="discovered_entity",
                            priority=0.80,
                            reasoning=f"Fresh lead discovered from retrieval: '{entity_name}'",
                            entity_origin=entity_name,
                        ))
                continue

            # P1: Alias-based queries (HIGHEST value — new search surface)
            for alias in profile.aliases[:3]:
                q = self._substitute_entity(alias)
                if q and q.lower() not in exclude:
                    queries.append(InvestigationQuery(
                        query=q,
                        source="alias",
                        priority=0.95,
                        reasoning=f"Alternative name: '{alias}' for '{entity_name}'",
                        entity_origin=entity_name,
                    ))

            # P2: Property-derived queries
            for prop_key, prop_value in profile.properties.items():
                iq = self._property_to_query(prop_key, prop_value, entity_name, topic)
                if iq and iq.query.lower() not in exclude:
                    queries.append(iq)

            # P3: Type-hierarchy queries (broader context)
            for type_name in profile.type_hierarchy[:2]:
                q = f"{type_name} {topic} latest"
                if q.lower() not in exclude:
                    queries.append(InvestigationQuery(
                        query=q,
                        source="hierarchy",
                        priority=0.55,
                        reasoning=f"'{entity_name}' is a {type_name}",
                        entity_origin=entity_name,
                    ))

            # P4: Connected entity queries (relationship-based)
            for triple in self._adjacency.get(entity_name.lower(), [])[:5]:
                if triple.predicate not in ("instance_of", "subclass_of"):
                    obj_clean = triple.object
                    if obj_clean and not obj_clean.startswith("http") and len(obj_clean) > 3:
                        q = f"{obj_clean} {entity_name} {topic}"
                        if q.lower() not in exclude:
                            queries.append(InvestigationQuery(
                                query=q,
                                source="relationship",
                                priority=0.65,
                                reasoning=f"{entity_name} → {triple.predicate} → {obj_clean}",
                                entity_origin=entity_name,
                            ))

        # Sort by priority, dedup by content similarity, cap
        queries.sort(key=lambda q: q.priority, reverse=True)
        deduped = self._dedup_queries(queries)
        selected = deduped[:max_queries]

        # Track attempted queries
        for q in selected:
            self._attempted_queries.add(q.query.lower())

        if selected:
            logger.info(
                "Detective KG: generated %d investigation queries from %d entities "
                "(sources: %s)",
                len(selected), len(self.entities),
                [q.source for q in selected],
            )

        return selected

    def get_investigation_log(self) -> list[str]:
        """Return the detective's notebook entries."""
        return list(self._investigation_log)

    def get_investigation_summary(self) -> str:
        """Compact summary of detective findings for reasoning prompts."""
        if not self._investigation_log:
            return ""
        return "Detective Notebook:\n" + "\n".join(f"- {entry}" for entry in self._investigation_log[-8:])

    def ingest_learnings(self, learnings: list, extract_fn=None):
        """After retrieval: feed learnings BACK into the KG.

        Round 1 learnings → extract entities → add to ephemeral KG
        → KG is now RICHER → round 2 queries are SMARTER

        This is the iterative enrichment loop.
        """
        for learning in learnings[:10]:
            text = getattr(learning, 'text', str(learning))
            # Simple entity extraction from learnings (capitalized multi-word phrases)
            entities = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text)
            for entity in entities[:3]:
                entity_lower = entity.lower()
                if entity_lower not in {e.lower() for e in self.entities}:
                    # Add as a shallow entity (no full investigation, just name)
                    self.entities[entity] = EntityProfile(name=entity, investigation_depth=0)
                    self._investigation_log.append(f"Discovered new entity from learnings: '{entity}'")

    # ── Private helpers ──

    def _extract_topic_words(self) -> str:
        """Extract the 'topic' from the original query (strip entity names)."""
        topic = self.query.lower()
        # Remove entity names from topic to get the intent
        for entity_name in self.entities:
            topic = topic.replace(entity_name.lower(), "").strip()
        # Clean up
        topic = re.sub(r'\s+', ' ', topic).strip()
        return topic if topic else "latest news"

    def _substitute_entity(self, alias: str) -> str:
        """Replace entity name in original query with alias."""
        result = self.query
        for entity_name in self.entities:
            # Case-insensitive replacement
            pattern = re.compile(re.escape(entity_name), re.IGNORECASE)
            new_result = pattern.sub(alias, result)
            if new_result != result:
                return new_result
        # If no substitution matched, just prepend alias to topic
        topic = self._extract_topic_words()
        return f"{alias} {topic}"

    def _property_to_query(
        self, prop: str, value: str, entity: str, topic: str,
    ) -> Optional[InvestigationQuery]:
        """Convert a KG property into a search query.
        
        The detective's art: which properties are worth investigating?
        """
        prop_lower = prop.lower()

        # Location properties → geo-contextual search
        if prop_lower in _LOCATION_PROPS:
            return InvestigationQuery(
                query=f"{entity} {value} {topic}",
                source="location",
                priority=0.70,
                reasoning=f"Location context: {entity} in {value}",
                entity_origin=entity,
            )

        # Temporal properties → timeline search
        if prop_lower in _TEMPORAL_PROPS:
            return InvestigationQuery(
                query=f"{entity} history timeline since {value}",
                source="temporal",
                priority=0.60,
                reasoning=f"Founded/started {value} — historical context",
                entity_origin=entity,
            )

        # Person properties → key figure search
        if prop_lower in _PERSON_PROPS:
            return InvestigationQuery(
                query=f"{value} {entity} {topic}",
                source="person",
                priority=0.75,
                reasoning=f"{value} is {prop} of {entity}",
                entity_origin=entity,
            )

        # Award/accreditation → status search
        if prop_lower in _ACCREDITATION_PROPS:
            return InvestigationQuery(
                query=f"{entity} {value} status update",
                source="accreditation",
                priority=0.80,
                reasoning=f"Accreditation: {value}",
                entity_origin=entity,
            )

        # Identity properties → official name search
        if prop_lower in _IDENTITY_PROPS:
            return InvestigationQuery(
                query=f"{value} {topic}",
                source="alias",
                priority=0.90,
                reasoning=f"Official name: {value}",
                entity_origin=entity,
            )

        # Skip generic/noisy properties
        return None

    @staticmethod
    def _dedup_queries(queries: list[InvestigationQuery]) -> list[InvestigationQuery]:
        """Dedup investigation queries by word overlap (Jaccard > 0.7)."""
        if len(queries) <= 1:
            return queries

        kept: list[InvestigationQuery] = []
        for q in queries:
            q_words = set(q.query.lower().split())
            is_dup = False
            for existing in kept:
                e_words = set(existing.query.lower().split())
                if q_words and e_words:
                    jaccard = len(q_words & e_words) / len(q_words | e_words)
                    if jaccard > 0.7:
                        is_dup = True
                        break
            if not is_dup:
                kept.append(q)

        return kept

    @property
    def summary(self) -> str:
        """Human-readable summary of the investigation."""
        parts = [f"EphemeralKG for '{self.query}':"]
        for name, profile in self.entities.items():
            parts.append(
                f"  {name}: {len(profile.aliases)} aliases, "
                f"{len(profile.properties)} props, {len(profile.type_hierarchy)} types"
            )
        return "\n".join(parts)
