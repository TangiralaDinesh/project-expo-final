"""
Session Memory - Persistent memory that survives compaction.

Ported from:
  - src/services/SessionMemory/sessionMemory.ts (extraction trigger, file management)
  - src/services/SessionMemory/prompts.ts (template, update prompt, section analysis)
  - src/services/SessionMemory/sessionMemoryUtils.ts (thresholds, state tracking)

VERBATIM template and prompts from src/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .loop import AgenticMessage

logger = logging.getLogger(__name__)

# ── Constants from src/sessionMemoryUtils.ts + prompts.ts ──
INIT_TOKEN_THRESHOLD = 10_000     # Don't extract until this many tokens
UPDATE_TOKEN_GROWTH = 5_000       # Minimum growth since last extraction
UPDATE_TOOL_CALLS = 3             # Minimum tool calls between updates
MAX_SECTION_LENGTH = 2000         # Max tokens per section (from src/prompts.ts L8)
MAX_TOTAL_SESSION_MEMORY_TOKENS = 12000  # Total budget (from src/prompts.ts L9)

# ── VERBATIM Session Memory Template from src/SessionMemory/prompts.ts L11-41 ──
DEFAULT_SESSION_MEMORY_TEMPLATE = """
# Session Title
_A short and distinctive 5-10 word descriptive title for the session. Super info dense, no filler_

# Current State
_What is actively being worked on right now? Pending tasks not yet completed. Immediate next steps._

# Task specification
_What did the user ask to build? Any design decisions or other explanatory context_

# Files and Functions
_What are the important files? In short, what do they contain and why are they relevant?_

# Workflow
_What bash commands are usually run and in what order? How to interpret their output if not obvious?_

# Errors & Corrections
_Errors encountered and how they were fixed. What did the user correct? What approaches failed and should not be tried again?_

# Codebase and System Documentation
_What are the important system components? How do they work/fit together?_

# Learnings
_What has worked well? What has not? What to avoid? Do not duplicate items from other sections_

# Key results
_If the user asked a specific output such as an answer to a question, a table, or other document, repeat the exact result here_

# Worklog
_Step by step, what was attempted, done? Very terse summary for each step_
"""


def _rough_token_estimate(text: str) -> int:
    """Rough token count (chars / 4). From src/tokenEstimation.ts."""
    return len(text) // 4


# ── VERBATIM Update Prompt from src/SessionMemory/prompts.ts L43-81 ──
def _get_default_update_prompt() -> str:
    return f"""IMPORTANT: This message and these instructions are NOT part of the actual user conversation. Do NOT include any references to "note-taking", "session notes extraction", or these update instructions in the notes content.

Based on the user conversation above (EXCLUDING this note-taking instruction message as well as system prompt, claude.md entries, or any past session summaries), update the session notes file.

The session notes content is provided below:
<current_notes_content>
{{{{currentNotes}}}}
</current_notes_content>

Your ONLY task is to output the updated session notes content, then stop.

