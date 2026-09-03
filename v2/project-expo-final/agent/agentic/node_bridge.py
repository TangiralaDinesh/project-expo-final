"""
Node.js Bridge — Python interface to the src_reference/ TypeScript tools.

Calls the Node.js bridge.ts via subprocess, getting industrial-grade Claude Code
tool execution (bash, file_read, file_write, file_edit, glob, grep, web_fetch)
without rewriting any of the 40K lines of TS.

Usage:
    bridge = NodeBridge()
    result = await bridge.call("file_read", {"file_path": "/some/file.py"})
    # result is a dict: {"content": "...", "lines": 42, "truncated": false}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Path to bridge.ts (relative to this file)
_BRIDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "src_reference")
_BRIDGE_PATH = os.path.join(_BRIDGE_DIR, "bridge.ts")


class NodeBridge:
    """Python → Node.js bridge for tool execution.

    Calls bridge.ts via subprocess with JSON args/results.
    Falls back to Python implementations if Node.js fails.
    """

    def __init__(self, bridge_dir: Optional[str] = None):
        self.bridge_dir = bridge_dir or _BRIDGE_DIR
        self.bridge_path = os.path.join(self.bridge_dir, "bridge.ts")
        self._node_available: Optional[bool] = None

    async def is_available(self) -> bool:
        """Check if Node.js bridge is available."""
        if self._node_available is not None:
            return self._node_available

        try:
            proc = await asyncio.create_subprocess_exec(
                "node", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            version = stdout.decode().strip()
            # Need Node v22+ for --experimental-strip-types
            major = int(version.lstrip("v").split(".")[0])
            self._node_available = major >= 22 and os.path.exists(self.bridge_path)
            if self._node_available:
                logger.info("Node.js bridge available: %s", version)
            else:
                logger.warning("Node.js %s found but need v22+ or bridge.ts missing", version)
        except Exception as e:
            logger.debug("Node.js not available: %s", e)
            self._node_available = False

        return self._node_available

    async def call(
        self,
        action: str,
        args: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Call a bridge action and return the JSON result.

        Args:
            action: One of: bash, file_read, file_write, file_edit, glob, grep,
                    web_fetch, estimate_tokens
            args: Action-specific arguments (see bridge.ts for schemas)
            timeout: Max seconds to wait

        Returns:
            Dict with action-specific result fields

        Raises:
            BridgeError: If the bridge call fails
        """
        if not await self.is_available():
            raise BridgeError("Node.js bridge not available")

        args_json = json.dumps(args)

        try:
            proc = await asyncio.create_subprocess_exec(
                "node",
                "--experimental-strip-types",
                self.bridge_path,
                action,
                args_json,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.bridge_dir,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                # Try to parse error JSON from stdout
                try:
                    error_data = json.loads(stdout)
                    raise BridgeError(
                        f"Bridge {action} failed: {error_data.get('message', stderr)}"
                    )
                except json.JSONDecodeError:
                    raise BridgeError(f"Bridge {action} failed (exit {proc.returncode}): {stderr}")

            if not stdout:
                raise BridgeError(f"Bridge {action} returned empty output")

            result = json.loads(stdout)

            if isinstance(result, dict) and result.get("error"):
                raise BridgeError(f"Bridge {action}: {result.get('message', 'Unknown error')}")

            return result

        except asyncio.TimeoutError:
            raise BridgeError(f"Bridge {action} timed out after {timeout}s")
        except json.JSONDecodeError as e:
            raise BridgeError(f"Bridge {action} returned invalid JSON: {e}")

    # ── Convenience methods ──

    async def bash(self, command: str, cwd: str = "", timeout: float = 30.0) -> dict:
        """Execute a shell command."""
        args: dict[str, Any] = {"command": command}
        if cwd:
            args["cwd"] = cwd
        args["timeout"] = int(timeout * 1000)
        return await self.call("bash", args, timeout=timeout + 5)

    async def file_read(
        self,
        file_path: str,
        start_line: int = 0,
        end_line: int = 0,
    ) -> dict:
        """Read a file with optional line range."""
        args: dict[str, Any] = {"file_path": file_path}
        if start_line > 0:
            args["start_line"] = start_line
        if end_line > 0:
            args["end_line"] = end_line
        return await self.call("file_read", args)

    async def file_write(self, file_path: str, content: str) -> dict:
        """Write content to a file."""
        return await self.call("file_write", {"file_path": file_path, "content": content})

    async def file_edit(self, file_path: str, old_text: str, new_text: str) -> dict:
        """Edit a file by replacing text."""
        return await self.call("file_edit", {
            "file_path": file_path,
            "old_text": old_text,
            "new_text": new_text,
        })

    async def glob(self, pattern: str, cwd: str = "") -> dict:
        """Find files matching a glob pattern."""
        args: dict[str, Any] = {"pattern": pattern}
        if cwd:
            args["cwd"] = cwd
        return await self.call("glob", args)

    async def grep(self, pattern: str, path: str, include: str = "") -> dict:
        """Search files using regex pattern."""
        args: dict[str, Any] = {"pattern": pattern, "path": path}
        if include:
            args["include"] = include
        return await self.call("grep", args)

    async def web_fetch(self, url: str, timeout: float = 15.0) -> dict:
        """Fetch URL content."""
        return await self.call("web_fetch", {"url": url, "timeout": int(timeout * 1000)}, timeout=timeout + 5)

    async def estimate_tokens(self, text: str) -> int:
        """Estimate token count using the src/ algorithm."""
        result = await self.call("estimate_tokens", {"text": text})
        return result.get("tokens", len(text) // 4)


class BridgeError(Exception):
    """Error from the Node.js bridge."""
    pass


# ── Singleton instance ──
_bridge_instance: Optional[NodeBridge] = None


def get_bridge() -> NodeBridge:
    """Get the global NodeBridge singleton."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = NodeBridge()
    return _bridge_instance
