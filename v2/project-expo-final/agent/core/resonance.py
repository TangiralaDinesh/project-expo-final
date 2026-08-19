"""
Information Resonance Score — derived pattern for non-sycophantic sufficiency.

Constraint: LLM sufficiency checks are sycophantic (they say "enough" when shallow).
           Fixed depth limits are arbitrary.
Insight:   The LEARNINGS THEMSELVES contain the signal for when to stop.
Mechanism: Measure overlap between retrieval rounds.

Physics analogy: Resonance occurs when a driving frequency matches a system's
natural frequency — maximum energy transfer. In retrieval, "resonance" = when
new learnings strongly REINFORCE existing learnings rather than adding NEW info.

  High resonance (>0.7): New learnings restate what we know → STOP
  Low resonance (<0.3):  Entirely novel info → KEEP GOING
  Medium (0.3-0.7):      Mix → CHECK SATISFACTION per-concept

This replaces the decision LLM's `sufficient` as the PRIMARY signal.
Decision LLM becomes secondary confirmation:
  - resonance > 0.7 AND LLM sufficient → confident stop
  - resonance < 0.3 → keep going REGARDLESS of LLM
  - resonance 0.3-0.7 → defer to LLM + satisfaction tracker
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ResonanceResult:
    """Result of resonance computation between retrieval rounds."""
    score: float                    # 0-1: how much new info resonates with existing
    novel_concepts: list[str]       # Concepts found in new but not existing
    reinforced_concepts: list[str]  # Concepts found in both
    recommendation: str             # "stop" | "continue" | "check_satisfaction"
    confidence: float               # How confident we are in this recommendation


def _extract_concept_terms(text: str) -> set[str]:
    """Extract meaningful concept terms from text (words > 3 chars, lowered)."""
    stopwords = {
        "this", "that", "with", "from", "about", "which", "where", "when",
        "have", "make", "will", "should", "could", "would", "than", "then",
        "also", "been", "some", "more", "most", "very", "just", "into",
        "over", "such", "only", "other", "their", "there", "what", "your",
        "they", "each", "does", "were", "like", "many", "well", "back",
        "much", "after", "before", "these", "those", "being", "between",
        "through", "during", "both", "same", "different", "used", "using",
    }
    words = set()
    for w in text.lower().split():
        clean = "".join(c for c in w if c.isalnum())
        if len(clean) > 3 and clean not in stopwords:
            words.add(clean)
    return words


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two term sets."""
    if not set_a and not set_b:
        return 1.0  # Both empty = identical
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def compute_resonance(
    existing_learnings: list,
    new_learnings: list,
) -> ResonanceResult:
    """
    Compute how much new learnings RESONATE with (reinforce) existing knowledge.

    Algorithm:
    1. Extract concept terms from all existing learnings (combined)
    2. Extract concept terms from all new learnings (combined)
    3. Compute per-learning max similarity to existing knowledge
    4. resonance = weighted mean of per-learning similarities
    5. Adjust by information density: novel concepts / total concepts

    Args:
        existing_learnings: Learnings from previous retrieval rounds
        new_learnings: Learnings from the current retrieval round

    Returns:
        ResonanceResult with score, novel concepts, and recommendation
    """
    if not existing_learnings or not new_learnings:
        return ResonanceResult(
            score=0.0,
            novel_concepts=[],
            reinforced_concepts=[],
            recommendation="continue",
            confidence=0.5,
        )

    # Build existing knowledge term set (combined across all existing learnings)
    existing_terms: set[str] = set()
    existing_per_learning: list[set[str]] = []
    for learning in existing_learnings:
        text = getattr(learning, "text", str(learning))
        terms = _extract_concept_terms(text)
        existing_terms.update(terms)
        existing_per_learning.append(terms)

    # Compute per-new-learning resonance against existing knowledge
    per_learning_scores: list[float] = []
    all_new_terms: set[str] = set()

    for learning in new_learnings:
        text = getattr(learning, "text", str(learning))
        new_terms = _extract_concept_terms(text)
        all_new_terms.update(new_terms)

        if not new_terms:
            per_learning_scores.append(0.5)
            continue

        # Method 1: Similarity against combined existing knowledge
        global_sim = _jaccard_similarity(new_terms, existing_terms)

        # Method 2: Max similarity against any single existing learning
        # (catches the case where new info restates ONE specific existing learning)
        max_individual_sim = 0.0
        for existing_set in existing_per_learning:
            sim = _jaccard_similarity(new_terms, existing_set)
            max_individual_sim = max(max_individual_sim, sim)

        # Combined: weight individual match higher (catches restatement)
        combined = 0.4 * global_sim + 0.6 * max_individual_sim
        per_learning_scores.append(combined)

    # Overall resonance = mean of per-learning scores
    resonance = sum(per_learning_scores) / len(per_learning_scores)

    # Identify novel vs reinforced concepts
    novel = all_new_terms - existing_terms
    reinforced = all_new_terms & existing_terms

    # Information density adjustment:
    # If many novel concepts relative to total, reduce resonance
    # (even if some overlap exists, lots of new concepts = keep going)
    if all_new_terms:
        novelty_ratio = len(novel) / len(all_new_terms)
        # Dampen resonance when there's high novelty
        resonance = resonance * (1.0 - novelty_ratio * 0.3)

    # Clamp to [0, 1]
    resonance = max(0.0, min(1.0, resonance))

    # Determine recommendation
    if resonance > 0.7:
        recommendation = "stop"
        confidence = min(0.9, resonance)
    elif resonance < 0.3:
        recommendation = "continue"
        confidence = min(0.9, 1.0 - resonance)
    else:
        recommendation = "check_satisfaction"
        # Confidence is lower in the middle zone
        confidence = 0.5 + abs(resonance - 0.5) * 0.4

    logger.info(
        "Resonance: %.3f (novel=%d, reinforced=%d) → %s (conf=%.2f)",
        resonance, len(novel), len(reinforced), recommendation, confidence,
    )

    return ResonanceResult(
        score=resonance,
        novel_concepts=sorted(list(novel))[:20],
        reinforced_concepts=sorted(list(reinforced))[:20],
        recommendation=recommendation,
        confidence=confidence,
    )


