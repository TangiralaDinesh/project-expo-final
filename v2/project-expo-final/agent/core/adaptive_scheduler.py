"""
Adaptive Scheduler (Plan 1, Phase B)

Decides which tasks to execute in the next wave based on dependencies,
priorities, and parallelization constraints.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .task_queue import TaskQueue, TaskQueueItem, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class SchedulingDecision:
    """Decision for what to execute in the next wave."""
    wave_number: int
    tasks_to_execute: list[TaskQueueItem] = field(default_factory=list)
    blocked_tasks: list[str] = field(default_factory=list)  # task_ids blocked by failures
    reasoning: str = ""  # Why these tasks?
    
    @property
    def is_final_wave(self) -> bool:
        """True if this wave includes final synthesis/completion task."""
        return any("synthesis" in t.task_description.lower() for t in self.tasks_to_execute)
    
    @property
    def parallelization_level(self) -> int:
        """Number of parallel tasks to execute."""
        return len(self.tasks_to_execute)


class AdaptiveScheduler:
    """Determines which tasks execute in each wave.
    
    Works with TaskQueue + TaskReflectionEngine to implement dynamic scheduling:
    1. Get tasks ready to execute (dependencies met)
    2. Sort by priority
    3. Respect parallelization limit
    4. Handle failures and cascading blocks
    """
    
    def __init__(self, parallelization_limit: int = 3):
        self.parallelization_limit = parallelization_limit
        self.wave_count = 0
    
    def schedule_next_wave(
        self,
        queue: TaskQueue,
        parallelization_limit: Optional[int] = None,
    ) -> SchedulingDecision:
        """
        Decide which tasks to execute next.
        
        Args:
            queue: TaskQueue to schedule from
            parallelization_limit: Override default limit for this wave
        
        Returns:
            SchedulingDecision with tasks ready to execute
        """
        self.wave_count += 1
        limit = parallelization_limit or self.parallelization_limit
        
        # Get tasks ready to execute
        next_tasks = queue.get_next_wave(max_parallel=limit)
        
        # Find blocked tasks (dependencies failed)
        all_tasks = queue.get_all_tasks()
        blocked = [t.task_id for t in all_tasks if t.status == TaskStatus.BLOCKED]
        
        reasoning = (
            f"Scheduled {len(next_tasks)} tasks (parallelization={len(next_tasks)}). "
            f"Queue has {len(queue.get_pending_tasks())} pending, "
            f"{len(blocked)} blocked."
        )
        
        decision = SchedulingDecision(
            wave_number=self.wave_count,
            tasks_to_execute=next_tasks,
            blocked_tasks=blocked,
            reasoning=reasoning,
        )
        
        logger.info(f"Wave {self.wave_count}: Scheduling {len(next_tasks)} tasks. {reasoning}")
        
        return decision
    
    def update_and_reschedule(
        self,
        queue: TaskQueue,
        wave_results: dict[str, Optional[list]],  # task_id -> learnings or None if failed
        learnings: list,
    ) -> SchedulingDecision:
        """
        Update queue based on wave results and get next wave.
        
        Args:
            queue: TaskQueue to update
            wave_results: {task_id: learnings} or {task_id: None} for failures
            learnings: Combined learnings from this wave (used for reflection)
        
        Returns:
            Next SchedulingDecision
        """
        # Update queue with results
        for task_id, result in wave_results.items():
            if result is None:
                queue.mark_failed(task_id, "execution_failed")
            else:
                queue.mark_done(task_id, result)
        
        # Add learnings to queue state
        for learning in learnings:
            queue.state.add_learning(learning)
        
        # Proceed to next wave
        return self.schedule_next_wave(queue)
    
    def can_continue(self, queue: TaskQueue) -> bool:
        """Check if there are more tasks to execute."""
        return not queue.is_empty()


class AdaptiveParallelizer:
    """Manages adaptive parallelization level.
    
    Can increase/decrease parallelization based on:
    - Error rates
    - Response times
    - System load
    - User preferences
    
    For now: fixed parallelization, future work to make truly adaptive.
    """
    
    def __init__(self, initial_level: int = 3):
        self.current_level = initial_level
        self.min_level = 1
        self.max_level = 8
        
        self.error_count = 0
        self.success_count = 0
    
    def record_success(self, task_id: str):
        """Task completed successfully."""
        self.success_count += 1
        self.error_count = max(0, self.error_count - 1)  # Recovery
    
    def record_failure(self, task_id: str):
        """Task failed."""
        self.error_count += 1
        
        # If error rate > 30%, reduce parallelization
        if self.error_count > self.success_count * 0.3:
            self.current_level = max(self.min_level, self.current_level - 1)
            logger.warning(f"High error rate, reducing parallelization to {self.current_level}")
    
    def get_parallelization_level(self) -> int:
        """Get current suggested parallelization level."""
        return self.current_level
    
    def reset(self):
        """Reset counters for next batch."""
        self.error_count = 0
        self.success_count = 0
