"""Execution Plan — Task decomposition for complex queries.

Industry-grade todo list mechanism:
  - Break complex queries into actionable tasks
  - Track task dependencies and status
  - Support mid-execution task addition (like wave reflection)
  - Integrate with existing TaskQueue without replacing it

This module provides a USER-FRIENDLY task representation layer
on top of the existing orchestrator infrastructure.
"""

from __future__ import annotations

import time
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Status of a task in execution."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class TaskPriority(int, Enum):
    """Priority levels for task execution."""
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class TaskItem:
    """One task in an execution plan."""
    task_id: str
    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.NORMAL
    depends_on: List[str] = field(default_factory=list)
    
    # Status tracking
    status: TaskStatus = TaskStatus.NOT_STARTED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    # Results and metadata
    result: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    
    # For integration with subagent execution
    subagent_type: Optional[str] = None  # "retriever", "code_gen_executor", etc.
    subagent_input: Optional[dict] = None
    subagent_result: Optional[dict] = None
    
    @property
    def elapsed_seconds(self) -> float:
        """Time spent on this task."""
        if not self.started_at:
            return 0.0
        end = self.completed_at or time.time()
        return end - self.started_at
    
    @property
    def is_active(self) -> bool:
        """Whether task is currently running."""
        return self.status in (TaskStatus.IN_PROGRESS,)
    
    @property
    def is_done(self) -> bool:
        """Whether task is complete (success or failure)."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
    
    def mark_in_progress(self):
        """Mark task as started."""
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = time.time()
        logger.debug(f"Task {self.task_id} started")
    
    def mark_completed(self, result: str = None):
        """Mark task as successfully completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = time.time()
        if result:
            self.result = result
        logger.debug(f"Task {self.task_id} completed in {self.elapsed_seconds:.1f}s")
    
    def mark_failed(self, error: str):
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.completed_at = time.time()
        self.error = error
        logger.warning(f"Task {self.task_id} failed: {error}")
    
    def mark_blocked(self, reason: str):
        """Mark task as blocked (dependency not met)."""
        self.status = TaskStatus.BLOCKED
        self.error = reason
        logger.debug(f"Task {self.task_id} blocked: {reason}")
    
    def can_run(self, completed_tasks: set[str]) -> bool:
        """Check if this task can run (all dependencies met)."""
        if self.status != TaskStatus.NOT_STARTED:
            return False
        return all(dep_id in completed_tasks for dep_id in self.depends_on)


@dataclass
class ExecutionPlan:
    """High-level execution plan for a query."""
    query: str
    plan_id: str
    tasks: List[TaskItem] = field(default_factory=list)
    reasoning: str = ""  # Why was it decomposed this way?
    
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    # Metadata
    total_estimat_duration_s: float = 0.0  # Rough estimate
    metadata: dict = field(default_factory=dict)
    
    def add_task(self, task: TaskItem):
        """Add a task to the plan."""
        if any(t.task_id == task.task_id for t in self.tasks):
            logger.warning(f"Task {task.task_id} already in plan")
            return
        self.tasks.append(task)
    
    def add_tasks(self, tasks: List[TaskItem]):
        """Add multiple tasks."""
        for task in tasks:
            self.add_task(task)
    
    def get_task(self, task_id: str) -> Optional[TaskItem]:
        """Get a task by ID."""
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None
    
    def get_ready_tasks(self) -> List[TaskItem]:
        """Get all tasks that can run now (dependencies met)."""
        completed = {t.task_id for t in self.tasks if t.is_done}
        return [t for t in self.tasks if t.can_run(completed)]
    
    def get_next_task(self) -> Optional[TaskItem]:
        """Get the highest-priority ready task."""
        ready = self.get_ready_tasks()
        if not ready:
            return None
        return sorted(ready, key=lambda t: -t.priority.value)[0]
    
    def mark_started(self):
        """Mark plan as started."""
        self.started_at = time.time()
        logger.info(f"Execution plan {self.plan_id} started ({len(self.tasks)} tasks)")
    
    def mark_completed(self):
        """Mark plan as completed."""
        self.completed_at = time.time()
        successful = sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self.tasks if t.status == TaskStatus.FAILED)
        logger.info(f"Execution plan {self.plan_id} completed: {successful} done, {failed} failed")
    
    @property
    def is_complete(self) -> bool:
        """Whether all tasks are done."""
        return all(t.is_done for t in self.tasks)
    
    @property
    def progress_percent(self) -> float:
        """Percentage of tasks completed."""
        if not self.tasks:
            return 100.0
        done = sum(1 for t in self.tasks if t.is_done)
        return (done / len(self.tasks)) * 100.0
    
    @property
    def status_summary(self) -> dict:
        """Summary of plan status."""
        return {
            "total_tasks": len(self.tasks),
            "completed": sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED),
            "failed": sum(1 for t in self.tasks if t.status == TaskStatus.FAILED),
            "in_progress": sum(1 for t in self.tasks if t.status == TaskStatus.IN_PROGRESS),
            "blocked": sum(1 for t in self.tasks if t.status == TaskStatus.BLOCKED),
            "not_started": sum(1 for t in self.tasks if t.status == TaskStatus.NOT_STARTED),
            "progress_percent": self.progress_percent,
            "elapsed_seconds": time.time() - self.created_at if self.started_at else 0.0,
        }
