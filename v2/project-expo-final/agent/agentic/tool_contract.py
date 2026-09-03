"""
Tool Contract — Defines all tools available to the agentic loop.

Ported from src/Tool.ts + src/services/tools/toolOrchestration.ts
Now connected to Node.js bridge for industrial-grade execution.

Hybrid mode:
  - If Node.js bridge is available → use src_reference/ TS tools (40K lines)
  - If not → fall back to Python implementations
  - Decision per-call, automatic, transparent

Each tool has:
  - name: unique identifier
  - description: what it does (shown to LLM)
  - parameters: dict schema
  - execute: async function
  - is_read_only: bool (for parallel/serial partitioning)

The v2 pipeline becomes the `deep_research` tool — the LLM can call it
for complex queries that need KG investigation, speculative exploration,
and progressive depth. For simple lookups, `web_search` is faster.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional

from .node_bridge import get_bridge, BridgeError

logger = logging.getLogger(__name__)



@dataclass
class ToolDefinition:
    """One tool available to the agentic loop."""
    name: str
    description: str
    parameters: dict[str, dict]  # {param_name: {"type": "string", "description": "..."}}
    required: list[str] = field(default_factory=list)
    is_read_only: bool = True  # Read-only tools run in parallel
    max_result_chars: int = 100_000  # From src/toolResultStorage.ts
    is_compactable: bool = True  # From src/microCompact.ts COMPACTABLE_TOOLS
    _execute_fn: Optional[Callable] = field(default=None, repr=False)

    def to_prompt_schema(self) -> dict:
        """Convert to schema for LLM prompt (JSON function calling)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": self.parameters,
                "required": self.required,
            },
        }


@dataclass
class ToolCall:
    """One parsed tool call from LLM output."""
    tool_name: str
    args: dict[str, Any]
    call_id: str = ""  # For matching results back


@dataclass
class ToolResult:
    """Result of executing one tool call."""
    tool_name: str
    call_id: str
    success: bool
    output: str
    error: str = ""
    duration_ms: float = 0.0
    token_estimate: int = 0  # For compaction tracking


