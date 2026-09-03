"""
KG Activation Gate — Decide if a query needs detective-style KG investigation.

Pure heuristic — NO LLM call (0ms).

Activate KG when:
  ✅ SEMANTIC/HYBRID mode AND has named entities (acronyms or proper nouns)
  ✅ Query asks about specific thing/person/place/organization
  ✅ Time-sensitive queries with entities ("SCSVMV live news")

Skip KG when:
  ❌ PARAMETRIC / COMPUTATION mode (no retrieval needed)
  ❌ Pure conceptual question ("what is recursion")
  ❌ Simple factual ("what is 2+2")
  ❌ Code questions ("how to sort in python")
  ❌ Very short queries (< 3 words — not enough context)
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Common English words that look like acronyms but aren't
_FALSE_ACRONYMS = frozenset({
    "I", "A", "OK", "US", "UK", "AI", "IT", "OR", "AND", "NOT",
    "AM", "PM", "TV", "VS", "ID", "PC", "CD", "DJ", "HR",
})

# Words that indicate the query is about a SPECIFIC entity (not generic)
_SPECIFICITY_SIGNALS = frozenset({
    "news", "latest", "recent", "update", "live", "current", "today",
    "about", "review", "vs", "versus", "compare", "history", "profile",
})


def should_investigate_kg(query: str, gate_mode: str) -> bool:
    """Decide if this query warrants KG investigation.
    
    Returns True if the query has named entities worth investigating.
    Zero LLM calls — pure pattern matching.
    """
    # Only investigate for retrieval-bound queries
    if gate_mode not in ("SEMANTIC", "HYBRID"):
        return False

    words = query.split()
    
    # Too short — not enough context for meaningful investigation
    if len(words) < 2:
        return False

    # Count proper nouns (capitalized words not at sentence start)
    proper_nouns = [
        w for i, w in enumerate(words)
        if i > 0 and w[0:1].isupper() and len(w) > 2
        and w not in _FALSE_ACRONYMS
    ]

    # Acronyms (all caps, 2+ chars, alphabetic)
    acronyms = [
        w for w in words
        if w.isupper() and len(w) >= 2 and w.isalpha()
        and w not in _FALSE_ACRONYMS
    ]

    # Check first word too if it looks like an entity (not a question word)
    question_starters = {"what", "who", "where", "when", "how", "why", "is", "are", "do", "does", "can", "should"}
    if words[0].lower() not in question_starters and words[0][0:1].isupper() and len(words[0]) > 2:
        proper_nouns.append(words[0])

    has_entities = len(proper_nouns) + len(acronyms) > 0

    # Time-sensitive + entity = definitely investigate
    has_specificity = any(w.lower() in _SPECIFICITY_SIGNALS for w in words)

    if has_entities:
        logger.debug(
            "KG gate: ACTIVATE — entities=%s, acronyms=%s, specific=%s",
            proper_nouns[:3], acronyms[:3], has_specificity,
        )
        return True

    return False


def extract_entities_fast(query: str) -> list[str]:
    """Fast entity extraction from query text (no LLM).
    
    Uses:
    1. Acronyms (all caps, 2+ chars)
    2. Capitalized multi-word phrases ("Tom Holland", "Sri Chandrasekharendra")
    3. Single capitalized words that aren't question starters
    
    Returns deduplicated entity names.
    """
    entities = []

    # Pattern 1: Acronyms (SCSVMV, IIT, NASA)
    for match in re.finditer(r'\b([A-Z]{2,})\b', query):
        word = match.group(1)
        if word not in _FALSE_ACRONYMS:
            entities.append(word)

    # Pattern 2: Capitalized multi-word phrases ("Tom Holland", "New York")
    for match in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', query):
        entities.append(match.group(1))

    # Pattern 3: Single capitalized words (not at sentence start, not common)
    words = query.split()
    question_starters = {"What", "Who", "Where", "When", "How", "Why", "Is", "Are", "Do", "Does", "Can", "Should", "Tell", "Give", "Show", "Find", "Get"}
    for i, word in enumerate(words):
        clean = word.strip(".,!?\"'()[]")
        if (
            clean and clean[0].isupper()
            and len(clean) > 2
            and clean not in question_starters
            and clean not in _FALSE_ACRONYMS
            and clean not in entities  # Not already captured
            and not clean.isupper()   # Not an acronym (already captured)
        ):
            # Only add if it's not the first word OR if it looks entity-like
            if i > 0 or (i == 0 and clean.lower() not in {w.lower() for w in question_starters}):
                entities.append(clean)

    # Deduplicate (case-insensitive)
    seen = set()
    unique = []
    for e in entities:
        if e.lower() not in seen:
            seen.add(e.lower())
            unique.append(e)

    return unique[:5]  # Cap at 5 entities
