"""
Self-Directed Tool Use — Agent uses code execution for ITS OWN reasoning.

The agent has code_execution.py (Python/Bash executors) but currently only
uses them when explicitly asked. This module gives the agent AWARENESS that
it can generate and execute code to help its own thinking.

When to self-execute:
  1. Gap is computational (calculate something, parse structured data)
  2. Parametric knowledge can't do it (read CSV, analyze table, date math)
  3. Hypothesis needs verification (does this code compile? does regex match?)

NOT for every query — only when:
  - decision_llm detects needs_computation = True
  - Or pivot loop needs hypothesis verification
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

from ..llm.client import NIMClient, get_client
from .code_execution import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionLanguage,
    ExecutionSafety,
    execute_code,
)

logger = logging.getLogger(__name__)


@dataclass
class SelfToolDecision:
    """Whether and how the agent should use tools for its own reasoning."""
    should_execute: bool
    reason: str
    execution_request: Optional[ExecutionRequest] = None
    fallback_approach: str = ""  # What to do if execution fails


_COMPUTATION_PATTERNS = [
    # Math/calculation
    r"(?i)(calculate|compute|sum|average|total|percentage|ratio|compound|interest|multiply)",
    # Data analysis
    r"(?i)(parse|csv|json|table|column|row|extract from|read file|analyze data)",
    # Date/time
    r"(?i)(days between|years since|date difference|how old|when was|time elapsed)",
    # Verification
    r"(?i)(does this (code|regex|pattern)|verify|validate|check if|test whether)",
    # Conversion
    r"(?i)(convert|transform|translate|encode|decode|base64|hex|binary)",
]


def _detect_computation_need(query: str, gap_description: str = "") -> bool:
    """Quick heuristic: does this query/gap need actual computation?"""
    text = f"{query} {gap_description}".lower()
    for pattern in _COMPUTATION_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


class SelfToolRouter:
    """Decides when the agent should use tools for ITS OWN reasoning."""

    def __init__(self, client: Optional[NIMClient] = None):
        self._client = client

    async def should_self_execute(
        self,
        query: str,
        gap_description: str = "",
        learnings: Optional[list] = None,
    ) -> SelfToolDecision:
        """Decide if the agent should generate and execute code to help reason.

        Two-stage check:
        1. Fast heuristic (regex patterns) — catches obvious computation queries
        2. LLM judgment (if heuristic unclear) — asks "is this gap code-shaped?"

        Returns:
            SelfToolDecision with execution_request if should execute
        """
        # Stage 1: Fast heuristic
        if _detect_computation_need(query, gap_description):
            code = await self._generate_computation_code(query, gap_description, learnings)
            if code:
                return SelfToolDecision(
                    should_execute=True,
                    reason="computation_detected_heuristic",
                    execution_request=ExecutionRequest(
                        language=ExecutionLanguage.PYTHON,
                        code=code,
                        timeout_s=15.0,
                        safety=ExecutionSafety.SANDBOXED,
                        description=f"Self-computation for: {query[:80]}",
                    ),
                    fallback_approach="Use LLM parametric knowledge for approximation",
                )

        # Stage 2: LLM judgment (only if heuristic didn't trigger)
        if gap_description and ("calculate" in gap_description.lower() or
                                "compute" in gap_description.lower() or
                                "analyze" in gap_description.lower()):
            code = await self._generate_computation_code(query, gap_description, learnings)
            if code:
                return SelfToolDecision(
                    should_execute=True,
                    reason="computation_detected_llm",
                    execution_request=ExecutionRequest(
                        language=ExecutionLanguage.PYTHON,
                        code=code,
                        timeout_s=15.0,
                        safety=ExecutionSafety.SANDBOXED,
                        description=f"Self-computation for: {query[:80]}",
                    ),
                )

        return SelfToolDecision(
            should_execute=False,
            reason="no_computation_needed",
        )

    async def _generate_computation_code(
        self,
        query: str,
        gap_description: str,
        learnings: Optional[list] = None,
    ) -> Optional[str]:
        """Generate Python code to answer a computational question.

        The generated code should:
        1. Be self-contained (no external imports beyond stdlib)
        2. Print the result to stdout
        3. Be safe (no file writes, no network)
        """
        client = self._client or get_client()

        context = ""
        if learnings:
            context = "\n".join(
                f"- {getattr(l, 'text', str(l))[:200]}"
                for l in learnings[:5]
            )

        prompt = f"""Generate a short Python script to answer this question.

