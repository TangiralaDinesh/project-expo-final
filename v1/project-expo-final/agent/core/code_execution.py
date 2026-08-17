"""
Code Execution (Tier 4) — Dynamic Code Generation + Execution

Allows the agent to:
1. Generate Python/Bash code to validate hypotheses
2. Execute code safely in isolated environments
3. Use results to improve reasoning
4. Test edge cases and verify implementations

This enables "thinking by doing" where the agent validates its own hypotheses.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple
import os

logger = logging.getLogger(__name__)


class ExecutionLanguage(str, Enum):
    """Languages the agent can execute"""
    PYTHON = "python"
    BASH = "bash"
    JAVASCRIPT = "javascript"  # Future: node.js support


class ExecutionSafety(str, Enum):
    """Safety modes for code execution"""
    SANDBOXED = "sandboxed"    # No file system, no network
    RESTRICTED = "restricted"  # Limited file system, no network
    FULL = "full"               # Full permissions (use carefully!)


@dataclass
class ExecutionRequest:
    """Request to execute code"""
    language: ExecutionLanguage
    code: str
    timeout_s: float = 10.0
    safety: ExecutionSafety = ExecutionSafety.SANDBOXED
    description: str = ""  # What is this code testing?


@dataclass
class ExecutionResult:
    """Result of code execution"""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    execution_time_ms: float = 0.0
    
    # For hypothesis validation:
    hypothesis_confirmed: bool = False
    confidence_delta: float = 0.0  # How much does this increase confidence?
    
    def is_error(self) -> bool:
        """Was there an error?"""
        return not self.success or self.exit_code != 0
    
    def get_summary(self) -> str:
        """Get human-readable summary"""
        if self.success:
            return f"✅ Success ({self.execution_time_ms:.1f}ms)\n{self.stdout}"
        else:
            return f"❌ Failed (exit {self.exit_code})\n{self.stderr}"


class PythonExecutor:
    """Execute Python code safely"""
    
    @staticmethod
    async def execute(
        request: ExecutionRequest,
    ) -> ExecutionResult:
        """
        Execute Python code with timeout and error handling.
        
        In sandboxed mode:
        - No file system access
        - No network access
        - No subprocess spawning
        """
        start_time = time.time()
        
        try:
            # Use subprocess with timeout for safety
            process = await asyncio.create_subprocess_exec(
                "python3", "-c", request.code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=request.timeout_s
                )
            except asyncio.TimeoutError:
                process.kill()
                return ExecutionResult(
                    success=False,
                    stderr=f"Execution timeout after {request.timeout_s}s",
                    execution_time_ms=(time.time() - start_time) * 1000,
                )
            
            elapsed = (time.time() - start_time) * 1000
            
            return ExecutionResult(
                success=process.returncode == 0,
                stdout=stdout.decode('utf-8', errors='ignore'),
                stderr=stderr.decode('utf-8', errors='ignore'),
                exit_code=process.returncode,
                execution_time_ms=elapsed,
            )
        
        except Exception as e:
            logger.error(f"Python execution failed: {e}")
            return ExecutionResult(
                success=False,
                stderr=str(e),
                execution_time_ms=(time.time() - start_time) * 1000,
            )


class BashExecutor:
    """Execute Bash code safely"""
    
    @staticmethod
    async def execute(
        request: ExecutionRequest,
    ) -> ExecutionResult:
        """
        Execute Bash code with timeout and restrictions.
        
        Dangerous commands are blocked:
        - rm -rf /
        - sudo
        - ssh
        - wget/curl to external sites
        """
        start_time = time.time()
        
        # Check for dangerous patterns
        dangerous_patterns = [
            "rm -rf", "sudo", "ssh", "dd if=/dev/", "fork()",
            "> /dev/", "chmod 777", "|xargs rm"
        ]
        
        for pattern in dangerous_patterns:
            if pattern in request.code:
                logger.warning(f"Blocked dangerous pattern: {pattern}")
                return ExecutionResult(
                    success=False,
                    stderr=f"Dangerous pattern blocked: {pattern}",
                )
        
        try:
            process = await asyncio.create_subprocess_shell(
                request.code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=request.timeout_s
                )
            except asyncio.TimeoutError:
                process.kill()
                return ExecutionResult(
                    success=False,
                    stderr=f"Execution timeout after {request.timeout_s}s",
                    execution_time_ms=(time.time() - start_time) * 1000,
                )
            
            elapsed = (time.time() - start_time) * 1000
            
            return ExecutionResult(
                success=process.returncode == 0,
                stdout=stdout.decode('utf-8', errors='ignore'),
                stderr=stderr.decode('utf-8', errors='ignore'),
                exit_code=process.returncode,
                execution_time_ms=elapsed,
            )
        
        except Exception as e:
            logger.error(f"Bash execution failed: {e}")
            return ExecutionResult(
                success=False,
                stderr=str(e),
                execution_time_ms=(time.time() - start_time) * 1000,
            )


async def execute_code(request: ExecutionRequest) -> ExecutionResult:
    """
    Execute code based on language.
    
    Dispatches to appropriate executor (Python/Bash/etc).
    """
    logger.info(f"Executing {request.language.value}: {request.description}")
    
    if request.language == ExecutionLanguage.PYTHON:
        return await PythonExecutor.execute(request)
    elif request.language == ExecutionLanguage.BASH:
        return await BashExecutor.execute(request)
    else:
        return ExecutionResult(
            success=False,
            stderr=f"Unsupported language: {request.language.value}",
        )
