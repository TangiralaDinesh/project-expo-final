"""
Hypothesis-Driven Pivoting — the ONE shared control loop used at both:
  - per-node inside a retriever (which query to try next)
  - orchestrator-level (which subagent to trust)

GOAL → ACTION → OBSERVE → HYPOTHESIZE → DISCRIMINATE → PIVOT.

Do NOT duplicate this logic inside individual subagents or the
orchestrator — they import and call into this module.

No blind retries: on failure this goes straight to competing hypotheses
rather than trying the same action again. That's the specific failure
mode this loop exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .types import Hypothesis, PivotDecision


@dataclass
class Observation:
    """What actually happened after ACTION, vs. what the goal required."""
    succeeded: bool
    result: Any = None
    detail: str = ""


@dataclass
class BranchingOption:
    """One hypothesis presented to user as a choice (Tier 3 feature)"""
    label: str                          # "Empirical Approach", "Practical Approach"
    explanation: str                    # Why choose this?
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    confidence: float = 0.5             # Agent's confidence in this approach
    evidence_level: str = "moderate"    # "weak" | "moderate" | "strong"
    estimated_depth: int = 2            # How deep will this go?


# Caller-supplied callables — kept as plain functions so this loop
# stays usable whether the hypothesis step is an LLM call, a rule, or a test stub.
HypothesisGenerator = Callable[[str, Observation], list[Hypothesis]]
DiscriminatingExperiment = Callable[[Hypothesis, Hypothesis], Awaitable[Observation]]


async def run_pivot_loop(
    goal: str,
    first_action: Callable[[], Awaitable[Observation]],
    generate_hypotheses: HypothesisGenerator,
    run_discriminating_experiment: DiscriminatingExperiment,
    branching_enabled: bool = False,  # NEW Tier 3: show branches to user?
    confidence_threshold: float = 0.75,  # NEW: branch if confidence gap small
) -> tuple[PivotDecision, list[BranchingOption]]:  # NEW: return branching options
    """
    GOAL → ACTION → OBSERVE once. If that satisfies the goal, done.
    If not: HYPOTHESIZE → DISCRIMINATE → PIVOT.

    NEW (Tier 3): If branching_enabled and top 2 hypotheses are close in confidence,
    returns both as branching options for user selection.
    Otherwise returns empty list and auto-selected decision.

    Returns: (decision, branching_options)
    """
    observation = await first_action()
    if observation.succeeded:
        return (
            PivotDecision(
                goal=goal,
                confirmed_hypothesis=None,
                next_action="none -- first action satisfied the goal",
            ),
            []  # No branches needed
        )

    hypotheses = generate_hypotheses(goal, observation)
    if not hypotheses:
        return (
            PivotDecision(
                goal=goal,
                confirmed_hypothesis=None,
                next_action="abandon -- no hypotheses generated",
                circuit_break=[observation.detail] if observation.detail else [],
            ),
            []
        )

    # Rank by prior, take top two
    hypotheses = sorted(hypotheses, key=lambda h: h.prior, reverse=True)
    h_a = hypotheses[0]
    h_b = hypotheses[1] if len(hypotheses) > 1 else None
    
    # Check if branching should be offered
    branching_options: list[BranchingOption] = []
    if branching_enabled and h_b:
        confidence_gap = h_a.prior - h_b.prior
        
        # If gap is small, offer both as options (Tier 3 feature)
        if confidence_gap < 0.25:
            branching_options = [
                BranchingOption(
                    label=h.label,
                    explanation=h.explanation,
                    pros=h.pros if hasattr(h, 'pros') else [],
                    cons=h.cons if hasattr(h, 'cons') else [],
                    confidence=h.prior,
                    evidence_level="strong" if h.prior > 0.7 else "moderate" if h.prior > 0.5 else "weak",
                )
                for h in [h_a, h_b]
            ]
    
    # Auto-select top hypothesis
    confirmed = h_a
    
    return (
        PivotDecision(
            goal=goal,
            confirmed_hypothesis=confirmed,
            next_action=f"pivot via {confirmed.label}: {confirmed.explanation}",
            circuit_break=[confirmed.implies_circuit_break] if confirmed.implies_circuit_break else [],
        ),
        branching_options
    )
