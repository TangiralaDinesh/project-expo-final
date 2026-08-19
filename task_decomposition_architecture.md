# Task Decomposition & Orchestration System Analysis

## Quick Overview
The orchestrator uses a **Directed Acyclic Graph (DAG)-based task execution model** with **per-node decision LLM**, **comparison query detection**, and **pivot loop error recovery**. Task dependencies are explicit; execution is parallel when no dependencies exist.

---

## 1. TASK DECOMPOSITION (decompose_task)

### Location
`/workspaces/project-expo/v2/project-expo-final/agent/orchestrator/orchestrator.py` (lines 128-280)

### Function Signature
```python
async def decompose_task(
    task: str,
    gate_mode: str = "SEMANTIC",
    *,
    client: Optional[NIMClient] = None,
) -> Decomposition
```

### Core Logic
**Three main paths:**

1. **PARAMETRIC mode** → empty nodes (no delegation needed)
2. **Comparison query detection** → creates parallel entity-specific nodes + validation + synthesis nodes
3. **SEMANTIC/HYBRID modes** → LLM-driven decomposition

### Comparison Query Handling (Phase 1)
When comparison detected (e.g., "X vs Y"):
- Creates **parallel retriever nodes** for each entity (no dependencies)
- Adds **validation node** that depends on all entity nodes (ensures equal exploration)
- Adds **synthesis node** that depends on validation (comparison synthesis)
- Sets `is_comparison=True` and `fan_out_eligible=True` for increased parallelism

### Task Representation (TaskNode)
```python
@dataclass
class TaskNode:
    node_id: str                          # e.g., "n1", "compare_entity_0_X"
    subagent_type: SubagentType           # RETRIEVER, CODE_RETRIEVER, SANDBOX, FILE_GENERATOR, CODE_GEN_EXECUTOR
    task: str                             # The actual subtask text
    depends_on: list[str]                 # List of node_id strings this depends on
```

### Decomposition Output Structure
```python
@dataclass
class Decomposition:
    nodes: list[TaskNode]                 # Empty = no delegation needed
    fan_out_eligible: bool                # True = can spawn more than DEFAULT_MAX_SUBAGENTS
    is_comparison: bool                   # True = was a comparison query
    comparison_entities: list[str]        # Names of entities being compared
```

---

## 2. TASK EXECUTION (run_orchestrator)

### Location
`/workspaces/project-expo/v2/project-expo-final/agent/orchestrator/orchestrator.py` (lines 283-475)

### Function Signature
```python
async def run_orchestrator(
    task: str,
    run_subagent: RunSubagentFn,
    gate_mode: str = "SEMANTIC",
    *,
    client: Optional[NIMClient] = None,
    registry: Optional[SubagentRegistry] = None,
    max_subagents: int = DEFAULT_MAX_SUBAGENTS,
    thinking_profile: Optional[ThinkingProfile] = None,
    satisfaction: Optional[SatisfactionTracker] = None,
) -> dict[str, SubagentResult]
```

### Execution Strategy: **DAG-based with Topological Layers**

**Not linear, not simple parallel — explicitly DAG-based:**
1. Build **topological layers** using `_topological_layers()` function
2. Execute each layer in parallel using `asyncio.gather()`
3. Each layer waits for all dependencies to complete before executing

### Key Functions
- `_topological_layers(nodes) → list[list[TaskNode]]`
  - Groups nodes into layers by dependency order
  - Raises ValueError if circular dependencies detected
  - Returns layers ready for parallel execution

### Execution Loop Pattern
```
for layer_idx, layer in enumerate(layers):
    # All nodes in layer execute in parallel via asyncio.gather()
    layer_results = await asyncio.gather(*(_dispatch(n) for n in layer))
    # Each result stored before moving to next layer
```

### Error Recovery: Pivot Loop Integration
Each node execution wraps the subagent call in a **pivot loop**:
```python
decision, branching_options = await run_pivot_loop(
    goal=node.task,
    first_action=first_action,           # Execute subagent once
    generate_hypotheses=gen_hypotheses,  # Generate competing explanations
    run_discriminating_experiment=discriminate,  # Try alternative approach
    branching_enabled=thinking_profile.branching_enabled,  # Tier 3 feature
)
```

**Pivot loop outcomes:**
- Success → return result
- Failure → generate hypotheses (transient error? wrong subagent?), try alternative
- Circuit breaking → mark subagent type as broken after threshold

---

## 3. SUBAGENT RESULT REPRESENTATION

### Location
`/workspaces/project-expo/v2/project-expo-final/agent/core/types.py` (lines 34-55)

### SubagentInput
```python
@dataclass
class SubagentInput:
    task: str                          # The task description
    subagent_type: SubagentType        # Enum: RETRIEVER, CODE_RETRIEVER, etc.
    payload: dict                      # Optional context (upstream results, thinking profile, satisfaction)
    session_id: Optional[str]
    turn_id: Optional[str]
    parent_id: Optional[str]
```

