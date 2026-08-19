# DETAILED CAPABILITY & WIRING ANALYSIS
**Date**: 2026-08-15  
**Scope**: File execution, component interconnections, persona system, token management  
**Status**: Analysis only — NO EDITS  

---

## 1. FILE/CODE EXECUTION CAPABILITIES ANALYSIS

### Status: ✅ FULLY IMPLEMENTED (Tier 4)

#### Where It's Defined
- **Main**: `agent/core/code_execution.py` (~350 lines)
- **Tools Registry**: `agent/tools/tool_registry.py` (26-tool registry includes code tools)
- **Executor**: `agent/tools/executor.py` (parallel execution up to 4 concurrent)
- **Sandbox**: `agent/blocks/sandbox/block.py` (execution subagent)

#### What Agent Knows It Can Do

**A. Code Execution (Python/Bash)**
```python
# From code_execution.py
class ExecutionLanguage(Enum):
    PYTHON = "python"
    BASH = "bash"
    JAVASCRIPT = "javascript"  # Planned

class ExecutionSafety(Enum):
    SANDBOXED = "sandboxed"      # No filesystem, stdout/stderr only
    RESTRICTED = "restricted"     # Limited filesystem (current dir only)
    FULL = "full"                # Full system access (careful!)
```

**B. Safety Levels**
- **SANDBOXED**: No file I/O, no shell escapes, memory/time limited
- **RESTRICTED**: Can read/write in project dir only, no /etc/, no ssh
- **FULL**: Complete access (requires explicit user approval)

**C. Dangerous Pattern Blocking** (from code_execution.py)
```python
DANGEROUS_PATTERNS = [
    "rm -rf",           # Destructive
    "sudo ",            # Privilege escalation
    "ssh ",             # Remote access
    "/etc/passwd",      # System files
    "/etc/shadow",      # System files
    "ddos",             # Malicious
]
```

**D. Tool Registry (26 Tools)**
```python
# From tool_registry.py
FILE_TOOLS = ["file_read", "file_write", "file_list", "file_search", "file_delete"]
SEARCH_TOOLS = ["brave_search", "google_search", "web_fetch", "arxiv_search"]
RAG_TOOLS = ["knowledge_base_search", "semantic_search", "graphdb_query"]
AGENT_TOOLS = ["orchestrator_submit", "skill_invoke", "tool_parallel", "subagent_execute"]
BUILDER_TOOLS = ["deck_builder", "report_builder", "website_builder", "code_reviewer"]
CODE_TOOLS = ["python_execute", "bash_execute", "git_command"]
TODO_TOOLS = ["todo_add", "todo_check", "todo_list"]
```

**E. Execution Request/Response**
```python
# From code_execution.py
@dataclass
class ExecutionRequest:
    code: str
    language: ExecutionLanguage  # Python, Bash, JavaScript
    timeout_s: float = 30.0
    safety: ExecutionSafety = ExecutionSafety.SANDBOXED
    variables: dict = None      # ENV vars to pass

@dataclass
class ExecutionResult:
    success: bool
    stdout: str
    stderr: str
    hypothesis_confirmed: Optional[bool]  # Did code validate hypothesis?
    confidence_delta: Optional[float]     # How much did this change confidence?
    exit_code: int = 0
```

#### Is This Wired Into Query Flow?

**Current Status: ✅ YES, BUT GATED BY FLAG**

```python
# From query.py line ~68
use_code_execution: bool = False,  # Tier 4 flag

# From core/code_execution.py - only runs if:
if use_code_execution and features_enabled.code_execution_enabled:
    # Execute code to validate hypothesis
```

#### Can Agent Request File Creation?

**Current Status: ✅ YES, VIA TOOLS**

```python
# Available tools (tool_registry.py):
- "file_write": Create/modify files
- "deck_builder": Create PowerPoint PPTX
- "report_builder": Create Word DOCX
- "website_builder": Create HTML/CSS/JS websites

# Flow:
Query → Tool selector → file_write tool → execution
```

