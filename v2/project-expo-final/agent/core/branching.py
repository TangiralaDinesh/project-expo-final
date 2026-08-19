"""
Bayesian Branching (Tier 3) — Present Competing Hypotheses to User

When the agent is uncertain (confidence gap between top hypotheses is small),
present both options to the user and let them choose which direction to explore.

This enables speculative reasoning: "I think it could be A or B, which interests you?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class BranchingDecisionType(str, Enum):
    """Types of branching decisions"""
    AUTOMATIC = "automatic"      # Agent picked one hypothesis (high confidence)
    PRESENTED = "presented"      # Agent presented multiple to user (uncertain)
    USER_SELECTED = "user_selected"  # User chose a hypothesis
    CLARIFIED = "clarified"      # User provided clarification, no need for branching


@dataclass
class BranchingDecision:
    """Represents a branching decision point"""
    decision_type: BranchingDecisionType
    
    # For automatic decisions:
    selected_hypothesis_idx: int = 0     # Which hypothesis won
    confidence: float = 0.0
    
    # For presented decisions:
    presented_options: list = field(default_factory=list)  # List of BranchingOption
    confidence_gap: float = 0.0          # Gap between top 2 (small = presented to user)
    
    # For user-selected decisions:
    user_selected_idx: int = -1          # Index of user's choice (-1 = no selection)
    user_reasoning: str = ""             # Why did user choose this?
    
    # Metadata:
    domain: str = ""                     # oauth, react, etc.
    query: str = ""                      # Original query
    timestamp: float = field(default_factory=lambda: __import__('time').time())


def should_present_branching(
    top_hypothesis_confidence: float,
    second_hypothesis_confidence: float,
    threshold: float = 0.25,
) -> bool:
    """
    Decide whether to present branching options to user.
    
    If confidence gap is small (< threshold), options are too close to pick automatically.
    Present them to user instead.
    
    Args:
        top_hypothesis_confidence: Confidence in best hypothesis (0-1)
        second_hypothesis_confidence: Confidence in second best (0-1)
        threshold: Gap below which to present options (default 0.25)
    
    Returns:
        True if should present options, False if auto-select
    """
    gap = top_hypothesis_confidence - second_hypothesis_confidence
    
    # Only present if:
    # 1. There's meaningful ambiguity (gap < threshold)
    # 2. Top hypothesis isn't already very confident (>0.8)
    should_present = (
        gap < threshold and
        top_hypothesis_confidence < 0.85
    )
    
    logger.debug(
        f"Branching decision: gap={gap:.3f}, top_conf={top_hypothesis_confidence:.3f}, "
        f"should_present={should_present}"
    )
    
    return should_present


def format_branching_for_user(options: list) -> str:
    """
    Format branching options for user presentation.
    
    Returns a readable string showing all options with their pros/cons.
    """
    from ..core.pivot import BranchingOption
    
    lines = [
        "🤔 **I'm uncertain about the best approach. Here are your options:**\n",
    ]
    
    for i, opt in enumerate(options, 1):
        lines.append(f"**Option {i}: {opt.label}**")
        lines.append(f"  {opt.explanation}")
        
        if opt.pros:
            lines.append(f"  ✅ Pros: {', '.join(opt.pros)}")
        if opt.cons:
            lines.append(f"  ❌ Cons: {', '.join(opt.cons)}")
        
        lines.append(f"  Confidence: {opt.confidence*100:.0f}% | "
                    f"Depth: {opt.estimated_depth} levels")
        lines.append("")
    
    lines.append("**Which approach would you like to explore?**")
    lines.append("(Reply with: Option 1, Option 2, etc. or provide clarification)")
    
    return "\n".join(lines)


def parse_user_branch_selection(user_input: str, num_options: int) -> Optional[int]:
    """
    Parse user's selection of which branch to follow.
    
    Supports formats like:
    - "Option 1"
    - "1" 
    - "First option"
    - Or returns None if user provided clarification instead
    
    Returns:
        0-based index of selected option, or None if couldn't parse
    """
    import re
    
    user_lower = user_input.lower().strip()
    
    # Try to extract number
    numbers = re.findall(r'\b(\d+)\b', user_lower)
    if numbers:
        idx = int(numbers[0]) - 1  # Convert to 0-based
        if 0 <= idx < num_options:
            return idx
    
    # Try ordinal parsing
    ordinals = {
        'first': 0, 'second': 1, 'third': 2, 'fourth': 3,
        'option 1': 0, 'option 2': 1, 'option 3': 2, 'option 4': 3,
    }
    
    for text, idx in ordinals.items():
        if text in user_lower and idx < num_options:
            return idx
    
    # Couldn't parse as selection, probably clarification
    logger.debug(f"Could not parse branch selection from: {user_input}")
    return None


@dataclass
class BranchingContext:
    """Context for a branching decision that spans multiple turns"""
    decision_id: str              # Unique ID for this branching point
    original_query: str
    options: list                 # BranchingOption objects
    active: bool = True
    user_selection: Optional[int] = None
    user_clarification: str = ""
    
    def mark_resolved(self, selection_idx: int, clarification: str = ""):
        """User resolved the branching by selecting or clarifying"""
        self.active = False
        self.user_selection = selection_idx
        self.user_clarification = clarification
        logger.info(f"Branching {self.decision_id} resolved: "
                   f"selection={selection_idx}, clarification={clarification[:50]}")


# ── Phase 14: Bayesian Branch Selection ──


def compute_branch_entropy(options: list) -> float:
    """Compute Shannon entropy across branching options.

    High entropy = options equally likely = high ambiguity → ask user
    Low entropy = one option dominates = low ambiguity → auto-select

    Args:
        options: List of BranchingOption objects with .confidence field

    Returns:
        Normalized entropy (0-1)
    """
    import math

    if not options or len(options) < 2:
        return 0.0

    confidences = [getattr(opt, "confidence", 0.5) for opt in options]
    total = sum(confidences) or 1.0
    probs = [c / total for c in confidences]

    entropy = 0.0
    for p in probs:
        if p > 0:
            entropy -= p * math.log2(p)

    max_entropy = math.log2(len(options))
    return entropy / max_entropy if max_entropy > 0 else 0.0


async def bayesian_branch_selection(
    branches: list,
    learnings: list,
    query: str,
    *,
    client=None,
) -> BranchingDecision:
    """Use information entropy to decide between branches.

    Decision matrix:
    1. Low entropy (one branch dominates) → auto-select
    2. High entropy (branches close) → compute which QUESTION would most
       reduce entropy if answered → present THAT question to user

    This is Bayesian optimal experiment design:
    - Each branch is a hypothesis
    - Each speculative question is an "experiment"
    - Pick the experiment that maximizes expected information gain

    Args:
        branches: BranchingOption objects to choose between
        learnings: Current learnings for context
        query: Original query

    Returns:
        BranchingDecision with auto-selection or user-prompt
    """
    if not branches:
        return BranchingDecision(
            decision_type=BranchingDecisionType.AUTOMATIC,
            confidence=1.0,
        )

    if len(branches) == 1:
        return BranchingDecision(
            decision_type=BranchingDecisionType.AUTOMATIC,
            selected_hypothesis_idx=0,
            confidence=getattr(branches[0], "confidence", 0.8),
        )

    entropy = compute_branch_entropy(branches)

    # Get confidence-sorted indices
    indexed = sorted(
        enumerate(branches),
        key=lambda x: getattr(x[1], "confidence", 0),
        reverse=True,
    )

    top_idx, top_branch = indexed[0]
    second_idx, second_branch = indexed[1]

    top_conf = getattr(top_branch, "confidence", 0.5)
    second_conf = getattr(second_branch, "confidence", 0.5)
    gap = top_conf - second_conf

    logger.info(
        "Bayesian branch selection: entropy=%.3f, gap=%.3f, "
        "top=%s(%.2f), second=%s(%.2f)",
        entropy, gap,
        getattr(top_branch, "label", "?"), top_conf,
        getattr(second_branch, "label", "?"), second_conf,
    )

    # Low entropy or clear winner → auto-select
    if entropy < 0.5 or gap > 0.25 or top_conf > 0.85:
        return BranchingDecision(
            decision_type=BranchingDecisionType.AUTOMATIC,
            selected_hypothesis_idx=top_idx,
            confidence=top_conf,
            confidence_gap=gap,
        )

    # High entropy → generate discriminating question
    discriminating_question = await _generate_discriminating_question(
        branches, query, learnings, client=client,
    )

    decision = BranchingDecision(
        decision_type=BranchingDecisionType.PRESENTED,
        presented_options=branches,
        confidence_gap=gap,
    )

    # Store the discriminating question in metadata
    if discriminating_question:
        decision.user_reasoning = discriminating_question

    return decision


async def _generate_discriminating_question(
    branches: list,
    query: str,
    learnings: list,
    *,
    client=None,
) -> str:
    """Generate the single question that would most discriminate between branches.

    This is the EIG principle applied to branch disambiguation:
    "If I could ask the user ONE question, which question would most
    reduce my uncertainty about which branch is correct?"
    """
    if client is None:
        from ..llm.client import get_client
        client = get_client()

    branch_descriptions = "\n".join(
        f"Option {i+1}: {getattr(b, 'label', str(b))} — "
        f"{getattr(b, 'explanation', '')} (confidence: {getattr(b, 'confidence', 0):.0%})"
        for i, b in enumerate(branches)
    )

    prompt = f"""I'm trying to answer: "{query}"

I have {len(branches)} possible approaches but can't decide between them:

{branch_descriptions}

Generate the SINGLE most discriminating question I could ask the user that would make one option clearly better than the others.

The question should:
1. Be specific and actionable (not "what do you prefer?")
2. Have answers that clearly favor one option over another
3. Address the KEY difference between options

Return ONLY the question, no explanation."""

    try:
        response = await client.chat_worker(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=100,
        )
        question = response.strip().strip('"').strip("'")
        logger.info("Generated discriminating question: %s", question[:80])
        return question
    except Exception as e:
        logger.warning("Discriminating question generation failed: %s", e)
        return ""

