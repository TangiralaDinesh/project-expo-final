#!/usr/bin/env python3
"""
Comprehensive test suite for Plan 1 - Adaptive Task Queue System

Tests all components:
- Task queue data structures
- Priority handling and dependencies
- Reflection engine
- Scheduler and wave execution
- Adaptive behavior (new tasks, reprioritization)
- Backward compatibility
"""

import pytest
import asyncio
from agent.core.task_queue import TaskQueue, TaskQueueItem, TaskStatus, QueueState
from agent.core.task_reflection import TaskReflectionEngine, ReprioritizationRules
from agent.core.adaptive_scheduler import AdaptiveScheduler, SchedulingDecision
from agent.core.wave_executor import WaveExecutor, WaveExecution
from agent.core.orchestrator_adapter import decomposition_to_task_queue, AdaptiveOrchestrator
from agent.orchestrator.orchestrator import TaskNode, Decomposition, SubagentType


class TestTaskQueue:
    """Test basic task queue functionality."""
    
    def test_enqueue_dequeue(self):
        """Test adding and removing tasks."""
        queue = TaskQueue()
        item = TaskQueueItem(
            task_id="t1",
            task_description="Test task",
            priority=50,
        )
        
        queue.enqueue(item)
        assert len(queue.get_all_tasks()) == 1
        
        retrieved = queue.get("t1")
        assert retrieved is not None
        assert retrieved.task_id == "t1"
        
        dequeued = queue.dequeue("t1")
        assert dequeued is not None
        assert len(queue.get_all_tasks()) == 0
    
    def test_priority_sorting(self):
        """Test that tasks are sorted by priority."""
        queue = TaskQueue()
        
        # Add in random order
        for task_id, priority in [("t1", 30), ("t2", 90), ("t3", 60)]:
            item = TaskQueueItem(task_id=task_id, task_description=f"Task {task_id}", priority=priority)
            queue.enqueue(item)
        
        # Get next wave - should be sorted by priority (highest first)
        wave = queue.get_next_wave(max_parallel=3)
        assert len(wave) == 3
        assert wave[0].task_id == "t2"  # Priority 90
        assert wave[1].task_id == "t3"  # Priority 60
        assert wave[2].task_id == "t1"  # Priority 30
    
    def test_dependencies(self):
        """Test that dependent tasks don't execute until dependencies complete."""
        queue = TaskQueue()
        
        # Create dependency chain: t1 -> t2 -> t3
        items = [
            TaskQueueItem(task_id="t1", task_description="Task 1", priority=100, dependencies=[]),
            TaskQueueItem(task_id="t2", task_description="Task 2", priority=90, dependencies=["t1"]),
            TaskQueueItem(task_id="t3", task_description="Task 3", priority=80, dependencies=["t2"]),
        ]
        
        for item in items:
            queue.enqueue(item)
        
        # Wave 1: only t1 should be ready
        wave1 = queue.get_next_wave(max_parallel=3)
        assert len(wave1) == 1
        assert wave1[0].task_id == "t1"
        
        # Mark t1 complete
        queue.mark_done("t1")
        
        # Wave 2: only t2 should be ready
        wave2 = queue.get_next_wave(max_parallel=3)
        assert len(wave2) == 1
        assert wave2[0].task_id == "t2"
        
        # Mark t2 complete
        queue.mark_done("t2")
        
        # Wave 3: only t3 should be ready
        wave3 = queue.get_next_wave(max_parallel=3)
        assert len(wave3) == 1
        assert wave3[0].task_id == "t3"
    
    def test_is_empty(self):
        """Test queue emptiness check."""
        queue = TaskQueue()
        assert queue.is_empty()
        
        item = TaskQueueItem(task_id="t1", task_description="Task", priority=50)
        queue.enqueue(item)
        assert not queue.is_empty()
        
        queue.mark_done("t1")
        assert queue.is_empty()
    
    def test_failure_blocks_dependents(self):
        """Test that failed tasks block dependent tasks."""
        queue = TaskQueue()
        
        items = [
            TaskQueueItem(task_id="t1", task_description="Task 1", priority=100, dependencies=[]),
            TaskQueueItem(task_id="t2", task_description="Task 2", priority=90, dependencies=["t1"]),
        ]
        
        for item in items:
            queue.enqueue(item)
        
        queue.mark_done("t1")
        queue.mark_failed("t1", "execution_failed")
        
        # t2 should be blocked
        wave = queue.get_next_wave(max_parallel=3)
        assert len(wave) == 0  # t2 is blocked, not queued
        
        t2 = queue.get("t2")
        assert t2.status == TaskStatus.BLOCKED