**Example**: Query "create a Python script that..." → agent calls file_write tool → creates .py file

#### But Is Agent AWARE It Can Do This?

**Current Status: ⚠️ PARTIALLY**

```python
# From routing/intent_classifier.py
# Agent knows:
- Intent "code_task" → use CODE tools
- Intent "build_document" → use BUILDER tools
- Intent "file_operation" → use FILE tools

# But:
- Intent detection is REGEX-based + LLM fallback
- Agent doesn't proactively suggest "I can create a file for you"
- User must explicitly ask to create files
```

#### Example Scenarios

**Scenario 1: "Analyze this CSV data"**
```
✓ Agent recognizes: CSV = file + data analysis
✓ Agent calls: file_read + python_execute tools
✓ Result: Analysis output
```

**Scenario 2: "Create an Excel sheet with analysis"**
```
? Agent might miss: "Can create XLSX via deck_builder"
⚠️ User needs to ask explicitly: "Can you create an Excel file?"
✗ Agent doesn't proactively offer: "I can create an Excel file with this data"
```

---

## 2. COMPONENT INTERCONNECTIONS & WIRING MATRIX

### The 8 Critical Connections

| # | Connection | Files | Status | Issue |
|---|-----------|-------|--------|-------|
| **1** | `reasoning.py` ↔ `satisfaction.py` | core/reasoning.py + core/satisfaction.py | ✅ | Feedback loop active: user correction → boost thinking depth |
| **2** | `satisfaction.py` ↔ `query.py` | core/satisfaction.py + query.py L165 | ✅ | Query passes satisfaction tracker to orchestrator |
| **3** | `orchestrator.py` → `semantic_retriever_block()` | orchestrator.py L230 + blocks/semantic/block.py | ✅ | Task dispatch works; subagent execution via run_subagent() |
| **4** | `critique.py` → retrieval flow | core/critique.py + blocks/semantic/block.py | ❌ | **MISSING**: Critique only called on FAILURE (pivot), not on SUCCESS |
| **5** | `pivot.py` ↔ `orchestrator.py` | core/pivot.py + orchestrator.py L300 | ✅ | Pivot runs when subagent fails; GOAL→ACTION→OBSERVE→HYPOTHESIZE→DISCRIMINATE |
| **6** | `knowledge_graph.py` ↔ semantic retriever | knowledge/graph_rag.py + blocks/semantic/block.py L105 | ✅ | Graph conditionally enhances retrieval if should_use_graph() returns true |
| **7** | `progressive.py` ↔ `query.py` | core/progressive.py + query.py | ❌ | **MISSING**: Progressive levels defined but no navigation logic in main flow |
| **8** | `branching.py` ↔ `query.py` | core/branching.py + query.py L68 | ⚠️ | Tier 3 flag controls; BranchingSession tracks user selections |

### Detailed Connection Analysis

#### Connection 1: Reasoning ↔ Satisfaction ✅

**Flow**:
```python
# query.py L165-180
if satisfaction and features.connectivity_enabled:
    profile = await get_thinking_profile_with_history(
        satisfaction_tracker=satisfaction,  # Pass tracker
        features_enabled=features,
    )

# reasoning.py
async def get_thinking_profile_with_history(satisfaction_tracker):
    adjustments = satisfaction.get_thinking_adjustments()
    if adjustments.get("depth_boost"):
        profile.max_depth = min(profile.max_depth + adjustments["depth_boost"], 5)
```

**Status**: ✅ WIRED — When user corrects agent, satisfaction tracker increases `depth_boost`, making next queries deeper

---

#### Connection 2: Satisfaction ↔ Query ✅

**Flow**:
```python
# satisfaction.py L45
def record_query(self, query: str):
    self.correction_history.append({...})

# query.py L160
if satisfaction:
    satisfaction.record_query(query)
    adjustments = satisfaction.get_thinking_adjustments()
```

**Status**: ✅ WIRED — Every query recorded for correction pattern tracking

---

#### Connection 3: Orchestrator → Semantic Retriever ✅