def should_continue_retrieval(
    existing_learnings: list,
    new_learnings: list,
    decision_llm_says_sufficient: bool = False,
    satisfaction_scores: Optional[dict[str, float]] = None,
    min_satisfaction: float = 0.6,
) -> tuple[bool, str]:
    """
    Combine resonance + decision LLM + satisfaction for final continue/stop decision.

    Decision matrix:
    ┌──────────┬──────────────┬───────────────┬──────────┐
    │ Resonance│ LLM says     │ Satisfaction  │ Decision │
    │          │ sufficient?  │ all > 0.6?    │          │
    ├──────────┼──────────────┼───────────────┼──────────┤
    │ > 0.7    │ Yes          │ Yes           │ STOP     │
    │ > 0.7    │ Yes          │ No            │ CONTINUE │ ← satisfaction overrides
    │ > 0.7    │ No           │ Yes           │ STOP     │ ← resonance overrides LLM
    │ 0.3-0.7  │ Yes          │ Yes           │ STOP     │
    │ 0.3-0.7  │ No           │ -             │ CONTINUE │
    │ < 0.3    │ -            │ -             │ CONTINUE │ ← always continue
    └──────────┴──────────────┴───────────────┴──────────┘

    Returns:
        (should_continue: bool, reason: str)
    """
    result = compute_resonance(existing_learnings, new_learnings)

    # Check satisfaction if provided
    satisfaction_met = True
    unsatisfied = []
    if satisfaction_scores:
        for concept, score in satisfaction_scores.items():
            if score < min_satisfaction:
                satisfaction_met = False
                unsatisfied.append(f"{concept}={score:.2f}")

    # Decision matrix
    if result.score < 0.3:
        return True, f"resonance_low ({result.score:.2f}): new info is mostly novel, keep going"

    if result.score > 0.7:
        if not satisfaction_met:
            return True, (
                f"resonance_high ({result.score:.2f}) but unsatisfied concepts: "
                f"{', '.join(unsatisfied)}"
            )
        return False, f"resonance_high ({result.score:.2f}): new info is mostly restating, stop"

    # Middle zone: defer to LLM + satisfaction
    if decision_llm_says_sufficient and satisfaction_met:
        return False, (
            f"resonance_medium ({result.score:.2f}), LLM says sufficient, "
            f"satisfaction met → stop"
        )

    reason_parts = [f"resonance_medium ({result.score:.2f})"]
    if not decision_llm_says_sufficient:
        reason_parts.append("LLM says insufficient")
    if not satisfaction_met:
        reason_parts.append(f"unsatisfied: {', '.join(unsatisfied)}")
    return True, " + ".join(reason_parts) + " → continue"
""", "Description": "Derived pattern: resonance-based sufficiency detection that measures learning overlap between retrieval rounds instead of relying on sycophantic LLM judgments."
