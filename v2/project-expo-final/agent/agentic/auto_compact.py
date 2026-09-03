"""
Auto-compact — Conversation summarization (Layer 2).

Ported from:
  - src/services/compact/prompt.ts (9-section prompt template — VERBATIM)
  - src/services/compact/compact.ts (compactConversation, retry-on-PTL, circuit breaker)
  - src/services/compact/autoCompact.ts (threshold, consecutive failure tracking)

Key differences from our simplified version:
  - Full 9-section prompt with <analysis> scratchpad + <summary> block
  - formatCompactSummary() strips analysis, formats summary with headers
  - getCompactUserSummaryMessage() creates proper continuation message
  - Retry loop: if compact itself hits prompt-too-long, drop oldest 20% and retry (max 3)
  - Circuit breaker: max 3 consecutive failures, then stop retrying
  - Transcript path injection for post-compact file reading
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .loop import AgenticMessage

logger = logging.getLogger(__name__)

# ── Constants from src/compact/autoCompact.ts ──
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
MAX_PTL_RETRIES = 3
MAX_COMPACT_STREAMING_RETRIES = 2
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000

# Track consecutive failures (module-level state from src/)
_consecutive_failures = 0

# ── Prompt templates — VERBATIM from src/services/compact/prompt.ts ──

NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

DETAILED_ANALYSIS_INSTRUCTION = """Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""

BASE_COMPACT_PROMPT = f"""Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

{DETAILED_ANALYSIS_INSTRUCTION}

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages: 
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response. 

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>
"""

NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only - "
    "an <analysis> block followed by a <summary> block. "
    "Tool calls will be rejected and you will fail the task."
)


def get_compact_prompt(custom_instructions: str = "") -> str:
    """Build the full compact prompt. From src/compact/prompt.ts getCompactPrompt()."""
    prompt = NO_TOOLS_PREAMBLE + BASE_COMPACT_PROMPT
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"
    prompt += NO_TOOLS_TRAILER
    return prompt


def format_compact_summary(summary: str) -> str:
    """Format compact summary by stripping analysis and formatting summary tags.

    From src/compact/prompt.ts formatCompactSummary():
      - Strip <analysis>...</analysis> (drafting scratchpad, no informational value)
      - Extract <summary>...</summary> and replace tags with headers
      - Clean up extra whitespace
    """
    formatted = summary

    # Strip analysis section
    formatted = re.sub(r'<analysis>[\s\S]*?</analysis>', '', formatted)

    # Extract and format summary section
    match = re.search(r'<summary>([\s\S]*?)</summary>', formatted)
    if match:
        content = match.group(1).strip()
        formatted = re.sub(r'<summary>[\s\S]*?</summary>', f'Summary:\n{content}', formatted)

    # Clean up extra whitespace
    formatted = re.sub(r'\n\n+', '\n\n', formatted)

    return formatted.strip()


def get_compact_user_summary_message(
    summary: str,
    suppress_follow_up_questions: bool = True,
) -> str:
    """Build the user-facing summary message for post-compact context.

    From src/compact/prompt.ts getCompactUserSummaryMessage():
      - Wraps formatted summary with continuation context
      - Tells the LLM to resume without asking questions
    """
    formatted = format_compact_summary(summary)

    base = (
        "This session is being continued from a previous conversation that ran "
        "out of context. The summary below covers the earlier portion of the conversation.\n\n"
        f"{formatted}"
    )

    if suppress_follow_up_questions:
        return (
            f"{base}\n"
            "Continue the conversation from where it left off without asking the user "
            "any further questions. Resume directly - do not acknowledge the summary, "
            "do not recap what was happening, do not preface with \"I'll continue\" or "
            "similar. Pick up the last task as if the break never happened."
        )

    return base


# ── Core compact logic — from src/services/compact/compact.ts ──

