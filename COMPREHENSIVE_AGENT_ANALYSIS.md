# Comprehensive Agent Implementation Analysis — v1 vs v2

**Analysis Date**: 2026-08-17  
**Scope**: Complete v1 & v2 implementation, directory structure, architecture, and degradation  
**Format**: Facts only, no summaries — organized by requested focus areas

---

## PART 1: FILE ORGANIZATION & DIRECTORY STRUCTURE

### V1 Structure (24 directories, 86 files)
```
agent/
├── blocks/
│   ├── base.py
│   ├── code/block.py (code retriever)
│   ├── sandbox/ (planner, validator)
│   └── semantic/ (core retrieval engine)
│       ├── block.py (main recursive retriever)
│       ├── chunk.py, embed.py, rerank.py
│       ├── decision.py, corrective.py
│       └── sources.py (resolve URLs)
├── cache/
│   ├── llm_cache.py, embed_cache.py
│   ├── lru_cache.py, semantic_cache.py
├── config/
│   ├── budgets.py (concurrency limits, timeouts, depths)
│   ├── feature_flags.py, settings.py
├── connectors/ (github.py, google_drive.py)
├── core/
│   ├── reasoning.py (ThinkingProfile, specificity classification)
│   ├── progressive.py (geohash-style progressive loading)
│   ├── pivot.py (hypothesis/discriminate loop)
│   ├── branching.py (Tier 3 multi-choice options)
│   ├── branching_session.py (session tracking)
│   ├── critique.py (4-persona multi-critic system)
│   ├── satisfaction.py (user feedback loop)
│   ├── context_policy.py (token budgeting, auto-summarization)
│   ├── code_execution.py (Python/Bash sandboxed execution)
│   ├── observability.py (metrics tracking)
│   └── types.py (core dataclasses)
├── knowledge/
│   ├── graph_rag.py (knowledge graph traversal)
│   ├── graph_store.py (entity extraction, triple storage)
│   ├── kb_store.py (internal KB vector store)
│   ├── doc_ingest.py, folder_ingest.py, pdf_ingest.py, image_ingest.py
├── llm/
│   ├── synthesis.py (final answer generation with persona)
│   ├── synthesis_levels.py (depth-aware synthesis)
│   ├── persona.py (Jarvis personality system)
│   ├── clarify.py (generate clarifying questions)
│   ├── client.py (NIM LLM client wrapper)
├── memory/ (session_store.py, store.py)
├── orchestrator/orchestrator.py (task decomposition, subagent coordination)
├── query.py (main entry point, orchestration)
├── query_integration.py
├── retrieval/coordinator.py
├── routing/
│   ├── entry_gate.py (6-way classification)
│   ├── input_router.py
│   ├── intent_classifier.py
├── skills/
│   ├── executor.py, registry.py
│   └── builtin/ (code_reviewer, data_analyzer, deck_builder, etc.)
├── tools/
│   ├── executor.py (parallel tool execution up to 4)
│   ├── tool_registry.py (tool registration)
│   ├── agent_tools.py (tool definitions)
│   ├── brave_search.py, web_fetch.py
│   ├── jarvis_bridge.py, output_renderer.py
└── transport/server.py (FastAPI server)
```

### V2 Structure (24 directories, 100 files) — NEW FILES
```
agent/core/ (14 NEW files)
├── adaptive_scheduler.py (Plan 1: wave-based scheduling)
├── wave_executor.py (Plan 1: multi-wave parallel execution)
├── task_queue.py (Plan 1: adaptive priority queue)
├── task_reflection.py (Plan 1: LLM analysis of learnings → new tasks)
├── parallel_state.py (Plan 1: state coordination across parallel branches)
├── progressive_scraping.py (enhanced progressive loading)
├── queue_observability.py (metrics for queue operations)
├── code_execution_integration.py (Tier 4 code execution wiring)
├── decision_queue_integration.py (queue + decision integration)
├── orchestrator_adapter.py (adapter pattern for new queue system)

agent/llm/ (2 NEW files)
├── speculative_questions.py (Phase 4: Bayesian question generation)
├── speculative.py (speculative retrieval strategy)

agent/routing/ (1 NEW file)
├── comparison_detector.py (detect "X vs Y" queries)

agent/tools/ (1 NEW file)
├── code_execution.py (tool wrapper for code execution)
```

**KEY DIFFERENCE**: V2 adds 9 new core orchestration files for multi-wave adaptive execution. V1 uses static DAG + pivot loop. V2 introduces dynamic task queue.

---

## PART 2: REASONING LAYER ARCHITECTURE

### V1: Hierarchical Progressive Retrieval (Geohash Model)
**Location**: `core/progressive.py`, `blocks/semantic/block.py`