class ToolRegistry:
    """Registry of all tools available to the agentic loop.

    Supports partitioned execution (from src/toolOrchestration.ts):
    - Consecutive read-only tools → run in PARALLEL
    - Single write tool → run SERIALLY
    """

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable[..., Awaitable[str]]] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, dict],
        handler: Callable[..., Awaitable[str]],
        *,
        required: Optional[list[str]] = None,
        is_read_only: bool = True,
        max_result_chars: int = 100_000,
        is_compactable: bool = True,
    ):
        """Register a tool with its handler."""
        tool_def = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            required=required or list(parameters.keys()),
            is_read_only=is_read_only,
            max_result_chars=max_result_chars,
            is_compactable=is_compactable,
        )
        self._tools[name] = tool_def
        self._handlers[name] = handler

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def get_all_schemas(self) -> list[dict]:
        """Get all tool schemas for the LLM prompt."""
        return [t.to_prompt_schema() for t in self._tools.values()]

    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute a single tool call with per-tool result budgeting."""
        handler = self._handlers.get(call.tool_name)
        tool_def = self._tools.get(call.tool_name)
        if not handler:
            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                success=False,
                output="",
                error=f"Unknown tool: {call.tool_name}",
            )

        # Per-tool result budget (from src/toolResultStorage.ts)
        max_chars = tool_def.max_result_chars if tool_def else 100_000

        t0 = time.time()
        try:
            output = await handler(**call.args)
            duration = (time.time() - t0) * 1000
            
            # Apply per-tool result budget
            output_str = str(output)
            if len(output_str) > max_chars:
                output_str = output_str[:max_chars] + f"\n[... truncated at {max_chars} chars]"
            
            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                success=True,
                output=output_str,
                duration_ms=duration,
                token_estimate=len(output_str) // 4,
            )
        except Exception as e:
            duration = (time.time() - t0) * 1000
            logger.warning("Tool %s failed: %s (%.0fms)", call.tool_name, e, duration)
            return ToolResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                success=False,
                output="",
                error=str(e)[:500],
                duration_ms=duration,
            )

    async def execute_batch(self, calls: list[ToolCall]) -> list[ToolResult]:
        """Execute tool calls with smart partitioning.

        From src/toolOrchestration.ts partitionToolCalls():
        - Consecutive read-only tools → asyncio.gather (parallel)
        - Single write tool → sequential
        """
        if not calls:
            return []

        # Partition into batches
        batches = self._partition(calls)
        results: list[ToolResult] = []

        for is_parallel, batch in batches:
            if is_parallel and len(batch) > 1:
                # Run read-only batch in parallel WITH sibling abort
                # From src/StreamingToolExecutor.ts: if one tool errors,
                # cancel siblings immediately to prevent wasted work
                sibling_abort = asyncio.Event()

                async def _execute_with_abort(call: ToolCall) -> ToolResult:
                    try:
                        if sibling_abort.is_set():
                            return ToolResult(
                                tool_name=call.tool_name,
                                call_id=call.call_id,
                                success=False,
                                output="",
                                error="Cancelled: sibling tool errored",
                            )
                        result = await self.execute(call)
                        if not result.success:
                            sibling_abort.set()  # Signal siblings to stop
                        return result
                    except Exception as e:
                        sibling_abort.set()
                        return ToolResult(
                            tool_name=call.tool_name,
                            call_id=call.call_id,
                            success=False,
                            output="",
                            error=str(e)[:500],
                        )

                batch_results = await asyncio.gather(
                    *[_execute_with_abort(c) for c in batch],
                    return_exceptions=True,
                )
                for i, r in enumerate(batch_results):
                    if isinstance(r, Exception):
                        results.append(ToolResult(
                            tool_name=batch[i].tool_name,
                            call_id=batch[i].call_id,
                            success=False,
                            output="",
                            error=str(r)[:500],
                        ))
                    else:
                        results.append(r)
            else:
                # Run serially
                for call in batch:
                    results.append(await self.execute(call))

        return results

    def _partition(self, calls: list[ToolCall]) -> list[tuple[bool, list[ToolCall]]]:
        """Partition calls into (is_parallel, [calls]) batches."""
        batches: list[tuple[bool, list[ToolCall]]] = []

        for call in calls:
            tool = self._tools.get(call.tool_name)
            is_readonly = tool.is_read_only if tool else False

            if batches and batches[-1][0] and is_readonly:
                # Extend current parallel batch
                batches[-1][1].append(call)
            else:
                batches.append((is_readonly, [call]))

        return batches


# ── Build default tool registry ──

def build_default_registry() -> ToolRegistry:
    """Create the default tool registry with all v2 tools.

    From src/ — every tool has:
    - name, description, parameters
    - is_read_only (parallel vs serial)
    - max_result_chars (from src/toolResultStorage.ts)
    - is_compactable (from src/microCompact.ts COMPACTABLE_TOOLS)

    The key insight: v2's entire pipeline becomes the `deep_research` tool.
    """
    registry = ToolRegistry()

    # Tool 1: Deep Research (v2's entire pipeline)
    registry.register(
        name="deep_research",
        description=(
            "Run comprehensive multi-source research with Knowledge Graph investigation, "
            "speculative exploration, fan-out queries, progressive depth, and entropy-based "
            "redundancy filtering. Use this for complex queries needing thorough investigation "
            "from multiple sources. Returns a detailed answer with citations."
        ),
        parameters={
            "query": {"type": "string", "description": "The research question to investigate thoroughly"},
        },
        handler=_handle_deep_research,
        is_read_only=True,
        max_result_chars=100_000,
    )

    # Tool 2: Quick Web Search
    registry.register(
        name="web_search",
        description=(
            "Quick web search for simple factual queries. Faster than deep_research but "
            "less thorough. Use for straightforward lookups, current events, or when you "
            "need a quick fact check."
        ),
        parameters={
            "query": {"type": "string", "description": "The search query"},
        },
        handler=_handle_web_search,
        is_read_only=True,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 3: Think (scratchpad for reasoning — NOT compactable)
    registry.register(
        name="think",
        description=(
            "Use this tool to think through a problem step-by-step before responding. "
            "The output is NOT shown to the user — it's your private scratchpad. "
            "Use it to plan, analyze, or reason through complex problems."
        ),
        parameters={
            "reasoning": {"type": "string", "description": "Your step-by-step reasoning"},
        },
        handler=_handle_think,
        is_read_only=True,
        max_result_chars=100_000,
        is_compactable=False,  # Never compact think results
    )

    # Tool 4: File Read — read file contents with optional line range
    # From src/tools/FileReadTool: maxResultSizeChars = Infinity
    registry.register(
        name="file_read",
        description=(
            "Read the contents of a file. Can read specific line ranges. "
            "Use this to inspect files, check code, or read configuration."
        ),
        parameters={
            "path": {"type": "string", "description": "Path to the file to read"},
            "start_line": {"type": "integer", "description": "Start line (1-indexed, optional)"},
            "end_line": {"type": "integer", "description": "End line (1-indexed, optional)"},
        },
        required=["path"],
        handler=_handle_file_read,
        is_read_only=True,
        max_result_chars=200_000,  # FileRead: never truncate (but we set practical limit)
        is_compactable=True,
    )

    # Tool 5: File Write — create/overwrite files
    # From src/tools/FileWriteTool: maxResultSizeChars = 100K
    registry.register(
        name="file_write",
        description=(
            "Write content to a file. Creates parent directories if needed. "
            "Overwrites the file if it already exists."
        ),
        parameters={
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Content to write"},
        },
        handler=_handle_file_write,
        is_read_only=False,  # WRITE — runs serially
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 6: File Edit — surgical find & replace
    # From src/tools/FileEditTool: maxResultSizeChars = 100K
    registry.register(
        name="file_edit",
        description=(
            "Edit a file by replacing old content with new content. "
            "Use this for surgical, targeted edits rather than full file rewrites."
        ),
        parameters={
            "path": {"type": "string", "description": "Path to the file to edit"},
            "old_text": {"type": "string", "description": "Exact text to find and replace"},
            "new_text": {"type": "string", "description": "Replacement text"},
        },
        handler=_handle_file_edit,
        is_read_only=False,  # WRITE — runs serially
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 7: Bash — execute shell commands
    # From src/tools/BashTool: isConcurrencySafe depends on input (read-only detection)
    registry.register(
        name="bash",
        description=(
            "Execute a shell command and return the output. "
            "Use for running scripts, installing packages, checking system state, "
            "or any task requiring command-line access."
        ),
        parameters={
            "command": {"type": "string", "description": "The shell command to execute"},
        },
        handler=_handle_bash,
        is_read_only=False,  # Conservative: treat as write (serial)
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 8: Glob — find files by pattern
    # From src/tools/GlobTool: maxResultSizeChars = 100K
    registry.register(
        name="glob",
        description=(
            "Find files matching a glob pattern (e.g., '**/*.py', 'src/**/*.ts'). "
            "Returns a list of matching file paths."
        ),
        parameters={
            "pattern": {"type": "string", "description": "Glob pattern to match files"},
            "path": {"type": "string", "description": "Base directory to search from (optional)"},
        },
        required=["pattern"],
        handler=_handle_glob,
        is_read_only=True,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 9: Grep — search in files by regex
    # From src/tools/GrepTool: maxResultSizeChars = 20K (intentionally small)
    registry.register(
        name="grep",
        description=(
            "Search file contents using a regex pattern. "
            "Returns matching lines with file paths and line numbers."
        ),
        parameters={
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory or file to search in"},
            "include": {"type": "string", "description": "File pattern to include (e.g., '*.py')"},
        },
        required=["pattern"],
        handler=_handle_grep,
        is_read_only=True,
        max_result_chars=20_000,  # From src/ GrepTool: 20K (small, focused results)
        is_compactable=True,
    )

    # Tool 10: Web Fetch — fetch and parse URL content
    # From src/tools/WebFetchTool: maxResultSizeChars = 100K
    registry.register(
        name="web_fetch",
        description=(
            "Fetch the content of a URL and convert HTML to readable text. "
            "Use when you have a specific URL to read."
        ),
        parameters={
            "url": {"type": "string", "description": "The URL to fetch"},
        },
        handler=_handle_web_fetch,
        is_read_only=True,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 11: Task Create — from src/tools/TaskCreateTool
    registry.register(
        name="task_create",
        description=(
            "Create a new task for tracking work. "
            "Use for managing multi-step projects, parallel work streams, "
            "or any work that needs progress tracking."
        ),
        parameters={
            "subject": {"type": "string", "description": "Brief title for the task"},
            "description": {"type": "string", "description": "What needs to be done"},
        },
        handler=_handle_task_create,
        is_read_only=False,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 12: Task List — from src/tools/TaskListTool
    registry.register(
        name="task_list",
        description="List all current tasks and their status.",
        parameters={},
        required=[],
        handler=_handle_task_list,
        is_read_only=True,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 13: Task Update — from src/tools/TaskUpdateTool
    registry.register(
        name="task_update",
        description=(
            "Update a task's status or output. "
            "Use to mark tasks as in_progress, done, or add output."
        ),
        parameters={
            "id": {"type": "string", "description": "Task ID to update"},
            "status": {"type": "string", "description": "New status: pending, in_progress, done, error"},
            "output": {"type": "string", "description": "Task output or result text"},
        },
        required=["id"],
        handler=_handle_task_update,
        is_read_only=False,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 14: Task Stop — from src/tools/TaskStopTool
    registry.register(
        name="task_stop",
        description="Stop/cancel a running task.",
        parameters={
            "id": {"type": "string", "description": "Task ID to stop"},
        },
        handler=_handle_task_stop,
        is_read_only=False,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 15: List Directory — list files in a directory
    registry.register(
        name="list_dir",
        description=(
            "List the contents of a directory, showing files and subdirectories "
            "with their sizes. Use to explore project structure."
        ),
        parameters={
            "path": {"type": "string", "description": "Directory path to list"},
        },
        handler=_handle_list_dir,
        is_read_only=True,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 16: PowerShell — Windows-specific shell (from src/tools/PowerShellTool)
    registry.register(
        name="powershell",
        description=(
            "Execute a PowerShell command. Use for Windows-specific operations, "
            "registry access, system administration, or complex scripting."
        ),
        parameters={
            "command": {"type": "string", "description": "PowerShell command to execute"},
        },
        handler=_handle_powershell,
        is_read_only=False,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 17: Todo Write — task decomposition from src/tools/TodoWriteTool
    # Philosophy: break complex work into tracked subtasks. Mark in_progress
    # BEFORE starting, completed AFTER finishing. First SEARCH scope, THEN plan.
    registry.register(
        name="todo_write",
        description=(
            "Create and manage a structured task list for your current session. "
            "Use for complex multi-step tasks (3+ steps). Write the full todo list "
            "as a markdown checklist. Helps track progress and show the user your plan.\n\n"
            "When to use: multi-step tasks, non-trivial work, user provides multiple tasks.\n"
            "When NOT to use: single trivial task, conversational/informational queries.\n\n"
            "Format: Write a markdown checklist with [ ], [x] (done), or [~] (in progress).\n"
            "Mark each task in_progress BEFORE starting. Only one task in_progress at a time."
        ),
        parameters={
            "todos": {"type": "string", "description": "Markdown checklist (e.g., '- [ ] Task 1\\n- [x] Task 2')"},
            "file_path": {"type": "string", "description": "Path to save the todo file (optional, defaults to .todos.md)"},
        },
        required=["todos"],
        handler=_handle_todo_write,
        is_read_only=False,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 18: Notebook Edit — edit Jupyter notebooks (bridge-backed)
    registry.register(
        name="notebook_edit",
        description=(
            "Edit a Jupyter notebook cell. Supports inserting, replacing, "
            "or deleting cells. Use for data science and analysis workflows."
        ),
        parameters={
            "path": {"type": "string", "description": "Path to the .ipynb file"},
            "cell_index": {"type": "integer", "description": "Cell index (0-based)"},
            "action": {"type": "string", "description": "Action: insert, replace, or delete"},
            "content": {"type": "string", "description": "Cell content (for insert/replace)"},
            "cell_type": {"type": "string", "description": "Cell type: code or markdown (default: code)"},
        },
        required=["path", "cell_index", "action"],
        handler=_handle_notebook_edit,
        is_read_only=False,
        max_result_chars=100_000,
        is_compactable=True,
    )

    # Tool 19: Spawn Subagent — isolated context research
    # From src/tools/AgentTool: subagents run in isolated contexts,
    # preventing massive tool results from polluting the main loop.
    # Philosophy from runAgent.ts:
    #   - "Research: fork open-ended questions. Launch parallel forks in one message."
    #   - "Never delegate understanding — write prompts that prove you understood."
    #   - Each subagent gets a fresh context with only the directive prompt.
    registry.register(
        name="spawn_subagent",
        description=(
            "Spawn an isolated research subagent that runs with its own context window. "
            "The subagent has access to the same tools but its context is separate — "
            "massive tool results won't pollute your main context.\n\n"
            "Use for: parallel research on independent questions, deep-dive investigation "
            "that would fill your context with raw output, multi-step implementation.\n\n"
            "IMPORTANT: Write the prompt as a directive — what to do, not what the situation is. "
            "Brief like 'a smart colleague who just walked into the room'. Explain what you've "
            "tried, what you've ruled out. Give enough context for judgment calls.\n\n"
            "If you ARE the subagent — execute directly; do not re-delegate."
        ),
        parameters={
            "prompt": {"type": "string", "description": "Detailed directive for the subagent. Be specific about what to research, what to look for, and what to return."},
            "subagent_type": {"type": "string", "description": "Type: 'research' (default, deep investigation) or 'explore' (broad codebase exploration)"},
        },
        required=["prompt"],
        handler=_handle_spawn_subagent,
        is_read_only=True,
        max_result_chars=100_000,
        is_compactable=True,
    )

    return registry


# ── Tool handlers ──

async def _handle_deep_research(query: str) -> str:
    """Execute v2's full pipeline as a tool."""
    from ..query import run_query
    from ..core.satisfaction import SatisfactionTracker

    tracker = SatisfactionTracker()
    result = await run_query(query, satisfaction=tracker)
    return result.answer