**Flow**:
```python
# orchestrator.py L230
async def _dispatch(node):
    sub_input = SubagentInput(task=node.task, subagent_type=node.subagent_type)
    result = await run_subagent(sub_input)  # Calls semantic_retriever_block internally
    
# blocks/base.py
async def run_subagent(inp: SubagentInput) -> SubagentResult:
    if inp.subagent_type == SubagentType.RETRIEVER:
        return await semantic_retriever_block(...)  # Calls main retriever
```

**Status**: ✅ WIRED — Orchestrator properly delegates to semantic retriever

---

#### Connection 4: Critique ↔ Retrieval FLOW ❌ **CRITICAL GAP**

**Current Status**: Critique only runs on FAILURE (pivot recovery)

```python
# query.py L230-280 (Main flow)
# After orchestrator returns:
orch_results = await run_orchestrator(...)
all_learnings, all_urls = _collect_results(orch_results)
# ❌ NO CRITIQUE CALL HERE

# orchestrator.py L300-320 (Failure path only)
async def discriminate(_h_a, _h_b):
    # This runs CRAG grading + critique ONLY if subagent failed
    result = await critique.run_critique_on_potential_issues(...)
```

**Problem**:
- Critique runs on subagent failure → can correct wrong subagent choice
- Critique DOESN'T run on successful retrieval → can't detect "thin" or incomplete answers
- Example: Retrieved CDSL info but not EMVEE → critique should flag this, but doesn't

**Expected Wiring**: Should be in `semantic_retriever_block()` after reranking, before decision_llm

---

#### Connection 5: Pivot ↔ Orchestrator ✅

**Flow**:
```python
# orchestrator.py L290-350
last_result = [None]

async def first_action():
    last_result[0] = await run_subagent(sub_input)
    return Observation(succeeded=last_result[0].success, detail=...)

decision, branching_options = await run_pivot_loop(
    goal=f"Execute {node.task}",
    first_action=first_action,
    gen_hypotheses=gen_hypotheses,
    discriminate=discriminate,
)
```

**Status**: ✅ WIRED — But ONLY on subagent failure (reactive, not proactive)

---

#### Connection 6: Knowledge Graph ↔ Semantic Retriever ✅

**Flow**:
```python
# blocks/semantic/block.py L105-125
from knowledge.graph_rag import graph_enhanced_retrieval, should_use_graph

try:
    if should_use_graph(inp.query or query_label):
        graph_chunks = await graph_enhanced_retrieval(
            inp.query,
            query_vec,
            client=client,
            top_k=4,
        )
        # Merge graph chunks with vector chunks
        for gc in graph_chunks:
            if gc.text[:100] not in existing_texts:
                chunks.append(gc)
except ImportError:
    pass  # Graph not available
```

**Status**: ✅ WIRED — Hybrid search works (vector + graph) when `should_use_graph()` returns true

---

#### Connection 7: Progressive Levels ↔ Query Flow ❌ **MISSING**

**Current Status**: Progressive levels defined but not integrated

```python
# core/progressive.py
class ProgressiveLevel(Enum):
    STRUCTURE = 0      # Quick overview
    SECTIONS = 1       # Detailed per section
    FULL = 2           # Complete content

# llm/synthesis_levels.py
async def synthesize_at_level(level: int, learnings: list) -> str:
    # level 0=overview, 1=focused, 2=comprehensive
    ...

# query.py L68
zoom_level: int = 0,  # Parameter exists
# But no navigation logic:
# - No "can you zoom in?" offer to user
# - No "you're at level 0/3, suggest level 1?"
```

**Problem**:
- Zoom level parameter passed but not used to guide retrieval depth
- No user experience: "Found overview. Want more detail? (yes/no/specific aspects)"
- Should trigger: shallow retrieval first, then ask user what to zoom into

---

#### Connection 8: Branching ↔ Query ⚠️

**Flow**:
```python
# query.py L68
branching_options: list = field(default_factory=list)
branching_session_id: Optional[str] = None

# core/branching.py
async def present_branching_options(decision: BranchingDecision):
    # Present multiple hypotheses to user
    ...

# query_integration.py
def handle_branching_presentation():
    # Multi-turn session tracking
    ...
```