**Mechanism**:
```python
# Depth-based recursion with information-gain gate
async def semantic_retriever_block(inp: BlockInput):
    chunks = await resolve_sources(inp)  # Get initial results
    chunks = await embed_chunks(chunks)   # Embed
    chunks = await rerank_chunks(chunks)  # Rerank
    
    # DECISION: recurse or stop?
    decision = await decision_llm(
        query=inp.query,
        current_chunks=chunks,
        depth=inp.depth,
        max_depth=inp.max_depth,
    )
    
    if decision.should_recurse and information_gain > 0.3:
        # Spawn children for each sub-query
        children = await asyncio.gather(*[
            semantic_retriever_block(child_input)
            for child_input in decision.child_queries
        ])
```

**Characteristics**:
- ✅ **Single-query progressive retrieval**: Given "What is OAuth2?", retrieves progressively deeper
- ✅ **Information-gain gate**: Stops recursing if new chunks don't add value
- ✅ **Parallel sibling execution**: Multiple child queries run concurrently
- ✅ **Global deadline enforcement**: Hard stop at 28 seconds (GLOBAL_BUDGET_S)
- ✅ **Per-node timeout**: 8 seconds per node (NODE_TIMEOUT_S)
- ⚠️ **Single query handling**: Not designed for multi-entity queries like "CDSL vs EMVEE"

**Architecture Pattern**: Tree-based recursive depth-first search with breadth-first parallelism at each level

### V2: Adaptive Wave-Based Multi-Query Execution
**Location**: `core/adaptive_scheduler.py`, `core/wave_executor.py`, `core/task_queue.py`, `core/task_reflection.py`

**New Mechanism**:
```python
# Wave-based execution with dynamic task reflection
class Wave:
    tasks: List[TaskQueueItem]  # Tasks ready to execute in parallel
    max_parallel: int = 3       # Parallelization limit
    
async def wave_executor.execute_wave(wave: SchedulingDecision):
    # Execute all tasks in parallel (up to limit)
    results = await asyncio.gather(*[
        run_subagent(task) for task in wave.tasks_to_execute
    ])
    
    # REFLECTION: analyze learnings, detect gaps
    reflection = await task_reflection_engine.analyze_learnings(
        learnings=results,
        original_query=query,
    )
    
    # REPRIORITIZATION: add new tasks or reorder existing ones
    for new_task in reflection.new_tasks_detected:
        queue.enqueue(new_task)
    
    # NEXT WAVE: schedule only the high-priority remaining tasks
    next_wave = scheduler.schedule_next_wave(queue)
```

**Characteristics**:
- ✅ **Multi-wave execution**: Queries decomposed into waves, each with parallel tasks
- ✅ **Adaptive task reflection**: After each wave, LLM analyzes what was learned and recommends new tasks
- ✅ **Dynamic reprioritization**: Tasks can be reordered mid-execution based on learnings
- ✅ **Comparison query support**: Should split "CDSL vs EMVEE" into parallel tasks
- ⚠️ **NOT INTEGRATED INTO QUERY.PY**: Core infrastructure exists but not wired into main flow

**Architecture Pattern**: Queue-based adaptive task scheduling with mid-flight course correction

### COMPARISON: v1 vs v2 Reasoning Approaches

| Aspect | v1 | v2 |
|--------|----|----|
| **Query Type** | Single concept, progressively deeper | Multiple entities, parallel analysis |
| **Decomposition** | LLM-driven at orchestrator level | Adaptive queue with per-wave reflection |
| **Parallelism** | Siblings within one branch | Cross-query parallelism (waves) |
| **Adaptation** | Information-gain threshold | Learning-driven task reflection |
| **Multi-query** | ❌ Not native | ✅ Native (but not wired) |
| **Token Strategy** | Global deadline (28s) | Per-wave time tracking |

---

## PART 3: PERSONA SYSTEM

### V1: Persona Architecture
**Location**: `llm/persona.py`, `llm/synthesis.py`

**Components**:
```python
# Persona Definition (lines 1-100 in persona.py)
JARVIS_PERSONA = """
You are Jarvis — a close, trusted assistant...
1. WARM BUT NOT SYCOPHANTIC: Direct, human, occasionally wry
2. CONNECT DOTS PROACTIVELY: Name patterns, add context
3. ANTICIPATE: Suggest next exploration step
4. BE HONEST ABOUT UNCERTAINTY: Admit knowledge gaps
5. CALIBRATE DEPTH: Match answer depth to query specificity
6. TRANSFER LEARNING: Connect to user's prior knowledge
7. DON'T DUMP: Give proportional answers, expand on demand
"""

# Synthesis with Persona
async def global_synthesis_llm(query, learnings, prompt_specificity="standard"):
    persona_prompt = build_persona_prompt(prompt_specificity)
    messages = [
        {"role": "system", "content": persona_prompt + _SYSTEM_PROMPT},
        {"role": "user", "content": f"Query: {query}\n\nLearnings:\n{learnings_block}"},
    ]
    # temperature=0.2 (low, deterministic)
```

