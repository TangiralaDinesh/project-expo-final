"""
Code Execution & File Tools (Tier 4) — Implement actual tool handlers

These are called by the executor when orchestrator generates code_execution nodes.
Each tool can be overridden via injection for production safety (E2B, Firecracker).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class ToolOutput:
    """Standard result from tool execution"""
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    execution_time_ms: float = 0.0
    files_created: list[str] = None
    files_modified: list[str] = None
    
    def __post_init__(self):
        self.files_created = self.files_created or []
        self.files_modified = self.files_modified or []


# ─── Python Execution Tool ───────────────────────────────────────────────────

async def execute_python_code(
    code: str,
    *,
    sandbox_dir: Optional[str] = None,
    timeout: float = 30.0,
    input_data: Optional[dict] = None,
) -> ToolOutput:
    """
    Execute Python code (Tier 4).
    
    Args:
        code: Python code to execute
        sandbox_dir: Working directory (if None, uses temp)
        timeout: Execution timeout in seconds
        input_data: Variables to inject into code's namespace
    
    Returns:
        ToolOutput with stdout/stderr/return_code
    
    Safety:
    - Use only in isolated environments (E2B, Firecracker)
    - Never execute untrusted code directly
    """
    if sandbox_dir is None:
        sandbox_dir = "/tmp/agent-exec"
    
    os.makedirs(sandbox_dir, exist_ok=True)
    
    # Create execution script
    script_path = os.path.join(sandbox_dir, "_exec.py")
    
    # Build namespace with injected data
    namespace_setup = ""
    if input_data:
        for key, value in input_data.items():
            # Safely serialize the value
            try:
                serialized = json.dumps(value)
                namespace_setup += f"{key} = json.loads({repr(serialized)})\n"
            except:
                logger.warning(f"Could not serialize input {key}, skipping")
    
    script_content = f"""
import json
import sys
import traceback

{namespace_setup}

try:
{chr(10).join('    ' + line for line in code.split(chr(10)))}
except Exception as e:
    traceback.print_exc()
    sys.exit(1)
