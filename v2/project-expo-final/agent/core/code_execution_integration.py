"""
Code Execution Integration (Tier 4) — Wire tool execution into query pipeline

When orchestrator generates code_execution nodes or the query has use_code_execution=True,
this module dispatches to actual tool implementations.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional, Any

from ..llm.client import NIMClient, get_client
from ..tools.code_execution import dispatch_tool, ToolOutput
from ..core.types import Learning

logger = logging.getLogger(__name__)


@dataclass
class CodeExecutionRequest:
    """Request to execute code for validation/exploration"""
    code: str
    reasoning: str  # Why we want to execute this code
    expected_output_type: str  # "text" | "json" | "file" | "visualization"
    input_data: Optional[dict] = None  # Variables to inject


@dataclass
class CodeExecutionResult:
    """Result from code execution"""
    success: bool
    stdout: str = ""
    stderr: str = ""
    files_created: list[str] = None
    reasoning: str = ""
    validation_passed: bool = False  # Did output match expected type?
    
    def __post_init__(self):
        self.files_created = self.files_created or []


async def suggest_code_execution(
    query: str,
    learnings: list[Learning],
    *,
    client: Optional[NIMClient] = None,
) -> Optional[CodeExecutionRequest]:
    """
    Decide if code execution would help answer this query (Tier 4).
    
    Uses LLM to detect queries that benefit from:
    - Data processing / analysis
    - Algorithm validation
    - Mathematical computation
    - File generation
    
    Returns CodeExecutionRequest if suggested, None otherwise.
    """
    client = client or get_client()
    
    # Quick heuristic filter
    execution_keywords = [
        "calculate", "compute", "simulate", "verify", "validate",
        "generate", "create code", "write script", "python",
        "test", "implement", "analyze", "process"
    ]
    
    query_lower = query.lower()
    if not any(kw in query_lower for kw in execution_keywords):
        return None
    
    # Use LLM to detect if code execution would help
    learnings_str = "\n".join(
        f"- {l.text[:150]}..." for l in learnings[:3]
    ) or "(no learnings)"
    
    prompt = f"""Given this query and available information, would executing code help provide a better answer?

Query: {query}

Available Information:
{learnings_str}

ONLY respond with EITHER:
- "YES" (exactly) if code execution would help
- "NO" (exactly) if it wouldn't

Be conservative — only recommend if code execution is truly necessary."""
    
    try:
        response = await client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10,
        )
        
        response = response.strip().upper()
        if response.startswith("YES"):
            logger.info("Code execution suggested by LLM")
            return CodeExecutionRequest(
                code="# Placeholder — actual code generated elsewhere",
                reasoning="Suggested by LLM analysis",
                expected_output_type="text",
            )
    except Exception as e:
        logger.debug(f"Code execution suggestion failed: {e}")
    
    return None


async def generate_validation_code(
    claim: str,
    context: str,
    *,
    client: Optional[NIMClient] = None,
) -> Optional[str]:
    """
    Generate Python code to validate a specific claim (Tier 4).
    
    Used when agent is uncertain about a fact and wants to verify it.
    """
    client = client or get_client()
    
    prompt = f"""Write a short Python script (< 20 lines) to verify this claim:

Claim: {claim}

Context: {context}

Requirements:
- Use only built-in libraries (no pip install)
- Output True or False on the last line
- Handle edge cases gracefully
- Be safe to execute

