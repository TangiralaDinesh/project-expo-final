"""
Context Assembly — Build the structured system prompt for the agentic loop.

Ported from src/context.ts + src/QueryEngine.ts

The system prompt has layers:
  1. Identity + capabilities (static)
  2. Tool definitions (static per session)
  3. Project context (dynamic — git state, user rules)
  4. Per-turn context (dynamic — session memory, previous compaction summary)

This replaces v2's inline prompt building with a structured, cacheable system.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ── System Prompt Template ──
# From src/'s defaultSystemPrompt — adapted for our agent

SYSTEM_PROMPT_TEMPLATE = """You are a research agent that helps users find comprehensive, accurate information.

## Your Capabilities

You have access to tools that you can use to research and answer questions. Use them wisely:

1. **deep_research**: For complex queries needing thorough investigation from multiple sources. This tool runs Knowledge Graph investigation, speculative exploration, fan-out queries, and entropy-based filtering. Use this for anything that needs depth.

2. **web_search**: For quick factual lookups, current events, or simple fact-checking. Faster but less thorough than deep_research.

3. **think**: Your private scratchpad. Use this to plan, analyze, or reason through problems before responding. The user won't see this.

## How to Work

- For simple questions (facts, definitions): Answer directly from knowledge, or use web_search for verification.
- For complex questions (comparisons, analysis, research): Use deep_research to get comprehensive data, then synthesize.
- For multi-part questions: Break them down. Use think to plan, then use tools for each part.
- If a tool returns poor results: Try different search terms. Don't give up after one attempt.

## Tool Calling Format

When you want to use a tool, output a tool call in this EXACT format:

<tool_call>
{{"tool": "TOOL_NAME", "args": {{"param": "value"}}}}
</tool_call>

You can make multiple tool calls in one response. Each must be in its own <tool_call> block.

When you're done researching and ready to give your final answer, just write the answer directly WITHOUT any tool_call blocks. This signals that you're finished.

## Response Quality

- Be thorough but concise
- Cite sources when available
- Acknowledge uncertainty
- If you can't find information, say so honestly
- Structure long answers with headers and bullet points

## Research Philosophy
 - From src/constants/prompts.ts — adapted for our research agent

- You are highly capable and often allow users to complete ambitious research that would otherwise be too complex or take too long.
- If an approach fails, diagnose why before switching tactics — read the error, check your assumptions, try a focused fix. Don't retry the identical action blindly, but don't abandon a viable approach after a single failure either.
- When you don't have enough information, investigate first before making assumptions. Understand existing data before suggesting conclusions.
- Avoid giving time estimates or predictions. Focus on what needs to be done, not how long it might take.
- If you notice the user's request is based on a misconception, say so. You're a collaborator, not just an executor — users benefit from your judgment, not just your compliance.

## Tool Selection Strategy
 - From src/constants/prompts.ts getUsingYourToolsSection()

- Do NOT use bash for operations when a relevant dedicated tool is provided. Using dedicated tools produces better results:
  - To read files use file_read instead of cat, head, tail, or sed
  - To edit files use file_edit instead of sed or awk
  - To create files use file_write instead of cat with heredoc or echo
  - To search for files use glob instead of find or ls
  - To search file content use grep instead of grep or rg
- Reserve bash exclusively for system commands and terminal operations that require shell execution.
- You can call multiple tools in a single response. If you intend to call multiple tools and there are no dependencies between them, make all independent tool calls in parallel. Maximize parallel tool calls for efficiency. However, if some calls depend on previous results, call them sequentially.

## Executing Actions with Care
 - From src/constants/prompts.ts getActionsSection()

- Carefully consider the reversibility and blast radius of actions. Generally you can freely take local, reversible actions like reading files or running searches.
- For actions that are hard to reverse, affect shared systems, or could be destructive, confirm with the user before proceeding.
- When you encounter an obstacle, do not use destructive actions as a shortcut. Investigate root causes and fix underlying issues.
- Match the scope of your actions to what was actually requested.

## Task Decomposition
 - From src/tools/TodoWriteTool/prompt.ts

