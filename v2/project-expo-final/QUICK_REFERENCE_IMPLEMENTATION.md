# Quick Reference: Implementation Guide
**Status**: Ready to Code  
**Target**: Fix comparison query problem + enhance reasoning  

---

## FILE MODIFICATION MATRIX

### Phase 1: Comparison Query Decomposition ⭐ START HERE

#### [NEW] `agent/routing/comparison_detector.py`
```python
# New file: ~200 lines
class ComparisonQueryDetector:
    async def detect(query: str) -> Optional[ComparisonDecision]:
        # Regex patterns: "X vs Y", "X or Y", "difference between X and Y"
        # LLM fallback for ambiguous cases
        # Returns: entity_a, entity_b, comparison_type, confidence

@dataclass
class ComparisonDecision:
    is_comparison: bool
    entity_a: Optional[str]
    entity_b: Optional[str]
    comparison_type: str  # "preference", "technical", "pros_cons", "cost_benefit"
    confidence: float
```

#### [MODIFY] `agent/orchestrator/orchestrator.py`
**Location**: Lines 108-170 (decompose_task function)

```python
# Add at top of decompose_task():
from agent.routing.comparison_detector import ComparisonQueryDetector

# NEW: Check for comparison query (before existing gate_mode checks)
detector = ComparisonQueryDetector()
comparison = await detector.detect(task, client=client)

if comparison and comparison.is_comparison:
    # Create parallel nodes for each entity
    nodes = [
        TaskNode("n_entity_a", SubagentType.RETRIEVER, 
                f"{comparison.entity_a}: {task}", depends_on=[]),
        TaskNode("n_entity_b", SubagentType.RETRIEVER,
                f"{comparison.entity_b}: {task}", depends_on=[]),
    ]
    
    # Optional: synthesis node depends on both
    if len(nodes) <= 2:
        nodes.append(TaskNode("n_synthesis", SubagentType.RETRIEVER,
                    f"Comparative analysis: {task}",
                    depends_on=["n_entity_a", "n_entity_b"]))
    
    return Decomposition(nodes=nodes, fan_out_eligible=True, comparison=comparison)

# ... rest of existing logic ...
```

#### [MODIFY] `agent/query.py`
**Location**: QueryResult dataclass (line ~45)

```python
@dataclass
class QueryResult:
    # ... existing fields ...
    
    # NEW: Tier 2.5 - Comparison Analysis
    comparison_analysis: Optional[dict] = None
    # {
    #   "is_comparison": True,
    #   "entity_a": {"name": "CDSL", "learnings": [...], "summary": "..."},
    #   "entity_b": {"name": "EMVEE", "learnings": [...], "summary": "..."},
    #   "comparison_verdict": "Choose CDSL if X; EMVEE if Y"
    # }
```

#### [NEW] Tests
File: `agent/../test_comparison_decomposition.py`
```python
import pytest
from agent.routing.comparison_detector import ComparisonQueryDetector

@pytest.mark.asyncio
async def test_comparison_detection_cdsl_vs_emvee():
    detector = ComparisonQueryDetector()
    result = await detector.detect("should I buy CDSL or EMVEE?")
    assert result.is_comparison == True
    assert "cdsl" in result.entity_a.lower()
    assert "emvee" in result.entity_b.lower()

@pytest.mark.asyncio
async def test_comparison_decomposition_parallel():
    from agent.orchestrator.orchestrator import decompose_task
    decomp = await decompose_task("should I buy CDSL or EMVEE?", "SEMANTIC")
    assert len(decomp.nodes) >= 2
    assert all(n.subagent_type == SubagentType.RETRIEVER for n in decomp.nodes)
    assert decomp.fan_out_eligible == True
```

---

### Phase 2: Critique-Guided Retrieval

#### [MODIFY] `agent/core/critique.py`
**Location**: After existing critique functions (add new function)