**Specialist/Expectation/Critique Integration**:
```python
# Four Critique Personas Defined (core/critique.py lines 30-70)
_BRUTAL_CRITIC = CritiquePersona(
    system_prompt="Find what is actually wrong... Do not soften findings"
)
_BRUTAL_EXPECTATIONIST = CritiquePersona(
    system_prompt="Would someone genuinely expert call this excellent?"
)
_BRUTAL_REALIST = CritiquePersona(
    system_prompt="Does this actually work?... not just ambition"
)
_OVERTHINKER = CritiquePersona(
    system_prompt="Assume this WILL fail. Find edge cases, race conditions..."
)

async def run_multi_critique(artifact, goal, client):
    # Run all 4 personas in parallel
    results = await asyncio.gather(*[
        client.chat([system_prompt, artifact])
        for system_prompt in ALL_PERSONAS
    ])
```

**Key Facts**:
- ✅ Persona system **completely defined** with 7 traits
- ✅ Multi-critique system with **4 distinct personas** defined (undifferentiated critique actively decreases accuracy)
- ✅ Persona is **prepended to synthesis prompts** (affects presentation, not retrieval)
- ✅ Proactive connections and anticipation prompts **defined**
- ❌ **run_multi_critique() is NEVER CALLED in main query flow**
  - Defined in `critique.py:123` but no usages in query.py or orchestrator.py
  - Only grade_retrieval() is called (CRAG grading, not persona critique)
- ❌ Specialists/expectations not separately instantiated in reasoning
- ⚠️ Persona affects **only synthesis**, not decision-making or retrieval

**Actual Usage**:
```python
# query.py line 267: Only CRAG grading is used
grade = await grade_retrieval(effective_query, all_learnings, client=client)
if grade.grade == RetrievalGrade.CORRECT:
    answer = await global_synthesis_llm(...)  # Persona applied here
elif grade.grade == RetrievalGrade.INCORRECT:
    answer = await direct_answer_llm(...)     # Different path, no critique
```

### V2: No Changes to Persona System
- Persona system copied from v1 unchanged
- Multi-critique still defined but unused
- Comparison detector added but doesn't use critique
- Speculative questions framework exists but doesn't integrate critique personas

---

## PART 4: KNOWLEDGE STRUCTURES

### V1: Knowledge Graph Integration
**Location**: `knowledge/graph_rag.py`, `knowledge/graph_store.py`, `blocks/semantic/block.py`

**Files & Implementation**:
```python
# graph_store.py: Graph data structure
@dataclass
class Triple:
    subject: str
    predicate: str
    object: str

class GraphStore:
    def __init__(self):
        self.triples: list[Triple] = []
        self.entities: dict[str, set[str]] = {}  # entity -> related entities
    
    async def extract_entities(query: str):
        # LLM extracts entities from query
        # Returns: ["OAuth2", "JWT", "Session-based"]

# graph_rag.py: Smart routing
def should_use_graph(query: str) -> bool:
    # Fast LLM call to decide: "Is this a relationship/multi-hop query?"
    # Relationship-heavy: "Connect historical facts about X across Y domains"
    # Simple: "What is X?" → no graph needed
    
async def graph_enhanced_retrieval(query, query_vec, client, top_k=4):
    # 1. Extract entities from query
    # 2. Graph traverse from query entities → connected facts
    # 3. Merge vector results + graph results
    # 4. Deduplicate by entity overlap

# blocks/semantic/block.py: Integration point
async def semantic_retriever_block(inp):
    chunks = await resolve_sources(inp)
    chunks = await embed_chunks(chunks)
    chunks = await rerank_chunks(chunks)
    
    # Graph enhancement (if beneficial)
    if should_use_graph(inp.query):
        graph_chunks = await graph_enhanced_retrieval(
            inp.query, query_vec, client, top_k=4
        )
        chunks.extend(graph_chunks)
        chunks = await rerank_chunks(chunks)  # Re-rank merged results
```

**Key Facts**:
- ✅ Knowledge graph **structure fully defined** (graph_store.py)
- ✅ Graph traversal **fully implemented** (graph_rag.py lines 83-120)
- ✅ Smart routing decision **implemented** (should_use_graph, lines 41-70)
- ✅ **Integrated into retrieval block** (semantic/block.py lines 94-102)
- ✅ Adds ~160ms latency (entity extraction 150ms + traversal 5ms + merge 5ms)
- ⚠️ Graph population: No code shown for actual graph building from ingested docs
- ⚠️ Entity extraction uses LLM call (expensive, replicated per query)

**Graph Storage**: `knowledge/kb_store.py`
```python
class KBStore:
    async def search(query_vec, top_k=10):
        # Vector similarity search in internal KB
        # Returns top-k relevant chunks
    
    async def add_documents(docs):
        # Ingest documents into KB
        # Chunks, embeds, stores
```

### V2: Knowledge Graph — No Changes
- Graph store copied unchanged from v1
- Graph RAG still in codebase
- Speculative questions could enhance graph querying but doesn't

