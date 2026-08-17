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
