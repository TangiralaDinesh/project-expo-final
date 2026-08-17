# Plan: Adaptive Task Queue System with Dynamic Prioritization

## TL;DR
Replace the current static DAG-based orchestration with a **dynamic priority task queue** that reorders/adds tasks based on learnings from each retrieval wave. System will:
1. Decompose initial query into priority-ordered tasks
2. Execute independent tasks in parallel (Wave 1)
3. After each wave: **reflect on learnings** → detect new needed tasks → reprioritize remaining queue
4. Iterate until task queue empty or query satisfied

**Why:** Current system decomposes all tasks upfront and executes DAG statically. New system adapts task list as knowledge grows - if Wave 1 discovers "need risk analysis", it auto-adds and prioritizes it without manual intervention.

---

## Architecture Overview

```
Query: "Best investment for my profile?"
         ↓
    Initial Decompose
         ↓
    TaskQueue [priority_1: sector_analysis, priority_2: top_stocks, ...]
         ↓
    Wave 1: Execute [sector_analysis] + [top_stocks] (independent, parallel)
         ↓
    TaskReflection: Analyze learnings
         "Found 5 sectors. User risk-averse. We need: deep-dive_defensive"
         ↓
    Queue Update: Reprioritize/Add [deep_dive_defensive] 
         ↓
    Wave 2: Execute [deep_dive_defensive] + ...
         ↓
    [Repeat until done]
```

---

## Steps

### Phase A: Core Data Structures (Week 1)

1. **Create `core/task_queue.py`**
   - `TaskQueueItem`: task description, priority (0-100), dependencies, status (QUEUED/IN_PROGRESS/DONE/BLOCKED), wave_added_at
   - `TaskQueue`: maintains ordered list, support enqueue/dequeue/reprioritize/mark_done
   - `QueueState`: tracks cumulative learnings, retrieval patterns, execution history
   - Methods: `get_next_wave(num_parallel)`, `update_learnings(learnings)`, `reprioritize(rules)`

2. **Create `core/task_reflection.py`** (*depends on Phase A.1*)
   - `ReflectionAnalysis`: dataclass with new_tasks_detected, reprioritization_suggestions, confidence scores
   - `TaskReflectionEngine`: analyzes learnings to detect what's missing
   - Methods: 
     - `analyze_learnings(current_learnings, query, domain)` → ReflectionAnalysis
     - `suggest_new_tasks(learnings)` → list of {task, priority, reasoning}
     - `detect_knowledge_gaps(learnings, query)` → list of gap descriptions
   - Uses LLM: prompt shows learnings, asks "what else should we explore?"

3. **Create `core/adaptive_scheduler.py`** (*depends on Phase A.1, A.2*)
   - `SchedulingDecision`: which tasks to run, which are blocked, parallelization strategy
   - `AdaptiveScheduler`: wraps TaskQueue + TaskReflectionEngine
   - Methods:
     - `schedule_next_wave(queue, parallelization_limit)` → list[TaskQueueItem] ready to execute
     - `update_and_reschedule(queue, wave_results, learnings)` → updated queue + next wave decisions
     - Dependency logic: only schedule tasks where all `depends_on` are DONE
     - Parallelization: schedule up to N independent tasks simultaneously

