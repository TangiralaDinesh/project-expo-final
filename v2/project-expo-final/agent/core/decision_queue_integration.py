"""
Decision-Queue Integration Hook (Plan 1, Phase B.7)

FUTURE: Wire decision.py next_queries into adaptive task queue.

This module provides the scaffolding for integrating the decision_llm() results
into the adaptive task queue. Currently, this is NOT wired into the main orchestrator
to avoid breaking existing functionality. When ready to enable, uncomment the
integration in orchestrator_adapter.py.

Key points:
1. decision_llm() generates next_queries as part of its decision output
2. These next_queries can be converted to TaskQueueItem and added to queue
3. Priority is based on decision_llm's confidence/EIG reasoning
4. Comparison query detection feeds into reprioritization

USAGE:
    # In orchestrator_adapter.py, after collecting learnings:
    # next_queries = extract_next_queries_from_decision(wave_results)
    # new_tasks = convert_queries_to_task_items(next_queries)
    # queue.add_tasks(new_tasks, wave_num=current_wave)
"""

from __future__ import annotations

import logging
from typing import Optional, Any

from .task_queue import TaskQueueItem, TaskStatus

logger = logging.getLogger(__name__)


def extract_next_queries_from_decision(
    decision_results: dict[str, dict],
) -> list[dict]:
    """
    Extract next_queries from decision_llm outputs.
    
    Args:
        decision_results: {node_id: decision_dict} from decision_llm calls
    
    Returns:
        List of {query: str, priority: int, reason: str}
    """
    next_queries = []
    
    for node_id, decision_dict in decision_results.items():
        if isinstance(decision_dict, dict) and decision_dict.get("next_queries"):
            queries = decision_dict["next_queries"]
            
            # Determine priority based on decision confidence
            # High EIG → high priority
            priority = 70  # Default
            
            # If comparison and imbalanced entities → boost priority
            if decision_dict.get("is_comparison_query"):
                if decision_dict.get("underexplored_entities"):
                    priority = 85
            
            for query in queries:
                next_queries.append({
                    "query": query,
                    "priority": priority,
                    "reason": f"From decision node {node_id}",
                    "parent_node": node_id,
                    "is_comparison": decision_dict.get("is_comparison_query", False),
                })
    
    return next_queries


def convert_queries_to_task_items(
    queries: list[dict],
) -> list[TaskQueueItem]:
    """
    Convert next_queries into TaskQueueItem for queue.
    
    Args:
        queries: List of {query, priority, reason} from extract_next_queries_from_decision
    
    Returns:
        List of TaskQueueItem ready to enqueue
    """
    items = []
    
    for i, query_dict in enumerate(queries):
        item = TaskQueueItem(
            task_id=f"decision_follow_up_{i}_{query_dict['query'][:20]}".replace(" ", "_"),
            task_description=query_dict["query"],
            priority=query_dict.get("priority", 70),
            dependencies=[],  # Follow-up queries don't depend on anything
        )
        items.append(item)
    
    return items


class DecisionQueueBridge:
    """
    Future: Bridge between decision_llm() and task queue.
    
    When enabled, this will:
    1. Capture next_queries from each decision_llm call
    2. Convert to TaskQueueItem
    3. Add to queue at appropriate priority
    4. Handle comparison query balancing
    
    For now: kept as template for future integration.
    """
    
    def __init__(self):
        self.captured_decisions = []
    
    def capture_decision(self, node_id: str, decision: dict):
        """Store a decision result for later processing."""
        self.captured_decisions.append({
            "node_id": node_id,
            "decision": decision,
        })
    
    def flush_to_queue(self, queue: Any, wave_num: int) -> int:
        """
        Convert captured decisions to task items and add to queue.
        
        Returns:
            Number of tasks added
        """
        if not self.captured_decisions:
            return 0
        
        # Extract and convert
        queries_dict = {}
        for cap in self.captured_decisions:
            node_id = cap["node_id"]
            decision = cap["decision"]
            if decision.get("next_queries"):
                queries_dict[node_id] = decision
        
        next_queries = extract_next_queries_from_decision(queries_dict)
        new_items = convert_queries_to_task_items(next_queries)
        
        # Add to queue
        queue.add_tasks(new_items, wave_num=wave_num)
        
        # Reset
        tasks_added = len(new_items)
        self.captured_decisions = []
        
        logger.info(f"Decision bridge: added {tasks_added} follow-up tasks to queue")
        return tasks_added


# Integration documentation
INTEGRATION_EXAMPLE = """
# HOW TO ENABLE DECISION-QUEUE INTEGRATION (Phase B.7)

## Step 1: Modify orchestrator_adapter.py

In the run_adaptive_orchestration method, add after wave execution:

```python
# After wave_exec = await self.executor.execute_wave(...)

# Optional: capture decisions and feed next_queries into queue
if hasattr(wave_exec, 'decision_results'):
    from ..core.decision_queue_integration import extract_next_queries_from_decision, convert_queries_to_task_items
    
    next_queries_list = extract_next_queries_from_decision(wave_exec.decision_results)
    if next_queries_list:
        new_items = convert_queries_to_task_items(next_queries_list)
        queue.add_tasks(new_items, wave_num=wave_num)
        logger.info(f"Added {len(new_items)} follow-up tasks from decision LLM")
```

## Step 2: Modify WaveExecution to track decision results

In wave_executor.py, update WaveExecution to store decision results:

```python
@dataclass
class WaveExecution:
    # ... existing fields ...
    decision_results: dict = field(default_factory=dict)  # node_id -> decision_dict
```

## Step 3: Update execute_task to capture decisions

When task executor calls decision_llm(), pass results to WaveExecution:

```python
if hasattr(node_result, 'decision'):
    wave.decision_results[node_id] = node_result.decision
```

## Benefits

Once wired, the system will:
1. Auto-discover follow-up queries from decision_llm
2. Prioritize imbalanced entity exploration in comparisons
3. Adapt task queue based on what decision engine recommends
4. Reduce manual task decomposition needs

## Backward compatibility

This is optional and doesn't affect the main orchestrator.
Set enable_decision_integration=False to disable.
"""
