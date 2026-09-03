"""
Entity Profile — data structures for detective-style KG exploration.

An EntityProfile holds EVERYTHING known about one entity:
  - aliases (alternative names for search)
  - type hierarchy (what category it belongs to)
  - properties (location, dates, people, accreditation...)
  - connected entities (relationships to other entities)

An InvestigationQuery is a search query derived from KG properties.
Each property becomes a "string to pull" for the detective.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EntityProfile:
    """Complete profile of one entity from KG investigation."""
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    wikidata_id: str = ""
    type_hierarchy: list[str] = field(default_factory=list)  # ["university", "educational institution"]
    properties: dict[str, str] = field(default_factory=dict)  # {"location": "Kanchipuram", ...}
    connected_entities: list[str] = field(default_factory=list)
    investigation_depth: int = 0  # How many hops we've explored

    def add_property(self, predicate: str, obj: str):
        """Add a property, deduplicating."""
        key = predicate.lower().strip()
        if key and obj and not obj.startswith("http"):
            self.properties[key] = obj

    @property
    def is_rich(self) -> bool:
        """Has enough data to generate investigation queries."""
        return bool(self.aliases) or len(self.properties) > 2 or bool(self.type_hierarchy)


@dataclass
class InvestigationQuery:
    """One query derived from KG exploration — a 'lead' for the detective."""
    query: str
    source: str          # "alias" | "property" | "hierarchy" | "relationship" | "location" | "temporal" | "person"
    priority: float      # 0-1, how valuable this lead is
    reasoning: str       # Why this query matters (detective's note)
    entity_origin: str = ""  # Which entity this came from