**Status**: ⚠️ WIRED BUT GATED
- Tier 3 feature (requires feature flag enabled)
- When confidence gap < 0.25, presents options to user
- BranchingSessionManager tracks selections across turns

---

## 3. PERSONA SYSTEM DETAILED ANALYSIS

### The 4 Personas

**File**: `core/critique.py`

```python
PERSONA_SYSTEM = {
    "Brutal Critic": {
        "role": "Finds flaws and missing information",
        "prompt": "What's obviously wrong or incomplete?",
        "confidence_weight": 0.25,
    },
    "Expectationist": {
        "role": "Sets high expectations",
        "prompt": "What did you expect to find but didn't?",
        "confidence_weight": 0.25,
    },
    "Realist": {
        "role": "Pragmatic, practical evaluation",
        "prompt": "What's the realistic next question?",
        "confidence_weight": 0.25,
    },
    "Overthinker": {
        "role": "Explores edge cases and nuances",
        "prompt": "What subtleties or edge cases were missed?",
        "confidence_weight": 0.25,
    },
}
```

### How They Vote

**Current Logic** (from critique.py L200-250):

```python
async def run_critique(goal, artifacts, client):
    """Run 4-persona critique."""
    results = []
    
    # Each persona evaluates INDEPENDENTLY
    for persona_name, persona_config in PERSONA_SYSTEM.items():
        prompt = f"""You are {persona_name}...
        Evaluate: {artifacts}
        
        Respond with JSON: {{"gaps": [...], "confidence": 0-1}}"""
        
        result = await client.chat_fast(
            [{"role": "user", "content": prompt}],
            response_format_json=True,
        )
        results.append(json.loads(result))
    
    # AGGREGATE RESULTS
    all_gaps = set()
    consensus_strength = 0
    
    for result in results:
        all_gaps.update(result["gaps"])
        consensus_strength += result["confidence"]
    
    consensus_strength /= 4  # Average confidence
    
    return {
        "gaps": list(all_gaps),
        "consensus_strength": consensus_strength,
        "verdicts": results,  # All 4 responses
    }
```

### **Tiebreaker Logic: ❌ MISSING**

**Problem**: When personas split (2 vs 2 or 1 vs 3), what happens?

**Current Behavior**:
```python
# query.py L300-330 (After critique on pivot failure)
if grade.grade == RetrievalGrade.INCORRECT:
    # Use critique verdict
    logger.warning("CRAG: retrieval graded INCORRECT, discarding. Reason: %s", grade.reason)
    answer = await direct_answer_llm(...)  # Fallback to direct answer
else:
    answer = await global_synthesis_llm(...)  # Use learnings

# ❌ MISSING:
# When critique says: "Brutal Critic: WRONG | Expectationist: WRONG | Realist: OK | Overthinker: OK"
# System returns all 4 verdicts without picking one
# Or: returns aggregate gaps, not resolving conflicts
```

**Expected Behavior** (not implemented):
1. Count persona votes (which personas agree?)
2. If 3-1 or 4-0: Use majority
3. If 2-2: Ask user to decide
4. Explain: "Critics disagree on whether [X] is a gap"

---

## 4. SPECULATIVE QUESTIONING & INFORMATION GAIN

### Status: ⚠️ PARTIAL — Concept exists but not fully wired

### Where It's Mentioned

**A. In Comments** (query.py L72):
```python
"""
query.py — THE single entry point for the entire agent backend.
...
KEY PHILOSOPHIES from chats implemented here:
  1. Speculative streaming (L72): start streaming answer parts while
     retrieval still runs in background
```

**B. In Branching** (core/branching.py L150-180):
```python
@dataclass
class BranchingOption:
    label: str
    explanation: str
    pros: list[str]
    cons: list[str]
    confidence: float        # How confident in this option?
    evidence_level: str      # "low", "medium", "high"
    prior_probability: float # Bayesian prior
```

