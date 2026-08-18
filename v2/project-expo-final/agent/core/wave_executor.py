"""
Wave Executor (Plan 1, Phase A.4)

Tracks and manages multi-wave execution. Each wave is one batch of parallel
task executions. WaveExecution collects results and learnings for each wave.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Any, Callable, Awaitable

from .task_queue import TaskQueueItem, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class WaveExecution:
    """Record of one wave's execution."""
    wave_number: int
    tasks_in_wave: list[TaskQueueItem]
    
    # Execution results
    results: dict[str, Optional[list]] = field(default_factory=dict)  # task_id -> learnings or None
    errors: dict[str, str] = field(default_factory=dict)  # task_id -> error message
    
    # Timing
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    
    # Learnings aggregation
    learnings_accumulated: list = field(default_factory=list)  # All learnings from this wave
    
    @property
    def duration_ms(self) -> float:
        """Duration of this wave in milliseconds."""
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000
    
    @property
    def success_count(self) -> int:
        """Number of successfully completed tasks."""
        return sum(1 for v in self.results.values() if v is not None)
    
    @property
    def failure_count(self) -> int:
        """Number of failed tasks."""
        return sum(1 for v in self.results.values() if v is None)
    
    @property
    def success_rate(self) -> float:
        """Success rate as percentage."""
        total = len(self.results)
        if total == 0:
            return 0.0
        return (self.success_count / total) * 100
    
    def mark_complete(self):
        """Mark wave as finished."""
        self.end_time = time.time()
    
    def add_result(self, task_id: str, learnings: Optional[list], error: Optional[str] = None):
        """Record result of one task."""
        self.results[task_id] = learnings
        if error:
            self.errors[task_id] = error
        if learnings:
            self.learnings_accumulated.extend(learnings)
    
    def get_summary(self) -> dict:
        """Get summary of this wave for logging."""
        return {
            "wave": self.wave_number,
            "tasks": len(self.tasks_in_wave),
            "success": self.success_count,
            "failures": self.failure_count,
            "success_rate": f"{self.success_rate:.1f}%",
            "duration_ms": f"{self.duration_ms:.0f}",
            "learnings_count": len(self.learnings_accumulated),
            "task_ids": [t.task_id for t in self.tasks_in_wave],
        }


class WaveExecutor:
    """Manages parallel execution of tasks within each wave.
    
    Handles:
    - Running multiple tasks in parallel (respecting parallelization limit)
    - Collecting results and learnings
    - Tracking execution history across waves
    - Failure recovery (failed tasks don't block entire wave)
    """
    
    def __init__(self):
        self.waves: list[WaveExecution] = []
        self.total_learnings: list = []
    
    async def execute_wave(
        self,
        wave_num: int,
        tasks: list[TaskQueueItem],
        execute_task_fn: Callable[[TaskQueueItem], Awaitable[Optional[list]]],
        timeout_per_task: float = 60.0,
    ) -> WaveExecution:
        """
        Execute one wave of tasks in parallel.
        
        Args:
            wave_num: Wave number (1, 2, 3, ...)
            tasks: List of TaskQueueItem to execute
            execute_task_fn: Async function that executes a task and returns learnings or None
            timeout_per_task: Timeout per task in seconds
        
        Returns:
            WaveExecution with results
        """
        if not tasks:
            logger.warning(f"Wave {wave_num}: No tasks to execute")
            wave = WaveExecution(wave_number=wave_num, tasks_in_wave=[])
            wave.mark_complete()
            return wave
        
        logger.info(f"🌊 Wave {wave_num}: Starting {len(tasks)} parallel task(s)")
        
        wave = WaveExecution(wave_number=wave_num, tasks_in_wave=tasks)
        
        # Execute all tasks in parallel
        async def execute_with_timeout(task: TaskQueueItem) -> tuple[str, Optional[list], Optional[str]]:
            """Execute one task with timeout and error handling."""
            task_id = task.task_id
            try:
                logger.debug(f"  → Task {task_id}: starting")
                result = await asyncio.wait_for(
                    execute_task_fn(task),
                    timeout=timeout_per_task,
                )
                logger.debug(f"  ✓ Task {task_id}: completed, {len(result or [])} learnings")
                return (task_id, result, None)
            except asyncio.TimeoutError:
                error = f"Task timed out after {timeout_per_task}s"
                logger.error(f"  ✗ Task {task_id}: {error}")
                return (task_id, None, error)
            except Exception as e:
                error = f"Task failed: {str(e)}"
                logger.error(f"  ✗ Task {task_id}: {error}")
                return (task_id, None, error)
        
        # Run all tasks concurrently
        task_coros = [execute_with_timeout(t) for t in tasks]
        results_raw = await asyncio.gather(*task_coros, return_exceptions=False)
        
        # Collect results
        for task_id, learnings, error in results_raw:
            wave.add_result(task_id, learnings, error)
        
        wave.mark_complete()
        
        # Log wave summary
        summary = wave.get_summary()
        logger.info(
            f"✓ Wave {wave_num} complete: {summary['tasks']} tasks, "
            f"{summary['success']} success, {summary['failures']} failed, "
            f"success_rate={summary['success_rate']}, "
            f"learnings={summary['learnings_count']}, "
            f"duration={summary['duration_ms']}ms"
        )
        
        # Track wave
        self.waves.append(wave)
        self.total_learnings.extend(wave.learnings_accumulated)
        
        return wave
    
    def get_wave_history(self) -> list[dict]:
        """Get summary of all completed waves."""
        return [w.get_summary() for w in self.waves]
    
    def get_total_learnings(self) -> list:
        """Get all learnings accumulated across all waves."""
        return self.total_learnings
    
    def get_all_tasks_executed(self) -> list[str]:
        """Get all task IDs that have been executed."""
        all_tasks = []
        for wave in self.waves:
            all_tasks.extend([t.task_id for t in wave.tasks_in_wave])
        return all_tasks
    
    def get_failed_tasks(self) -> dict[str, str]:
        """Get all failed tasks and their error messages."""
        failed = {}
        for wave in self.waves:
            failed.update(wave.errors)
        return failed
    
    def get_execution_report(self) -> dict:
        """Get comprehensive execution report."""
        total_tasks = sum(len(w.tasks_in_wave) for w in self.waves)
        total_success = sum(w.success_count for w in self.waves)
        total_failures = sum(w.failure_count for w in self.waves)
        
        return {
            "total_waves": len(self.waves),
            "total_tasks_executed": total_tasks,
            "total_success": total_success,
            "total_failures": total_failures,
            "overall_success_rate": f"{(total_success / total_tasks * 100) if total_tasks > 0 else 0:.1f}%",
            "total_learnings": len(self.total_learnings),
            "wave_summaries": self.get_wave_history(),
        }
