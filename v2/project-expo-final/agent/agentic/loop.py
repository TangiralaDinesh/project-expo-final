"""
Agentic Loop — The core while(true) tool-calling loop.

Ported from src/query.ts queryLoop() (L241-900).

The fundamental pattern:
  while True:
      response = LLM(messages + tools)
      if response has tool_use → execute tools → push results → continue
      if no tool_use → done (LLM decided to stop)

This wraps v2's entire pipeline as the `deep_research` tool.
The LLM can call it alongside web_search, think, etc.
It self-corrects — if results are bad, it tries different queries.

Adapted for:
  - Python async/await (instead of TypeScript generators)
  - NIM API (no native tool_use — we use JSON function calling in prompt)
  - v2's existing LLM client
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, AsyncGenerator

from .tool_contract import ToolRegistry, ToolCall, ToolResult, build_default_registry
from .context_assembly import (
    build_system_prompt, build_tool_prompt,
    context_collapse, apply_per_message_budget,
)
from .error_recovery import detect_recovery_needed, apply_recovery, RecoveryType
from .session_memory import SessionMemoryManager

logger = logging.getLogger(__name__)

# ── Constants ──
MAX_TURNS = 15           # From src/: maxTurns default
MAX_TOOL_CALLS_PER_TURN = 6
CONTEXT_TOKEN_ESTIMATE_PER_CHAR = 0.25  # ~4 chars per token


@dataclass
class AgenticMessage:
    """One message in the agentic conversation."""
    role: str  # "system" | "user" | "assistant" | "tool_result"
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    token_estimate: int = 0

    def __post_init__(self):
        if not self.token_estimate:
            self.token_estimate = int(len(self.content) * CONTEXT_TOKEN_ESTIMATE_PER_CHAR)


@dataclass
class AgenticState:
    """Mutable state carried across loop iterations (from src/ State type)."""
    messages: list[AgenticMessage] = field(default_factory=list)
    turn_count: int = 0
    total_tool_calls: int = 0
    total_tokens_used: int = 0
    has_compacted: bool = False
    max_output_recovery_count: int = 0
    start_time: float = field(default_factory=time.time)
    # Query chain tracking (from src/query.ts L347-355)
    chain_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    chain_depth: int = 0  # Increments on each tool call that triggers sub-queries


@dataclass
class AgenticResult:
    """Final result of the agentic loop."""
    answer: str
    messages: list[AgenticMessage]
    turn_count: int
    total_tool_calls: int
    timing_ms: float
    tools_used: list[str] = field(default_factory=list)


class AgenticLoop:
    """The core agentic loop — LLM decides what tools to call and when to stop.

    From src/query.ts queryLoop():
    - Pre-processing (compaction)
    - Call LLM with messages + tools
    - Parse response for tool_use blocks
    - Execute tools (parallel read-only, serial write)
    - Push results into messages
    - Continue or stop

    This WRAPS v2's pipeline — it does NOT replace it.
    v2's run_query becomes the `deep_research` tool callable by the LLM.
    """

    def __init__(
        self,
        client,  # NIMClient
        registry: Optional[ToolRegistry] = None,
        max_turns: int = MAX_TURNS,
        context_budget: int = 100_000,  # tokens before compaction
    ):
        self.client = client
        self.registry = registry or build_default_registry()
        self.max_turns = max_turns
        self.context_budget = context_budget

    async def run(
        self,
        query: str,
        *,
        system_context: Optional[dict] = None,
    ) -> AgenticResult:
        """Run the agentic loop until the LLM decides to stop.

        The loop:
          1. Build context (system prompt + tool definitions + messages)
          2. Call LLM
          3. Parse for tool calls
          4. If tool calls → execute → push results → continue
          5. If no tool calls → extract answer → return
        """
        state = AgenticState()
        recovery_count = 0

        # Initialize session memory (survives compaction)
        session_memory = SessionMemoryManager()

        # Inject session memory into context if available
        memory_content = session_memory.get_memory_for_context()
        if memory_content:
            sys_context = dict(system_context or {})
            sys_context["session_memory"] = memory_content
        else:
            sys_context = system_context or {}

        system_prompt = build_system_prompt(sys_context)
        tool_prompt = build_tool_prompt(self.registry.get_all_schemas())
        full_system = f"{system_prompt}\n\n{tool_prompt}"

        state.messages.append(AgenticMessage(role="system", content=full_system))
        state.messages.append(AgenticMessage(role="user", content=query))

        tools_used: list[str] = []

        while state.turn_count < self.max_turns:
            state.turn_count += 1
            elapsed = time.time() - state.start_time

            # Time budget: 120s total for agentic loop
            if elapsed > 120.0:
                logger.info("Agentic loop: time budget exhausted (%.0fs), stopping", elapsed)
                break

            logger.info(
                "Agentic turn %d/%d [chain=%s] (%.0fs elapsed, %d tool calls so far)",
                state.turn_count, self.max_turns, state.chain_id, elapsed, state.total_tool_calls,
            )

            # ── Step 1a: Context Collapse (lightweight, pre-compact) ──
            # From src/utils/collapseReadSearch.ts: collapse old large tool results
            msg_dicts = [
                {"role": m.role, "content": m.content,
                 "tool_name": m.tool_results[0].tool_name if m.tool_results else ""}
                for m in state.messages
            ]
            collapsed, collapse_count, saved = context_collapse(
                msg_dicts, budget_chars=self.context_budget * 4,  # chars ≈ tokens * 4
            )
            if collapse_count > 0:
                for i, msg_dict in enumerate(collapsed):
                    if i < len(state.messages):
                        state.messages[i].content = msg_dict["content"]
                        state.messages[i].token_estimate = int(
                            len(msg_dict["content"]) * CONTEXT_TOKEN_ESTIMATE_PER_CHAR
                        )
                logger.info(
                    "Context collapse: %d results collapsed, %d chars saved",
                    collapse_count, saved,
                )

            # ── Step 1b: Full compaction check ──
            total_tokens = sum(m.token_estimate for m in state.messages)
            if total_tokens > self.context_budget:
                await self._compact(state)

            # ── Step 2: Call LLM ──
            llm_response = await self._call_llm(state)

            if not llm_response:
                logger.warning("Agentic loop: empty LLM response, stopping")
                break

            # ── Step 3: Parse tool calls ──
            parsed_calls = self._parse_tool_calls(llm_response)

            # ── Step 4: If no tool calls → LLM decided to stop ──
            if not parsed_calls:
                # ── Error Recovery: Check for hallucination ──
                recovery = detect_recovery_needed(
                    llm_response,
                    error=None,
                    had_tool_calls=False,
                    recovery_count=recovery_count,
                )
                if recovery.should_retry and recovery.type == RecoveryType.HALLUCINATION:
                    recovery_count += 1
                    state.messages.append(AgenticMessage(role="assistant", content=llm_response))
                    state.messages = apply_recovery(recovery, state.messages)
                    logger.info("Hallucination recovery injected, retrying")
                    continue

                # Extract the answer (everything that's NOT a tool call)
                answer = self._extract_answer(llm_response)
                state.messages.append(AgenticMessage(role="assistant", content=answer))

                logger.info(
                    "Agentic loop: LLM stopped (no tool calls), %d turns, %d total tool calls",
                    state.turn_count, state.total_tool_calls,
                )

                return AgenticResult(
                    answer=answer,
                    messages=state.messages,
                    turn_count=state.turn_count,
                    total_tool_calls=state.total_tool_calls,
                    timing_ms=(time.time() - state.start_time) * 1000,
                    tools_used=tools_used,
                )

            # ── Step 5: Execute tools ──
            # Record the assistant message with tool calls
            state.messages.append(AgenticMessage(
                role="assistant",
                content=llm_response,
                tool_calls=parsed_calls,
            ))

            # Execute with partitioned batching (parallel read-only, serial write)
            results = await self.registry.execute_batch(parsed_calls)

            state.total_tool_calls += len(results)
            for r in results:
                if r.tool_name not in tools_used:
                    tools_used.append(r.tool_name)

            # ── Step 6: Push results into messages ──
            # Apply aggregate tool result budget (from src/query.ts L370-394)
            AGGREGATE_RESULT_BUDGET = 150_000  # Total chars across all results
            total_result_chars = sum(len(r.output) for r in results)

            result_parts = []
            for r in results:
                output = r.output
                # If aggregate exceeds budget, proportionally truncate each result
                if total_result_chars > AGGREGATE_RESULT_BUDGET and total_result_chars > 0:
                    proportion = len(output) / total_result_chars
                    per_tool_budget = int(AGGREGATE_RESULT_BUDGET * proportion)
                    if len(output) > per_tool_budget:
                        output = output[:per_tool_budget] + f"\n[... truncated by aggregate budget]"

                if r.success:
                    result_parts.append(
                        f"[Tool Result: {r.tool_name}] ({r.duration_ms:.0f}ms)\n{output}"
                    )
                else:
                    result_parts.append(
                        f"[Tool Error: {r.tool_name}] ({r.duration_ms:.0f}ms)\n{r.error}"
                    )

            state.messages.append(AgenticMessage(
                role="tool_result",
                content="\n\n---\n\n".join(result_parts),
                tool_results=results,
            ))

            logger.info(
                "Turn %d: %d tools executed (%s), continuing loop",
                state.turn_count,
                len(results),
                ", ".join(r.tool_name for r in results),
            )

            # ── Step 6b: Tool Use Summary (from src/query.ts L1455) ──
            # Generate a terse summary of what each tool found, so the LLM
            # has a compact reference instead of re-reading massive raw output
            if len(results) >= 2 and total_result_chars > 5000:
                summary_lines = []
                for r in results:
                    if r.success:
                        # Terse 1-line summary of output (first meaningful line)
                        first_lines = r.output.strip().split('\n')[:3]
                        preview = ' '.join(first_lines)[:200]
                        summary_lines.append(f"  • {r.tool_name}: {preview}")
                    else:
                        summary_lines.append(f"  • {r.tool_name}: ERROR — {r.error[:100]}")

                tool_summary = (
                    "[Tool Use Summary]\n"
                    + "\n".join(summary_lines)
                    + "\n[End Summary — refer to full results above for details]"
                )
                state.messages.append(AgenticMessage(
                    role="tool_result",
                    content=tool_summary,
                    # Not tagged with tool_results — this is synthetic metadata
                ))

            # ── Session Memory: Background extraction ──
            if session_memory.should_extract(state.messages, state.total_tool_calls):
                asyncio.create_task(
                    session_memory.extract(
                        state.messages, self.client, state.total_tool_calls,
                    )
                )

            # ── Error Recovery: Hallucination interception ──
            # From src/ query.ts: if LLM said "let me search" but produced no valid tool call
            if not any(r.success for r in results):
                logger.warning("All tools failed in turn %d — injecting recovery", state.turn_count)
                state.messages.append(AgenticMessage(
                    role="user",
                    content=(
                        "All tool calls failed. Please try a different approach. "
                        "You can try different search terms, simplify your query, "
                        "or answer with what you know so far."
                    ),
                ))

        # If we hit max turns without stopping, synthesize from what we have
        logger.warning("Agentic loop: hit max turns (%d), force-synthesizing", self.max_turns)
        return AgenticResult(
            answer=self._extract_final_answer(state),
            messages=state.messages,
            turn_count=state.turn_count,
            total_tool_calls=state.total_tool_calls,
            timing_ms=(time.time() - state.start_time) * 1000,
            tools_used=tools_used,
        )

    async def _call_llm(self, state: AgenticState) -> str:
        """Call the LLM with current messages.

        Uses NIM's chat completion API with JSON function calling
        (since NIM doesn't support native tool_use blocks).
        """
        # Build messages for the API call
        api_messages = []
        for msg in state.messages:
            if msg.role == "system":
                api_messages.append({"role": "system", "content": msg.content})
            elif msg.role == "user":
                api_messages.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                api_messages.append({"role": "assistant", "content": msg.content})
            elif msg.role == "tool_result":
                api_messages.append({"role": "user", "content": msg.content})

        try:
            response = await self.client.chat(
                messages=api_messages,
                max_tokens=4096,
                temperature=0.3,
                timeout=60,
            )
            return response.strip()
        except Exception as e:
            logger.error("LLM call failed in agentic loop: %s", e)

            # ── Error Recovery: Model fallback ──
            # From src/ query.ts: switch to fallback model on failure
            state.max_output_recovery_count += 1
            if state.max_output_recovery_count <= 3:
                logger.info("Retrying LLM call (attempt %d/3)", state.max_output_recovery_count)
                try:
                    response = await self.client.chat(
                        messages=api_messages,
                        max_tokens=4096,
                        temperature=0.5,  # Slightly higher temp on retry
                        timeout=90,
                    )
                    return response.strip()
                except Exception as retry_e:
                    logger.error("Retry also failed: %s", retry_e)

            return ""

    def _parse_tool_calls(self, response: str) -> list[ToolCall]:
        """Parse tool calls from LLM response.

        Since NIM doesn't have native tool_use blocks, we parse JSON:

        Expected format from the LLM:
        <tool_call>
        {"tool": "web_search", "args": {"query": "Tom Holland filmography"}}
        </tool_call>

        OR inline JSON:
        {"tool": "deep_research", "args": {"query": "..."}}
        """
        calls = []

        # Pattern 1: <tool_call>...</tool_call> blocks
        tool_call_pattern = re.compile(
            r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
            re.DOTALL,
        )
        for match in tool_call_pattern.finditer(response):
            try:
                data = json.loads(match.group(1))
                if "tool" in data and "args" in data:
                    calls.append(ToolCall(
                        tool_name=data["tool"],
                        args=data.get("args", {}),
                        call_id=str(uuid.uuid4())[:8],
                    ))
            except (json.JSONDecodeError, KeyError):
                continue

        # Pattern 2: ```json blocks with tool format
        if not calls:
            json_block_pattern = re.compile(
                r'```(?:json)?\s*(\{[^`]*?"tool"\s*:\s*"[^"]+?"[^`]*?\})\s*```',
                re.DOTALL,
            )
            for match in json_block_pattern.finditer(response):
                try:
                    data = json.loads(match.group(1))
                    if "tool" in data:
                        calls.append(ToolCall(
                            tool_name=data["tool"],
                            args=data.get("args", {}),
                            call_id=str(uuid.uuid4())[:8],
                        ))
                except (json.JSONDecodeError, KeyError):
                    continue

        # Validate: only known tools
        valid_calls = []
        for c in calls[:MAX_TOOL_CALLS_PER_TURN]:
            if self.registry.get_tool(c.tool_name):
                valid_calls.append(c)
            else:
                logger.warning("Unknown tool in LLM output: %s", c.tool_name)

        return valid_calls

    def _extract_answer(self, response: str) -> str:
        """Extract the answer text, removing tool_call blocks."""
        # Remove tool_call blocks
        cleaned = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
        # Remove JSON tool blocks
        cleaned = re.sub(r'```json\s*\{[^`]*?"tool"\s*:.*?\}\s*```', '', cleaned, flags=re.DOTALL)
        return cleaned.strip()

    def _extract_final_answer(self, state: AgenticState) -> str:
        """Extract answer from the last assistant message when we hit max turns."""
        for msg in reversed(state.messages):
            if msg.role == "assistant" and msg.content.strip():
                return self._extract_answer(msg.content)

        # Fallback: concatenate tool results
        tool_outputs = []
        for msg in state.messages:
            if msg.tool_results:
                for r in msg.tool_results:
                    if r.success and r.output:
                        tool_outputs.append(r.output)

        if tool_outputs:
            return "Based on research:\n\n" + "\n\n---\n\n".join(tool_outputs[-3:])

        return "I wasn't able to find a complete answer. Please try rephrasing your question."

    async def _compact(self, state: AgenticState):
        """Context compaction — 3-layer system from src/compact/.

        When context exceeds budget:
        1. Micro-compact: replace old tool results with stubs (tool-aware)
        2. Snip compact: drop oldest messages (cheaper than LLM call)
        3. Auto-compact: summarize entire conversation into 9-section format
        """
        from .micro_compact import micro_compact_messages
        from .auto_compact import auto_compact_messages

        logger.info(
            "Context compaction triggered (%d estimated tokens, budget=%d)",
            sum(m.token_estimate for m in state.messages),
            self.context_budget,
        )

        # Layer 1: Micro-compact (clear old tool results — tool-aware)
        state.messages = micro_compact_messages(state.messages, registry=self.registry)

        # Check if micro-compact was enough
        total_tokens = sum(m.token_estimate for m in state.messages)
        if total_tokens <= self.context_budget:
            logger.info("Micro-compact sufficient (down to %d tokens)", total_tokens)
            return

        # Layer 1.5: Snip compact (from src/snipCompact.ts)
        # Drop oldest non-system messages until under 80% of budget
        # This is cheaper than an LLM call
        snip_target = int(self.context_budget * 0.8)
        if total_tokens > snip_target:
            system_msgs = [m for m in state.messages if m.role == "system"]
            other_msgs = [m for m in state.messages if m.role != "system"]

            # Keep at least the last 6 messages
            if len(other_msgs) > 6:
                # Calculate how many to drop
                running_tokens = sum(m.token_estimate for m in system_msgs)
                keep_from = len(other_msgs)

                # Walk backward to find how many messages fit in budget
                for i in range(len(other_msgs) - 1, -1, -1):
                    running_tokens += other_msgs[i].token_estimate
                    if running_tokens > snip_target:
                        keep_from = i + 1
                        break

                # Keep at least last 6
                keep_from = min(keep_from, len(other_msgs) - 6)

                if keep_from > 0:
                    dropped = len(other_msgs) - (len(other_msgs) - keep_from)
                    dropped_tokens = sum(m.token_estimate for m in other_msgs[:keep_from])
                    state.messages = system_msgs + other_msgs[keep_from:]
                    logger.info(
                        "Snip-compact: dropped %d oldest messages (~%d tokens)",
                        dropped, dropped_tokens,
                    )

        # Re-check after snip
        total_tokens = sum(m.token_estimate for m in state.messages)
        if total_tokens <= self.context_budget:
            logger.info("Snip-compact sufficient (down to %d tokens)", total_tokens)
            return

        # Layer 2: Auto-compact (summarize conversation via LLM)
        try:
            state.messages = await auto_compact_messages(
                state.messages,
                self.client,
            )
            state.has_compacted = True
            logger.info(
                "Auto-compact done (now %d tokens)",
                sum(m.token_estimate for m in state.messages),
            )
        except Exception as e:
            logger.warning("Auto-compact failed: %s, continuing with snipped context", e)


# ── Public API ──

async def run_agentic(
    query: str,
    client=None,
    *,
    system_context: Optional[dict] = None,
    max_turns: int = MAX_TURNS,
) -> AgenticResult:
    """Run the agentic loop — the primary entry point.

    This is the hybrid pipeline:
    - The agentic loop handles tool calling, self-correction, compaction
    - v2's run_query is available as the `deep_research` tool
    - The LLM decides when to use which tool
    """
    if client is None:
        from ..llm.client import get_client
        client = get_client()

    loop = AgenticLoop(
        client=client,
        max_turns=max_turns,
    )

    return await loop.run(query, system_context=system_context)