### Knowledge Artifacts in Filesystem
**Status**: 
- ✅ graph_rag.py exists and is referenced
- ✅ graph_store.py defines Triple structure
- ✅ kb_store.py exists
- ⚠️ No `pivots/` directory with pivot definitions
- ⚠️ No `critiques/` directory with critique templates
- ⚠️ These would be external files if they existed, currently only in-code

---

## PART 5: RETRIEVAL STRATEGY

### V1: Retrieval Pattern
**Entry Point**: `query.py:run_query()`

```python
async def run_query(query, memory_context=None, ...):
    # ── Stage 1: Entry gate + Clarify (parallel) ──
    gate_task = entry_gate(query, client=client)
    clarify_task = generate_clarifying_question(query, ...)
    
    gate_result, clarify_result = await asyncio.gather(
        gate_task, clarify_task, return_exceptions=True
    )
    
    # ── Thinking profile (adapts to prompt + user history) ──
    specificity = classify_prompt_specificity(query)
    profile = get_thinking_profile(
        gate_result.mode,  # "SEMANTIC", "CODE", "HYBRID", "PARAMETRIC"
        query,
        effort_bias
    )
    
    # ── Case 0: Skill matched → execute skill ──
    # ── Case 0b: URL direct → fetch directly ──
    # ── Case 1: No retrieval needed ──
    # ── Case 2: Clarification blocks search ──
    
    # ── Stage 2: Orchestrator → blocks ──
    orch_results = await run_orchestrator(
        effective_query,
        gate_mode=gate_result.mode,
        thinking_profile=profile,
    )
    
    # ── Collect all learnings ──
    all_learnings, all_urls = _collect_results(orch_results)
    
    # ── Stage 3: CRAG grading + synthesis ──
    if all_learnings:
        grade = await grade_retrieval(effective_query, all_learnings, client)
        
        if grade.grade == RetrievalGrade.CORRECT:
            answer = await global_synthesis_llm(...)
        elif grade.grade == RetrievalGrade.INCORRECT:
            # Discard learnings, fallback to direct answer + disclaimer
            answer = await direct_answer_llm(...)
        else:  # AMBIGUOUS
            answer = await global_synthesis_llm(...)
```

**Progressive vs Single Query**:
- ✅ Each **semantic_retriever_block** recursively retrieves progressively deeper
- ✅ Recursion decision: based on information-gain threshold (0.3)
- ✅ But **SINGLE QUERY at query.py level** — one effective_query goes to orchestrator
- ❌ **NOT multi-query**: For "CDSL vs EMVEE", no automatic split into parallel retrieval branches

**Orchestrator Behavior** (`orchestrator.py`):
```python
async def run_orchestrator(task, run_subagent, gate_mode, ...):
    # LLM decomposition (fast-path for known modes)
    if gate_mode == "SEMANTIC":
        decomposition = Decomposition(nodes=[
            TaskNode("n1", SubagentType.RETRIEVER, task),
        ])  # Single node!
    
    # Topological execution
    layers = _topological_layers(decomposition.nodes)
    for layer in layers:
        results = await asyncio.gather(*[
            _dispatch(node) for node in layer
        ])
```

**CRAG Strategy** (`blocks/semantic/corrective.py`):
```python
async def grade_retrieval(query, learnings, client):
    # Fast path 1: if no learnings → INCORRECT
    # Fast path 2: if avg score > 0.85 && len >= 3 → CORRECT (confidence 0.9)
    # LLM path: Otherwise, ask LLM to grade
    
    # Three grades:
    # - CORRECT: Use learnings as-is
    # - INCORRECT: Discard learnings, direct answer + disclaimer
    # - AMBIGUOUS: Keep learnings but note uncertainty
```

### V2: Retrieval Pattern Changes
**New Comparison Detection** (`routing/comparison_detector.py`):
```python
class ComparisonQueryDetector:
    COMPARISON_PATTERNS = [
        r"(\w+)\s+(?:vs|vs\.|\bversus\b)\s+(\w+)",
        r"(?:should\s+i|should\s+we)\s+(?:buy|use|choose)\s+(\w+)\s+or\s+(\w+)",
        r"(?:compare|comparison)\s+(?:between\s+)?(\w+)\s+(?:and|\+|with)\s+(\w+)",
        # ... 8 more patterns
    ]
    
    async def detect(query):
        # Fast regex path first
        # Fall back to LLM for edge cases
        return ComparisonDecision(
            is_comparison=True,
            entities=[ComparisonEntity(name="CDSL"), ...],
            comparison_type="vs",
            confidence=0.92
        )
```

**BUT**: In `query.py:run_query()` — comparison_analysis field is added to result but:
- ✅ Detector is called (in entry_gate or routing layer)
- ❌ **Result is NOT used to split retrieval**
- ❌ Still goes to single orchestrator call
- ❌ Comparison entities are NOT passed to orchestrator for parallel decomposition