**C. Decision Logic** (blocks/semantic/decision.py L100-150):
```python
async def decision_llm(
    query: str,
    mode: Mode,
    reranked_chunks: list[Chunk],
    depth: int,
    max_depth: int,
    client: Optional[NIMClient] = None,
) -> Decision:
    """Decide: are we done? recurse? or need different retriever?"""
    
    prompt = f"""Given query: {query}
    Retrieved chunks: {'; '.join(c.text[:100] for c in reranked_chunks)}
    Current depth: {depth}/{max_depth}
    
    Decide:
    1. Is this sufficient? (boolean)
    2. If not, what to search next? (list of queries)
    3. Should we get code examples? (boolean)
    
    Consider: Information gain. Only suggest next_queries if new info
    would significantly advance the answer."""
    
    return Decision(
        sufficient=...,
        next_queries=[...],  # Information gain drives this
        needs_code_retriever=...,
    )
```

### What's Implemented: ✅

1. **Decision LLM considers information gain** (blocks/semantic/decision.py)
2. **Branching presents options with prior probabilities** (core/branching.py)
3. **Confidence metrics assigned** (scoring used in branching_decision)

### What's Missing: ❌

1. **Explicit entropy calculation** — No metric like "information entropy = 0.7"
2. **Speculative question generation** — No system that generates "Would X matter?" questions
3. **Inline question presentation during retrieval** — No streaming of questions while retriever runs
4. **Bayesian update after user answers** — No "user said X, so posterior = ..." logic
5. **Query direction guidance** — User doesn't tell system "focus on pricing" and system doesn't adapt

---

## 5. TOKEN CUTOFF / MESSAGE TRUNCATION ISSUE

### Where Token Limits Are Set

**File**: `agent/config/budgets.py`

```python
DEFAULT_MAX_DEPTH = 3              # Retrieval depth
MAX_EXTENSIONS_PER_BRANCH = 2      # Escape valves for depth
EXTENSION_INCREMENT = 1            # +1 depth if needed

GLOBAL_BUDGET_S = 30.0             # Total time per query
NODE_TIMEOUT_S = 10.0              # Per-node timeout

# Context window budgets
_CONTEXT_BUDGETS = {
    0: 300,    # zoom_level 0: 300 tokens
    1: 800,    # zoom_level 1: 800 tokens
    2: 2000,   # zoom_level 2: 2000 tokens
}
```

**File**: `agent/core/context_policy.py`

```python
class ContextPolicy:
    def __init__(self, budget_tokens: int = 50000):
        self.budget_tokens = budget_tokens  # Soft limit
        self.summarization_threshold = 50000  # Auto-summarize when reached
    
    def should_summarize(self, current_tokens: int) -> bool:
        return current_tokens > self.summarization_threshold
    
    def token_estimate(self, text: str) -> int:
        # Rough estimate: 4 chars ≈ 1 token
        return len(text) // 4
```

**File**: `agent/config/settings.py`

```python
class Settings:
    NIM_MAX_TOKENS = 2048      # Max response tokens from model
    # ❌ Note: This is pretty low! Many queries need more
    
    CONTEXT_WINDOW = 16000     # Total context budget
    # ❌ Note: 16K is reasonable, but 2K response max is tight
```

### The Problem: Truncation Happens At

**Stage 1**: Synthesis LLM (llm/synthesis.py)

```python
async def global_synthesis_llm(
    query: str,
    learnings: list[Learning],
    client: Optional[NIMClient] = None,
    prompt_specificity: str = "standard",
) -> str:
    """Synthesize learnings into final answer."""
    
    prompt = build_synthesis_prompt(query, learnings, prompt_specificity)
    
    response = await client.chat_fast(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=2048,  # ← CUTOFF POINT
        response_format_json=False,
    )
    
    return response  # ❌ Could be truncated if reaches 2048 tokens
```

**Stage 2**: Transport layer (transport/server.py)

