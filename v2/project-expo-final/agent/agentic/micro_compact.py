"""
Micro-Compact - Clear old tool results to save context space (Layer 1).

Ported from:
  - src/services/compact/microCompact.ts (L226-530 — FULL logic)
  - src/services/compact/timeBasedMCConfig.ts (time-based trigger)

Layer 1 of the 3-layer compaction system:
  Layer 1: Micro-compact (this file) - clear old tool results
  Layer 1.5: Snip compact (in loop.py) - drop oldest messages
  Layer 2: Auto-compact (auto_compact.py) - LLM summarization

Key features ported from src/:
  - collectCompactableToolIds() - walks messages for compactable tool IDs
  - Time-based MC: if gap since last assistant > threshold, clear aggressively
  - Per-block token estimation with 4/3 conservative padding
  - Tool-aware: respects is_compactable flag per tool
  - Content-cleared message format matching src/
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .loop import AgenticMessage

logger = logging.getLogger(__name__)

# ── Constants from src/ ──
MIN_TOKENS_TO_CLEAR = 200     # Don't clear results smaller than this
KEEP_RECENT = 2               # Keep N most recent tool results intact
IMAGE_MAX_TOKEN_SIZE = 2000   # Images ~2000 tokens regardless of format
TIME_BASED_MC_CLEARED_MSG = "[content cleared by time-based microcompact]"

# Time-based MC config (from src/timeBasedMCConfig.ts)
TIME_BASED_MC_ENABLED = True       # Enable time-based clearing
TIME_BASED_MC_GAP_MINUTES = 60     # Trigger after 60 min idle
TIME_BASED_MC_KEEP_RECENT = 5      # Keep last 5 results in time-based mode


def _rough_token_estimate(text: str) -> int:
    """Rough token count (chars / 4) with 4/3 conservative padding.

    From src/microCompact.ts L203-204:
      return Math.ceil(totalTokens * (4 / 3))
    """
    base = len(text) // 4
    return int(base * 4 / 3)  # 4/3 padding for conservatism


def _estimate_message_tokens(msg: 'AgenticMessage') -> int:
    """Estimate token count for a message.

    From src/microCompact.ts estimateMessageTokens() L164-204:
      - Text: rough estimate with padding
      - Tool results: calculate content tokens
      - Tool use: count name + input
    """
    tokens = _rough_token_estimate(msg.content)

    # If it has tool results, count those too
    if msg.tool_results:
        for result in msg.tool_results:
            if hasattr(result, 'content') and result.content:
                tokens += _rough_token_estimate(str(result.content))

    return tokens


def _collect_compactable_tool_indices(
    messages: list['AgenticMessage'],
    registry=None,
) -> list[int]:
    """Walk messages and collect indices of compactable tool_result messages.

    From src/microCompact.ts collectCompactableToolIds() L226-241:
      - Only collect tools that are in COMPACTABLE_TOOLS set
      - Skip non-compactable tools (think, etc.)
    """
    indices = []
    for i, msg in enumerate(messages):
        if msg.role != "tool_result":
            continue

        # Check if this tool is compactable
        is_compactable = True
        if registry and msg.tool_results:
            for r in msg.tool_results:
                tool_def = registry.get_tool(r.tool_name)
                if tool_def and not tool_def.is_compactable:
                    is_compactable = False
                    break

        if is_compactable:
            indices.append(i)

    return indices


def _get_last_assistant_time(messages: list['AgenticMessage']) -> Optional[float]:
    """Find the timestamp of the last assistant message.

    From src/microCompact.ts L434-439:
      const lastAssistant = messages.findLast(m => m.type === 'assistant')
    """
    for msg in reversed(messages):
        if msg.role == "assistant":
            return getattr(msg, 'timestamp', None)
    return None


def _maybe_time_based_microcompact(
    messages: list['AgenticMessage'],
    registry=None,
) -> Optional[list['AgenticMessage']]:
    """Time-based microcompact: clear aggressively if idle too long.

    From src/microCompact.ts L446-530:
      - When gap since last assistant > threshold, cache is cold
      - Content-clear all but most recent N compactable tool results
      - Returns None if trigger doesn't fire (caller falls through)
    """
    if not TIME_BASED_MC_ENABLED:
        return None

    last_assistant_time = _get_last_assistant_time(messages)
    if last_assistant_time is None:
        return None

    gap_minutes = (time.time() - last_assistant_time) / 60

    if gap_minutes < TIME_BASED_MC_GAP_MINUTES:
        return None

    # Fire! Collect compactable indices
    compactable_indices = _collect_compactable_tool_indices(messages, registry)
    if not compactable_indices:
        return None

    # Keep last N, clear the rest
    keep_count = max(1, TIME_BASED_MC_KEEP_RECENT)  # Floor at 1 (from src/ L461)
    keep_set = set(compactable_indices[-keep_count:])
    clear_set = set(compactable_indices) - keep_set

    if not clear_set:
        return None

    # Create new messages with cleared results
    from .loop import AgenticMessage

    tokens_saved = 0
    new_messages = []
    for i, msg in enumerate(messages):
        if i in clear_set:
            tokens_saved += msg.token_estimate
            new_messages.append(AgenticMessage(
                role="tool_result",
                content=TIME_BASED_MC_CLEARED_MSG,
                tool_results=[],
                token_estimate=len(TIME_BASED_MC_CLEARED_MSG) // 4,
            ))
        else:
            new_messages.append(msg)

    if tokens_saved == 0:
        return None

    logger.info(
        "[TIME-BASED MC] gap %.0fmin > %dmin, cleared %d tool results (~%d tokens), kept last %d",
        gap_minutes, TIME_BASED_MC_GAP_MINUTES,
        len(clear_set), tokens_saved, len(keep_set),
    )

    return new_messages


def micro_compact_messages(
    messages: list['AgenticMessage'],
    registry=None,  # ToolRegistry for checking is_compactable
) -> list['AgenticMessage']:
    """Apply micro-compaction to messages.

    From src/microCompact.ts microcompactMessages() L253-293:
      1. Time-based trigger runs first (short-circuits if fires)
      2. Then standard compaction: clear old, large tool results

    Returns:
        New list of messages (does NOT mutate originals).
    """
    if not messages:
        return messages

    # ── Step 1: Time-based trigger (from src/ L267-270) ──
    time_result = _maybe_time_based_microcompact(messages, registry)
    if time_result is not None:
        return time_result

    # ── Step 2: Standard micro-compact ──
    compactable_indices = _collect_compactable_tool_indices(messages, registry)

    if len(compactable_indices) <= KEEP_RECENT:
        return messages

    # Keep most recent
    keep_indices = set(compactable_indices[-KEEP_RECENT:])
    clear_indices = set(compactable_indices) - keep_indices

    # Also keep small results (from src/)
    for idx in list(clear_indices):
        if messages[idx].token_estimate < MIN_TOKENS_TO_CLEAR:
            clear_indices.discard(idx)

    if not clear_indices:
        return messages

    # Create new message list with cleared results
    from .loop import AgenticMessage

    new_messages = []
    tokens_freed = 0

    for i, msg in enumerate(messages):
        if i in clear_indices:
            original_tokens = msg.token_estimate
            tool_names = [r.tool_name for r in msg.tool_results] if msg.tool_results else ["tool"]
            stub_content = (
                f"[content cleared: was ~{original_tokens} tokens from {', '.join(tool_names)}]"
            )
            new_messages.append(AgenticMessage(
                role="tool_result",
                content=stub_content,
                tool_results=[],
                token_estimate=len(stub_content) // 4,
            ))
            tokens_freed += original_tokens - (len(stub_content) // 4)
        else:
            new_messages.append(msg)

    logger.info(
        "Micro-compact: cleared %d/%d tool results, freed ~%d tokens",
        len(clear_indices), len(compactable_indices), tokens_freed,
    )

    return new_messages