**Adaptive Queue System** (NOT integrated into query.py):
```python
# task_queue.py exists with full implementation
class TaskQueue:
    def enqueue(item: TaskQueueItem):
        # Item has: task_id, priority, dependencies, status
    
    def get_next_wave(max_parallel=3):
        # Return next batch of ready tasks
    
    def reprioritize(task_id, new_priority):
        # Adjust priority mid-execution

# adaptive_scheduler.py exists
class AdaptiveScheduler:
    def schedule_next_wave(queue, parallelization_limit=3):
        # Decide what runs in next wave

# task_reflection.py exists
class TaskReflectionEngine:
    async def analyze_learnings(learnings, original_query):
        # LLM analyzes and suggests new tasks/reprioritizations
        # Returns ReflectionAnalysis with new_tasks_detected, reprioritizations, gaps
```

**Summary**: V2 has infrastructure for multi-wave adaptive retrieval but **NOT WIRED INTO query.py**

---

## PART 6: TOOL EXECUTION

### V1: Tool Framework
**Location**: `tools/executor.py`, `tools/tool_registry.py`, `tools/agent_tools.py`

**Executor Framework**:
```python
class ToolExecutor:
    async def execute_tool(self, tool_name: str, args: dict):
        """Execute single tool"""
        raise NotImplementedError()
    
    async def execute_parallel(self, calls: List[dict], max_concurrent=4):
        """Execute up to 4 tools in parallel"""
        # Batches into groups of max_concurrent
        # Runs each batch concurrently

@dataclass
class ToolCallResult:
    tool_name: str
    success: bool
    output: str
    error: str
    duration_ms: float
```

**Tool Registry Framework**:
```python
class ToolRegistry:
    def register(self, name: str, tool: Tool):
        """Register a tool"""
    
    def get(self, name: str) -> Tool:
        """Get tool by name"""
    
    def list_tools(self) -> List[str]:
        """List all registered tools"""
```

**Registered Tools**:
- ❌ **Tool Registry is EMPTY**
  - `tool_registry.py` defines the framework
  - `agent_tools.py` defines tool interfaces
  - **No tools are actually instantiated/registered**
  - No code execution tools wired
  - No file creation tools wired

**Code Execution Framework** (`core/code_execution.py`):
```python
class ExecutionLanguage(str, Enum):
    PYTHON = "python"
    BASH = "bash"
    JAVASCRIPT = "javascript"  # Future

class ExecutionSafety(str, Enum):
    SANDBOXED = "sandboxed"     # No file system, no network
    RESTRICTED = "restricted"   # Limited file system
    FULL = "full"               # Full permissions

class PythonExecutor:
    @staticmethod
    async def execute(request: ExecutionRequest):
        # Runs: python3 -c "code"
        # Timeout: request.timeout_s (default 10s)
        # Returns: ExecutionResult with stdout, stderr, exit_code

class BashExecutor:
    @staticmethod
    async def execute(request: ExecutionRequest):
        # Blocks dangerous commands: rm -rf, sudo, ssh, wget/curl
        # Runs: bash -c "code"
        # Timeout: request.timeout_s
```

**Integration**: Code execution is implemented but:
- ❌ **Not called from query.py**
- ❌ **Not in tools registry**
- ⚠️ Would require tool registration + orchestrator invocation

### V2: Tool Execution Changes
**New Tool Wrapper** (`tools/code_execution.py`):
```python
# Lightweight wrapper, not a full new tool implementation
# Just adapts core/code_execution.py for tool calling
```

**Tool Registry Status**: Same as v1 — **EMPTY, no tools registered**

**Integration in query.py**:
```python
# v2 query.py line 180-185
use_code_execution = features.code_execution_enabled if not explicit else use_code_execution
# But no actual tool execution in the flow
```

### Summary: Tool Execution
| Aspect | v1 | v2 |
|--------|----|----|
| Framework | ✅ Complete | ✅ Complete |
| Executor | ✅ Parallel capable (4 max) | ✅ Same |
| Code execution | ✅ Implemented (Python, Bash, JS) | ✅ Same |
| Tool registry | ❌ Empty | ❌ Empty |
| Integration | ❌ Not called | ❌ Not called |

---

## PART 7: INFORMATION GAIN / BAYESIAN LOGIC

### V1: Information Gain Implementation
**Location**: `core/satisfaction.py`, `blocks/semantic/block.py`

**Information-Gain Gate for Recursion**:
```python
# blocks/semantic/block.py line 150-180
async def semantic_retriever_block(inp):
    chunks = await resolve_sources(inp)
    chunks = await rerank_chunks(chunks)
    
    # Decision: should recurse deeper?
    decision = await decision_llm(
        query=inp.query,
        current_chunks=chunks,
        depth=inp.depth,
        max_depth=inp.max_depth,
    )
    
    if decision.should_recurse:
        # Information gain check
        new_info_score = await calculate_info_gain(
            current_chunks,
            decision.child_queries,
        )
        
        if new_info_score > INFO_GAIN_THRESHOLD (0.3):  # budgets.py
            # Spawn children
            children = await asyncio.gather(...)
```

