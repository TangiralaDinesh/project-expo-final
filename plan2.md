Analyze current task decomposition architecture

Read 

Read 

Read 

Read 

Searched for regex `decision|decompose_task|run_orchestrator` (`project-expo` · `v2/project-expo-final/agent/**/*.py`), 154 results

Read , lines 1 to 150

Read , lines 1 to 150

Read , lines 128 to 280

Read , lines 140 to 202

Read , lines 283 to 450

Read , lines 440 to 482

Read , lines 1 to 70

Searched for regex `Learning|reflection|learnings` (`project-expo` · `v2/project-expo-final/agent/**/*.py`), 192 results

Read , lines 50 to 99

Searched for text `class SubagentInput|class SubagentResult|class SubagentType` (`project-expo` · `v2/project-expo-final/agent/core/types.py`), 3 results

Read 

Read , lines 1 to 100

Read , lines 1 to 150

Read , lines 1 to 100

Read , lines 100 to 127

Read , lines 1 to 100

Searched for text `class ThinkingProfile|adaptive` (`project-expo` · `v2/project-expo-final/agent/core/reasoning.py`), 2 results

Read , lines 1 to 100

Read , lines 1 to 100

Created memory file 

Created memory file 

---

## 📋 **PLAN: Adaptive Task Queue System with Dynamic Prioritization**

### **Core Concept**
Replace the current static DAG orchestration with a **dynamic priority queue** that reorders/adds tasks based on learnings from each retrieval wave.

```
Wave 1: Execute [sector_analysis, top_stocks] (parallel)
        ↓ Discover: "User risk-averse, need defensive stocks"
        ↓ Queue updates: Add [defensive_analysis] with high priority

Wave 2: Execute [defensive_analysis]
        ↓ Discover: "Stocks are correlated"
        ↓ Queue updates: Add [correlation_analysis]

Wave 3: Execute [correlation_analysis] → synthesis
```

---

### **Steps (3 Phases, ~3 weeks)**

#### **Phase A: Core Data Structures**
1. Create `core/task_queue.py` — TaskQueue, TaskQueueItem with priority/dependencies/status
2. Create `core/task_reflection.py` — LLM analyzes learnings, suggests new tasks
3. Create `core/adaptive_scheduler.py` — Decides which tasks run in next wave
4. Create `core/wave_executor.py` — Tracks multi-wave execution and learnings

#### **Phase B: Wire into Orchestrator** (*depends on Phase A*)
5. Modify `orchestrator.py::decompose_task()` — Return TaskQueue (not flat list)
6. Refactor `orchestrator.py::run_orchestrator()` — Loop: execute_wave → reflect → reprioritize → next_wave
7. Wire `decision.py::next_queries` into queue as new TaskQueueItems

#### **Phase C: Reflection + Observability** (*depends on Phase B*)
8. Implement LLM-powered reflection: analyze what's missing, suggest new tasks
9. Add reprioritization rules (if comparison + 1 entity missing → high priority)
10. Add logging/API for queue state visualization (which tasks ran when, why)

#### **Phase E: Tests**
11. Create `test_adaptive_queue.py` — 6 unit tests + 1 integration test
12. Verify: comparison queries auto-add 2nd entity, learnings trigger reprioritization

---

### **Relevant Files**

**Create:**
- `core/task_queue.py` — TaskQueue + TaskQueueItem
- `core/task_reflection.py` — ReflectionAnalysis + TaskReflectionEngine  
- `core/adaptive_scheduler.py` — AdaptiveScheduler
- `core/wave_executor.py` — WaveExecution tracking
- `test_adaptive_queue.py` — Tests

**Modify:**
- orchestrator.py — `decompose_task()` returns TaskQueue, `run_orchestrator()` uses wave loop
- blocks/semantic/decision.py — `next_queries` feed into queue
- core/reasoning.py — SatisfactionTracker reused for priority calculation

---

### **Verification**

| Phase | Test | Command |
|-------|------|---------|
| A | Data structures compile | `python -c "from agent.core.task_queue import TaskQueue"` |
| B | Orchestrator uses queue | `pytest test_adaptive_queue.py::test_queue_based_orchestration` |
| C | Learnings → reprioritization | `pytest test_adaptive_queue.py::test_reprioritization_on_learnings` |
| E | Comparison multi-wave | `pytest test_adaptive_queue.py::test_comparison_multi_wave` |

---

### **Decisions**

| Decision | Rationale |
|----------|-----------|
| **Backward compatible APIs** | Keep `orchestrator.decompose_task(task)` signature; only change internals to return TaskQueue |
| **Failed tasks don't retry** | Mark FAILED, move on; rely on corrective RAG later |
| **Reflection every wave** | ~500 tokens/wave cost; enable full adaptability |
| **Hard dependencies only** | Soft dependencies add complexity; start simple |
| **In-memory queue** | No persistence across sessions; single-query scope |

---

### **Questions for You**

1. **Should reflection happen on EVERY wave, or only when satisfaction_score drops?** 
   - Every wave (transparent + adaptive) vs. Selective (cheaper)

2. **After Wave N, ask user which new tasks to prioritize, or auto-prioritize?**
   - Auto (trust reflection) vs. Ask user (give control)

3. **Timeline: Start now, or review first?**

Review the plan and let me know if you want to adjust anything before I start implementation!