async def auto_compact_messages(
    messages: list['AgenticMessage'],
    client,  # NIMClient
) -> list['AgenticMessage']:
    """Summarize the conversation into a compact boundary message.

    Full port from src/:
      - 9-section prompt with <analysis> scratchpad
      - formatCompactSummary() strips analysis, extracts summary
      - Retry loop: if compact itself hits PTL, drop oldest 20% and retry (max 3)
      - Circuit breaker: max 3 consecutive failures
      - Proper continuation message from getCompactUserSummaryMessage()
    """
    global _consecutive_failures
    from .loop import AgenticMessage

    # Circuit breaker from src/autoCompact.ts L70
    if _consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
        logger.warning(
            "Auto-compact circuit breaker: %d consecutive failures, skipping",
            _consecutive_failures,
        )
        return _fallback_compact(messages)

    # Build conversation text for summarizer
    conversation_parts = []
    for msg in messages:
        if msg.role == "system":
            continue  # Don't include system prompt in summary input
        role_label = {
            "user": "User",
            "assistant": "Assistant",
            "tool_result": "Tool Result",
        }.get(msg.role, msg.role.title())

        # Truncate very long tool results in the summary input
        content = msg.content
        if msg.role == "tool_result" and len(content) > 2000:
            content = content[:2000] + f"\n[... truncated {len(msg.content) - 2000} chars]"

        conversation_parts.append(f"### {role_label}\n{content}")

    conversation_text = "\n\n".join(conversation_parts)

    # Retry loop for prompt-too-long (from src/compact/compact.ts L450-491)
    compact_prompt = get_compact_prompt()
    current_conv_text = conversation_text
    summary = None

    for attempt in range(MAX_PTL_RETRIES + 1):
        summary_messages = [
            {"role": "system", "content": compact_prompt},
            {"role": "user", "content": f"Here is the conversation to summarize:\n\n{current_conv_text}"},
        ]

        try:
            summary = await client.chat(
                messages=summary_messages,
                max_tokens=4000,
                temperature=0.2,
                timeout=60,
            )
        except Exception as e:
            _consecutive_failures += 1
            logger.error("Auto-compact LLM call failed (attempt %d): %s", attempt + 1, e)
            if attempt < MAX_PTL_RETRIES:
                # Drop oldest 20% and retry (from src/ truncateHeadForPTLRetry)
                lines = current_conv_text.split("\n\n### ")
                drop_count = max(1, len(lines) // 5)
                current_conv_text = "\n\n### ".join(lines[drop_count:])
                logger.info(
                    "PTL retry %d: dropped %d oldest sections, %d remaining",
                    attempt + 1, drop_count, len(lines) - drop_count,
                )
                continue
            return _fallback_compact(messages)

        if summary and len(summary.strip()) >= 100:
            # Success - reset circuit breaker
            _consecutive_failures = 0
            break
        else:
            _consecutive_failures += 1
            logger.warning("Auto-compact produced empty/short summary (attempt %d)", attempt + 1)
            if attempt < MAX_PTL_RETRIES:
                continue
            return _fallback_compact(messages)
    else:
        return _fallback_compact(messages)

    # Format the summary (strip <analysis>, extract <summary>)
    formatted_summary = get_compact_user_summary_message(summary, suppress_follow_up_questions=True)

    # Build the compacted message list
    new_messages = []

    # 1. Keep system message
    for msg in messages:
        if msg.role == "system":
            new_messages.append(msg)
            break

    # 2. Add compaction boundary (the formatted summary)
    new_messages.append(AgenticMessage(
        role="user",
        content=formatted_summary,
    ))

    # 3. Add assistant acknowledgment (from src/ compact.ts)
    new_messages.append(AgenticMessage(
        role="assistant",
        content=(
            "I've reviewed the conversation summary above. "
            "I have full context of what we've discussed and what needs to be done. "
            "I'll continue from where we left off."
        ),
    ))

    # 4. Keep the last user message (so the LLM knows the current task)
    last_user = None
    for msg in reversed(messages):
        if msg.role == "user":
            last_user = msg
            break

    if last_user and last_user.content != new_messages[-1].content:
        new_messages.append(last_user)

    pre_tokens = sum(m.token_estimate for m in messages)
    post_tokens = sum(m.token_estimate for m in new_messages)
    logger.info(
        "Auto-compact complete: %d tokens -> %d tokens (%.0f%% reduction)",
        pre_tokens, post_tokens,
        (1 - post_tokens / max(pre_tokens, 1)) * 100,
    )

    return new_messages


def _fallback_compact(messages: list['AgenticMessage']) -> list['AgenticMessage']:
    """Fallback when LLM compaction fails - just keep system + recent messages."""
    new_messages = []

    # Keep system
    for msg in messages:
        if msg.role == "system":
            new_messages.append(msg)
            break

    # Keep last 6 messages
    recent = messages[-6:]
    new_messages.extend(recent)

    logger.info("Fallback compact: kept system + %d recent messages", len(recent))
    return new_messages