**How It Works**:
1. Recursion depth budget: max 3 (DEFAULT_MAX_DEPTH)
2. Information-gain threshold: 0.3 (INFO_GAIN_THRESHOLD)
3. Per-node timeout: 8 seconds
4. Global budget: 28 seconds

**Satisfaction Tracking** (`core/satisfaction.py`):
```python
class SatisfactionTracker:
    def record_query(self, query: str):
        # Track user query
    
    def record_correction(self, category: str, correction_text: str):
        # User corrected agent output
        # Increment correction_count[category]
    
    def record_feedback(self, satisfaction_level: float):
        # User explicit feedback: 0.0-1.0
    
    def get_thinking_adjustments(self) -> dict:
        # Based on correction history, recommend adjustments
        # "If user keeps correcting on technical depth, increase depth"
        # Returns: {"depth_boost": 1, "self_consistency_calls": 1, ...}
```

**Effort Bias** (`core/reasoning.py`):
```python
@dataclass
class EffortBias:
    """User preferences for effort/latency tradeoff"""
    prefer_speed: bool = True
    prefer_thoroughness: bool = False
    max_budget_s: float = 28.0
```

### V2: Information Gain Changes
**Task Reflection for Gap Detection** (`core/task_reflection.py`):
```python
class TaskReflectionEngine:
    async def analyze_learnings(
        current_learnings,
        original_query,
        domain="research",
        context=None,
    ) -> ReflectionAnalysis:
        # LLM analyzes learnings and recommends next steps
        # Returns:
        # - new_tasks_detected: List of new tasks to add to queue
        # - reprioritization_suggestions: Which existing tasks should run sooner
        # - knowledge_gaps: What we still don't know
        # - confidence: 0-1 score for this analysis
        
        # Example response:
        # {
        #   "new_tasks": [
        #     {"task": "Get CDSL risk analysis", "priority": 85, "reasoning": "..."},
        #   ],
        #   "gaps": ["What about CDSL's dividend yield?"],
        #   "confidence": 0.85
        # }
```

### Key Differences
| Aspect | v1 | v2 |
|--------|----|----|
| **Threshold Type** | Information-gain numeric (0.3) | Reflection-based (LLM) |
| **Trigger** | After each chunk rerank | After each wave completes |
| **Drives** | Recursion depth decision | Task addition + reprioritization |
| **Entropy Calc** | Implicit in decision_llm | Explicit in reflection prompt |
| **User Feedback** | Satisfaction tracker | Queue state + satisfaction score |

### Entropy-Based Querying
- ❌ **NO explicit entropy calculation** in either v1 or v2
- ⚠️ Information gain is **heuristic-based** (not Bayesian, no explicit probability math)
- ⚠️ Reflection engine in v2 uses LLM to decide gaps, not information theory

---

## PART 8: TOKEN MANAGEMENT

### V1: Token Handling
**Location**: `core/context_policy.py`, `llm/synthesis.py`

**Context Policy**:
```python
# Approximate tokens: 4 characters ≈ 1 token (conservative)
CHARS_PER_TOKEN = 4

# Summarize when context exceeds this
SUMMARIZE_THRESHOLD_TOKENS = 50_000

# Always keep recent N turns verbatim
KEEP_RECENT_TURNS = 6

# Safety margin to avoid hitting limit
SAFETY_MARGIN_TOKENS = 5_000

def estimate_tokens(history: List[Dict]) -> int:
    """Estimate tokens in conversation history"""
    total_chars = 0
    for msg in history:
        total_chars += len(msg.get("content", ""))
        if "tool_calls" in msg:
            total_chars += len(json.dumps(msg["tool_calls"]))
    return max(1, total_chars // CHARS_PER_TOKEN)

def get_context_status(history) -> ContextStatus:
    """Check if context needs summarization"""
    estimated = estimate_tokens(history)
    percent_full = (estimated / SUMMARIZE_THRESHOLD_TOKENS) * 100
    should_summarize = estimated > SUMMARIZE_THRESHOLD_TOKENS
    
    warning = ""
    if percent_full > 90:
        warning = "Context 90% full"
    if percent_full > 100:
        warning = "Context overflow warning"
```

**Synthesis Token Management** (`llm/synthesis.py`):
```python
async def global_synthesis_llm(
    query,
    learnings,
    client=None,
    prompt_specificity="standard",
) -> str:
    """Generate synthesis"""
    return await client.chat(
        _build_messages(query, learnings, prompt_specificity),
        temperature=0.2,
        max_tokens=2048,  # ← Hard limit on output
    )
```

### V2: Token Handling — No Changes
- `context_policy.py` copied unchanged from v1
- Same SUMMARIZE_THRESHOLD_TOKENS (50K)
- Same max_tokens=2048 in synthesis

### Known Token Issues
**From ANALYSIS_COMPLETE.md**:
- ⚠️ Token estimation **underestimates** (4 chars per token is conservative)
- ⚠️ No **response chunking** if synthesis exceeds max_tokens
- ⚠️ No **graceful truncation** — LLM just cuts off
- ⚠️ **No streaming response handling** for long answers
- ⚠️ Learning aggregation doesn't check token budget before synthesis