```python
# Add NEW function ~100 lines

async def run_critique_on_retrieval(
    query: str,
    learnings: list[str],
    depth: int,
    max_depth: int,
    *,
    client: Optional[NIMClient] = None,
) -> CritiqueResult:
    """Run 4-persona critique on partial retrieval results."""
    # For each persona: ask if gaps exist in current learnings
    # Aggregate gaps and suggested next queries
    # Return: CritiqueResult with gaps_found, suggested_queries

@dataclass
class CritiqueResult:
    gaps_found: list[str]
    suggested_queries: list[str]
    consensus_strength: float  # 0-1
```

#### [MODIFY] `agent/blocks/semantic/block.py`
**Location**: semantic_retriever_block function (around line ~163, after decision_llm)

```python
# After: top_chunks = await rerank_chunks(...)
# Before: decision = await decision_llm(...)

# NEW: Stage 4.5 - Critique-guided continuation
if critique_fn and inp.depth < inp.max_depth:
    critique_result = await critique_fn(
        query=inp.query or query_label,
        learnings=[c.text for c in top_chunks],
        depth=inp.depth,
        max_depth=inp.max_depth,
    )
    
    if critique_result.gaps_found:
        logger.info("Critique identified gaps: %s", critique_result.gaps_found)
        # Force these as next_queries even if decision.sufficient
        for gap_query in critique_result.suggested_queries:
            decision.next_queries.append(gap_query)
```

#### [NEW] Tests
```python
@pytest.mark.asyncio
async def test_critique_detects_missing_entity():
    from agent.core.critique import run_critique_on_retrieval
    
    learnings = [
        "CDSL is a centralized system for X",
        "CDSL costs $100 per month",
    ]
    
    result = await run_critique_on_retrieval(
        query="should I buy CDSL or EMVEE?",
        learnings=learnings,
        depth=1,
        max_depth=3,
    )
    
    # Should identify missing EMVEE info
    assert len(result.gaps_found) > 0
    assert any("emvee" in gap.lower() for gap in result.gaps_found)
```

---

### Phase 3: Progressive Scraping

#### [NEW] `agent/core/progressive_scraping.py`
```python
# New file: ~400 lines

@dataclass
class ProgressiveScrapingPhase:
    phase_number: int
    concepts_to_explore: list[str]
    depth_budget_s: float
    query_to_ask_user: Optional[str] = None
    expected_learnings_count: int = 3

async def run_progressive_scraping(...) -> ProgressiveScrapingResult:
    """Multi-phase retrieval with user guidance."""
    # Phase 0: Quick overview (30s)
    # Ask: "Which aspects matter?"
    # Phase 1: Deep dive (60s)
    # Ask: "How important is factor X?"
    # Phase 2: Comprehensive (unlimited)
```

#### [MODIFY] `agent/query.py`
**Location**: run_query function (after entry_gate + clarify, before orchestrator)

```python
# NEW: Progressive scraping flow (around line ~180)
if progressive_mode and gate_result.needs_retrieval:
    comparison = await comparison_detector.detect(query, client=client)
    
    if comparison and comparison.is_comparison:
        progressive = await run_progressive_scraping(
            query, comparison, client=client, code_tool_fn=code_tool_fn,
        )
        
        if progressive.user_guidance_needed:
            return QueryResult(
                answer="",
                progressive_guidance_needed=True,  # NEW field
                progressive_status=progressive,    # NEW field
                aspect_question=progressive.aspect_question,
                # Don't run full orchestrator yet
            )
    
    # User has provided guidance (subsequent call)
    if aspect_selection:
        # Run phases 1-2 with user's priorities
        pass

# Then existing orchestrator flow continues...
```

---

### Phase 4: Speculative Questioning

#### [NEW] `agent/llm/speculative_questioning.py`
```python
# New file: ~200 lines

async def generate_speculative_questions_during_retrieval(
    query: str,
    current_learnings: list[Learning],
    depth: int,
    max_depth: int,
    *,
    client: Optional[NIMClient] = None,
) -> list[SpeculativeQuestion]:
    """Generate Bayesian follow-up questions while retrieval runs."""

@dataclass
class SpeculativeQuestion:
    text: str
    reasoning: str
    if_yes_searches: list[str]
    if_no_searches: list[str]
    prior_probability: float  # P(user cares about this)
```

