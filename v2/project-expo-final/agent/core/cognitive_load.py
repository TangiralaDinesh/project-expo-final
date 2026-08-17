"""
Graduated Activation for Feature Control (Phase 5).

Determines which features to enable based on query complexity.

Solves: v2's "feature explosion" where critique + speculation ran on every query,
wasting time and degrading performance on simple queries.

Approach: Compute cognitive load, then activate features based on complexity.
- Light queries: Basic retrieval only
- Standard queries: Basic retrieval + critique
- Complex queries: Full pipeline (retrieval + critique + speculation + progressive)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ActivationLevel(Enum):
    """Feature activation levels based on cognitive load."""
    LIGHT = "light"           # Simple queries: basic retrieval
    STANDARD = "standard"     # Moderate: retrieval + critique
    FULL = "full"             # Complex: full pipeline


@dataclass
class CognitiveLoad:
    """Represents query complexity and resource requirements."""
    complexity_score: float        # 0-1, computed from signals
    activation_level: ActivationLevel
    rationale: str                 # Explanation of classification
    
    # Feature flags based on activation level
    @property
    def enable_critique(self) -> bool:
        """Enable multi-persona critique guidance."""
        return self.activation_level in [ActivationLevel.STANDARD, ActivationLevel.FULL]
    
    @property
    def enable_speculation(self) -> bool:
        """Enable speculative subquery generation."""
        return self.activation_level == ActivationLevel.FULL
    
    @property
    def enable_progressive(self) -> bool:
        """Enable progressive loading and streaming."""
        return self.activation_level in [ActivationLevel.STANDARD, ActivationLevel.FULL]
    
    @property
    def enable_branching(self) -> bool:
        """Enable dynamic subquery branching."""
        return self.activation_level == ActivationLevel.FULL
    
    @property
    def max_depth(self) -> int:
        """Max retrieval depth by activation level."""
        levels = {
            ActivationLevel.LIGHT: 2,
            ActivationLevel.STANDARD: 3,
            ActivationLevel.FULL: 4,
        }
        return levels.get(self.activation_level, 3)
    
    @property
    def timeout_seconds(self) -> int:
        """Max retrieval time by activation level."""
        levels = {
            ActivationLevel.LIGHT: 5,
            ActivationLevel.STANDARD: 15,
            ActivationLevel.FULL: 30,
        }
        return levels.get(self.activation_level, 15)


def compute_cognitive_load(query: str) -> CognitiveLoad:
    """
    Compute cognitive load from query characteristics.
    
    Signals:
    1. Query length and vocabulary sophistication
    2. Query structure (single-part vs multi-part)
    3. Expected answer complexity
    4. Implicit aspect count
    """
    
    # Signal 1: Query length & word count
    word_count = len(query.split())
    length_score = min(word_count / 30, 1.0)  # Normalize: 30+ words = high
    
    # Signal 2: Technical language
    has_technical_terms = _detect_technical_language(query)
    technical_score = 1.0 if has_technical_terms else 0.0
    
    # Signal 3: Query structure (multi-part question)
    is_multi_part = _detect_multi_part_question(query)
    multi_part_score = 1.0 if is_multi_part else 0.0
    
    # Signal 4: Aspect count (implicit concepts in query)
    aspect_keywords = _detect_aspect_keywords(query)
    aspect_count = len(aspect_keywords)
    aspect_score = min(aspect_count / 5, 1.0)  # Normalize: 5+ aspects = high
    
    # Signal 5: Comparative language
    is_comparative = _detect_comparative_language(query)
    comparative_score = 0.8 if is_comparative else 0.0
    
    # Compute weighted complexity score
    complexity = (
        length_score * 0.25 +              # 25%: Length
        technical_score * 0.20 +           # 20%: Technical language
        multi_part_score * 0.20 +          # 20%: Multi-part structure
        aspect_score * 0.25 +              # 25%: Aspect count
        comparative_score * 0.10            # 10%: Comparative intent
    )
    
    # Clamp to 0-1
    complexity = min(max(complexity, 0.0), 1.0)
    
    # Determine activation level
    if complexity < 0.3:
        level = ActivationLevel.LIGHT
        rationale = "Simple query: basic retrieval only"
    elif complexity < 0.6:
        level = ActivationLevel.STANDARD
        rationale = "Moderate complexity: standard pipeline with critique"
    else:
        level = ActivationLevel.FULL
        rationale = "Complex query: full pipeline with all features"
    
    logger.debug(
        f"Cognitive load: complexity={complexity:.2f}, level={level.value}, "
        f"signals=[len={length_score:.2f}, tech={technical_score:.2f}, "
        f"multi={multi_part_score:.2f}, aspects={aspect_score:.2f}]"
    )
    
    return CognitiveLoad(
        complexity_score=complexity,
        activation_level=level,
        rationale=rationale,
    )


def _detect_technical_language(query: str) -> bool:
    """Detect if query uses technical/domain-specific language."""
    technical_keywords = {
        "algorithm", "architecture", "api", "binary", "cache", "crypto",
        "database", "debug", "deploy", "encode", "framework", "gpu",
        "implement", "integrate", "optimize", "protocol", "regex",
        "schema", "syntax", "threading", "async", "tcp", "http",
        "machine learning", "neural", "tensor", "vector", "distributed",
        "blockchain", "smart contract", "microservice", "container",
    }
    
    query_lower = query.lower()
    found = sum(1 for kw in technical_keywords if kw in query_lower)
    return found > 0


def _detect_multi_part_question(query: str) -> bool:
    """Detect if query has multiple distinct sub-questions."""
    # Signs of multi-part: multiple question marks, "and", "also", commas with conjunctions
    question_count = query.count("?")
    if question_count > 1:
        return True
    
    # Look for conjunction patterns
    multi_part_patterns = ["and how", "also ", "as well as", "furthermore", "; also"]
    found = sum(1 for pattern in multi_part_patterns if pattern in query.lower())
    
    return found > 0


def _detect_comparative_language(query: str) -> bool:
    """Detect if query is comparative (vs, compare, better, etc.)."""
    comparative_keywords = {
        "vs", "versus", "compare", "comparison", "better", "best",
        "worse", "difference", "or", "choose", "should i", "which one",
        "alternative", "instead of", "rather than",
    }
    
    query_lower = query.lower()
    return any(kw in query_lower for kw in comparative_keywords)


def _detect_aspect_keywords(query: str) -> list[str]:
    """Extract implicit aspects mentioned in query."""
    aspects = []
    
    # Factual aspects
    if any(w in query.lower() for w in ["who", "biography", "life", "story", "background"]):
        aspects.extend(["origin", "biography", "impact"])
    
    # Technical aspects
    if any(w in query.lower() for w in ["how", "work", "mechanism", "process", "algorithm"]):
        aspects.extend(["mechanism", "components", "workflow"])
    
    # Comparative aspects
    if any(w in query.lower() for w in ["compare", "vs", "better", "choose", "should i buy"]):
        aspects.extend(["cost", "features", "performance", "support"])
    
    # Trend aspects
    if any(w in query.lower() for w in ["trend", "future", "evolution", "change"]):
        aspects.extend(["history", "trends", "future"])
    
    return list(set(aspects))  # Deduplicate


# Feature flags for query.py to use
def should_enable_feature(query: str, feature_name: str) -> bool:
    """Determine if a feature should be enabled for this query."""
    load = compute_cognitive_load(query)
    
    feature_flags = {
        "critique": load.enable_critique,
        "speculation": load.enable_speculation,
        "progressive": load.enable_progressive,
        "branching": load.enable_branching,
        "knowledge_graph": load.enable_critique,  # Use with critique
        "adaptive_depth": True,  # Always on
    }
    
    return feature_flags.get(feature_name, False)


def get_query_parameters(query: str) -> dict:
    """Get recommended query parameters based on cognitive load."""
    load = compute_cognitive_load(query)
    
    return {
        "activation_level": load.activation_level.value,
        "max_depth": load.max_depth,
        "timeout_seconds": load.timeout_seconds,
        "enable_critique": load.enable_critique,
        "enable_speculation": load.enable_speculation,
        "enable_progressive": load.enable_progressive,
        "enable_branching": load.enable_branching,
        "complexity_score": load.complexity_score,
        "rationale": load.rationale,
    }