### Edge Cases Not Handled
```python
# What happens if:
# 1. All learnings combined > max_tokens?
#    → LLM truncates mid-sentence
# 2. Synthesis runs out of tokens mid-thought?
#    → Incomplete answer returned
# 3. Conversation history + query + learnings > 32K context?
#    → Depends on model, but likely to fail
```

---

## PART 9: MULTI-QUERY SUPPORT

### V1: Multi-Query Handling
**Example Query**: "What's better for mobile auth — OAuth2 or JWT?"

**Actual Flow**:
```python
# query.py:run_query()
gate_result = await entry_gate(query)
# gate_result.mode = "SEMANTIC" (no special comparison detection)

profile = get_thinking_profile(gate_result.mode, query, ...)

orch_results = await run_orchestrator(
    query,  # ← Still single query!
    gate_mode="SEMANTIC",
)

# orchestrator.py:decompose_task()
if gate_mode == "SEMANTIC":
    return Decomposition(nodes=[
        TaskNode("n1", SubagentType.RETRIEVER, query),  # ← Single node
    ])

# Result: Retrieves for "mobile auth" generically
# Does NOT split into:
# - Task 1: Retrieve OAuth2 details
# - Task 2: Retrieve JWT details
# - Task 3: Synthesis comparing both
```

**What Should Happen**:
```python
# Hypothetical correct flow
gate_result = await comparison_detector.detect(query)
# gate_result.is_comparison = True
# gate_result.entities = ["OAuth2", "JWT"]

orch_results = await run_orchestrator(
    query,
    comparison_entities=gate_result.entities,  # ← Pass entities
)

# orchestrator.py:decompose_task()
if comparison_decision.is_comparison:
    return Decomposition(nodes=[
        TaskNode("n1", SubagentType.RETRIEVER, "Retrieve OAuth2 specs and pros/cons"),
        TaskNode("n2", SubagentType.RETRIEVER, "Retrieve JWT specs and pros/cons"),
        # Both run in parallel!
    ], fan_out_eligible=True)
```

### V2: Multi-Query Support Status
**Comparison Detector** (`routing/comparison_detector.py`):
- ✅ **Fully implemented**: Detects 8+ comparison patterns
- ✅ Regex-based fast path + LLM fallback
- ✅ Returns: ComparisonDecision with entities and confidence

**But Integration is INCOMPLETE**:
```python
# query.py:run_query() has:
# Line 134-136
comparison_analysis: Optional[dict] = None
# "For 'should I buy X or Y?' type queries"
# Structure: {"is_comparison": bool, "entities": [...], "comparison_verdict": str}

# But:
# 1. comparison_detector.detect() is NOT called in query.py
# 2. Even if it were, result is NOT passed to orchestrator
# 3. orchestrator.decompose_task() doesn't check for comparison
# 4. Still creates single TaskNode
```

### Multi-Query Verdict
| Aspect | v1 | v2 |
|--------|----|----|
| **Detection** | ❌ None | ✅ Implemented |
| **Decomposition** | ❌ Single query | ❌ Single node still |
| **Parallel Execution** | ✅ Within branches | ❌ Not split for comparison |
| **Synthesis** | ✅ Combines learnings | ⚠️ Not aware of split |
| **Overall** | ❌ Not native | ⚠️ Partially built, not wired |

---

## PART 10: DEGRADATION ANALYSIS (v1 → v2)

### What Actually Regressed
| Component | v1 | v2 | Change |
|-----------|----|----|--------|
| **Core retrieval** | ✅ Working | ✅ Unchanged | — |
| **Persona system** | ✅ Defined | ✅ Defined | No change |
| **Multi-critique** | ✅ Defined | ✅ Defined | Still unused |
| **Code execution** | ✅ Implemented | ✅ Unchanged | No regression |
| **Knowledge graph** | ✅ Wired | ✅ Wired | No change |
| **Tool registry** | ❌ Empty | ❌ Empty | Same state |
| **Comparison queries** | ❌ Not detected | ⚠️ Detected but not used | Worse (false positive) |
| **Token handling** | ⚠️ Basic | ⚠️ Unchanged | No improvement |

### What Didn't Regress — What's New
**V2 Additions** (not regressions, but incomplete):
1. **Adaptive scheduler** — New infrastructure, not wired
2. **Wave executor** — New infrastructure, not wired
3. **Task queue** — New infrastructure, not wired
4. **Task reflection** — New infrastructure, not wired
5. **Comparison detector** — New functionality, not integrated
6. **Speculative questions** — New framework, not integrated
7. **File creation suggestions** — New heuristics, just for UI hints

### Root Causes of Incompleteness
1. **Architectural layering**: Core pieces built, but not connected to query.py
2. **Incomplete integration**: New files added but orchestrator.py not updated
3. **No end-to-end tests**: Individual pieces tested, not flows
4. **UI/backend mismatch**: Backend structures don't map to query.py results
5. **Feature flag system**: All features defined but feature flags don't enable new flows

