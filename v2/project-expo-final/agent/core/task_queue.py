"""
Adaptive Task Queue System (Plan 1)

Core data structures for dynamic prioritization and wave-based execution.
Replaces static DAG decomposition with adaptive task queue that reorders/adds tasks
based on learnings from each retrieval wave.

Key classes:
- TaskQueueItem: One task with priority, dependencies, status
- TaskQueue: Maintains ordered list of tasks, supports enqueue/dequeue/reprioritize
- QueueState: Tracks cumulative learnings, execution history
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Lifecycle of a task in the queue."""
    QUEUED = "queued"          # Waiting to execute
    IN_PROGRESS = "in_progress"  # Currently running
    DONE = "done"              # Successfully completed
    FAILED = "failed"          # Execution failed (not retried)
    BLOCKED = "blocked"        # Depends on task that failed


@dataclass
class TaskQueueItem:
    """One task in the adaptive queue."""
    task_id: str                # Unique identifier (e.g., "n1", "compare_entity_0")
    task_description: str       # Human-readable task description
    priority: int              # 0-100, higher = execute sooner
    dependencies: list[str] = field(default_factory=list)  # task_ids this depends on
    status: TaskStatus = TaskStatus.QUEUED
    
    # Metadata
    wave_added_at: int = 0     # Which wave was this task added (0 = initial)
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    
    # Execution results
    result_learnings: list = field(default_factory=list)  # Learning objects from execution
    error_reason: str = ""     # If status=FAILED, why?
    
    def __lt__(self, other: TaskQueueItem) -> bool:
        """Support sorting by priority (higher priority first)."""
        if self.priority != other.priority:
            return self.priority > other.priority  # Reverse for higher priority first
        # Tiebreaker: tasks added earlier have priority
        return self.wave_added_at < other.wave_added_at
    
    def is_ready(self, completed_tasks: set[str]) -> bool:
        """Check if all dependencies are done."""
        if self.status != TaskStatus.QUEUED:
            return False
        # All dependencies must be in completed_tasks
        return all(dep_id in completed_tasks for dep_id in self.dependencies)
    
    def mark_in_progress(self):
        self.status = TaskStatus.IN_PROGRESS
    
    def mark_done(self, learnings: list = None):
        self.status = TaskStatus.DONE
        self.completed_at = time.time()
        if learnings:
            self.result_learnings = learnings
    
    def mark_failed(self, error: str):
        self.status = TaskStatus.FAILED
        self.error_reason = error
        self.completed_at = time.time()
    
    def mark_blocked(self, reason: str):
        self.status = TaskStatus.BLOCKED
        self.error_reason = reason


@dataclass
class QueueState:
    """Tracks cumulative queue evolution and learnings."""
    total_learnings: list = field(default_factory=list)  # All learnings from all waves
    retrieval_patterns: dict = field(default_factory=dict)  # What was searched/how
    execution_history: list = field(default_factory=list)  # [wave_1_summary, wave_2_summary, ...]
    
    # Satisfaction/feedback
    user_satisfaction_score: float = 0.5  # 0-1 scale, affects future prioritization
    
    def add_learning(self, learning_obj):
        """Add a learning to cumulative pool."""
        self.total_learnings.append(learning_obj)
    
    def record_retrieval_pattern(self, pattern_name: str, details: dict):
        """Log what kind of retrieval happened."""
        if pattern_name not in self.retrieval_patterns:
            self.retrieval_patterns[pattern_name] = []
        self.retrieval_patterns[pattern_name].append(details)
    
    def record_wave_execution(self, wave_num: int, tasks_executed: list[str],
                             learnings_discovered: int, reflection_output: str):
        """Log summary of a wave's execution."""
        self.execution_history.append({
            "wave": wave_num,
            "tasks": tasks_executed,
            "learnings_count": learnings_discovered,
            "reflection": reflection_output,
            "timestamp": time.time(),
        })