class TestAdaptiveScheduler:
    """Test scheduling logic."""
    
    def test_parallelization_limit(self):
        """Test that scheduler respects parallelization limit."""
        queue = TaskQueue()
        
        # Add 5 independent tasks
        for i in range(5):
            item = TaskQueueItem(
                task_id=f"t{i}",
                task_description=f"Task {i}",
                priority=100-i,
                dependencies=[],
            )
            queue.enqueue(item)
        
        scheduler = AdaptiveScheduler(parallelization_limit=3)
        decision = scheduler.schedule_next_wave(queue, parallelization_limit=3)
        
        assert len(decision.tasks_to_execute) == 3
        assert decision.parallelization_level == 3
    
    def test_scheduling_decision(self):
        """Test SchedulingDecision structure."""
        items = [
            TaskQueueItem(task_id="t1", task_description="Task 1", priority=100, dependencies=[]),
        ]
        
        decision = SchedulingDecision(wave_number=1, tasks_to_execute=items)
        assert decision.wave_number == 1
        assert len(decision.tasks_to_execute) == 1


@pytest.mark.asyncio
class TestWaveExecutor:
    """Test wave execution."""
    
    async def test_wave_execution(self):
        """Test executing one wave of tasks."""
        
        async def mock_task_executor(task: TaskQueueItem):
            """Simulate task execution."""
            await asyncio.sleep(0.01)  # Simulate work
            return [f"Learning from {task.task_id}"]
        
        executor = WaveExecutor()
        
        tasks = [
            TaskQueueItem(task_id="t1", task_description="Task 1", priority=100, dependencies=[]),
            TaskQueueItem(task_id="t2", task_description="Task 2", priority=90, dependencies=[]),
        ]
        
        wave = await executor.execute_wave(
            wave_num=1,
            tasks=tasks,
            execute_task_fn=mock_task_executor,
        )
        
        assert wave.wave_number == 1
        assert len(wave.results) == 2
        assert wave.success_count == 2
        assert wave.failure_count == 0
        assert wave.success_rate == 100.0
    
    async def test_wave_with_timeout(self):
        """Test that tasks timeout correctly."""
        
        async def slow_task_executor(task: TaskQueueItem):
            await asyncio.sleep(10)  # Will timeout
            return ["Should not reach here"]
        
        executor = WaveExecutor()
        
        tasks = [
            TaskQueueItem(task_id="t1", task_description="Slow task", priority=100, dependencies=[]),
        ]
        
        wave = await executor.execute_wave(
            wave_num=1,
            tasks=tasks,
            execute_task_fn=slow_task_executor,
            timeout_per_task=0.1,  # 100ms timeout
        )
        
        assert wave.failure_count == 1
        assert wave.success_count == 0


class TestOrchestratorAdapter:
    """Test backward compatibility and adaptation."""
    
    def test_decomposition_to_queue(self):
        """Test converting legacy Decomposition to TaskQueue."""
        # Create legacy decomposition
        nodes = [
            TaskNode(node_id="n1", subagent_type=SubagentType.RETRIEVER, task="Task 1"),
            TaskNode(node_id="n2", subagent_type=SubagentType.RETRIEVER, task="Task 2", depends_on=["n1"]),
        ]
        decomp = Decomposition(nodes=nodes)
        
        # Convert to queue
        queue = decomposition_to_task_queue(decomp)
        
        assert len(queue.get_all_tasks()) == 2
        
        t1 = queue.get("n1")
        t2 = queue.get("n2")
        
        assert t1 is not None
        assert t2 is not None
        assert t2.dependencies == ["n1"]


class TestReprioritizationRules:
    """Test rule-based reprioritization."""
    
    def test_priority_decay(self):
        """Test that priority decays over time."""
        items = [
            TaskQueueItem(task_id="t1", task_description="Task 1", priority=100, wave_added_at=0),
            TaskQueueItem(task_id="t2", task_description="Task 2", priority=80, wave_added_at=1),
            TaskQueueItem(task_id="t3", task_description="Task 3", priority=60, wave_added_at=2),
        ]
        
        reprioritizations = ReprioritizationRules.apply_priority_decay(items)
        
        # t2 should decay (added at wave 1, now at wave 2+)
        # t3 should decay more (added at wave 2)
        assert len(reprioritizations) > 0


def test_queue_state():
    """Test queue state tracking."""
    state = QueueState()
    
    state.add_learning("Learning 1")
    state.add_learning("Learning 2")
    
    assert len(state.total_learnings) == 2
    
    state.record_retrieval_pattern("semantic_search", {"query": "test"})
    assert "semantic_search" in state.retrieval_patterns


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