### Specific Regressions (Not Improvements)
1. **Comparison handling**: Detector added, but ignored → "Why did you add detection if you don't use it?" perception
2. **File suggestions**: Added UI hints, but no actual file tools → User sees suggestion but can't create
3. **Speculative questions**: Framework added, but not wired → No user-facing change
4. **Task reflection**: LLM component added, but never called → Wasted code

### Code Quality Assessment
**v1**:
- ✅ Solid foundation
- ✅ Type-safe throughout
- ✅ Feature flags allow staged rollout
- ✅ Observable (metrics, logging)
- ❌ Integration gaps (some pieces not called)

**v2**:
- ✅ Better architecture (wave-based more flexible than DAG)
- ✅ Adaptive reflection is novel contribution
- ✅ Comparison detection solid
- ❌ **Integration regressions** (more pieces added but not connected)
- ❌ **Increased technical debt** (9 new files, most not integrated)
- ❌ **Worse from user POV** (features detected but not used)

---

## PART 11: KEY FINDINGS SUMMARY

### What's Working End-to-End
1. **Tier 1 Connectivity**: User corrections → satisfaction tracking → thinking profile adjustment ✅
2. **Semantic Retrieval**: Query → entry gate → orchestrator → blocks → chunks → synthesis ✅
3. **CRAG Grading**: Learnings → grade → fallback if needed ✅
4. **Knowledge Graph**: Graph-enhanced retrieval for relationship-heavy queries ✅
5. **Persona System**: Jarvis persona applied to all synthesis calls ✅

### What's Built But Not Wired
1. **Comparison Queries**: Detector works, but orchestrator ignores result
2. **Multi-wave Execution**: Task queue & scheduler fully implemented, not called
3. **Task Reflection**: Engine analyzes learnings, not integrated into queue
4. **Speculative Questions**: Generation framework exists, not wired into query flow
5. **Multi-critique Personas**: Four personas defined, never used
6. **Tool Execution**: Code execution implemented, not in tool registry
7. **File Creation**: Suggestions generated, no tools to create files
8. **Progressive Scraping**: Framework exists, not called from blocks

### What's Missing
1. **Comparison entity-based decomposition**: Detector output not passed to orchestrator
2. **Wave-based orchestration integration**: query.py still calls run_orchestrator() single-style
3. **Information-gain Bayesian calculation**: Uses heuristics, not probability theory
4. **Response chunking**: Long answers just truncate at max_tokens
5. **Tool executor integration**: No tools registered or callable from orchestrator
6. **Speculative question UX**: No branching_options shown to user
7. **Entropy-based query generation**: Reflection uses LLM, not entropy math

### Architectural Strengths (Both v1 & v2)
1. **Async-first design**: All I/O operations properly async
2. **Type safety**: Dataclasses everywhere, no duck typing
3. **Extensibility**: Easy to add new retriever types, tools, personas
4. **Observability**: Metrics tracking built in
5. **Feature flags**: Clean staged rollout mechanism
6. **Progressive overloading**: Depth budgets, timeouts, deadlines

### Architectural Weaknesses
1. **Integration gaps**: Components built in isolation
2. **Single-query assumption**: query.py designed for one query, not multi-query
3. **No end-to-end testing**: Individual modules tested, flows untested
4. **UI-backend mismatch**: New data structures (branching_options, comparison_analysis) not mapped to response
5. **Token strategy incomplete**: No response chunking or graceful degradation

---

## PART 12: IMPLEMENTATION READINESS

### Can Deploy Today (Tier 1 Only)
```python
settings.features = FeatureFlags.tier_1_only()
# Connectivity loop fully working
# Knowledge graph enhancement available
# Satisfaction tracking complete
```

### Can Enable This Week (Tiers 1-2)
```python
settings.features = FeatureFlags.tiers_1_2()
# Add progressive zoom (overview/focused/comprehensive)
# Depth-adaptive synthesis
# Fix: 3-4 hours to wire zoom_level from UI through query.py to synthesis
```

### Blocked Until Fixed
| Feature | Blocker | Time to Fix |
|---------|---------|-------------|
| Comparison queries | Orchestrator ignores detector result | 1-2 hrs |
| Code execution | No tools registered | 2-3 hrs |
| File creation | No CreateFileTool implemented | 2 hrs |
| Speculative questions | No flow to show/collect user answers | 3-4 hrs |
| Multi-wave execution | query.py not calling wave executor | 2-3 hrs |
| Response chunking | No chunking logic in synthesis | 2-3 hrs |

### Total Fix Effort for Full Integration
- **Tier 1 + 2 complete & working**: 4-6 hours
- **Tier 3 complete & working**: +6-8 hours (branching UI + speculative questions)
- **Tier 4 complete & working**: +4-6 hours (code execution + file tools + tool registry)
- **Total**: 14-20 hours for full working system

