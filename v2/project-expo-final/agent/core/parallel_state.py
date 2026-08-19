"""Parallel State Machine for operation tracking and progress visibility.

Phase 5: Explicit state tracking for parallel operations.
Provides transparency into what's running, what's queued, and what's complete.
"""

import asyncio
import logging
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)


class OperationState(Enum):
    """State of an individual operation."""
    QUEUED = "queued"           # Waiting to start
    IN_PROGRESS = "in_progress" # Currently running
    COMPLETED = "completed"     # Finished successfully
    FAILED = "failed"           # Failed with error
    CANCELLED = "cancelled"     # User cancelled


@dataclass
class OperationProgress:
    """Progress for one operation."""
    operation_id: str
    operation_name: str
    state: OperationState = OperationState.QUEUED
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    progress_percent: float = 0.0  # 0-100
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def elapsed_seconds(self) -> float:
        """Elapsed time in seconds."""
        if not self.start_time:
            return 0.0
        end = self.end_time or time.time()
        return end - self.start_time
    
    @property
    def is_active(self) -> bool:
        """Whether operation is still running."""
        return self.state in (OperationState.QUEUED, OperationState.IN_PROGRESS)


class ParallelStateCoordinator:
    """Tracks state of parallel operations (Phase 5).
    
    Example:
        coordinator = ParallelStateCoordinator()
        
        # Register operations
        coordinator.register("retriever_cdsl", "Retrieve CDSL info")
        coordinator.register("retriever_emvee", "Retrieve EMVEE info")
        
        # Execute with tracking
        async with coordinator.track("retriever_cdsl"):
            results_cdsl = await retrieve_cdsl()
        
        # Query status
        status = coordinator.get_status()  # Overall progress
        cdsl_op = coordinator.get_operation("retriever_cdsl")
    """
    
    def __init__(self):
        self.operations: Dict[str, OperationProgress] = {}
        self._lock = asyncio.Lock()
        self._callbacks: List[Callable] = []  # Progress callbacks
    
    def register(
        self,
        operation_id: str,
        operation_name: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a new operation to track (Phase 5)."""
        self.operations[operation_id] = OperationProgress(
            operation_id=operation_id,
            operation_name=operation_name,
            metadata=metadata or {},
        )
        logger.debug(f"Registered operation: {operation_id}")
    
    async def start(self, operation_id: str) -> None:
        """Mark operation as started."""
        async with self._lock:
            if operation_id in self.operations:
                op = self.operations[operation_id]
                op.state = OperationState.IN_PROGRESS
                op.start_time = time.time()
                logger.debug(f"Started operation: {operation_id}")
                await self._notify_callbacks()
    
    async def update_progress(
        self,
        operation_id: str,
        progress_percent: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update progress for an operation."""
        async with self._lock:
            if operation_id in self.operations:
                op = self.operations[operation_id]
                op.progress_percent = min(100.0, max(0.0, progress_percent))
                if metadata:
                    op.metadata.update(metadata)
                await self._notify_callbacks()
    
    async def complete(self, operation_id: str) -> None:
        """Mark operation as completed."""
        async with self._lock:
            if operation_id in self.operations:
                op = self.operations[operation_id]
                op.state = OperationState.COMPLETED
                op.progress_percent = 100.0
                op.end_time = time.time()
                logger.debug(f"Completed operation: {operation_id} ({op.elapsed_seconds:.1f}s)")
                await self._notify_callbacks()
    
    async def fail(self, operation_id: str, error: str) -> None:
        """Mark operation as failed."""
        async with self._lock:
            if operation_id in self.operations:
                op = self.operations[operation_id]
                op.state = OperationState.FAILED
                op.error_message = error
                op.end_time = time.time()
                logger.warning(f"Failed operation: {operation_id} - {error}")
                await self._notify_callbacks()
    
    async def context_manager(self, operation_id: str):
        """Async context manager for automatic state tracking (Phase 5).
        
        Usage:
            async with coordinator.context_manager("op_id"):
                await do_work()
            # Automatically marks as complete or failed
        """
        class _ContextManager:
            def __init__(self, coordinator, op_id):
                self.coordinator = coordinator
                self.op_id = op_id
            
            async def __aenter__(self):
                await self.coordinator.start(self.op_id)
                return self
            
            async def __aexit__(self, exc_type, exc, tb):
                if exc_type:
                    await self.coordinator.fail(self.op_id, str(exc))
                else:
                    await self.coordinator.complete(self.op_id)
        
        return _ContextManager(self, operation_id)
    
    def get_operation(self, operation_id: str) -> Optional[OperationProgress]:
        """Get status of one operation."""
        return self.operations.get(operation_id)
    
    def get_status(self) -> Dict[str, Any]:
        """Get overall status of all operations (Phase 6).
        
        Returns:
            {
                "total": 5,
                "queued": 2,
                "in_progress": 2,
                "completed": 1,
                "failed": 0,
                "overall_progress": 50.0,
                "human_display": "Retrieve CDSL info (100%) | Retrieve EMVEE info (75%) | Synthesis (0%)",
                "operations": [...]
            }
        """
        total = len(self.operations)
        if not total:
            return {
                "total": 0,
                "queued": 0,
                "in_progress": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
                "overall_progress": 100.0,
                "human_display": "No operations",
                "operations": [],
            }
        
        state_counts = {
            OperationState.QUEUED: 0,
            OperationState.IN_PROGRESS: 0,
            OperationState.COMPLETED: 0,
            OperationState.FAILED: 0,
            OperationState.CANCELLED: 0,
        }
        
        progress_sum = 0.0
        operations_list = []
        human_parts = []  # For user-friendly display
        
        for op in self.operations.values():
            state_counts[op.state] += 1
            progress_sum += op.progress_percent
            
            operations_list.append({
                "id": op.operation_id,
                "name": op.operation_name,
                "state": op.state.value,
                "progress": op.progress_percent,
                "elapsed_s": op.elapsed_seconds,
                "error": op.error_message,
            })
            
            # Phase 6: Build human-readable display
            # Format: "Operation Name (75%)" for active operations, "Operation Name (✓)" for completed
            if op.state == OperationState.COMPLETED:
                human_parts.append(f"{op.operation_name} (✓)")
            elif op.state == OperationState.FAILED:
                human_parts.append(f"{op.operation_name} (✗)")
            elif op.state == OperationState.IN_PROGRESS:
                human_parts.append(f"{op.operation_name} ({op.progress_percent:.0f}%)")
            elif op.state == OperationState.QUEUED:
                human_parts.append(f"{op.operation_name} (queued)")
        
        overall_progress = (progress_sum / total) if total > 0 else 0.0
        human_display = " | ".join(human_parts) if human_parts else "No operations"
        
        return {
            "total": total,
            "queued": state_counts[OperationState.QUEUED],
            "in_progress": state_counts[OperationState.IN_PROGRESS],
            "completed": state_counts[OperationState.COMPLETED],
            "failed": state_counts[OperationState.FAILED],
            "cancelled": state_counts[OperationState.CANCELLED],
            "overall_progress": overall_progress,
            "human_display": human_display,  # Phase 6: User-friendly progress display
            "operations": operations_list,
        }
    
    def on_progress_update(self, callback: Callable) -> None:
        """Register callback for progress updates.
        
        Callback should be: async fn(status: Dict) -> None
        Called whenever status changes.
        """
        self._callbacks.append(callback)
    
    async def _notify_callbacks(self) -> None:
        """Notify all registered callbacks of status change."""
        status = self.get_status()
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(status)
                else:
                    callback(status)
            except Exception as e:
                logger.warning(f"Callback failed: {e}")


# Global state coordinator instance
_global_coordinator: Optional[ParallelStateCoordinator] = None


def get_state_coordinator() -> ParallelStateCoordinator:
    """Get or create global state coordinator (Phase 5)."""
    global _global_coordinator
    if _global_coordinator is None:
        _global_coordinator = ParallelStateCoordinator()
    return _global_coordinator