- For complex multi-step tasks (3+ steps), break them down into tracked subtasks.
- Mark each task as in_progress BEFORE beginning work. Only have one task in_progress at a time.
- Mark tasks completed AFTER finishing. Add follow-up tasks discovered during work.
- First SEARCH to understand scope, THEN create tasks.
- Skip task tracking for trivial single-step operations.

## Output Efficiency
 - From src/constants/prompts.ts getOutputEfficiencySection()

- Go straight to the point. Try the simplest approach first without going in circles.
- Keep text output brief and direct. Lead with the answer or action, not the reasoning.
- Focus text output on: decisions needing user input, key status updates, errors or blockers.
- If you can say it in one sentence, don't use three.

## Preserving Important Results
 - From src/ SUMMARIZE_TOOL_RESULTS_SECTION

- When working with tool results, write down any important information you might need later in your response, as the original tool result may be cleared from context later to free up space.
- The most recent results are always kept, but older ones may be automatically collapsed.

{context_section}
"""

TOOL_PROMPT_TEMPLATE = """## Available Tools

{tool_definitions}

Remember: Use <tool_call> blocks to call tools. When you're ready to answer, write your response WITHOUT tool_call blocks.
"""


def build_system_prompt(context: dict[str, str] = None) -> str:
    """Build the full system prompt with dynamic context.

    Args:
        context: Dynamic context dict, e.g.:
            - "current_date": "2026-08-28"
            - "project_context": "Python research agent..."
            - "user_rules": "Prefer detailed responses..."
            - "session_memory": "User previously asked about..."

    Returns:
        Complete system prompt string.
    """
    context = context or {}

    # Build context section from dynamic data
    context_parts = []

    # Current date/time
    now = datetime.now(timezone.utc)
    context_parts.append(f"Current date: {now.strftime('%Y-%m-%d %H:%M UTC')}")

    # Project context (if provided)
    if "project_context" in context:
        context_parts.append(f"\n## Project Context\n{context['project_context']}")

    # User rules (if provided)
    if "user_rules" in context:
        context_parts.append(f"\n## User Preferences\n{context['user_rules']}")

    # Session memory (survives compaction — from src/SessionMemory)
    if "session_memory" in context:
        context_parts.append(f"\n## Session Memory\n{context['session_memory']}")

    # Previous compaction summary (if conversation was compacted)
    if "compaction_summary" in context:
        context_parts.append(
            f"\n## Previous Conversation Summary\n{context['compaction_summary']}"
        )

    context_section = "\n".join(context_parts)

    return SYSTEM_PROMPT_TEMPLATE.format(context_section=context_section)


def build_tool_prompt(tool_schemas: list[dict]) -> str:
    """Build the tool definitions section for the system prompt.

    Args:
        tool_schemas: List of tool schemas from ToolRegistry.get_all_schemas()

    Returns:
        Formatted tool definitions section.
    """
    if not tool_schemas:
        return ""

    tool_lines = []
    for schema in tool_schemas:
        name = schema["name"]
        desc = schema["description"]
        params = schema.get("parameters", {}).get("properties", {})
        required = schema.get("parameters", {}).get("required", [])

        param_lines = []
        for pname, pinfo in params.items():
            req = " (required)" if pname in required else " (optional)"
            ptype = pinfo.get("type", "string")
            pdesc = pinfo.get("description", "")
            param_lines.append(f"    - {pname} ({ptype}{req}): {pdesc}")

        tool_block = f"### {name}\n{desc}\n**Parameters:**\n" + "\n".join(param_lines)
        tool_lines.append(tool_block)

    definitions = "\n\n".join(tool_lines)
    return TOOL_PROMPT_TEMPLATE.format(tool_definitions=definitions)


# ── Context Collapse (from src/utils/collapseReadSearch.ts) ──
# Collapses large old tool results into summaries to save context space.
# Keeps recent results intact (recency bias from src/).

# Tool names that are collapsible (from src/ COMPACTABLE_TOOLS + readSearch)
COLLAPSIBLE_TOOLS = {
    "file_read", "bash", "grep", "glob", "web_search", "web_fetch",
    "file_edit", "file_write", "list_dir",
}

# Threshold: results above this size (in chars) are candidates for collapse
COLLAPSE_THRESHOLD_CHARS = 5000

# Keep last N% of messages intact regardless
RECENCY_KEEP_RATIO = 0.2
RECENCY_KEEP_MIN = 5


def context_collapse(
    messages: list[dict],
    budget_chars: int = 150_000,
) -> tuple[list[dict], int, int]:
    """Collapse large old tool results to save context space.

    Ported from src/utils/collapseReadSearch.ts logic:
    - Consecutive read/search results from older turns get collapsed
    - Recent results (last 20%) are preserved intact
    - Budget enforcement: if total chars exceed budget, collapse aggressively

    Args:
        messages: List of message dicts with 'role', 'content', optional 'tool_name'
        budget_chars: Max total chars across all messages

    Returns:
        (collapsed_messages, removed_count, saved_chars)
    """
    total_messages = len(messages)
    if total_messages == 0:
        return messages, 0, 0

    keep_recent = max(RECENCY_KEEP_MIN, int(total_messages * RECENCY_KEEP_RATIO))
    cutoff = total_messages - keep_recent

    collapsed = []
    removed_count = 0
    saved_chars = 0
    total_chars = 0

    for i, msg in enumerate(messages):
        content = msg.get("content", "")
        tool_name = msg.get("tool_name", "")

        # Check if this message is collapsible
        is_old = i < cutoff
        is_large = len(content) > COLLAPSE_THRESHOLD_CHARS
        is_tool = tool_name in COLLAPSIBLE_TOOLS

        if is_old and is_large and is_tool:
            # Collapse: keep first 200 chars + summary
            preview = content[:200].rstrip()
            summary = (
                f"[Collapsed {tool_name} result: {len(content):,} chars]\n"
                f"{preview}\n[... {len(content) - 200:,} chars omitted]"
            )
            collapsed.append({**msg, "content": summary})
            saved_chars += len(content) - len(summary)
            removed_count += 1
        else:
            collapsed.append(msg)

        total_chars += len(collapsed[-1].get("content", ""))

        # Emergency budget enforcement
        if total_chars > budget_chars and i < cutoff:
            last = collapsed[-1]
            last_content = last.get("content", "")
            if len(last_content) > 1000:
                truncated = last_content[:500] + "\n[... collapsed for budget]"
                saved_chars += len(last_content) - len(truncated)
                total_chars -= len(last_content) - len(truncated)
                collapsed[-1] = {**last, "content": truncated}

    return collapsed, removed_count, saved_chars


def apply_per_message_budget(
    messages: list[dict],
    aggregate_budget: int = 150_000,
) -> list[dict]:
    """Apply aggregate token budget across all tool results in a turn.

    Ported from src/utils/toolResultStorage.ts applyToolResultBudget().
    Each tool already has per-tool max_result_chars, but this enforces
    the AGGREGATE budget across ALL results in one turn.

    Args:
        messages: List of tool result messages
        aggregate_budget: Max total chars for all results combined

    Returns:
        Budget-enforced messages (truncated if needed)
    """
    if not messages:
        return messages

    total = sum(len(m.get("content", "")) for m in messages)
    if total <= aggregate_budget:
        return messages  # Under budget, no action needed

    # Over budget — proportionally truncate from largest to smallest
    result = []
    remaining_budget = aggregate_budget

    # Sort by size (largest first) for proportional trimming
    indexed = [(i, m, len(m.get("content", ""))) for i, m in enumerate(messages)]
    indexed.sort(key=lambda x: -x[2])

    budgets = {}
    for i, msg, size in indexed:
        share = max(500, int(remaining_budget * (size / max(total, 1))))
        budgets[i] = min(size, share)
        remaining_budget -= budgets[i]
        total -= size

    # Apply budgets in original order
    for i, msg in enumerate(messages):
        budget = budgets.get(i, len(msg.get("content", "")))
        content = msg.get("content", "")
        if len(content) > budget:
            truncated = content[:budget] + f"\n[... truncated to {budget:,} chars of {len(content):,}]"
            result.append({**msg, "content": truncated})
        else:
            result.append(msg)

    return result