#### [MODIFY] `agent/query.py`
**Location**: run_query_stream function (NEW streaming variant or enhance existing)

```python
# Add streaming variant that yields speculative questions
async def run_query_stream(...) -> AsyncIterator[StreamEvent]:
    """Streaming query with speculative questions."""
    
    # ... existing gate + clarify ...
    
    # Stage 3: Orchestrator with streaming
    orch_task = asyncio.create_task(run_orchestrator(...))
    
    # While orchestrator runs, generate speculative questions
    while not orch_task.done():
        learnings_so_far = []  # Track interim results
        
        if len(learnings_so_far) > 0 and len(learnings_so_far) < 4:
            spec_qs = await generate_speculative_questions_during_retrieval(
                query, learnings_so_far, depth=1, max_depth=3, client=client,
            )
            
            for q in spec_qs:
                yield StreamEvent(
                    type="speculative_question",
                    data=q.text,
                    metadata={"reasoning": q.reasoning, "prior": q.prior_probability},
                )
        
        await asyncio.sleep(1.0)
    
    # ... synthesis + final answer ...
```

---

### Phase 5: Parallel State Machine

#### [NEW] `agent/core/parallel_state.py`
```python
# New file: ~300 lines

class OperationState(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED_WAITING = "blocked_waiting"
    RETRIEVING = "retrieving"
    PROCESSING = "processing"
    DECISION_PENDING = "decision_pending"
    SPAWNING_CHILDREN = "spawning_children"
    CHILDREN_RUNNING = "children_running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"

class ParallelStateCoordinator:
    async def create_operation(op_id: str, query: str, ...) -> ParallelOperationState
    async def update_state(op_id: str, new_state: OperationState, ...)
    async def get_status() -> dict  # Full status snapshot
```

#### [MODIFY] `agent/blocks/semantic/block.py`
**Location**: semantic_retriever_block function (add state tracking)

```python
# At start of semantic_retriever_block:
state_coordinator = state_coordinator or get_parallel_state_coordinator()
op_id = inp.operation_id or f"retriever_{uuid4()}"

op_state = await state_coordinator.create_operation(
    op_id=op_id,
    query=inp.query,
    depth=inp.depth,
    depends_on=inp.depends_on or [],
)

# After each major stage, update state:
await state_coordinator.update_state(op_id, OperationState.RETRIEVING)
# ... retrieve ...
await state_coordinator.update_state(op_id, OperationState.PROCESSING)
# ... process ...
await state_coordinator.update_state(op_id, OperationState.COMPLETE)
```

---

## DEPENDENCY GRAPH

```
Phase 1 (Comparison Detector)
    ↓
Phase 2 (Critique Integration) — depends on Phase 1
    ↓
Phase 3 (Progressive Scraping) — can be parallel with Phase 2
    ↓
Phase 4 (Speculative Questions) — can be parallel with Phase 3
    ↓
Phase 5 (State Machine) — can be parallel with Phases 3-4
```

### Recommended Order
1. **Do Phase 1 first** (solves main problem)
2. **Then Phase 2** (catches Phase 1 edge cases)
3. **Then Phases 3, 4, 5 in parallel** (independent features)

---

## TEST FILES TO CREATE/UPDATE

### Phase 1 Tests
```
test_comparison_decomposition.py       [NEW]
  - test_comparison_detection_cdsl_vs_emvee()
  - test_comparison_detection_pattern_matching()
  - test_comparison_decomposition_creates_parallel_nodes()
  - test_non_comparison_query_unchanged()
```

### Phase 2 Tests
```
test_critique_guided_retrieval.py      [NEW]
  - test_critique_detects_missing_concepts()
  - test_critique_suggests_follow_up_queries()
  - test_critique_consensus_strength()
```

### Phase 3 Tests
```
test_progressive_scraping.py           [NEW]
  - test_phase_0_quick_overview()
  - test_phase_0_generates_user_question()
  - test_phase_1_with_aspect_selection()
```