4. **Create `core/wave_executor.py`** (*depends on Phase A.3*)
   - Tracks execution across "waves" (each wave = one parallel execution batch)
   - `WaveExecution`: wave_id, tasks_in_wave, results, learnings_accumulated
   - Handles failure recovery: if task fails, mark FAILED (don't retry), but continue others
   - Collects "learnings" from all task results (chunks + metadata)

---

### Phase B: Integration with Orchestrator (Week 2)

5. **Refactor `orchestrator.py::decompose_task()`** (*depends on Phase A*)
   - Keep LLM-based decomposition, but return TaskQueue instead of flat Decomposition
   - Add priority scoring: "sector_analysis" = 100 (foundation), "top_stocks" = 80 (depends on sector)
   - For comparison queries: create task with "equal_coverage_validation" dependency

6. **Refactor `orchestrator.py::run_orchestrator()`** (*depends on Phase B.5 + Phase A.4*)
   - Replace static `_topological_layers()` loop with dynamic wave iteration:
     ```
     queue = await decompose_task(task)  # Returns TaskQueue
     while not queue.is_empty():
         wave = scheduler.schedule_next_wave(queue)
         results = await execute_wave(wave)  # Parallel execution
         learnings = extract_learnings(results)
         reflection = reflection_engine.analyze_learnings(learnings, task)
         queue.reprioritize(reflection.suggestions)
         queue.add_tasks(reflection.new_tasks)
     ```
   - Uses WaveExecutor to track multi-wave progression
   - Logs "Wave 1/5: [sector_analysis, top_stocks] executing... → completed with 5 sectors found"

7. **Wire `decision.py::decision_llm()` into reflection loop** (*depends on Phase B.6*)
   - When decision_llm generates `next_queries`, feed them into queue as new TaskQueueItems
   - Instead of immediately spawning children, add to queue with `priority = satisfaction_score`
   - High satisfaction = low priority for new queries; low satisfaction = high priority
   - This makes decision_llm feed the dynamic queue

---

### Phase C: Reflection Loop Feedback (Week 2)

8. **Implement `TaskReflectionEngine.analyze_learnings()`** (*depends on Phase B.6*)
   - LLM-powered analysis: given learnings chunk + original query, suggest what's missing
   - Examples:
     - Learnings: "5 sectors found (tech=high_risk, finance=medium, utilities=low_risk)"
       Query: "Best investment for my profile?" (no profile given)
       → New task: "extract_user_profile" (priority=95, add before sector deep-dive)
     - Learnings: "Top 3 stocks in finance: HDFC, ICICI, Kotak"
       Query: "compare investment options"
       → New task: "correlation_analysis" (priority=70, add to queue)

9. **Implement reprioritization rules** (*depends on Phase A.3*)
   - Rules engine: IF (pattern) THEN (new_priority)
   - Examples:
     - IF (comparison_query AND only_1_entity_explored) THEN (priority of other_entity_task = 100)
     - IF (user_risk_averse AND defensive_stocks_not_yet_analyzed) THEN (priority = 90)
     - IF (query_contains_"why") THEN (priority of explanatory_task = 80)
   - Priority decay: tasks waiting in queue lose priority by 5 every wave (explore breadth before depth)

---

### Phase D: Observability & Diagnostics (Week 3)

10. **Add queue state logging** (*depends on Phase B.6 + Phase C.8*)
    - Per wave: log which tasks executed, what learnings discovered, what reprioritizations happened
    - Example log:
      ```
      Wave 1: [sector_analysis(100), top_stocks(80)] → DONE
        Learnings: 5 sectors, user profile extracted
        Reflection: Need defensive_stocks(95), correlation_analysis(70)
      Wave 2: [defensive_stocks(95), correlation_analysis(70)] → DONE
        Learnings: Volatility data, correlation matrix
        Reflection: Need diversification_rules(60)
      Wave 3: [diversification_rules(60)] → DONE
        Queue empty. Total waves: 3, Total tasks: 5
      ```

11. **Add queue visualization API** (*depends on Phase D.10*)
    - Endpoint: `GET /queue-status` → {current_wave, tasks_done, tasks_pending, next_wave_preview}
    - UI: show queue evolution across waves (diagnostic tool for debugging adaptation behavior)

---

### Phase E: Testing & Validation (Week 3)

12. **Create test file `test_adaptive_queue.py`**
    - Test 1: Simple linear queue (A → B → C, no reflection)
    - Test 2: Comparison query auto-adds 2nd entity when imbalanced
    - Test 3: Learnings trigger reprioritization (move task X from priority 50 to 95)
    - Test 4: New tasks inserted mid-execution
    - Test 5: Circular dependency detection (raises error)
    - Test 6: Parallelization: independent tasks run together, dependent tasks wait

13. **Integration test: End-to-end flow**
    - Query: "Should I invest in CDSL or EMVEE? I'm risk-averse."
    - Expected waves:
      - Wave 1: [profile_extraction, sector_analysis]
      - Wave 2: [cdsl_analysis, emvee_analysis] (parallel, equal priority)
      - Wave 3: [risk_metrics, correlation_analysis]
      - Wave 4: [comparison_synthesis]
    - Verify: all 4 waves executed, comparison node was auto-added, synthesis only after all data ready

---

## Relevant Files

### Current (to understand):
- `orchestrator.py` — replace `run_orchestrator()` loop logic (lines 283-475)
- `blocks/semantic/decision.py` — integrate next_queries into queue
- `core/reasoning.py` — ThinkingProfile already tracks satisfaction, reuse
- `core/satisfaction.py` — SatisfactionTracker feeds into priority calc

### New (to create):
- `core/task_queue.py` — TaskQueue, TaskQueueItem, QueueState
- `core/task_reflection.py` — ReflectionAnalysis, TaskReflectionEngine
- `core/adaptive_scheduler.py` — AdaptiveScheduler, SchedulingDecision
- `core/wave_executor.py` — WaveExecution, multi-wave tracking
- `test_adaptive_queue.py` — comprehensive tests

### Modify:
- `orchestrator.py` — decompose_task return type (TaskQueue), run_orchestrator loop (Phase B.6)
- `blocks/semantic/decision.py` — next_queries feed into queue (Phase B.7)
- Any new reflection API endpoint (Phase D.11)

---

## Verification

1. **Phase A** (Data structures): Python import tests, type checking
   ```bash
   python -c "from agent.core.task_queue import TaskQueue; from agent.core.task_reflection import TaskReflectionEngine"
   ```

2. **Phase B** (Orchestrator integration): Modified run_orchestrator works with TaskQueue
   ```bash
   # Unit test: decompose_task returns TaskQueue with sorted priorities
   # Unit test: schedule_next_wave returns independent tasks
   ```

3. **Phase C** (Reflection): Learnings trigger reprioritization
   ```bash
   pytest test_adaptive_queue.py::test_reprioritization_on_learnings
   ```

4. **Phase D** (Observability): Queue state logged, API works
   ```bash
   curl http://localhost/queue-status
   # Check log output for wave progression
   ```

5. **Phase E** (End-to-end): Comparison query uses multi-wave execution correctly
   ```bash
   pytest test_adaptive_queue.py::test_comparison_query_multi_wave
   # Verify all waves executed in order
   ```

---

## Decisions & Scope

| Item | Decision |
|------|----------|
| **Backward compatibility** | Keep existing decision_llm(), orchestrator.decompose_task() callable signatures. Only change internal queue implementation. |
| **Task retry logic** | Failed tasks do NOT auto-retry (rely on corrective RAG in later phase). Mark FAILED, move on. |
| **Parallel limit** | Adaptive: start with 3 parallel tasks per wave, decrease if error rates rise (future: adaptive pooling). |
| **Reflection LLM calls** | New LLM calls only at wave boundaries, not per-task. Amortized cost: 1 reflection call per 3-5 waves. |
| **Circular dependency detection** | Validate on queue creation (fail fast). Do NOT allow dynamically-added tasks to create cycles. |
| **Queue persistence** | In-memory only (scope: single query execution). NOT persisted across sessions. |

---

## Further Considerations

1. **Reflection LLM Token Cost**
   - Q: Should we reflection-analyze on EVERY wave, or only when satisfaction_score changes?
   - Recommendation: Analyze every wave (transparency + adaptability). Cost ~500 tokens/wave. Mitigate with token budgeting if needed.

2. **User Interaction Hook**
   - Q: After Wave N, should system ask user "which of these new tasks matter most?", or auto-prioritize?
   - Recommendation: Auto-prioritize for now (trust reflection engine). Add user hook in Phase D as optional feature.

3. **Task Interdependencies**
   - Q: Should tasks track "soft dependencies" (prefer after X completes, but don't block)? Or only hard dependencies (must wait)?
   - Recommendation: Hard dependencies only for now. Soft dependencies add complexity (Phase C future work).

---

## Timeline

- **Week 1:** Phase A (data structures) + Phase B.5 (decompose_task returns queue)
- **Week 2:** Phase B.6-7 (orchestrator integration, decision.py wiring) + Phase C (reflection loop)
- **Week 3:** Phase D (observability) + Phase E (testing)
- **Estimate:** 3 weeks, 80-100 hours

