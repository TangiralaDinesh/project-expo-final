"""
Scatter-Gather-Merge Pattern for Aspect-Based Parallel Retrieval (Phase 4).

Coordinates parallel aspect retrieval and merges results hierarchically.

Flow:
1. SCATTER: Fire retrieval tasks for each aspect in parallel
2. GATHER: Collect results as they complete  
3. MERGE: Combine results preserving aspect structure
4. RANK: Re-rank by relevance + importance across aspects
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Awaitable

from ..core.types import SubagentInput, SubagentResult, Learning
from ..core.aspect_extractor import Aspect, AspectExtractionResult

logger = logging.getLogger(__name__)


@dataclass
class AspectRetrievalResult:
    """Result of retrieving one aspect."""
    aspect: Aspect
    learnings: list[str]
    source_urls: list[str]
    success: bool
    error: Optional[str] = None
    token_count: int = 0


async def scatter_gather_retrieve(
    query: str,
    aspects: list[Aspect],
    run_subagent_fn: Callable[[SubagentInput], Awaitable[SubagentResult]],
) -> tuple[list[Learning], list[str]]:
    """
    Scatter-gather-merge pattern for parallel aspect retrieval.
    
    Args:
        query: Original user query
        aspects: List of aspects to retrieve in parallel
        run_subagent_fn: Function to execute retrieval subagent
    
    Returns:
        (merged_learnings, unique_urls) - results combined across all aspects
    """
    
    # SCATTER: Create retrieval tasks for each aspect
    retrieval_tasks = []
    aspect_to_task: dict[Aspect, asyncio.Task] = {}
    
    for aspect in aspects:
        # Create task input for this aspect
        task_input = SubagentInput(
            query=f"{aspect.description} for: {query}",
            mode="public",
            payload={
                "original_query": query,
                "aspect_name": aspect.name,
                "aspect_description": aspect.description,
                "depth_target": aspect.depth_target,
            }
        )
        
        # Fire off the task
        task = asyncio.create_task(_retrieve_aspect(aspect, task_input, run_subagent_fn))
        retrieval_tasks.append(task)
        aspect_to_task[task] = aspect
    
    if not retrieval_tasks:
        return [], []
    
    # GATHER: Collect results as they complete
    all_learnings: list[Learning] = []
    all_urls: list[str] = []
    aspect_results: dict[str, AspectRetrievalResult] = {}
    
    logger.info(f"🔄 Scatter-Gather: Parallel retrieving {len(retrieval_tasks)} aspects...")
    
    for coro in asyncio.as_completed(retrieval_tasks, timeout=30):
        try:
            result = await coro
            aspect_name = result.aspect.name
            
            aspect_results[aspect_name] = result
            
            if result.success:
                # Create Learning objects from retrieved text
                for learning_text in result.learnings:
                    learning = Learning(
                        text=learning_text,
                        source_url=result.source_urls[0] if result.source_urls else "internal",
                        confidence=0.8,
                        is_code=False,
                        metadata={
                            "aspect_source": aspect_name,
                            "aspect_priority": result.aspect.priority,
                            "aspect_depth_target": result.aspect.depth_target,
                        }
                    )
                    all_learnings.append(learning)
                
                all_urls.extend(result.source_urls)
                
                logger.info(
                    f"✅ Aspect '{aspect_name}' complete: "
                    f"{len(result.learnings)} learnings, priority={result.aspect.priority:.2f}"
                )
            else:
                logger.warning(
                    f"❌ Aspect '{aspect_name}' retrieval failed: {result.error}"
                )
        
        except asyncio.TimeoutError:
            logger.warning(f"⏱️  Aspect retrieval timed out after 30s")
        except Exception as e:
            logger.error(f"❌ Aspect retrieval exception: {e}")
    
    # MERGE: Combine results preserving aspect hierarchy
    merged_learnings = _merge_with_aspect_metadata(all_learnings, aspect_results)
    
    # Deduplicate URLs
    unique_urls = list(set(all_urls))
    
    logger.info(
        f"✓ Gather complete: {len(merged_learnings)} learnings from "
        f"{len(aspect_results)} aspects"
    )
    
    return merged_learnings, unique_urls


async def _retrieve_aspect(
    aspect: Aspect,
    task_input: SubagentInput,
    run_subagent_fn: Callable[[SubagentInput], Awaitable[SubagentResult]],
) -> AspectRetrievalResult:
    """Retrieve one aspect."""
    try:
        result = await run_subagent_fn(task_input)
        
        # Extract learnings from result
        learnings = []
        source_urls = []
        
        if result.learnings:
            learnings = [l.text if isinstance(l, Learning) else str(l) for l in result.learnings]
        
        if result.source_urls:
            source_urls = result.source_urls
        
        return AspectRetrievalResult(
            aspect=aspect,
            learnings=learnings,
            source_urls=source_urls,
            success=result.success,
            error=result.error,
            token_count=sum(len(l.split()) for l in learnings)
        )
    except Exception as e:
        logger.error(f"Aspect retrieval exception for {aspect.name}: {e}")
        return AspectRetrievalResult(
            aspect=aspect,
            learnings=[],
            source_urls=[],
            success=False,
            error=str(e),
        )


def _merge_with_aspect_metadata(
    learnings: list[Learning],
    aspect_results: dict[str, AspectRetrievalResult],
) -> list[Learning]:
    """Merge learnings and add aspect metadata to each."""
    merged = []
    
    for learning in learnings:
        # Aspect source should already be in metadata from scatter phase
        aspect_source = learning.metadata.get("aspect_source")
        
        if aspect_source and aspect_source in aspect_results:
            aspect_result = aspect_results[aspect_source]
            
            # Enhance metadata
            learning.metadata.update({
                "aspect_name": aspect_source,
                "aspect_priority": aspect_result.aspect.priority,
                "aspect_depth_target": aspect_result.aspect.depth_target,
                "aspect_category": aspect_result.aspect.category.value,
            })
        
        merged.append(learning)
    
    # Re-rank by aspect priority (primary) then by confidence (secondary)
    merged.sort(
        key=lambda l: (
            -(l.metadata.get("aspect_priority", 0) or 0),  # Higher priority first
            -(l.confidence or 0),  # Higher confidence second
        )
    )
    
    return merged
