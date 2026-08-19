"""
Wave Executor - Progressive multi-wave retrieval system.

Phase 1B: Manages Wave 2 retrieval for unsatisfied concepts.

When Wave 1 retrieval leaves concepts with satisfaction < 0.7,
Wave 2 is triggered with focused queries on those concepts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable, Optional, Any

from ..core.types import SubagentInput, SubagentResult, SubagentType
from ..llm.client import NIMClient, get_client

logger = logging.getLogger(__name__)


async def execute_wave_2(
    tasks: list[str],
    run_subagent: Callable[[SubagentInput], Awaitable[SubagentResult]],
    client: Optional[NIMClient] = None,
    thinking_profile: Optional[Any] = None,
) -> list[SubagentResult]:
    """
    Execute Wave 2 retrieval for unsatisfied concepts.
    
    Args:
        tasks: List of focused retrieval queries (one per unsatisfied concept)
        run_subagent: Subagent executor function
        client: LLM client
        thinking_profile: Current thinking profile for context
    
    Returns:
        List of SubagentResult from all Wave 2 retrievers
    
    Wave 2 is NARROWER than Wave 1:
    - Focuses on specific concepts
    - Uses targeted queries
    - Runs in parallel
    - Results merged with Wave 1 before synthesis
    """
    
    client = client or get_client()
    
    if not tasks:
        logger.debug("No Wave 2 tasks, returning empty results")
        return []
    
    logger.info("Starting Wave 2 retrieval with %d tasks", len(tasks))
    
    # Create parallel retriever nodes for each task
    async def run_wave_2_task(task: str, task_id: int) -> SubagentResult:
        """Run a single Wave 2 retrieval task."""
        logger.debug("Wave 2 Task %d: %s", task_id, task)
        
        # Create input for retriever subagent
        subagent_input = SubagentInput(
            query=task,
            subagent_type=SubagentType.RETRIEVER,
            task_description=f"Wave 2 focused retrieval: {task}",
            parent_task_id=f"wave_2_{task_id}",
            context_from_previous="Wave 1 results were thin on this concept",
            budget_s=15,  # Wave 2 gets shorter budget
        )
        
        try:
            result = await run_subagent(subagent_input)
            logger.debug(
                "Wave 2 Task %d complete: %d learnings from %d sources",
                task_id,
                len(result.learnings),
                len(result.source_urls),
            )
            return result
        except Exception as e:
            logger.warning("Wave 2 Task %d failed: %s", task_id, e)
            # Return empty result on failure, don't break the flow
            return SubagentResult(
                query=task,
                learnings=[],
                source_urls=[],
                error=str(e),
            )
    
    # Run all Wave 2 tasks in parallel
    wave_2_results = await asyncio.gather(
        *[run_wave_2_task(task, i) for i, task in enumerate(tasks)]
    )
    
    logger.info(
        "Wave 2 complete: %d total learnings from %d tasks",
        sum(len(r.learnings) for r in wave_2_results),
        len(wave_2_results),
    )
    
    return wave_2_results