### SubagentResult
```python
@dataclass
class SubagentResult:
    subagent_type: SubagentType
    success: bool
    learnings: list                    # Facts extracted from retrieval
    source_urls: list                  # Sources used
    error_reason: str                  # If success=False
    session_id: Optional[str]
    turn_id: Optional[str]
    parent_id: Optional[str]
```

### Learnings (extracted facts)
```python
@dataclass
class Learning:
    text: str                          # The actual learning/fact
    source_url: str
    score: float                       # Relevance score
```

---

## 4. PER-NODE DECISION LLM (decision.py)

### Location
`/workspaces/project-expo/v2/project-expo-final/agent/blocks/semantic/decision.py`

### Function Signature
```python
async def decision_llm(
    query: str,
    mode: Mode,
    reranked_chunks: list[Chunk],
    depth: int,
    max_depth: int,
    *,
    client: Optional[NIMClient] = None,
    cache: Optional[LLMCache] = None,
) -> Decision
```

### Decision Determination (Dynamic!)

**Decides whether to continue searching or accept current findings:**

#### At `depth < max_depth` (normal nodes)
- Single LLM call at temperature=0.0 (speed matters)
- Produces `Decision` object

#### At `depth == max_depth` (boundary nodes)
- **Two calls at temperature=0.3** (anti-sycophancy self-consistency)
- Only accepts `sufficient=True` if BOTH calls agree
- This directly targets the failure mode where single LLM call reports "sufficient" prematurely

### Decision Output Structure
```python
@dataclass
class Decision:
    sufficient: bool                   # True → terminate this branch
    reason: str                        # Why this decision?
    next_queries: list[str]            # If insufficient: what to search next (2-3 queries)
    next_mode: Optional[Mode]          # Optional pivot between "public" and "kb"
    needs_code_retriever: bool         # Gap is code-shaped?
    request_extension: bool            # RARE: ask for depth extension?
    extension_justification: str       # Why extension needed?
    
    # Phase 1: Comparison query detection
    is_comparison_query: bool          # This is a comparison?
    underexplored_entities: list[str]  # Which entities need more depth?
```

### Next Queries Generation (EIG-based)
When insufficient, generates follow-up queries using **Expected Information Gain (EIG) principle**:
- For comparison queries: prioritize exploring under-covered entities
- Queries should be specific and non-overlapping
- Goal: "which question eliminates the most possibilities?"

### Comparison-Specific Logic
**Phase 1 safety check:** If comparison detected and entity coverage unbalanced (< 0.7):
- Forces `sufficient=False` even if LLM says sufficient
- Ensures both entities explored equally before marking done

---

## 5. TASK DEPENDENCY MANAGEMENT

### Explicit Dependency Model
**TaskNode.depends_on is the dependency source of truth:**
- Empty list `[]` → node can run immediately
- `["n1", "n2"]` → wait for results of n1 and n2

### Topological Resolution
```python
def _topological_layers(nodes: list[TaskNode]) -> list[list[TaskNode]]:
    # Groups nodes into layers
    # Layer 0: all nodes with depends_on=[]
    # Layer 1: all nodes depending only on Layer 0 nodes
    # Layer N: nodes depending on Layer 0..N-1
    # Raises ValueError if circular dependencies
```

### Upstream Result Passing
When node runs, its `payload` includes:
```python
upstream = {dep: results[dep] for dep in node.depends_on if dep in results}
payload = {"upstream": upstream} if upstream else {}
```

Node can access upstream results but is not required to use them.

---

## 6. REFLECTION & LEARNING MECHANISMS

### A. Per-Node Learnings Extraction
**Semantic block (blocks/semantic/block.py):**
- Each node returns `NodeResult` with learnings list
- Learnings automatically extracted from retrieved chunks

### B. Corrective RAG (blocks/semantic/corrective.py)
**3-path retrieval grading BETWEEN retrieval and synthesis:**
- `CORRECT` → use learnings as-is
- `INCORRECT` → discard learnings, re-search with refined query
- `AMBIGUOUS` → keep learnings + fire additional search concurrently
- Max 3 correction cycles per query
- Tracks correction rate (15-30% considered healthy)

### C. Satisfaction Tracker (core/satisfaction.py)
**Reward/punishment system (Tier 1):**
```python
@dataclass
class SatisfactionTracker:
    total_queries: int
    accepted_count: int                # New query = last accepted
    correction_count: int              # User corrections
    severity_score: float              # Accumulated (with decay)
    correction_types: Counter          # What types of corrections?
```

**Patterns learned:**
- `error_correction` (weight 3.0) → worst
- `wanted_more_depth` (weight 2.0) → increase thinking depth
- `incomplete_work` (weight 2.5) → ensure full completion
- `too_verbose` (weight 1.0), `too_slow` (0.5), `style_preference` (0.3)