Question: {query}
{"Gap to fill: " + gap_description if gap_description else ""}
{"Available context:" + chr(10) + context if context else ""}

Rules:
1. Use ONLY Python standard library (math, datetime, json, re, statistics, etc.)
2. Print the answer clearly to stdout with print()
3. No file I/O, no network calls, no subprocess
4. Keep it under 30 lines
5. Include brief comments explaining the calculation
6. If you can't write code for this, output ONLY the word "SKIP"

Output ONLY the Python code, no markdown fences, no explanation."""

        try:
            response = await client.chat_worker(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=512,
            )

            code = response.strip()

            # Clean up markdown fences if present
            if code.startswith("```"):
                code = code.split("\n", 1)[1] if "\n" in code else ""
                if code.endswith("```"):
                    code = code[:-3]
                code = code.strip()

            # Skip if LLM said it can't do it
            if code.upper().strip() == "SKIP" or len(code) < 10:
                return None

            # Basic safety: reject if it has dangerous patterns
            dangerous = ["import os", "import subprocess", "open(", "exec(", "eval("]
            for d in dangerous:
                if d in code:
                    logger.warning("Self-tool code rejected: contains '%s'", d)
                    return None

            return code

        except Exception as e:
            logger.warning("Code generation for self-tool failed: %s", e)
            return None

    async def execute_and_learn(
        self,
        decision: SelfToolDecision,
    ) -> Optional[dict]:
        """Execute the self-tool code and return the result as a learning-compatible dict.

        Returns:
            {"text": result_text, "source_url": "self_computation", "title": description}
            or None if execution failed
        """
        if not decision.should_execute or not decision.execution_request:
            return None

        result = await execute_code(decision.execution_request)

        if result.success and result.stdout.strip():
            learning_text = (
                f"[Computed] {decision.execution_request.description}\n"
                f"Result: {result.stdout.strip()}"
            )
            logger.info("Self-tool execution succeeded: %s", result.stdout.strip()[:100])
            return {
                "text": learning_text,
                "source_url": "self_computation",
                "title": f"Computation: {decision.execution_request.description[:80]}",
                "score": 0.9,  # High confidence — we computed it ourselves
            }
        else:
            logger.warning(
                "Self-tool execution failed: %s",
                result.stderr[:200] if result.stderr else "no output"
            )
            return None

    async def verify_hypothesis(
        self,
        hypothesis: str,
        test_description: str,
    ) -> tuple[bool, str]:
        """Generate and execute test code to verify a hypothesis.

        Used by pivot.py during the DISCRIMINATE step.

        Args:
            hypothesis: The hypothesis to test
            test_description: What specific test to run

        Returns:
            (confirmed: bool, explanation: str)
        """
        client = self._client or get_client()

        prompt = f"""Write a Python test to verify this hypothesis:
Hypothesis: {hypothesis}
Test: {test_description}

The script should print "CONFIRMED" or "REJECTED" followed by a brief explanation.
Use ONLY standard library. Keep under 20 lines. No file I/O or network."""

        try:
            response = await client.chat_worker(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=400,
            )

            code = response.strip()
            if code.startswith("```"):
                code = code.split("\n", 1)[1] if "\n" in code else ""
                if code.endswith("```"):
                    code = code[:-3]

            if not code or len(code) < 10:
                return False, "Could not generate test code"

            result = await execute_code(ExecutionRequest(
                language=ExecutionLanguage.PYTHON,
                code=code,
                timeout_s=10.0,
                safety=ExecutionSafety.SANDBOXED,
                description=f"Hypothesis test: {test_description[:60]}",
            ))

            if result.success:
                output = result.stdout.strip()
                confirmed = "CONFIRMED" in output.upper()
                return confirmed, output
            else:
                return False, f"Test execution failed: {result.stderr[:100]}"

        except Exception as e:
            return False, f"Hypothesis verification error: {e}"


# Module singleton
_router: Optional[SelfToolRouter] = None


def get_self_tool_router() -> SelfToolRouter:
    global _router
    if _router is None:
        _router = SelfToolRouter()
    return _router
