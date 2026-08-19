"""
Core shared types used across the orchestrator, pivot loop, and critique.

These are ORCHESTRATOR-level types (SubagentInput/SubagentResult), not
block-internal types (BlockInput/NodeResult from blocks/semantic/types.py).
The adapter in blocks/base.py bridges the two.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SubagentType(str, Enum):
    RETRIEVER = "retriever"
    CODE_RETRIEVER = "code_retriever"
    SANDBOX = "sandbox"
    FILE_GENERATOR = "file_generator"
    CODE_GEN_EXECUTOR = "code_gen_executor"


class DecisionSource(str, Enum):
    """Where did a decision originate?"""
    PARAMETRIC = "parametric"          # LLM internal knowledge
    RETRIEVAL = "retrieval"            # From retrieved sources
    HYBRID = "hybrid"                  # Both
    USER_SELECTION = "user_selection"  # User chose branch


@dataclass
class SubagentInput:
    """Rigid tool interface for all subagents."""
    task: str
    subagent_type: SubagentType
    payload: dict = field(default_factory=dict)
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    parent_id: Optional[str] = None


@dataclass
class SubagentResult:
    """Rigid result interface from all subagents."""
    subagent_type: SubagentType
    success: bool
    learnings: list = field(default_factory=list)
    source_urls: list = field(default_factory=list)
    error_reason: str = ""
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    parent_id: Optional[str] = None


@dataclass
class Hypothesis:
    """One competing explanation for why an action failed."""
    label: str
    explanation: str
    prior: float = 0.5
    implies_circuit_break: str = ""
    pros: list[str] = field(default_factory=list)      # NEW
    cons: list[str] = field(default_factory=list)      # NEW
    supporting_evidence: list[str] = field(default_factory=list)  # NEW


@dataclass
class PivotDecision:
    """Output of the pivot loop."""
    goal: str
    confirmed_hypothesis: Optional[Hypothesis] = None
    next_action: str = ""
    circuit_break: list[str] = field(default_factory=list)


@dataclass
class Learning:
    """One fact extracted from retrieval, with provenance and relevance."""
    text: str
    source_url: str = ""
    score: float = 0.0


@dataclass
class DecisionTrace:
    """Trace of a decision point for transparency"""
    decision_id: str
    source: DecisionSource
    confidence: float
    alternatives: list[str] = field(default_factory=list)
    reasoning: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class CorrectionPattern:
    """Record of one user correction for learning"""
    pattern_type: str              # "error", "wanted_more_depth", "too_verbose", etc.
    severity: float                # 0-1, how bad?
    domain: str = ""               # "oauth", "react", etc. or empty for general
    timestamp: float = field(default_factory=time.time)
    decay_factor: float = 1.0      # multiplier for age (fades over time)