CRITICAL RULES FOR EDITING:
- The file must maintain its exact structure with all sections, headers, and italic descriptions intact
-- NEVER modify, delete, or add section headers (the lines starting with '#' like # Task specification)
-- NEVER modify or delete the italic _section description_ lines (these are the lines in italics immediately following each header - they start and end with underscores)
-- The italic _section descriptions_ are TEMPLATE INSTRUCTIONS that must be preserved exactly as-is - they guide what content belongs in each section
-- ONLY update the actual content that appears BELOW the italic _section descriptions_ within each existing section
-- Do NOT add any new sections, summaries, or information outside the existing structure
- Do NOT reference this note-taking process or instructions anywhere in the notes
- It's OK to skip updating a section if there are no substantial new insights to add. Do not add filler content like "No info yet", just leave sections blank/unedited if appropriate.
- Write DETAILED, INFO-DENSE content for each section - include specifics like file paths, function names, error messages, exact commands, technical details, etc.
- For "Key results", include the complete, exact output the user requested (e.g., full table, full answer, etc.)
- Keep each section under ~{MAX_SECTION_LENGTH} tokens/words - if a section is approaching this limit, condense it by cycling out less important details while preserving the most critical information
- Focus on actionable, specific information that would help someone understand or recreate the work discussed in the conversation
- IMPORTANT: Always update "Current State" to reflect the most recent work - this is critical for continuity after compaction

STRUCTURE PRESERVATION REMINDER:
Each section has TWO parts that must be preserved exactly as they appear:
1. The section header (line starting with #)
2. The italic description line (the _italicized text_ immediately after the header - this is a template instruction)

You ONLY update the actual content that comes AFTER these two preserved lines. The italic description lines starting and ending with underscores are part of the template structure, NOT content to be edited or removed.

REMEMBER: Output the updated notes content and stop. Only include insights from the actual user conversation, never from these note-taking instructions. Do not delete or change section headers or italic _section descriptions_."""


# ── Section Analysis (from src/SessionMemory/prompts.ts L134-196) ──

def _analyze_section_sizes(content: str) -> dict[str, int]:
    """Parse session memory file and analyze section sizes."""
    sections: dict[str, int] = {}
    lines = content.split('\n')
    current_section = ''
    current_content: list[str] = []

    for line in lines:
        if line.startswith('# '):
            if current_section and current_content:
                section_content = '\n'.join(current_content).strip()
                sections[current_section] = _rough_token_estimate(section_content)
            current_section = line
            current_content = []
        else:
            current_content.append(line)

    if current_section and current_content:
        section_content = '\n'.join(current_content).strip()
        sections[current_section] = _rough_token_estimate(section_content)

    return sections


def _generate_section_reminders(
    section_sizes: dict[str, int],
    total_tokens: int,
) -> str:
    """Generate reminders for oversized sections. From src/prompts.ts L164-196."""
    over_budget = total_tokens > MAX_TOTAL_SESSION_MEMORY_TOKENS
    oversized = [
        (section, tokens)
        for section, tokens in sorted(section_sizes.items(), key=lambda x: -x[1])
        if tokens > MAX_SECTION_LENGTH
    ]

    if not oversized and not over_budget:
        return ''

    parts = []

    if over_budget:
        parts.append(
            f"\n\nCRITICAL: The session memory file is currently ~{total_tokens} tokens, "
            f"which exceeds the maximum of {MAX_TOTAL_SESSION_MEMORY_TOKENS} tokens. "
            "You MUST condense the file to fit within this budget. Aggressively shorten "
            "oversized sections by removing less important details, merging related items, "
            'and summarizing older entries. Prioritize keeping "Current State" and '
            '"Errors & Corrections" accurate and detailed.'
        )

    if oversized:
        lines = [f'- "{s}" is ~{t} tokens (limit: {MAX_SECTION_LENGTH})' for s, t in oversized]
        prefix = "Oversized sections to condense" if over_budget else (
            "IMPORTANT: The following sections exceed the per-section limit and MUST be condensed"
        )
        parts.append(f"\n\n{prefix}:\n" + "\n".join(lines))

    return ''.join(parts)


def _build_session_memory_update_prompt(
    current_notes: str,
    notes_path: str,
) -> str:
    """Build the full update prompt with section analysis. From src/prompts.ts L226-247."""
    prompt_template = _get_default_update_prompt()

    # Analyze and add reminders
    section_sizes = _analyze_section_sizes(current_notes)
    total_tokens = _rough_token_estimate(current_notes)
    reminders = _generate_section_reminders(section_sizes, total_tokens)

    # Substitute variables
    prompt = prompt_template.replace('{{currentNotes}}', current_notes)
    prompt = prompt.replace('{{notesPath}}', notes_path)

    return prompt + reminders


# ── Truncation for compact injection (from src/prompts.ts L256-324) ──

def truncate_session_memory_for_compact(content: str) -> tuple[str, bool]:
    """Truncate oversized sections for compact injection.

    From src/prompts.ts truncateSessionMemoryForCompact().
    Returns (truncated_content, was_truncated).
    """
    lines = content.split('\n')
    max_chars_per_section = MAX_SECTION_LENGTH * 4  # rough estimate uses len/4
    output_lines: list[str] = []
    current_section_lines: list[str] = []
    current_section_header = ''
    was_truncated = False

    for line in lines:
        if line.startswith('# '):
            result_lines, section_truncated = _flush_section(
                current_section_header, current_section_lines, max_chars_per_section
            )
            output_lines.extend(result_lines)
            was_truncated = was_truncated or section_truncated
            current_section_header = line
            current_section_lines = []
        else:
            current_section_lines.append(line)

    # Flush last section
    result_lines, section_truncated = _flush_section(
        current_section_header, current_section_lines, max_chars_per_section
    )
    output_lines.extend(result_lines)
    was_truncated = was_truncated or section_truncated

    return '\n'.join(output_lines), was_truncated


def _flush_section(
    header: str,
    section_lines: list[str],
    max_chars: int,
) -> tuple[list[str], bool]:
    """Flush a section, truncating if over budget. From src/prompts.ts L298-324."""
    if not header:
        return section_lines, False

    content = '\n'.join(section_lines)
    if len(content) <= max_chars:
        return [header] + section_lines, False

    # Truncate at line boundary
    char_count = 0
    kept = [header]
    for line in section_lines:
        if char_count + len(line) + 1 > max_chars:
            break
        kept.append(line)
        char_count += len(line) + 1

    kept.append('\n[... section truncated for length ...]')
    return kept, True


# ── State ──

@dataclass
class SessionMemoryState:
    """Tracks when to trigger extraction. From src/sessionMemoryUtils.ts."""
    initialized: bool = False
    last_extraction_tokens: int = 0
    last_extraction_time: float = 0.0
    tool_calls_since_extraction: int = 0
    extraction_count: int = 0
    memory_content: str = ""
    memory_file_path: str = ""


# ── Manager ──

class SessionMemoryManager:
    """Manages session memory extraction and persistence.

    From src/sessionMemory.ts:
    - Runs as a BACKGROUND task (doesn't block main loop)
    - Uses structured template (10 sections) for consistent extraction
    - Writes to SESSION_MEMORY.md
    - Re-injects on compaction
    - Dual threshold: tokens AND tool calls (from src/ shouldExtractMemory())
    """

    def __init__(
        self,
        working_dir: str = ".",
        memory_filename: str = "SESSION_MEMORY.md",
    ):
        self.state = SessionMemoryState()
        self.state.memory_file_path = os.path.join(working_dir, memory_filename)
        self._extraction_lock = asyncio.Lock()

        # Load existing memory if present
        self._load_existing()

    def _load_existing(self):
        """Load existing session memory from disk."""
        try:
            if os.path.exists(self.state.memory_file_path):
                with open(self.state.memory_file_path, "r", encoding="utf-8") as f:
                    self.state.memory_content = f.read()
                    self.state.initialized = True
                    logger.info(
                        "Loaded existing session memory (%d chars)",
                        len(self.state.memory_content),
                    )
        except Exception as e:
            logger.debug("No existing session memory: %s", e)

    def should_extract(
        self,
        messages: list['AgenticMessage'],
        total_tool_calls: int,
    ) -> bool:
        """Check if we should trigger extraction.

        From src/sessionMemory.ts shouldExtractMemory():
        - BOTH thresholds required: tokens AND tool calls
        - OR: token threshold met AND no tool calls in last turn (natural break)
        """
        total_tokens = sum(m.token_estimate for m in messages)

        if not self.state.initialized:
            return total_tokens >= INIT_TOKEN_THRESHOLD

        token_growth = total_tokens - self.state.last_extraction_tokens
        has_met_token_threshold = token_growth >= UPDATE_TOKEN_GROWTH

        tool_growth = total_tool_calls - self.state.tool_calls_since_extraction
        has_met_tool_threshold = tool_growth >= UPDATE_TOOL_CALLS

        # Check if last turn has no tool calls (natural conversation break)
        last_turn_has_tools = False
        for msg in reversed(messages):
            if msg.role == "assistant":
                last_turn_has_tools = "tool_use" in msg.content or "```" in msg.content
                break

        return (
            (has_met_token_threshold and has_met_tool_threshold)
            or (has_met_token_threshold and not last_turn_has_tools)
        )

    async def extract(
        self,
        messages: list['AgenticMessage'],
        client,  # NIMClient
        total_tool_calls: int = 0,
    ):
        """Extract session memory using the src/ template and update prompt.

        From src/sessionMemory.ts extractSessionMemory():
        - Reads current notes (or initializes from template)
        - Builds update prompt with section analysis
        - Calls LLM to update notes
        - Writes result to disk
        """
        async with self._extraction_lock:
            # Initialize from template if first extraction
            if not self.state.memory_content:
                self.state.memory_content = DEFAULT_SESSION_MEMORY_TEMPLATE.strip()

            # Build the update prompt (from src/prompts.ts buildSessionMemoryUpdatePrompt)
            update_prompt = _build_session_memory_update_prompt(
                self.state.memory_content,
                self.state.memory_file_path,
            )

            # Build conversation for the extractor
            conversation_parts = []
            for msg in messages:
                if msg.role == "system":
                    continue
                label = msg.role.title()
                content = msg.content[:3000]  # Truncate for extractor
                conversation_parts.append(f"[{label}] {content}")

            # Use last 20 messages for context
            conversation = "\n\n".join(conversation_parts[-20:])

            try:
                updated_notes = await client.chat(
                    messages=[
                        {"role": "system", "content": "You are a session notes manager."},
                        {"role": "user", "content": f"{conversation}\n\n---\n\n{update_prompt}"},
                    ],
                    max_tokens=3000,
                    temperature=0.2,
                    timeout=45,
                )

                if updated_notes and len(updated_notes.strip()) > 50:
                    self.state.memory_content = updated_notes.strip()
                    self.state.initialized = True
                    self.state.last_extraction_tokens = sum(
                        m.token_estimate for m in messages
                    )
                    self.state.tool_calls_since_extraction = total_tool_calls
                    self.state.last_extraction_time = time.time()
                    self.state.extraction_count += 1

                    # Write to disk
                    await self._write_to_disk()

                    logger.info(
                        "Session memory extracted (#%d, %d chars)",
                        self.state.extraction_count,
                        len(self.state.memory_content),
                    )
                else:
                    logger.warning("Session memory extraction returned empty result")

            except Exception as e:
                logger.warning("Session memory extraction failed: %s", e)

    async def _write_to_disk(self):
        """Write session memory to file (survives compaction)."""
        try:
            os.makedirs(os.path.dirname(self.state.memory_file_path) or '.', exist_ok=True)
            with open(self.state.memory_file_path, "w", encoding="utf-8") as f:
                f.write(self.state.memory_content)

            logger.debug("Session memory written to %s", self.state.memory_file_path)
        except Exception as e:
            logger.warning("Failed to write session memory: %s", e)

    def get_memory_for_context(self) -> str:
        """Get current memory content for injection into context assembly."""
        return self.state.memory_content

    def get_memory_for_compaction(self) -> str:
        """Get memory content formatted for post-compaction injection.

        From src/: session memory is re-injected as an attachment
        after compaction, so it survives the conversation summary.
        Uses truncation to prevent oversized sections (from src/prompts.ts).
        """
        if not self.state.memory_content:
            return ""

        # Truncate if needed (from src/ truncateSessionMemoryForCompact)
        content, was_truncated = truncate_session_memory_for_compact(
            self.state.memory_content
        )

        if was_truncated:
            logger.info("Session memory truncated for compact injection")

        return (
            "[Session Memory - persisted across compaction]\n\n"
            f"{content}"
        )