**Decay:** Severity multiplied by 0.7 every 5 queries (don't punish forever)

### D. Adaptive Thinking Profile (core/reasoning.py)
**Tier 1: Adaptive depth based on:**
1. Gate mode → base parameters
2. Prompt specificity (expert vs casual)
3. Correction history → increases depth for error-prone domains
4. Uncertainty tolerance → how much to explore

```python
@dataclass
class ThinkingProfile:
    max_depth: int
    budget_s: float
    use_deep_propositions: bool
    use_critique: bool
    use_multi_query_expansion: bool
    prompt_specificity: str             # "expert", "standard", "casual"
    self_consistency_calls: int         # 1 or 2 (anti-sycophancy)
    correction_history_active: bool
    uncertainty_tolerance: float        # 0.3=stop early, 0.9=explore lots
    branching_enabled: bool             # Show user choice points?
    confidence_target: float
    knowledge_graph_enabled: bool
    active_pivot_enabled: bool
```

### E. Pivot Loop (core/pivot.py)
**Hypothesis-driven error recovery:**
```
GOAL → ACTION → OBSERVE → HYPOTHESIZE → DISCRIMINATE → PIVOT
```

No blind retries. On failure:
1. Generate competing hypotheses (e.g., "transient error" vs "wrong subagent")
2. Run discriminating experiment (try again, observe)
3. Pivot to best-supported hypothesis

**Output:**
```python
@dataclass
class PivotDecision:
    confirmed_hypothesis: Optional[Hypothesis]
    next_action: str
    circuit_break: list[str]           # Mark subagent types as broken
```

### F. Speculative Questions (llm/speculative_questions.py)
**Phase 4 feature:**
- Generates speculative questions during retrieval
- Uses Bayesian importance scoring
- Identifies high-value clarification questions
- Guides subsequent searches based on user priorities

---

## 7. STATE COORDINATION & OBSERVABILITY (Tier 5)

### Location
`core/parallel_state.py`

### ParallelStateCoordinator
Tracks state of all parallel operations:
```python
class OperationState(Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### Progress Tracking
- Register all nodes upfront
- Call `await coordinator.start(node_id)` when starting
- Call `await coordinator.complete(node_id)` or `coordinator.fail()` on completion
- Query `coordinator.get_status()` for overall progress percentage

---

## 8. COMPARISON QUERY ORCHESTRATION (Phase 1)

### Parallel Entity Retrieval
For query like "compare X vs Y":
1. **Parallel retrieval** for entity X and entity Y (no dependencies)
2. **Validation node** (depends on both) checks if exploration was equal
3. **Synthesis node** (depends on validation) produces comparison

### Balanced Coverage Enforcement
- `decision.py` includes `entity_coverage_balance` metric
- If balance < 0.7 and comparison detected → force `sufficient=False`
- Prevents premature termination when one entity under-explored

### Comparison Entity Tracking
```python
@dataclass
class Decomposition:
    is_comparison: bool
    comparison_entities: list[str]     # Entity names being compared
```

---

## 9. CIRCUIT BREAKER PATTERN

### Location
`orchestrator.py` - `SubagentRegistry` class

```python
class SubagentRegistry:
    def __init__(self, break_threshold: int = 3):
        self._failures: dict[str, int] = {}
        self._threshold = break_threshold
    
    def record_failure(self, subagent_name: str)
    def record_success(self, subagent_name: str)
    def is_circuit_broken(self, subagent_type: SubagentType) -> bool
```

**Logic:**
- 3 consecutive failures → circuit break
- Don't keep trying broken subagent types
- Marked via `PivotDecision.circuit_break` list

---

## 10. KNOWLEDGE GRAPH INTEGRATION (Tier 1)

### Location
`orchestrator.py` - `query_knowledge_graph_for_context()` function

**During orchestration:**
1. Extract key terms from query
2. Query knowledge graph for related concepts
3. Pass `related_concepts` in payload to all subagents

Allows subagents to consider conceptual neighbors during retrieval.

---

## Key Patterns Summary

| Aspect | Pattern |
|--------|---------|
| **Execution Model** | DAG-based with topological layers + parallel asyncio.gather() |
| **Dependencies** | Explicit `TaskNode.depends_on` lists |
| **Task Representation** | TaskNode (node_id, subagent_type, task, depends_on) |
| **Decision Mechanism** | Per-node decision_llm with anti-sycophancy at depth ceiling |
| **Next Query Generation** | Dynamic EIG-based, comparison-aware |
| **Comparison Queries** | Special case: parallel entity nodes + validation + synthesis |
| **Error Recovery** | Pivot loop with hypothesis generation + discrimination |
| **Learning Mechanism** | Learnings extraction + Corrective RAG (3-path) + satisfaction tracking |
| **Adaptation** | ThinkingProfile (depth, budget, specificity, correction history) |
| **Observability** | ParallelStateCoordinator tracks all operations |
| **Health Monitoring** | SubagentRegistry circuit breaker + correction rate metrics |