class TaskQueue:
    """Adaptive priority task queue for dynamic orchestration.
    
    Replaces static DAG approach with dynamic prioritization that changes
    based on learnings from each wave. Tasks can be added, reprioritized,
    or reordered mid-execution.
    """
    
    def __init__(self):
        self._items: dict[str, TaskQueueItem] = {}  # task_id -> TaskQueueItem
        self._completed: set[str] = set()  # task_ids that finished (DONE or FAILED)
        self.state = QueueState()
    
    def enqueue(self, item: TaskQueueItem):
        """Add a task to the queue."""
        if item.task_id in self._items:
            logger.warning(f"Task {item.task_id} already in queue, skipping duplicate")
            return
        self._items[item.task_id] = item
        logger.debug(f"Enqueued task {item.task_id} with priority {item.priority}")
    
    def enqueue_multiple(self, items: list[TaskQueueItem]):
        """Add multiple tasks."""
        for item in items:
            self.enqueue(item)
    
    def dequeue(self, task_id: str) -> Optional[TaskQueueItem]:
        """Remove and return a task by ID."""
        item = self._items.pop(task_id, None)
        if item:
            logger.debug(f"Dequeued task {task_id}")
        return item
    
    def get(self, task_id: str) -> Optional[TaskQueueItem]:
        """Get a task without removing it."""
        return self._items.get(task_id)
    
    def reprioritize(self, task_id: str, new_priority: int):
        """Change a task's priority (triggers re-sorting on next get_next_wave)."""
        if task_id in self._items:
            old_priority = self._items[task_id].priority
            self._items[task_id].priority = new_priority
            logger.info(f"Reprioritized {task_id} from {old_priority} to {new_priority}")
    
    def mark_done(self, task_id: str, learnings: list = None):
        """Mark a task as completed."""
        if task_id in self._items:
            self._items[task_id].mark_done(learnings)
            self._completed.add(task_id)
            logger.debug(f"Marked {task_id} as DONE")
    
    def mark_failed(self, task_id: str, error: str):
        """Mark a task as failed."""
        if task_id in self._items:
            self._items[task_id].mark_failed(error)
            self._completed.add(task_id)
            logger.debug(f"Marked {task_id} as FAILED: {error}")
            
            # Mark dependent tasks as blocked
            for task_id_other, item_other in self._items.items():
                if task_id in item_other.dependencies:
                    item_other.mark_blocked(f"Depends on failed task {task_id}")
    
    def get_pending_tasks(self) -> list[TaskQueueItem]:
        """Get all tasks not yet completed."""
        return [item for item in self._items.values()
                if item.status in (TaskStatus.QUEUED, TaskStatus.BLOCKED)]
    
    def get_next_wave(self, max_parallel: int = 3) -> list[TaskQueueItem]:
        """Get ready-to-execute tasks for next wave.
        
        Returns tasks that:
        1. Have status QUEUED (or unblocked)
        2. All dependencies completed
        3. Up to max_parallel tasks
        Sorted by priority (higher first)
        """
        ready = []
        for item in self._items.values():
            if item.status == TaskStatus.QUEUED and item.is_ready(self._completed):
                ready.append(item)
        
        # Sort by priority, then by FIFO
        ready.sort()
        
        # Return up to max_parallel
        return ready[:max_parallel]
    
    def is_empty(self) -> bool:
        """Check if queue is fully processed."""
        pending = self.get_pending_tasks()
        return len(pending) == 0
    
    def get_all_tasks(self) -> list[TaskQueueItem]:
        """Get all tasks regardless of status."""
        return list(self._items.values())
    
    def add_tasks(self, new_items: list[TaskQueueItem], wave_num: int = 0):
        """Add new tasks to queue (typically from reflection loop).
        
        Args:
            new_items: List of TaskQueueItem to add
            wave_num: Which wave discovered these tasks (for tracking)
        """
        for item in new_items:
            item.wave_added_at = wave_num
            self.enqueue(item)
    
    def get_queue_summary(self) -> dict:
        """Get status snapshot for logging/debugging."""
        tasks = self.get_all_tasks()
        by_status = {}
        for status in TaskStatus:
            by_status[status.value] = [t.task_id for t in tasks if t.status == status]
        
        return {
            "total_tasks": len(tasks),
            "completed": len(self._completed),
            "pending": len(self.get_pending_tasks()),
            "by_status": by_status,
            "learnings_count": len(self.state.total_learnings),
        }