async def _handle_web_search(query: str) -> str:
    """Quick web search without the full pipeline."""
    from ..tools.brave_search import search_brave

    results = await search_brave(query, max_results=5)
    if not results:
        return "No search results found."

    output_parts = []
    for r in results[:5]:
        title = getattr(r, 'title', '')
        snippet = getattr(r, 'snippet', getattr(r, 'description', ''))
        url = getattr(r, 'url', '')
        output_parts.append(f"**{title}**\n{snippet}\nSource: {url}\n")

    return "\n---\n".join(output_parts)


async def _handle_think(reasoning: str) -> str:
    """Private scratchpad — just echoes back (not shown to user)."""
    return f"[Thought recorded: {len(reasoning)} chars]"


async def _handle_file_read(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """Read file contents — tries Node.js bridge first, Python fallback."""
    bridge = get_bridge()
    try:
        if await bridge.is_available():
            args = {"file_path": os.path.abspath(path)}
            if start_line > 0:
                args["start_line"] = start_line
            if end_line > 0:
                args["end_line"] = end_line
            result = await bridge.call("file_read", args)
            content = result.get("content", "")
            if result.get("truncated"):
                content += f"\n[... truncated, total {result.get('lines', '?')} lines]"
            return content
    except BridgeError as e:
        logger.debug("Bridge file_read failed, using Python fallback: %s", e)

    # Python fallback
    try:
        target = os.path.abspath(path) if not os.path.isabs(path) else path
        if not os.path.exists(target):
            return f"Error: File not found: {path}"

        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        if start_line > 0 or end_line > 0:
            start = max(0, (start_line or 1) - 1)
            end = end_line or len(lines)
            selected = lines[start:end]
            numbered = [f"{start + i + 1}: {line}" for i, line in enumerate(selected)]
            return "".join(numbered)
        else:
            if len(lines) > 200:
                numbered = [f"{i + 1}: {line}" for i, line in enumerate(lines[:200])]
                return "".join(numbered) + f"\n[... {len(lines) - 200} more lines]"
            return "".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
    except Exception as e:
        return f"Error reading file: {e}"


async def _handle_file_write(path: str, content: str) -> str:
    """Write content — tries Node.js bridge first, Python fallback."""
    bridge = get_bridge()
    try:
        if await bridge.is_available():
            result = await bridge.call("file_write", {
                "file_path": os.path.abspath(path),
                "content": content,
            })
            return f"Successfully wrote {result.get('bytes_written', len(content))} bytes to {path}"
    except BridgeError as e:
        logger.debug("Bridge file_write failed, using Python fallback: %s", e)

    # Python fallback
    try:
        target = os.path.abspath(path)
        os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


async def _handle_file_edit(path: str, old_text: str, new_text: str) -> str:
    """Edit file — tries Node.js bridge first, Python fallback."""
    bridge = get_bridge()
    try:
        if await bridge.is_available():
            result = await bridge.call("file_edit", {
                "file_path": os.path.abspath(path),
                "old_text": old_text,
                "new_text": new_text,
            })
            return f"Successfully edited {path} ({result.get('replacements', 1)} replacement(s))"
    except BridgeError as e:
        logger.debug("Bridge file_edit failed, using Python fallback: %s", e)

    # Python fallback
    try:
        target = os.path.abspath(path)
        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
        if old_text not in content:
            return f"Error: old_text not found in {path}"
        content = content.replace(old_text, new_text, 1)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully edited {path}"
    except Exception as e:
        return f"Error editing file: {e}"


async def _handle_bash(command: str) -> str:
    """Execute shell command — tries Node.js bridge first, Python fallback."""
    bridge = get_bridge()
    try:
        if await bridge.is_available():
            result = await bridge.call("bash", {
                "command": command,
                "cwd": os.getcwd(),
                "timeout": 30000,
            }, timeout=35.0)
            output = result.get("stdout", "")
            stderr = result.get("stderr", "")
            exit_code = result.get("exitCode", 0)
            if stderr:
                output += f"\nSTDERR: {stderr}"
            if exit_code != 0:
                return f"Error (exit code {exit_code}): {output}"
            return output
    except BridgeError as e:
        logger.debug("Bridge bash failed, using Python fallback: %s", e)

    # Python fallback
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        output = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")
        if err_text:
            output += f"\nSTDERR: {err_text}"
        if proc.returncode != 0:
            return f"Error (exit code {proc.returncode}): {output}"
        return output
    except asyncio.TimeoutError:
        return "Error: Command timed out after 30s"
    except Exception as e:
        return f"Error executing command: {e}"


async def _handle_glob(pattern: str, path: str = "") -> str:
    """Find files matching glob pattern."""
    import glob as glob_module
    import os

    base = path or os.getcwd()
    full_pattern = os.path.join(base, pattern)

    try:
        matches = glob_module.glob(full_pattern, recursive=True)
        if not matches:
            return f"No files matching: {pattern}"

        # Show relative paths for readability
        rel_matches = [os.path.relpath(m, base) for m in matches[:100]]
        result = "\n".join(rel_matches)
        if len(matches) > 100:
            result += f"\n[... {len(matches) - 100} more files]"
        return result

    except Exception as e:
        return f"Error: {e}"


async def _handle_grep(pattern: str, path: str = "", include: str = "") -> str:
    """Search files using regex pattern."""
    import re
    import os

    base = path or os.getcwd()
    results_parts = []
    files_searched = 0
    matches_found = 0

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Invalid regex: {e}"

    try:
        for root, dirs, files in os.walk(base):
            # Skip hidden dirs and common junk
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', '.git')]

            for fname in files:
                # Apply include filter
                if include and not fname.endswith(include.lstrip('*')):
                    continue

                filepath = os.path.join(root, fname)
                files_searched += 1

                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                rel_path = os.path.relpath(filepath, base)
                                results_parts.append(f"{rel_path}:{line_num}: {line.rstrip()}")
                                matches_found += 1
                                if matches_found >= 50:
                                    break
                except (PermissionError, IsADirectoryError):
                    continue

                if matches_found >= 50:
                    break
            if matches_found >= 50:
                break

        if not results_parts:
            return f"No matches for /{pattern}/ in {base} ({files_searched} files searched)"

        return "\n".join(results_parts)

    except Exception as e:
        return f"Error: {e}"


async def _handle_web_fetch(url: str) -> str:
    """Fetch URL content."""
    from ..tools.web_fetch import fetch_url
    try:
        content = await fetch_url(url)
        return content[:100_000]  # Cap at 100K chars
    except Exception as e:
        return f"Error fetching URL: {e}"


# ── Task Store (in-memory, persists across calls within one loop run) ──
_task_store: dict[str, dict] = {}
_task_counter: int = 0


async def _handle_task_create(subject: str, description: str = "") -> str:
    """Create a task — Python in-memory store (stateful, always runs locally)."""
    global _task_counter
    _task_counter += 1
    task_id = f"task-{_task_counter}"
    _task_store[task_id] = {
        "id": task_id, "subject": subject,
        "description": description, "status": "pending", "output": "",
    }
    return f"Task #{task_id} created: {subject}"


async def _handle_task_list() -> str:
    """List all tasks."""
    if not _task_store:
        return "No tasks."
    lines = [f"  {t['id']}: [{t['status']}] {t['subject']}" for t in _task_store.values()]
    return "Tasks:\n" + "\n".join(lines)


async def _handle_task_update(id: str, status: str = "", output: str = "") -> str:
    """Update a task's status or output."""
    if id not in _task_store:
        return f"Task {id} not found."
    if status:
        _task_store[id]["status"] = status
    if output:
        _task_store[id]["output"] = output
    return f"Task {id} updated: status={status or 'unchanged'}"


async def _handle_task_stop(id: str) -> str:
    """Stop a task."""
    if id not in _task_store:
        return f"Task {id} not found."
    _task_store[id]["status"] = "stopped"
    return f"Task {id} stopped."



async def _handle_list_dir(path: str) -> str:
    """List directory contents — tries Node.js bridge first, Python fallback."""
    bridge = get_bridge()
    try:
        if await bridge.is_available():
            result = await bridge.call("list_dir", {
                "path": os.path.abspath(path),
            })
            entries = result.get("entries", [])
            if not entries:
                return f"Empty directory: {path}"
            lines = []
            for e in entries:
                if e["type"] == "directory":
                    lines.append(f"  {e['name']}/")
                else:
                    size = e.get("size", 0)
                    if size > 1048576:
                        lines.append(f"  {e['name']}  ({size / 1048576:.1f} MB)")
                    elif size > 1024:
                        lines.append(f"  {e['name']}  ({size / 1024:.1f} KB)")
                    else:
                        lines.append(f"  {e['name']}  ({size} B)")
            return f"Directory: {path}\n" + "\n".join(lines)
    except BridgeError as e:
        logger.debug("Bridge list_dir failed, using Python fallback: %s", e)

    # Python fallback
    try:
        target = os.path.abspath(path)
        if not os.path.isdir(target):
            return f"Error: Not a directory: {path}"
        entries = os.listdir(target)
        lines = []
        for name in sorted(entries):
            full = os.path.join(target, name)
            if os.path.isdir(full):
                lines.append(f"  {name}/")
            else:
                try:
                    size = os.path.getsize(full)
                    if size > 1048576:
                        lines.append(f"  {name}  ({size / 1048576:.1f} MB)")
                    elif size > 1024:
                        lines.append(f"  {name}  ({size / 1024:.1f} KB)")
                    else:
                        lines.append(f"  {name}  ({size} B)")
                except OSError:
                    lines.append(f"  {name}")
        return f"Directory: {path}\n" + "\n".join(lines)
    except Exception as e:
        return f"Error listing directory: {e}"


async def _handle_powershell(command: str) -> str:
    """Execute PowerShell command — tries Node.js bridge first, Python fallback."""
    bridge = get_bridge()
    try:
        if await bridge.is_available():
            result = await bridge.call("powershell", {
                "command": command,
                "cwd": os.getcwd(),
                "timeout": 30000,
            }, timeout=35.0)
            output = result.get("stdout", "")
            stderr = result.get("stderr", "")
            exit_code = result.get("exitCode", 0)
            if stderr:
                output += f"\nSTDERR: {stderr}"
            if exit_code != 0:
                return f"Error (exit code {exit_code}): {output}"
            return output
    except BridgeError as e:
        logger.debug("Bridge powershell failed, using Python fallback: %s", e)

    # Python fallback
    try:
        escaped = command.replace('"', '\\"')
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive", "-Command", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        output = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")
        if err_text:
            output += f"\nSTDERR: {err_text}"
        if proc.returncode != 0:
            return f"Error (exit code {proc.returncode}): {output}"
        return output
    except asyncio.TimeoutError:
        return "Error: PowerShell command timed out after 30s"
    except Exception as e:
        return f"Error executing PowerShell: {e}"


