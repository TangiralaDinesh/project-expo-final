"""
External Knowledge Graph Bridge — Wikidata / DBpedia / ConceptNet integration.

Upgrades in-memory graph_store.py from session-scoped LLM-extracted entities
to persistent, large-scale external knowledge graphs.

Layered KG with fallback:
  Layer 1: Local GraphStore (in-memory, fast)
  Layer 2: Wikidata SPARQL (free, massive, 100M+ items)
  Layer 3: DBpedia Lookup (free REST, structured facts)
  Layer 4: ConceptNet (free, commonsense reasoning)

Why Wikidata first: Free, no API key, public SPARQL endpoint, 1.5B+ statements,
proper ontology (P31=instance_of, P279=subclass_of), real-time updates.
"""

from __future__ import annotations

import asyncio
import logging
import json
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

import aiohttp

from .graph_store import Triple, GraphStore, get_graph_store

logger = logging.getLogger(__name__)

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
DBPEDIA_LOOKUP = "https://lookup.dbpedia.org/api/search"
CONCEPTNET_API = "https://api.conceptnet.io"

# Cache TTL in seconds
CACHE_TTL_FACTUAL = 86400  # 24 hours for stable facts
CACHE_TTL_DYNAMIC = 3600   # 1 hour for dynamic data


@dataclass
class EntityInfo:
    """Structured info about an entity from external KGs."""
    name: str
    description: str = ""
    wikidata_id: str = ""          # Q-number (e.g., "Q312" for Apple Inc.)
    entity_type: str = ""          # "company", "person", "concept", etc.
    aliases: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class DisambiguationOption:
    """One possible meaning of an ambiguous entity."""
    label: str
    description: str
    wikidata_id: str
    entity_type: str = ""
    confidence: float = 0.0


