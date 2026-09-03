"""
Error Recovery — 5-layer recovery system for the agentic loop.

Ported from src/query.ts (L800-1200) recovery mechanisms:

1. Reactive Compact: API returns context too long → compact → retry
2. Output Cap Escalation: Response truncated → retry with higher max_tokens
3. Output Recovery: Still truncated → inject "continue, no recap" → retry 3x
4. Hallucination Interception: LLM says "let me" but no tool call → inject reminder
5. Model Fallback: Primary model fails → switch to fallback endpoint

Each recovery returns a RecoveryAction that the agentic loop applies.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .loop import AgenticMessage, AgenticState

logger = logging.getLogger(__name__)


class RecoveryType(str, Enum):
    NONE = "none"
    COMPACT = "compact"
    ESCALATE_TOKENS = "escalate_tokens"
    CONTINUE_OUTPUT = "continue_output"
    HALLUCINATION = "hallucination"
    MODEL_FALLBACK = "model_fallback"


@dataclass
class RecoveryAction:
    """Action to take in response to an error."""
    type: RecoveryType
    message: str = ""  # Message to inject into conversation
    max_tokens_override: int = 0  # For escalation
    should_retry: bool = False


# ── Hallucination patterns ──
# From src/: model describes actions but doesn't execute tools
HALLUCINATION_PATTERNS = [
    r"(?i)let me search",
    r"(?i)I'll look (?:up|into|for)",
    r"(?i)I'll use the .+ tool",
    r"(?i)searching for",
    r"(?i)let me find",
    r"(?i)I need to search",
    r"(?i)I'll research",
    r"(?i)let me check",
]


def detect_recovery_needed(
    response: str,
    error: Optional[str] = None,
    had_tool_calls: bool = False,
    recovery_count: int = 0,
) -> RecoveryAction:
    """Detect if recovery is needed and what type.

    Args:
        response: The LLM's response text
        error: Error message if the LLM call failed
        had_tool_calls: Whether the response contained valid tool calls
        recovery_count: How many recoveries we've already done (circuit breaker)

    Returns:
        RecoveryAction describing what to do.
    """
    # Circuit breaker: max 3 total recoveries per session
    if recovery_count >= 3:
        logger.info("Recovery circuit breaker: %d recoveries, stopping", recovery_count)
        return RecoveryAction(type=RecoveryType.NONE)

    # ── Recovery 1: Context too long ──
    if error and any(phrase in error.lower() for phrase in [
        "context too long",
        "prompt too long",
        "token limit",
        "maximum context",
        "context length exceeded",
    ]):
        logger.info("Recovery: context too long detected, triggering compact")
        return RecoveryAction(
            type=RecoveryType.COMPACT,
            should_retry=True,
        )

    # ── Recovery 2: Output truncated ──
    if error and "max_output_tokens" in error.lower():
        logger.info("Recovery: output truncated, escalating max_tokens")
        return RecoveryAction(
            type=RecoveryType.ESCALATE_TOKENS,
            max_tokens_override=8192,  # Escalate from 4K to 8K
            should_retry=True,
        )

    # ── Recovery 3: Output still truncated after escalation ──
    if response and response.rstrip().endswith(("...", "…", "```")) and len(response) > 3500:
        logger.info("Recovery: output appears truncated, injecting continue")
        return RecoveryAction(
            type=RecoveryType.CONTINUE_OUTPUT,
            message=(
                "Your previous response was truncated. Please continue EXACTLY "
                "where you left off. Do NOT repeat or summarize what you've already said. "
                "Resume directly from the point of truncation."
            ),
            should_retry=True,
        )

    # ── Recovery 4: Hallucination interception ──
    # Model describes tool usage but doesn't actually call tools
    if not had_tool_calls and response:
        for pattern in HALLUCINATION_PATTERNS:
            if re.search(pattern, response):
                logger.info("Recovery: hallucination detected (said tool action but no tool call)")
                return RecoveryAction(
                    type=RecoveryType.HALLUCINATION,
                    message=(
                        "You described a tool action but didn't actually call a tool. "
                        "To use a tool, you MUST output it in the exact format:\n\n"
                        "<tool_call>\n"
                        '{{"tool": "TOOL_NAME", "args": {{"param": "value"}}}}\n'
                        "</tool_call>\n\n"
                        "Please either call the tool properly, or provide your answer directly."
                    ),
                    should_retry=True,
                )

    # ── Recovery 5: Model fallback ──
    if error and any(phrase in error.lower() for phrase in [
        "overloaded",
        "rate limit",
        "503",
        "502",
        "connection",
        "timeout",
    ]):
        logger.info("Recovery: model error detected, recommending fallback")
        return RecoveryAction(
            type=RecoveryType.MODEL_FALLBACK,
            should_retry=True,
        )

    return RecoveryAction(type=RecoveryType.NONE)


def apply_recovery(
    action: RecoveryAction,
    messages: list['AgenticMessage'],
) -> list['AgenticMessage']:
    """Apply a recovery action to the message list.

    Returns the modified message list with recovery injections.
    """
    from .loop import AgenticMessage

    if action.type == RecoveryType.NONE:
        return messages

    if action.message:
        messages.append(AgenticMessage(
            role="user",
            content=f"[System Recovery: {action.type.value}]\n{action.message}",
        ))

    return messages