### Phase 4 Tests
```
test_speculative_questioning.py        [NEW]
  - test_questions_generated_during_retrieval()
  - test_questions_have_search_guidance()
  - test_prior_probability_assigned()
```

### Phase 5 Tests
```
test_parallel_state_machine.py         [NEW]
  - test_operation_state_transitions()
  - test_dependency_blocking()
  - test_concurrent_operations_tracked()
```

---

## IMPORT STATEMENTS TO ADD

### `agent/orchestrator/orchestrator.py`
```python
from agent.routing.comparison_detector import ComparisonQueryDetector, ComparisonDecision
```

### `agent/blocks/semantic/block.py`
```python
from agent.core.critique import run_critique_on_retrieval, CritiqueResult
from agent.core.parallel_state import (
    ParallelStateCoordinator, 
    OperationState,
    get_parallel_state_coordinator,
)
```

### `agent/query.py`
```python
from agent.routing.comparison_detector import ComparisonQueryDetector
from agent.core.progressive_scraping import run_progressive_scraping, ProgressiveScrapingResult
from agent.llm.speculative_questioning import generate_speculative_questions_during_retrieval
```

---

## FEATURE FLAGS (Optional: Add to config/feature_flags.py)

```python
class FeatureFlags:
    # Existing
    connectivity_enabled: bool
    progressive_revelation_enabled: bool
    branching_enabled: bool
    code_execution_enabled: bool
    
    # NEW
    comparison_query_detection: bool = True      # Phase 1
    critique_guided_retrieval: bool = True       # Phase 2
    progressive_scraping: bool = False           # Phase 3 (beta)
    speculative_questioning: bool = False        # Phase 4 (beta)
    parallel_state_tracking: bool = False        # Phase 5 (debug)
```

---

## DEBUGGING CHECKLIST

When testing Phase 1-5:

### Phase 1
- [ ] Query "CDSL vs EMVEE" → orchestrator creates 2 nodes
- [ ] Both nodes have SubagentType.RETRIEVER
- [ ] Both nodes have depends_on=[]
- [ ] Query result includes comparison_analysis field
- [ ] Test with multiple comparison patterns

### Phase 2
- [ ] After retrieving CDSL, critique fires
- [ ] Critique identifies missing EMVEE
- [ ] New query spawned for EMVEE
- [ ] System logs show "Critique identified gaps"

### Phase 3
- [ ] Phase 0 completes in ~30s
- [ ] User question appears: "Which aspects matter?"
- [ ] Phase 1 respects aspect_selection parameter
- [ ] Result includes aspect_question field

### Phase 4
- [ ] Speculative questions appear while retrieval runs
- [ ] Questions have reasoning + search guidance
- [ ] Prior probabilities assigned (0-1 range)

### Phase 5
- [ ] State transitions tracked via coordinator
- [ ] Status API returns operation states
- [ ] Child operations have correct depends_on links

---

## ESTIMATED LINE COUNT CHANGES

| Phase | New Files | Modified Files | New Lines | Modified Lines |
|-------|-----------|-----------------|-----------|-----------------|
| 1 | 1 | 2 | ~250 | ~50 |
| 2 | 0 | 2 | ~100 | ~30 |
| 3 | 1 | 1 | ~400 | ~40 |
| 4 | 1 | 1 | ~200 | ~30 |
| 5 | 1 | 1 | ~300 | ~40 |
| **TOTAL** | **4** | **7** | **~1,250** | **~190** |

---

## QUICK START (Today)

```bash
# 1. Create Phase 1 detector
touch agent/routing/comparison_detector.py
# ... implement ~200 lines ...

# 2. Modify orchestrator decompose_task()
# ... ~40 lines in agent/orchestrator/orchestrator.py ...

# 3. Add to QueryResult
# ... ~10 lines in agent/query.py ...

# 4. Run tests
pytest test_comparison_decomposition.py -v

# 5. Test manually
# python -c "
# from agent.query import run_query
# result = await run_query('should I buy CDSL or EMVEE?')
# print(result.comparison_analysis)
# "
```

---