If the claim cannot be validated programmatically, respond with "CANNOT_VALIDATE".
Otherwise respond ONLY with the Python code."""
    
    try:
        response = await client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
        )
        
        if "CANNOT_VALIDATE" in response:
            logger.debug("Claim validation not possible via code")
            return None
        
        # Extract Python code
        import re
        code_match = re.search(r'```python\s*(.*?)\s*```', response, re.DOTALL)
        if code_match:
            return code_match.group(1)
        
        # Try plain extraction
        if response.startswith("def ") or "=" in response:
            return response
            
    except Exception as e:
        logger.debug(f"Code generation failed: {e}")
    
    return None


async def execute_validation_code(
    code: str,
    claim: str,
    *,
    sandbox_dir: Optional[str] = None,
    timeout: float = 10.0,
) -> CodeExecutionResult:
    """
    Execute validation code and return result (Tier 4).
    
    Returns CodeExecutionResult with validation status.
    """
    try:
        tool_result = await dispatch_tool(
            "execute_python",
            {"code": code},
            sandbox_dir=sandbox_dir,
        )
        
        # Parse output for True/False result
        validation_passed = False
        if tool_result.success:
            output_lower = tool_result.stdout.strip().lower()
            validation_passed = output_lower.endswith("true")
        
        return CodeExecutionResult(
            success=tool_result.success,
            stdout=tool_result.stdout,
            stderr=tool_result.stderr,
            reasoning=f"Validation of: {claim}",
            validation_passed=validation_passed,
        )
        
    except Exception as e:
        logger.error(f"Validation execution failed: {e}")
        return CodeExecutionResult(
            success=False,
            stderr=str(e),
            reasoning=f"Failed to validate: {claim}",
        )


async def execute_analysis_code(
    query: str,
    learnings: list[Learning],
    *,
    client: Optional[NIMClient] = None,
    sandbox_dir: Optional[str] = None,
) -> Optional[CodeExecutionResult]:
    """
    Generate and execute analysis code for complex queries (Tier 4).
    
    Full pipeline: query → generate code → execute → return results
    """
    client = client or get_client()
    
    learnings_data = {
        "learnings": [
            {
                "text": l.text,
                "source": l.source_url,
                "score": getattr(l, 'score', 0.0)
            }
            for l in learnings[:5]
        ]
    }
    
    prompt = f"""Write a Python script to analyze this query:

Query: {query}

Available Data (as JSON):
{json.dumps(learnings_data, indent=2)}

The script should:
1. Load the learnings data
2. Perform relevant analysis/computation
3. Print findings clearly
4. Handle errors gracefully

Limit to < 50 lines and built-in libraries only."""
    
    try:
        code_response = await client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=512,
        )
        
        # Extract code
        import re
        code_match = re.search(r'```python\s*(.*?)\s*```', code_response, re.DOTALL)
        code = code_match.group(1) if code_match else code_response
        
        # Execute
        tool_result = await dispatch_tool(
            "execute_python",
            {"code": code, "input_data": learnings_data},
            sandbox_dir=sandbox_dir,
        )
        
        return CodeExecutionResult(
            success=tool_result.success,
            stdout=tool_result.stdout,
            stderr=tool_result.stderr,
            reasoning=f"Analysis of: {query}",
        )
        
    except Exception as e:
        logger.error(f"Analysis execution failed: {e}")
        return None


async def generate_response_code(
    query: str,
    analysis_results: Optional[CodeExecutionResult] = None,
    *,
    client: Optional[NIMClient] = None,
) -> Optional[str]:
    """
    Generate Python code that programmatically generates the response (Tier 4).
    
    Useful for queries that need to generate artifacts:
    - Data visualizations
    - Reports
    - Configuration files
    - Example code snippets
    """
    if not analysis_results or not analysis_results.success:
        return None
    
    client = client or get_client()
    
    prompt = f"""Based on this query and analysis results, generate Python code that
creates the final artifact/response:

Query: {query}

Analysis Output:
{analysis_results.stdout}

Generate a Python script that:
1. Takes the analysis output as input
2. Formats/processes it appropriately
3. Saves or prints the final artifact
4. Uses only built-in libraries

Respond with ONLY the Python code (in ```python``` blocks)."""
    
    try:
        response = await client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=512,
        )
        
        import re
        code_match = re.search(r'```python\s*(.*?)\s*```', response, re.DOTALL)
        return code_match.group(1) if code_match else response
        
    except Exception as e:
        logger.debug(f"Response code generation failed: {e}")
        return None