# Need os for file handlers
import os


async def _handle_todo_write(todos: str, file_path: str = ".todos.md") -> str:
    """Write a structured todo list to a file.

    From src/tools/TodoWriteTool: tracks multi-step work.
    Philosophy: break complex work into subtasks, mark in_progress before starting.
    """
    try:
        abs_path = os.path.abspath(file_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(todos)

        # Count status
        total = todos.count('- [')
        done = todos.count('- [x]')
        in_progress = todos.count('- [~]') + todos.count('- [/]')
        pending = total - done - in_progress

        return (
            f"Todo list written to {abs_path}\n"
            f"  {total} tasks: {done} done, {in_progress} in progress, {pending} pending"
        )
    except Exception as e:
        return f"Error writing todos: {e}"


async def _handle_notebook_edit(
    path: str,
    cell_index: int,
    action: str,
    content: str = "",
    cell_type: str = "code",
) -> str:
    """Edit a Jupyter notebook cell — tries bridge first, Python fallback.

    From src/tools/NotebookEditTool.
    """
    bridge = get_bridge()
    try:
        if await bridge.is_available():
            result = await bridge.call("notebook_edit", {
                "notebook_path": path,
                "cell_number": cell_index,
                "new_source": content,
                "cell_type": cell_type,
            }, timeout=15.0)
            return result.get("message", f"Notebook cell {cell_index} {action}d")
    except BridgeError as e:
        logger.debug("Bridge notebook_edit failed, using Python fallback: %s", e)

    # Python fallback — direct JSON manipulation
    try:
        abs_path = os.path.abspath(path)

        if not os.path.exists(abs_path):
            # Create a new notebook
            notebook = {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [],
            }
        else:
            with open(abs_path, 'r', encoding='utf-8') as f:
                notebook = json.load(f)

        cells = notebook.get("cells", [])

        if action == "insert":
            new_cell = {
                "cell_type": cell_type,
                "source": content.split('\n'),
                "metadata": {},
            }
            if cell_type == "code":
                new_cell["execution_count"] = None
                new_cell["outputs"] = []
            cells.insert(cell_index, new_cell)
        elif action == "replace":
            if 0 <= cell_index < len(cells):
                cells[cell_index]["source"] = content.split('\n')
            else:
                return f"Error: cell index {cell_index} out of range (0-{len(cells)-1})"
        elif action == "delete":
            if 0 <= cell_index < len(cells):
                cells.pop(cell_index)
            else:
                return f"Error: cell index {cell_index} out of range (0-{len(cells)-1})"
        else:
            return f"Unknown action: {action}. Use insert, replace, or delete."

        notebook["cells"] = cells

        with open(abs_path, 'w', encoding='utf-8') as f:
            json.dump(notebook, f, indent=1, ensure_ascii=False)

        return f"Notebook cell {cell_index} {action}d in {path} ({len(cells)} cells total)"
    except Exception as e:
        return f"Error editing notebook: {e}"


async def _handle_spawn_subagent(prompt: str, subagent_type: str = "research") -> str:
    """Spawn an isolated subagent with its own context window.

    From src/tools/AgentTool/runAgent.ts:
    - Subagent gets a fresh context with only the directive prompt
    - Its tool results don't pollute the parent's context
    - Returns a concise report of findings

    Implementation: creates a mini AgenticLoop with limited turns,
    runs it, and returns the final answer.
    """
    from .loop import AgenticLoop

    try:
        # Create an isolated mini-loop (fresh context)
        subagent = AgenticLoop(
            max_turns=8,  # Limited — subagents shouldn't run forever
            context_budget=30_000,  # Smaller budget for focused work
        )

        # Build the directive (from src/constants/prompts.ts DEFAULT_AGENT_PROMPT)
        directive = (
            "You are a research subagent. Given the directive below, use the tools "
            "available to complete the task. Complete it fully — don't gold-plate, "
            "but don't leave it half-done. When you finish, respond with a concise "
            "report covering what was done and any key findings.\n\n"
            f"## Directive\n\n{prompt}"
        )

        # Run the subagent (isolated context — no parent messages leak in)
        result = await subagent.run(directive)

        return (
            f"[Subagent Report — {result.turn_count} turns, "
            f"{result.total_tool_calls} tool calls, "
            f"{result.timing_ms:.0f}ms]\n\n"
            f"{result.answer}"
        )
    except Exception as e:
        logger.error("Subagent execution failed: %s", e)
        return f"Subagent error: {e}"