"""
    
    try:
        with open(script_path, 'w') as f:
            f.write(script_content)
        
        # Execute
        import time
        t0 = time.time()
        
        result = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable, script_path,
                cwd=sandbox_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout,
        )
        
        stdout, stderr = await asyncio.wait_for(
            result.communicate(),
            timeout=timeout,
        )
        
        elapsed = (time.time() - t0) * 1000
        
        return ToolOutput(
            success=result.returncode == 0,
            stdout=stdout.decode('utf-8', errors='replace'),
            stderr=stderr.decode('utf-8', errors='replace'),
            return_code=result.returncode,
            execution_time_ms=elapsed,
        )
        
    except asyncio.TimeoutError:
        return ToolOutput(
            success=False,
            stderr=f"Execution timeout after {timeout} seconds",
            return_code=-1,
            execution_time_ms=timeout * 1000,
        )
    except Exception as e:
        logger.error(f"Python execution failed: {e}")
        return ToolOutput(
            success=False,
            stderr=str(e),
            return_code=-1,
        )


# ─── File Creation Tool ──────────────────────────────────────────────────────

async def create_file_tool(
    file_path: str,
    content: str,
    *,
    sandbox_dir: Optional[str] = None,
    overwrite: bool = False,
) -> ToolOutput:
    """
    Create a new file (Tier 4).
    
    Args:
        file_path: Relative or absolute path
        content: File content
        sandbox_dir: Base directory (if relative path given)
        overwrite: Allow overwriting existing files
    
    Returns:
        ToolOutput with success status
    """
    if sandbox_dir is None:
        sandbox_dir = "/tmp/agent-files"
    
    os.makedirs(sandbox_dir, exist_ok=True)
    
    # Resolve path safely
    if os.path.isabs(file_path):
        full_path = file_path
    else:
        full_path = os.path.join(sandbox_dir, file_path)
    
    full_path = os.path.realpath(full_path)
    
    # Verify not escaping sandbox
    if not full_path.startswith(os.path.realpath(sandbox_dir)):
        return ToolOutput(
            success=False,
            stderr=f"Path escape blocked: {file_path}",
        )
    
    # Check if exists
    if os.path.exists(full_path) and not overwrite:
        return ToolOutput(
            success=False,
            stderr=f"File already exists: {full_path}. Use overwrite=True to replace.",
        )
    
    try:
        # Create parent directories
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, 'w') as f:
            f.write(content)
        
        logger.info(f"Created file: {full_path} ({len(content)} bytes)")
        
        return ToolOutput(
            success=True,
            stdout=f"Created {full_path}",
            files_created=[full_path],
        )
        
    except Exception as e:
        logger.error(f"File creation failed: {e}")
        return ToolOutput(
            success=False,
            stderr=str(e),
        )


# ─── File Reading Tool ──────────────────────────────────────────────────────

async def read_file_tool(
    file_path: str,
    *,
    sandbox_dir: Optional[str] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> ToolOutput:
    """
    Read file contents (Tier 4).
    
    Args:
        file_path: Path to file
        sandbox_dir: Base directory (if relative path given)
        start_line: Start line number (1-indexed, inclusive)
        end_line: End line number (1-indexed, inclusive)
    
    Returns:
        ToolOutput with file content in stdout
    """
    if sandbox_dir is None:
        sandbox_dir = "/tmp/agent-files"
    
    # Resolve path safely
    if os.path.isabs(file_path):
        full_path = file_path
    else:
        full_path = os.path.join(sandbox_dir, file_path)
    
    full_path = os.path.realpath(full_path)
    
    # Verify not escaping sandbox
    if not full_path.startswith(os.path.realpath(sandbox_dir)):
        return ToolOutput(
            success=False,
            stderr=f"Path escape blocked: {file_path}",
        )
    
    try:
        if not os.path.exists(full_path):
            return ToolOutput(
                success=False,
                stderr=f"File not found: {full_path}",
            )
        
        with open(full_path, 'r') as f:
            lines = f.readlines()
        
        # Handle line ranges
        if start_line is not None or end_line is not None:
            start = (start_line or 1) - 1  # Convert to 0-indexed
            end = (end_line or len(lines))
            lines = lines[start:end]
        
        content = ''.join(lines)
        
        return ToolOutput(
            success=True,
            stdout=content,
        )
        
    except Exception as e:
        logger.error(f"File read failed: {e}")
        return ToolOutput(
            success=False,
            stderr=str(e),
        )


# ─── Bash Command Tool ──────────────────────────────────────────────────────

async def bash_tool(
    command: str,
    *,
    sandbox_dir: Optional[str] = None,
    timeout: float = 30.0,
) -> ToolOutput:
    """
    Execute bash command (Tier 4).
    
    Args:
        command: Bash command to execute
        sandbox_dir: Working directory
        timeout: Execution timeout
    
    Returns:
        ToolOutput with command result
    """
    if sandbox_dir is None:
        sandbox_dir = "/tmp/agent-exec"
    
    os.makedirs(sandbox_dir, exist_ok=True)
    
    try:
        import time
        t0 = time.time()
        
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                command,
                cwd=sandbox_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout,
        )
        
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
        
        elapsed = (time.time() - t0) * 1000
        
        return ToolOutput(
            success=proc.returncode == 0,
            stdout=stdout.decode('utf-8', errors='replace'),
            stderr=stderr.decode('utf-8', errors='replace'),
            return_code=proc.returncode,
            execution_time_ms=elapsed,
        )
        
    except asyncio.TimeoutError:
        return ToolOutput(
            success=False,
            stderr=f"Command timeout after {timeout} seconds",
            return_code=-1,
        )
    except Exception as e:
        logger.error(f"Bash execution failed: {e}")
        return ToolOutput(
            success=False,
            stderr=str(e),
        )


# ─── Tool Dispatcher ─────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "execute_python": execute_python_code,
    "create_file": create_file_tool,
    "read_file": read_file_tool,
    "bash": bash_tool,
}


async def dispatch_tool(
    tool_name: str,
    tool_args: dict,
    *,
    sandbox_dir: Optional[str] = None,
) -> ToolOutput:
    """
    Dispatch a tool call to the appropriate handler.
    
    Args:
        tool_name: Name of tool (execute_python, create_file, etc.)
        tool_args: Tool-specific arguments
        sandbox_dir: Sandbox directory for file operations
    
    Returns:
        ToolOutput from tool execution
    """
    if tool_name not in TOOL_HANDLERS:
        return ToolOutput(
            success=False,
            stderr=f"Unknown tool: {tool_name}",
        )
    
    handler = TOOL_HANDLERS[tool_name]
    
    # Inject sandbox_dir into all tool calls
    if sandbox_dir is not None:
        tool_args = {**tool_args, "sandbox_dir": sandbox_dir}
    
    try:
        return await handler(**tool_args)
    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}")
        return ToolOutput(
            success=False,
            stderr=f"Tool execution error: {str(e)}",
        )
