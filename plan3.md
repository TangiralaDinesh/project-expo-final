I'll analyze this specific case where the agent answered about `ls` instead of actually executing it. No edits—just workflow diagnosis.   

Searched for regex `clarif|clarify.*question|use_code_execution|code_execution_enabled` (`project-expo` · `v2/project-expo-final/agent/**/*.py`), 77 results

Read , lines 35 to 95

Read , lines 270 to 290

Read , lines 1 to 100

Searched for regex `code_execution|bash|shell|execute.*command|decompose` (`project-expo` · `v2/project-expo-final/agent/orchestrator/orchestrator.py`), 12 results

Read , lines 300 to 360

Read , lines 106 to 250

Now I see the complete issue. Let me save this diagnosis to memory and report it. 

Created memory file 

## Diagnosis: Why Tool Execution Isn't Triggered

For your query **"can you tell what files are in this directory"**, here's exactly where the tool path gets bypassed:

### 1. Entry Gate Decision
- Query doesn't match math, syntax, or current-events patterns
- Falls to LLM classification
- Likely classified as **PARAMETRIC** (it's a how-to question) or **SEMANTIC**
- Result: `gate_result.needs_retrieval = False` or `True` but NOT "EXECUTE_BASH"

### 2. What Happens Next

**If PARAMETRIC (most likely):**
```
Query → Entry Gate (PARAMETRIC) → direct_answer_llm() 
→ "You can use `ls` command" ✗ No tool execution
```

**If SEMANTIC:**
```
Query → Entry Gate (SEMANTIC) → orchestrator.decompose_task()
→ Creates RETRIEVER subagent (semantic search only)
→ "How to list files" (web knowledge) ✗ Not actual execution
```

### 3. Where Tools Are Missing

In agent/orchestrator/orchestrator.py:

The `decompose_task()` function **only creates two subagent types:**
- `SubagentType.RETRIEVER` (semantic search)
- `SubagentType.CODE_RETRIEVER` (code search on GitHub)

**There is NO:**
- `SubagentType.CODE_EXECUTOR` or `SubagentType.BASH_EXECUTOR`
- Mode that triggers actual Python/bash execution

### 4. Entry Gate Has No "Execute" Mode

In agent/routing/entry_gate.py:

Modes are only:
- `PARAMETRIC` → direct LLM (no tools)
- `SEMANTIC` → semantic retrieval (no tools)
- `CODE` → code *search* (not execution)
- `SKILL`, `URL_DIRECT`

**Missing:** A mode for "this needs actual system command execution"

### 5. Code Execution Module Exists But Disconnected

agent/core/code_execution_integration.py has:
- `suggest_code_execution()`
- `execute_analysis_code()`
- `generate_validation_code()`

But **no code path in** query.py **ever calls these**.

The `use_code_execution` parameter (L155) defaults to False and isn't used.

---

### Bottom Line

**The workflow is:**
```
Query → Gate classifies as PARAMETRIC/SEMANTIC → Orchestrator creates RETRIEVER nodes
→ Semantic search → LLM synthesis (no bash/python ever invoked)
```

**What it should be for certain queries:**
```
Query → Gate detects "system command" or "needs computation" 
→ Orchestrator creates CODE_EXECUTOR node 
→ Python/bash execution → use output as truth
```

This is **not a bug in semantic search**. It's a **design choice**: queries default to parametric/semantic classification, with code execution as an optional tier behind feature flags, never in the main decision path.




plan
# Plan: Fix Tool Execution Gap in Agent Workflow

## TL;DR
The agent has all the infrastructure for code execution (tools, executors, sandbox blocks) but they're never triggered because:
1. Entry gate only classifies queries as PARAMETRIC/SEMANTIC/CODE (no execution mode)
2. Orchestrator only creates RETRIEVER or CODE_RETRIEVER nodes (no execution nodes)
3. Code execution modules exist but aren't wired into main query flow

**Solution**: Add new gate mode + executor subagent type + wire into orchestrator + enable feature flag.

---

## Current Architecture Map

### SubagentTypes Defined (in agent/core/types.py)
- ✅ RETRIEVER (implemented, used)
- ✅ CODE_RETRIEVER (implemented, used)
- ✅ SANDBOX (implemented, used for file generation)
- ❌ FILE_GENERATOR (defined, no handler)
- ❌ CODE_GEN_EXECUTOR (defined, no handler)

### Handlers Available (in agent/blocks/base.py)
```python
SUBAGENT_DISPATCH = {
    SubagentType.RETRIEVER: run_retriever_subagent,
    SubagentType.CODE_RETRIEVER: run_code_retriever_subagent,
    SubagentType.SANDBOX: run_sandbox_subagent,
    # Missing: CODE_GEN_EXECUTOR handler
}
```

### Tools Available (agent/tools/)
- ✅ execute_python_code() — works, never called from main flow
- ✅ execute_bash_code() (from core/code_execution.py)
- ✅ Tool dispatch system (dispatch_tool())
- ✅ Parallel tool executor (execute_tools_parallel())

### Entry Gate Modes (agent/routing/entry_gate.py)
- PARAMETRIC → direct_answer_llm (no tools)
- SEMANTIC → semantic retrieval (no tools)
- CODE → code retrieval (search only)
- SKILL → skill execution
- URL_DIRECT → web fetch
- **Missing**: COMPUTATION or BASH_EXECUTION mode

### Decision Points Where Tool Path Breaks
1. `entry_gate()` → doesn't classify for execution
2. `decompose_task()` → only creates RETRIEVER/CODE_RETRIEVER nodes
3. `run_query()` → never calls suggest_code_execution() or execute_analysis_code()

---

## Fix Implementation (5 Independent Phases)

### PHASE 1: Add Execution Mode to Entry Gate
**File**: [agent/routing/entry_gate.py](agent/routing/entry_gate.py)

1. Add new gate classification mode: "COMPUTATION"
2. Update regex patterns to detect computation/system queries:
   - Bash/shell commands: `ls`, `find`, `grep`, `cat`
   - Python execution: `calculate`, `count`, `how many`, `compute`
   - File system: `files in`, `directory`, `list files`
   - Math validation: comparison with `vs`, `versus`, uncertainties
3. Update `_dynamic_intent_classifier()` to return "COMPUTATION" for these
4. Update `_regex_fast_path()` to handle COMPUTATION mode
5. Update `_DECOMPOSE_SYSTEM` prompt to include code execution subagent

**Result**: Queries like "what files are in directory" or "9.9 vs 9.11" → classified as COMPUTATION

---

### PHASE 2: Create CODE_GEN_EXECUTOR Handler
**File**: [agent/blocks/base.py](agent/blocks/base.py)

1. Create `run_code_executor_subagent(sub_input)` function:
   - Takes task from input
   - Calls `code_execution_integration.suggest_code_execution()` to decide if execution needed
   - If yes, calls `execute_analysis_code()` to generate and run code
   - Returns SubagentResult with execution output as learnings
2. Register in SUBAGENT_DISPATCH:
   ```python
   SubagentType.CODE_GEN_EXECUTOR: run_code_executor_subagent
   ```

**Result**: CODE_GEN_EXECUTOR subagent type is now callable and available

---

### PHASE 3: Wire CODE_GEN_EXECUTOR into Orchestrator
**File**: [agent/orchestrator/orchestrator.py](agent/orchestrator/orchestrator.py)

1. Update `decompose_task()` function:
   - If gate_mode == "COMPUTATION", return single CODE_GEN_EXECUTOR node
   - Keep RETRIEVER/CODE_RETRIEVER logic unchanged for other modes
2. Update `_DECOMPOSE_SYSTEM` prompt to mention CODE_GEN_EXECUTOR as available
3. Allow LLM decomposition to suggest CODE_GEN_EXECUTOR nodes for complex computation tasks

**Result**: When entry gate says "COMPUTATION", orchestrator creates CODE_GEN_EXECUTOR nodes

---

### PHASE 4: Enable Code Execution in Main Query Flow
**File**: [agent/query.py](agent/query.py)

1. Update `run_query()` signature:
   - Default `use_code_execution` to True (currently False)
   - OR auto-enable based on feature flags
2. After orchestrator returns results:
   - If no learnings and gate_mode is COMPUTATION, offer code execution fallback
   - Call `suggest_code_execution()` for uncertain questions
3. Wire execution results into synthesis:
   - Code execution output becomes learnings
   - Use actual execution output, not LLM speculation

**Result**: Main query flow can produce and use execution results

---

### PHASE 5: Enable Feature Flag by Default
**File**: [agent/config/feature_flags.py](agent/config/feature_flags.py)

1. Change `code_execution_enabled` default:
   - From: `code_execution_enabled: bool = False`
   - To: `code_execution_enabled: bool = True`
2. OR update settings.py to use `FeatureFlags.tier_1_only()` which already enables it

**Result**: Code execution is enabled globally, no opt-in needed

---

## Integration Points (Critical Dependencies)

### Entry Gate → Orchestrator → Blocks → Tools
```
Query "how many r's in strawberry"
    ↓
entry_gate() → "COMPUTATION"
    ↓
orchestrator.decompose_task() → [TaskNode(node_id="n1", subagent_type=CODE_GEN_EXECUTOR)]
    ↓
run_subagent(node) → run_code_executor_subagent()
    ↓
execute_analysis_code() → Python code → execute_python_code()
    ↓
"strawberry".count("r") → 3
    ↓
synthesis → answer: "3"
```

### Feature Flag Check
- `code_execution_enabled` must be True
- Entry gate COMPUTATION mode must be recognized
- CODE_GEN_EXECUTOR handler must be registered

---

## Files to Modify (Summary)

| File | Changes | Lines | Priority |
|------|---------|-------|----------|
| agent/routing/entry_gate.py | Add COMPUTATION mode detection + regex patterns | ~50 | 1 |
| agent/blocks/base.py | Create run_code_executor_subagent() + register | ~60 | 1 |
| agent/orchestrator/orchestrator.py | Route COMPUTATION → CODE_GEN_EXECUTOR in decompose_task() | ~30 | 1 |
| agent/query.py | Default use_code_execution=True, wire results | ~20 | 2 |
| agent/config/feature_flags.py | code_execution_enabled=True by default | ~5 | 3 |

---

## Verification Strategy

1. **Test COMPUTATION Classification**
   - Query: "how many r's in strawberry" → should gate as COMPUTATION
   - Query: "9.9 vs 9.11" → should gate as COMPUTATION
   - Query: "list files in directory" → should gate as COMPUTATION

2. **Test Orchestrator Routing**
   - COMPUTATION gate_mode → should create CODE_GEN_EXECUTOR node
   - Should NOT create RETRIEVER node for these

3. **Test Code Execution**
   - CODE_GEN_EXECUTOR subagent should execute Python code
   - Should return actual computation result as learnings
   - Synthesis should use that result in final answer

4. **End-to-End Smoke Test**
   - Run: `python -m agent.main --test "how many r's in strawberry"`
   - Expected: Agent executes code, returns "3"
   - NOT: "You could use Python" or semantic search result

---

## Decisions

1. **Use "COMPUTATION" mode** instead of "BASH_EXECUTION" because it covers both Python/bash
2. **Create CODE_GEN_EXECUTOR handler** rather than create new SubagentType because infrastructure already exists
3. **Default use_code_execution=True** because code execution should not be behind opt-in flag for better UX
4. **Reuse code_execution_integration.py** functions rather than rewrite, keeps integration points clean
5. **Entry gate detects COMPUTATION early** to avoid false semantic retrievals for factual questions

---

## Further Considerations

1. **Bash vs Python**: Should COMPUTATION mode auto-detect or require LLM to choose?
   - Recommend: LLM chooses in execute_analysis_code() based on task type
   
2. **Safety**: Code execution in production needs sandboxing
   - Assume user will provide safe sandbox_fn in production
   - Current implementation: /tmp/agent-exec (dev only)

3. **Fallback if execution fails**: What if code crashes?
   - Current: Returns error in SubagentResult
   - Recommend: Try semantic retrieval as fallback in synthesis phase

4. **When to NOT execute**: Should parametric queries ever trigger execution?
   - Recommend: No. "What is 2+2" should use parametric LLM directly.
   - Only COMPUTATION-gate queries should execute.

I'll analyze the full agent architecture and create a comprehensive fix plan. Let me gather all the pieces first.   

Searched for regex `class SubagentType|SubagentType\.|RETRIEVER|CODE_RETRIEVER|EXECUTOR` (`project-expo` · `v2/project-expo-final/agent/**/*.py`), 155 results

Searched for regex `class.*Tool|tool_registry|dispatch_tool|execute_python|execute_bash` (`project-expo` · `v2/project-expo-final/agent/**/*.py`), 32 results

Read , lines 1 to 100

Searched for regex `run_sandbox|run_code_gen_executor|run_file_generator|SubagentType\.SANDBOX|SubagentType\.CODE_GEN_EXECUTOR` (`project-expo` · `v2/project-expo-final/agent/**/*.py`), 4 results

Read , lines 1 to 120

Read , lines 100 to 160

Now let me create a comprehensive implementation plan. Saving to session memory first. 

Created memory file 

## Comprehensive Plan: Fix Tool Execution Gap

I've analyzed the full agent architecture. The good news: **all the tools and infrastructure already exist**. The issue is purely architectural wiring.

---

### What's Missing (Architecture Gap)

| Component | Status | Issue |
|-----------|--------|-------|
| Tool execution functions | ✅ Exist | `execute_python_code()`, `execute_bash_code()` available |
| Subagent executor type | ✅ Partially | `SubagentType.CODE_GEN_EXECUTOR` defined, but **no handler** |
| Execution block | ✅ Exists | Code execution modules present, **never called** |
| Entry gate mode | ❌ Missing | No "COMPUTATION" or "BASH_EXECUTION" mode for queries |
| Orchestrator routing | ❌ Missing | Only creates RETRIEVER/CODE_RETRIEVER nodes, never CODE_GEN_EXECUTOR |

---

### The Fix: 5 Independent Phases

**Phase 1: Entry Gate — Add COMPUTATION Mode** [`entry_gate.py`]
- Detect computation/system queries: `"how many r's"`, `"9.9 vs 9.11"`, `"files in directory"`
- Add regex patterns for bash commands, math/counting, file system queries
- Return gate mode: `"COMPUTATION"` instead of PARAMETRIC/SEMANTIC

**Phase 2: Execution Handler — Create CODE_GEN_EXECUTOR Adapter** [`blocks/base.py`]
- Create `run_code_executor_subagent()` function
- Wires task → code execution integration → actual Python/bash execution
- Register in `SUBAGENT_DISPATCH` dict
- Returns code execution output as learnings

**Phase 3: Orchestrator — Route to CODE_GEN_EXECUTOR** [`orchestrator/orchestrator.py`]
- In `decompose_task()`: if gate_mode == "COMPUTATION", create CODE_GEN_EXECUTOR node
- Allows LLM decomposition to suggest execution for complex computation

**Phase 4: Query Flow — Wire Results** [`query.py`]
- Default `use_code_execution=True` (currently False)
- Code execution output becomes learnings for synthesis
- Actual computation output used in final answer, not LLM speculation

**Phase 5: Enable Feature Flag** [`config/feature_flags.py`]
- Set `code_execution_enabled=True` by default
- Currently disabled, blocking entire path

---

### Data Flow After Fix

```
Query: "how many r's in strawberry?"
    ↓
Entry Gate: classifies as COMPUTATION (new mode)
    ↓
Orchestrator: creates CODE_GEN_EXECUTOR node
    ↓
run_code_executor_subagent():
    ├─ Generates: print("strawberry".count("r"))
    ├─ Executes: Python code
    ├─ Output: "3"
    └─ Returns as Learning
    ↓
Synthesis: "The answer is 3"
```

---

### Critical Files to Modify

| File | What | Lines | Priority |
|------|------|-------|----------|
| `agent/routing/entry_gate.py` | Add COMPUTATION detection patterns | ~50 | **1** |
| `agent/blocks/base.py` | Create CODE_GEN_EXECUTOR handler + register | ~60 | **1** |
| `agent/orchestrator/orchestrator.py` | Route COMPUTATION → CODE_GEN_EXECUTOR | ~30 | **1** |
| `agent/query.py` | Default execution=True, wire results | ~20 | **2** |
| `agent/config/feature_flags.py` | Enable code_execution_enabled | ~5 | **3** |

---

### Verification Tests

After implementation:
1. **Classification**: `"how many r's in strawberry"` → gate as COMPUTATION ✓
2. **Routing**: COMPUTATION mode → CODE_GEN_EXECUTOR node created ✓
3. **Execution**: Node executes Python code, returns "3" ✓
4. **End-to-end**: Agent answers "3", not "you could use Python" ✓

---

### Key Decisions

- **Use COMPUTATION mode** (broader than BASH_EXECUTION, covers Python/bash)
- **Reuse existing `code_execution_integration.py`** to avoid duplication
- **Default enable** code execution (better UX than opt-in)
- **Fallback to semantic** if execution fails (graceful degradation)

This is a **clean, minimal fix** with no breaking changes. All 5 phases are independent and can be implemented in parallel.

Ready to implement?