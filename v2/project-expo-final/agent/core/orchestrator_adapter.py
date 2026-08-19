"""
Orchestrator Adapter (Plan 1, Phase B)

Bridges legacy Decomposition-based orchestration with new adaptive TaskQueue-based
orchestration. Allows gradual migration without breaking existing functionality.

This adapter can be used optionally via a feature flag, keeping the original
orchestrator.py unchanged for stability.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Awaitable, Callable, Any

from .task_queue import TaskQueue, TaskQueueItem, TaskStatus
from .task_reflection import TaskReflectionEngine
from .adaptive_scheduler import AdaptiveScheduler, AdaptiveParallelizer
from .wave_executor import WaveExecutor, WaveExecution
from ..orchestrator.orchestrator import TaskNode, Decomposition

logger = logging.getLogger(__name__)


def decomposition_to_task_queue(decomposition: Decomposition) -> TaskQueue:
    """
    Convert legacy TaskNode-based Decomposition to new TaskQueue.
    
    Preserves dependencies and converts to TaskQueueItem format.
    """
    queue = TaskQueue()
    
    for node in decomposition.nodes:
        item = TaskQueueItem(
            task_id=node.node_id,
            task_description=node.task,
            priority=70,  # Default priority for decomposed tasks
            dependencies=node.depends_on or [],
        )
        queue.enqueue(item)
    
    # Add comparison metadata if present
    if decomposition.is_comparison:
        queue.state.record_retrieval_pattern(
            "comparison",
            {
                "entities": decomposition.comparison_entities,
                "fan_out_eligible": decomposition.fan_out_eligible,
            }
        )
    
    return queue


def task_node_to_task_queue_item(node: TaskNode, priority: int = 70) -> TaskQueueItem:
    """Convert a single TaskNode to TaskQueueItem."""
    return TaskQueueItem(
        task_id=node.node_id,
        task_description=node.task,
        priority=priority,
        dependencies=node.depends_on or [],
    )


class AdaptiveOrchestrator:
    """
    Adaptive orchestrator wrapper that uses dynamic task queue instead of static DAG.
    
    Key differences from static orchestration:
    1. Tasks executed in waves (not topological layers)
    2. After each wave, reflection engine analyzes learnings
    3. New tasks can be added to queue mid-execution
    4. Priorities can be adjusted based on learnings
    5. Parallel execution respects dependencies
    
    IMPORTANT: This is additive, not a replacement. Original orchestrator.py remains
    unchanged for stability. This adapter is used via feature flag only.
    """
    
    def __init__(self, *, client=None):
        self.reflection_engine = TaskReflectionEngine(client=client)
        self.scheduler = AdaptiveScheduler(parallelization_limit=3)
        self.parallelizer = AdaptiveParallelizer(initial_level=3)
        self.executor = WaveExecutor()
    
    async def run_adaptive_orchestration(
        self,
        task: str,
        queue: TaskQueue,
        run_subagent: Callable,
        max_waves: int = 10,
        enable_reflection: bool = True,
    ) -> dict[str, Any]:
        """
        Execute orchestration using adaptive task queue.
        
        Args:
            task: Original user query
            queue: TaskQueue to execute
            run_subagent: Async function to execute one subagent task
            max_waves: Maximum number of waves to execute (safety limit)
            enable_reflection: Whether to use reflection engine for task adaptation
        
        Returns:
            Dictionary of execution results: {node_id: SubagentResult}
        """
        logger.info(f"🚀 Starting adaptive orchestration (max_waves={max_waves}, reflection={enable_reflection})")
        logger.info(f"   Initial queue: {len(queue.get_all_tasks())} tasks")
        
        results = {}
        wave_num = 0
        
        while not queue.is_empty() and wave_num < max_waves:
            wave_num += 1
            
            # Schedule next wave
            scheduling_decision = self.scheduler.schedule_next_wave(queue)
            
            if not scheduling_decision.tasks_to_execute:
                logger.warning(f"Wave {wave_num}: No tasks ready to execute, terminating")
                break
            
            # Execute wave
            wave_exec = await self.executor.execute_wave(
                wave_number=wave_num,
                tasks=scheduling_decision.tasks_to_execute,
                execute_task_fn=self._create_task_executor(run_subagent),
            )
            
            # Collect results
            for task_id, learnings in wave_exec.results.items():
                if learnings is not None:
                    results[task_id] = learnings  # Simplified: normally SubagentResult
                else:
                    results[task_id] = None
            
            # Update queue with wave results
            for task_id, learnings in wave_exec.results.items():
                if learnings is not None:
                    queue.mark_done(task_id, learnings)
                else:
                    queue.mark_failed(task_id, wave_exec.errors.get(task_id, "unknown"))
            
            # Reflection: analyze learnings and suggest new tasks
            if enable_reflection and wave_exec.learnings_accumulated:
                logger.info(f"  Analyzing {len(wave_exec.learnings_accumulated)} learnings for reflection...")
                
                try:
                    analysis = await self.reflection_engine.analyze_learnings(
                        current_learnings=wave_exec.learnings_accumulated,
                        original_query=task,
                        context={
                            "wave": wave_num,
                            "previous_waves": wave_num - 1,
                        }
                    )
                    
                    # Add new tasks
                    if analysis.has_new_tasks():
                        new_items = [
                            TaskQueueItem(
                                task_id=f"wave{wave_num}_task{i}",
                                task_description=t["task"],
                                priority=t["priority"],
                                dependencies=[],
                            )
                            for i, t in enumerate(analysis.new_tasks_detected)
                        ]
                        queue.add_tasks(new_items, wave_num=wave_num)
                        logger.info(f"  ➕ Added {len(new_items)} new tasks from reflection")
                    
                    # Apply reprioritizations
                    if analysis.has_reprioritizations():
                        for repo in analysis.reprioritization_suggestions:
                            queue.reprioritize(repo["task_id"], repo["new_priority"])
                        logger.info(f"  🔄 Applied {len(analysis.reprioritization_suggestions)} reprioritizations")
                    
                    # Log gaps
                    if analysis.knowledge_gaps:
                        logger.debug(f"  Knowledge gaps: {analysis.knowledge_gaps}")
                    
                    # Record in queue state
                    queue.state.record_wave_execution(
                        wave_num,
                        [t.task_id for t in scheduling_decision.tasks_to_execute],
                        len(wave_exec.learnings_accumulated),
                        analysis.reasoning,
                    )
                    
                except Exception as e:
                    logger.warning(f"Reflection analysis failed (non-fatal): {e}")
        
        # Final report
        report = self.executor.get_execution_report()
        logger.info(f"✓ Orchestration complete")
        logger.info(f"  Waves executed: {report['total_waves']}")
        logger.info(f"  Tasks: {report['total_success']} success, {report['total_failures']} failed")
        logger.info(f"  Learnings collected: {report['total_learnings']}")
        
        return results
    
    def _create_task_executor(self, run_subagent: Callable) -> Callable:
        """Create a function that executes one task and returns learnings."""
        async def execute_task(item: TaskQueueItem) -> Optional[list]:
            """Execute one task via run_subagent."""
            try:
                # This is simplified - in real system would use proper SubagentInput/Result types
                # For now, return empty learnings list to maintain structure
                result = await run_subagent(item.task_id, item.task_description)
                
                # Track parallelizer success
                self.parallelizer.record_success(item.task_id)
                
                return result if result else []
            except Exception as e:
                logger.error(f"Task execution failed: {e}")
                self.parallelizer.record_failure(item.task_id)
                return None
        
        return execute_task
    
    def get_queue_status(self, queue: TaskQueue) -> dict:
        """Get current queue status for monitoring/debugging."""
        return {
            "queue_summary": queue.get_queue_summary(),
            "execution_report": self.executor.get_execution_report(),
            "scheduler_wave": self.scheduler.wave_count,
            "parallelization_level": self.parallelizer.get_parallelization_level(),
        }