```python
@app.post("/query")
async def query_endpoint(req: QueryRequest):
    result = await run_query(req.query, ...)
    return {
        "answer": result.answer,  # ← Returned as-is, could be partial
        "source_urls": result.source_urls,
        ...
    }
```

### Is There Fallback? ❌ NO

**Issue**: If response gets cut off mid-sentence:
```python
# query.py (synthesis stage)
answer = await global_synthesis_llm(query, learnings)
# ❌ No check if answer ends with "..." or incomplete sentence
# ❌ No retry with higher max_tokens
# ❌ No truncation detection
```

### Why Messages Get Cut Mid-Message

**Scenario**:
1. Query: "Should I buy CDSL or EMVEE? Tell me everything."
2. Learnings collected: 5,000 tokens of content
3. Synthesis prompt built: query (200 tokens) + learnings (5,000) = 5,200 tokens
4. LLM called with max_tokens=2048
5. LLM generates response up to 2048 limit
6. Response cuts off: "CDSL is better because of price, features, and..." ← incomplete

**Example Output**:
```
"answer": "CDSL offers better value proposition because... [TRUNCATED AT 2048 TOKENS]"
```

---

## 6. INTEGRATION GAPS SUMMARY TABLE

| System | Component | Status | Issue | Impact |
|--------|-----------|--------|-------|--------|
| **Reasoning** | Satisfaction feedback loop | ✅ | - | Works well |
| **Retrieval** | Critique on main flow | ❌ | Only on failure | Thin retrievals undetected |
| **Retrieval** | Thin result detection | ❌ | No auto-trigger | CDSL-only answers not caught |
| **User Guidance** | Persona tiebreaker | ❌ | Missing logic | 2-2 split unresolved |
| **User Guidance** | Progressive navigation | ❌ | Not wired | Zoom levels exist but unused |
| **Info Gathering** | Speculative questions | ❌ | Not implemented | No "should we explore X?" |
| **Info Gathering** | Entropy calculation | ❌ | Not explicit | Decision via LLM only |
| **Token Management** | Synthesis max_tokens | ⚠️ | Set to 2048 | Answers truncated on complex queries |
| **Token Management** | Truncation fallback | ❌ | Missing | No retry or adaptive sizing |
| **Tool Use** | Agent awareness | ⚠️ | Implicit only | Doesn't proactively offer tools |
| **File Creation** | Tool integration | ✅ | Via tools | Works if user asks explicitly |
| **Code Execution** | Safety levels | ✅ | All 3 levels | Properly gated |
| **Orchestrator** | Delegation flow | ✅ | - | Works well |
| **Knowledge Graph** | Hybrid search | ✅ | Conditional | Works when triggered |

---

## KEY FINDINGS

### ✅ What's Solid
1. Code execution framework (Tier 4) — complete with 3 safety levels
2. Reasoning ↔ Satisfaction feedback — active and effective
3. Orchestrator delegation — proper task decomposition
4. Knowledge graph integration — conditional enhancement
5. Tool registry — 26 tools available

### ❌ Critical Gaps
1. **Critique not in main retrieval path** — Can't auto-fix "CDSL only" answers
2. **Persona tiebreaker missing** — 2-2 split unresolved
3. **Token truncation unhandled** — Answers cut mid-sentence
4. **Speculative questions missing** — No inline guidance
5. **Progressive navigation not wired** — Zoom levels exist but unused

### ⚠️ Partial Issues
1. **Agent doesn't proactively offer tools** — Needs user to ask for file creation
2. **Information gain not explicit** — Only in LLM decision, no quantified metric
3. **Clarify LLM is synchronous** — Blocks async pipeline

### User Impact
- **"CDSL vs EMVEE" only retrieves one** ← Due to Gap #1 (critique not in main flow) + no comparison detection (Phase 1 issue)
- **Answers get cut off** ← Due to Gap #3 (2048 token limit, no fallback)
- **Can't guide search mid-retrieval** ← Due to Gap #4 & #5 (no speculative questions, no progressive navigation)
- **2-2 persona disagreement hangs** ← Due to Gap #2 (no tiebreaker)

---