class ExternalKGBridge:
    """Bridge between local GraphStore and external knowledge graphs.

    Uses the SAME Triple interface so the rest of the system doesn't change.
    Results are cached in the local GraphStore for future queries.
    """

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._cache: dict[str, tuple[float, list[Triple]]] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "AntigravityAgent/1.0"},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Wikidata SPARQL ──

    async def wikidata_search_entity(self, entity_name: str) -> Optional[str]:
        """Search Wikidata for an entity, return its Q-ID.

        Uses wbsearchentities API (faster than SPARQL for search).
        """
        session = await self._get_session()
        params = {
            "action": "wbsearchentities",
            "search": entity_name,
            "language": "en",
            "format": "json",
            "limit": "5",
        }

        try:
            async with session.get(
                "https://www.wikidata.org/w/api.php",
                params=params,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                results = data.get("search", [])
                if results:
                    return results[0].get("id", "")
        except Exception as e:
            logger.debug("Wikidata search failed for '%s': %s", entity_name, e)

        return None

    async def wikidata_get_properties(
        self, qid: str, max_props: int = 20
    ) -> list[Triple]:
        """Get structured properties for a Wikidata entity via SPARQL.

        Returns triples in our existing format for seamless integration.
        """
        session = await self._get_session()

        sparql = f"""
SELECT ?propLabel ?valueLabel WHERE {{
  wd:{qid} ?prop ?value .
  ?property wikibase:directClaim ?prop .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
  ?property rdfs:label ?propLabel .
  FILTER(LANG(?propLabel) = "en")
}} LIMIT {max_props}
"""

        try:
            async with session.get(
                WIKIDATA_SPARQL,
                params={"query": sparql, "format": "json"},
                headers={"Accept": "application/sparql-results+json"},
            ) as resp:
                if resp.status != 200:
                    logger.debug("Wikidata SPARQL returned %d", resp.status)
                    return []

                data = await resp.json()
                triples = []

                for binding in data.get("results", {}).get("bindings", []):
                    prop = binding.get("propLabel", {}).get("value", "")
                    value = binding.get("valueLabel", {}).get("value", "")

                    if prop and value and not value.startswith("http"):
                        triples.append(Triple(
                            subject=qid,
                            predicate=prop,
                            object=value,
                            source_url=f"https://www.wikidata.org/wiki/{qid}",
                            confidence=0.95,
                        ))

                return triples[:max_props]

        except Exception as e:
            logger.debug("Wikidata SPARQL failed for %s: %s", qid, e)
            return []

    async def enrich_entity(self, entity_name: str) -> list[Triple]:
        """Query Wikidata for structured facts about an entity.

        1. Search for entity → get Q-ID
        2. Query properties via SPARQL
        3. Return triples in our format

        Results are cached locally.
        """
        # Check cache
        cache_key = f"enrich:{entity_name.lower()}"
        if cache_key in self._cache:
            import time
            cached_time, cached_triples = self._cache[cache_key]
            if time.time() - cached_time < CACHE_TTL_FACTUAL:
                return cached_triples

        qid = await self.wikidata_search_entity(entity_name)
        if not qid:
            return []

        triples = await self.wikidata_get_properties(qid)

        # Replace Q-ID with entity name in triples for readability
        for t in triples:
            if t.subject == qid:
                t.subject = entity_name

        # Cache results
        import time
        self._cache[cache_key] = (time.time(), triples)

        logger.info("Enriched '%s' (%s): %d triples from Wikidata", entity_name, qid, len(triples))
        return triples

    async def get_concept_hierarchy(self, concept: str) -> list[Triple]:
        """Get subclass_of / instance_of relationships from Wikidata.

        This fills the gap in graph_store.py — no concept hierarchies currently.

        Example: "Python" → [
            Triple("Python", "instance_of", "programming language"),
            Triple("programming language", "subclass_of", "formal language"),
        ]
        """
        qid = await self.wikidata_search_entity(concept)
        if not qid:
            return []

        session = await self._get_session()

        sparql = f"""
SELECT ?typeLabel ?parentLabel WHERE {{
  {{
    wd:{qid} wdt:P31 ?type .
    OPTIONAL {{ ?type wdt:P279 ?parent . }}
  }} UNION {{
    wd:{qid} wdt:P279 ?type .
    OPTIONAL {{ ?type wdt:P279 ?parent . }}
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}} LIMIT 15
"""

        try:
            async with session.get(
                WIKIDATA_SPARQL,
                params={"query": sparql, "format": "json"},
                headers={"Accept": "application/sparql-results+json"},
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()
                triples = []

                for binding in data.get("results", {}).get("bindings", []):
                    type_label = binding.get("typeLabel", {}).get("value", "")
                    parent_label = binding.get("parentLabel", {}).get("value", "")

                    if type_label and not type_label.startswith("http"):
                        triples.append(Triple(
                            subject=concept,
                            predicate="instance_of",
                            object=type_label,
                            source_url=f"https://www.wikidata.org/wiki/{qid}",
                            confidence=0.9,
                        ))

                    if parent_label and not parent_label.startswith("http"):
                        triples.append(Triple(
                            subject=type_label,
                            predicate="subclass_of",
                            object=parent_label,
                            source_url=f"https://www.wikidata.org/wiki/{qid}",
                            confidence=0.85,
                        ))

                return triples

        except Exception as e:
            logger.debug("Concept hierarchy failed for '%s': %s", concept, e)
            return []

    async def disambiguate_entity(self, ambiguous_name: str) -> list[DisambiguationOption]:
        """Use Wikidata to identify which entity the user means.

        Returns multiple options when the name is ambiguous.

        Example: "Apple" → [
            DisambiguationOption(label="Apple Inc.", description="tech company", qid="Q312"),
            DisambiguationOption(label="Apple", description="fruit", qid="Q89"),
        ]
        """
        session = await self._get_session()
        params = {
            "action": "wbsearchentities",
            "search": ambiguous_name,
            "language": "en",
            "format": "json",
            "limit": "5",
        }

        try:
            async with session.get(
                "https://www.wikidata.org/w/api.php",
                params=params,
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()
                options = []

                for result in data.get("search", []):
                    options.append(DisambiguationOption(
                        label=result.get("label", ""),
                        description=result.get("description", ""),
                        wikidata_id=result.get("id", ""),
                        confidence=1.0 - len(options) * 0.15,  # First result highest
                    ))

                return options

        except Exception as e:
            logger.debug("Disambiguation failed for '%s': %s", ambiguous_name, e)
            return []

    # ── DBpedia Lookup (Fallback) ──

    async def dbpedia_lookup(self, query: str, max_results: int = 5) -> list[Triple]:
        """DBpedia REST lookup — structured facts, free, no auth."""
        session = await self._get_session()

        try:
            async with session.get(
                DBPEDIA_LOOKUP,
                params={
                    "query": query,
                    "maxResults": str(max_results),
                    "format": "json",
                },
                headers={"Accept": "application/json"},
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()
                triples = []

                for doc in data.get("docs", []):
                    label = doc.get("label", [""])[0] if isinstance(doc.get("label"), list) else doc.get("label", "")
                    comment = doc.get("comment", [""])[0] if isinstance(doc.get("comment"), list) else doc.get("comment", "")
                    categories = doc.get("category", [])

                    if label:
                        if comment:
                            triples.append(Triple(
                                subject=label,
                                predicate="description",
                                object=comment[:300],
                                source_url="dbpedia.org",
                                confidence=0.85,
                            ))

                        for cat in categories[:3]:
                            cat_name = cat if isinstance(cat, str) else str(cat)
                            if cat_name and not cat_name.startswith("http"):
                                triples.append(Triple(
                                    subject=label,
                                    predicate="category",
                                    object=cat_name,
                                    source_url="dbpedia.org",
                                    confidence=0.8,
                                ))

                return triples

        except Exception as e:
            logger.debug("DBpedia lookup failed: %s", e)
            return []

    # ── ConceptNet (Commonsense) ──

    async def conceptnet_relations(self, concept: str, max_results: int = 10) -> list[Triple]:
        """ConceptNet commonsense relationships.

        Useful for "what is related to X" type queries.
        """
        session = await self._get_session()
        encoded = quote(concept.replace(" ", "_"))

        try:
            async with session.get(
                f"{CONCEPTNET_API}/c/en/{encoded}",
                params={"limit": str(max_results)},
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()
                triples = []

                for edge in data.get("edges", []):
                    rel = edge.get("rel", {}).get("label", "")
                    start = edge.get("start", {}).get("label", "")
                    end = edge.get("end", {}).get("label", "")
                    weight = edge.get("weight", 1.0)

                    if rel and start and end:
                        triples.append(Triple(
                            subject=start,
                            predicate=rel,
                            object=end,
                            source_url="conceptnet.io",
                            confidence=min(weight / 5.0, 1.0),
                        ))

                return triples

        except Exception as e:
            logger.debug("ConceptNet failed for '%s': %s", concept, e)
            return []

    # ── Unified enrichment (layered fallback) ──

    async def enrich_with_fallback(
        self,
        entity_name: str,
        *,
        include_hierarchy: bool = True,
        include_commonsense: bool = False,
        graph: Optional[GraphStore] = None,
    ) -> list[Triple]:
        """Enrich an entity using the full layered KG stack.

        Layer 1: Local graph (already queried before this is called)
        Layer 2: Wikidata SPARQL
        Layer 3: DBpedia (if Wikidata thin)
        Layer 4: ConceptNet (optional, for commonsense)

        Results are auto-cached in local GraphStore.
        """
        all_triples: list[Triple] = []

        # Layer 2: Wikidata
        tasks = [self.enrich_entity(entity_name)]
        if include_hierarchy:
            tasks.append(self.get_concept_hierarchy(entity_name))

        wikidata_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in wikidata_results:
            if isinstance(result, list):
                all_triples.extend(result)

        # Layer 3: DBpedia (if Wikidata returned thin results)
        if len(all_triples) < 3:
            dbpedia = await self.dbpedia_lookup(entity_name)
            all_triples.extend(dbpedia)

        # Layer 4: ConceptNet (optional)
        if include_commonsense:
            conceptnet = await self.conceptnet_relations(entity_name)
            all_triples.extend(conceptnet)

        # Cache in local graph
        if all_triples and graph is not None:
            graph.add_triples(all_triples)
            logger.info(
                "External KG enrichment for '%s': %d triples cached locally",
                entity_name, len(all_triples),
            )

        return all_triples


# ── Module singleton ──

_bridge: Optional[ExternalKGBridge] = None


def get_external_kg() -> ExternalKGBridge:
    global _bridge
    if _bridge is None:
        _bridge = ExternalKGBridge()
    return _bridge
