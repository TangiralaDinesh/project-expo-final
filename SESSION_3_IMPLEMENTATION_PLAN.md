# SESSION 3 IMPLEMENTATION PLAN — Continuation & Bug Fixes

**Status**: Analysis Complete | Ready for Implementation  
**Previous Work**: Tiers 1-2 mostly done, Tier 3 partially done, Tier 4 incomplete  
**Session Goal**: Fix integration gaps + complete Tier 3 + begin Tier 4  

---

## PART 1: ROOT CAUSE ANALYSIS OF PROBLEMS

### Problem 1: "Only One Focus on Research" (Comparison Queries)

**User's Example Query**: "Should I buy CDSL or EMVEE?"

**Expected Flow**:
```
1. ComparisonQueryDetector.detect() → finds CDSL and EMVEE as entities
2. orchestrator.decompose_task() receives comparison_result
3. Creates 2 parallel task nodes:
   - n1: "Retrieve CDSL specs, pros, cons"
   - n2: "Retrieve EMVEE specs, pros, cons"
4. Both run in parallel via asyncio.gather()
5. Synthesis combines results: "CDSL is better for X, EMVEE for Y"
```

**What's Actually Happening**:
```
1. ✅ ComparisonQueryDetector.detect() works
   └─ Returns: ComparisonDecision(
       is_comparison=True,
       entities=[ComparisonEntity(name="CDSL"), ComparisonEntity(name="EMVEE")],
       comparison_type="vs",
       confidence=0.92
     )
2. ✗ orchestrator.decompose_task() receives result but IGNORES IT
   └─ Line ~80-90 in orchestrator.py: 
      ```
      comparison_result = await detector.detect(task)
      # Code sets comparison_result but never uses it!
      # Should create parallel nodes for each entity
      ```
3. ✗ Falls back to single retriever node instead of parallel
4. ✗ Only retrieves for first entity mentioned
```

**Fix Location**: `agent/orchestrator/orchestrator.py` lines 80-120  
**Effort**: 1-2 hours

---

### Problem 2: "Creating Files" (Agent Awareness)

**Issue**: Agent doesn't know it can:
- Execute Python code
- Create documents (txt, pdf, docx, md)
- Write code files
- Save outputs

**Root Cause**: 
- Tool registry exists (`agent/tools/tool_registry.py`)
- Tool definitions exist (`agent/tools/agent_tools.py`)
- But NO tools are actually defined in the registry
- Executor framework exists but tools are NOT registered

**What Needs to Happen**:
```python
# In agent/tools/tool_registry.py:

class ToolRegistry:
    def __init__(self):
        self.tools = {
            # MISSING:
            "execute_python": ExecutePythonTool(),
            "create_file": CreateFileTool(),
            "read_file": ReadFileTool(),
            "list_files": ListFilesTool(),
            # ... etc
        }
```

**Fix Locations**: 
- `agent/tools/tool_registry.py` - Register tools
- `agent/tools/agent_tools.py` - Implement tool classes
- `agent/query.py` - Pass tools to orchestrator
- `agent/orchestrator/orchestrator.py` - Call executor for file/code operations

**Effort**: 2-3 hours

---

### Problem 3: "Speculative Questioning" (Not Generating User Questions)

**Expected Flow**:
```
Query: "What's better for mobile auth?"

Agent thinks:
  Hypothesis A: OAuth2 (confidence 0.72)
  Hypothesis B: JWT (confidence 0.68)
  Hypothesis C: Session-based (confidence 0.55)

Confidence gap A-B = 0.04 (small!) → ask user:

"Your question could mean several things. Which applies to you?

A) OAuth2 — Multiple services, federated login, third-party integration
   - Best for: SaaS, multi-tenant apps
   - Confidence: 72%

B) JWT — Stateless tokens, good for APIs and SPAs
   - Best for: Microservices, single-page apps
   - Confidence: 68%

C) Session-Based — Traditional server-side sessions
   - Best for: Monoliths, simple monoliths
   - Confidence: 55%

Which one matches your scenario?"
```

**What's Actually Happening**:
```
✗ BranchingOption objects are created (backend done)
✗ But query.py doesn't:
  - Show them to user
  - Take branch_selection parameter
  - Resume reasoning on chosen branch
  - Generate the actual speculative question text

✗ Missing: generate_speculative_question() function
✗ Missing: Information gain calculation for hypothesis scoring
```

**Fix Locations**:
- `agent/llm/speculative.py` (NEW) - Question generation
- `agent/query.py` - Expose branching_options to UI
- `agent/core/information_theory.py` (NEW) - Entropy/info gain calculation
- `agent/routing/branching_orchestrator.py` (NEW) - Handle branch selection flow

**Effort**: 3-4 hours

---

### Problem 4: "Parallel Thing" (State Coordination)

**Expected**: "I'm retrieving CDSL... and EMVEE... Done! Both finished in 3.2s"

**What's Missing**:
```
ParallelStateCoordinator exists (complete)
  ✗ But never called from orchestrator

Should be:
  1. orchestrator.decompose_task() creates task nodes
  2. For each node: coordinator.register(node_id, node_name)
  3. For each parallel batch:
     async with coordinator.track(node_id):
         result = await run_subagent(...)
  4. User sees progress: "50% complete (2 of 4 retrievals done)"
```

**Fix Location**: `agent/orchestrator/orchestrator.py` line ~150-200  
**Effort**: 1 hour

---

### Problem 5: "Cut Off Responses" (Token Limit Truncation)

**Example**:
```
Query: "Explain quantum computing"
Response: "Quantum computing is a paradigm shift in computation that 
leverages quantum mechanical phenomena such as superposition and entanglement 
to process information in fundamentally different ways than classical 
computers. The key principles include..."
[CUT OFF MID-WORD]
```

**Root Causes**:
1. `synthesis.py` calculates adaptive max_tokens but might underestimate
2. No fallback when token budget is exceeded mid-answer
3. Zoom level budgets (300/800/2000) might be too tight for complex queries

**Fix Needed**:
```python
# In synthesis.py:

async def global_synthesis_llm_stream(...):
    # Current: Sets max_tokens once
    # Needed: 
    #   1. Estimate answer length from learnings
    #   2. If needed tokens > budget:
    #      - Split into chunks or
    #      - Prioritize top learnings first or
    #      - Add "... (continued in next message)" signal
    #   3. Track token usage in real-time
    #   4. If approaching limit: graceful truncation + continuation marker
```

**Fix Locations**:
- `agent/llm/synthesis.py` - Token estimation + chunking
- `agent/config/budgets.py` - Review token allocation strategy

**Effort**: 2-3 hours

---

## PART 2: INTEGRATION WIRING FIXES

### Phase A: Wire Comparison Detection → Orchestrator

**File**: `agent/orchestrator/orchestrator.py`

**Change**: Lines 80-120 in `decompose_task()`

```python
# BEFORE:
comparison_result = await detector.detect(task)
# ... comparison_result computed but IGNORED

# AFTER:
comparison_result = await detector.detect(task)
if comparison_result.is_comparison and comparison_result.confidence > 0.6:
    # Create parallel node for each entity
    nodes = []
    for entity in comparison_result.entities:
        node = TaskNode(
            node_id=f"retriever_{entity.name.lower()}",
            subagent_type=SubagentType.RETRIEVER,
            task=f"Retrieve detailed information about {entity.name}: specs, pros, cons, use cases",
            depends_on=[],  # No dependencies = parallel!
        )
        nodes.append(node)
    
    return Decomposition(
        nodes=nodes,
        fan_out_eligible=True,
        is_comparison=True,
        comparison_entities=[e.name for e in comparison_result.entities],
    )
```

**Effort**: 30 minutes  
**Testing**: Query "CDSL vs EMVEE" → should retrieve both in parallel

---

### Phase B: Wire Branching UI Flow

**File**: `agent/query.py`

**Change**: `run_query()` function

```python
# BEFORE:
@dataclass
class QueryResult:
    answer: str
    branching_options: list = field(default_factory=list)
    branch_selection: Optional[int] = None  # Not used!

async def run_query(...):
    # Pivot loop returns branching_options
    # But they're never exposed to result
    result = QueryResult(answer=final_answer)
    return result

# AFTER:
async def run_query(..., branch_selection: Optional[int] = None):
    # Check if this is a branch selection response
    if branch_selection is not None and branching_session_id:
        # Continue with selected branch
        selected = previous_branching_options[branch_selection]
        # Re-run orchestrator with narrowed context
        orchestrator_result = await run_orchestrator(
            refined_query + f" {selected.explanation}",
            # ...
        )
        # Return answer for selected branch
        return QueryResult(answer=answer_for_branch)
    
    # Normal flow: if branching options generated
    pivot_result, branching_options = await run_pivot_loop(...)
    
    if branching_options:
        # Return options to user, don't auto-select
        result = QueryResult(
            answer=None,  # No answer yet, pending user choice
            branching_options=branching_options,
            branching_session_id=generate_session_id(),
        )
    else:
        # Normal answer
        result = QueryResult(answer=final_answer)
    
    return result
```

**Effort**: 1-2 hours  
**Testing**: Query with ambiguous terms → should return BranchingOptions

---

### Phase C: Integrate ParallelStateCoordinator

**File**: `agent/orchestrator/orchestrator.py`

**Change**: `run_task_graph()` function (around line 150-200)

```python
# BEFORE:
async def run_task_graph(nodes: list[TaskNode], ...):
    # Run in batches but no progress tracking
    results = await asyncio.gather(*[run_subagent(n) for n in batch])

# AFTER:
from agent.core.parallel_state import get_state_coordinator

async def run_task_graph(nodes: list[TaskNode], ...):
    coordinator = get_state_coordinator()
    
    # Register all operations
    for node in nodes:
        coordinator.register(
            operation_id=node.node_id,
            operation_name=f"Retrieve {node.task[:50]}...",
            metadata={"subagent_type": node.subagent_type.value}
        )
    
    # Run in batches with tracking
    for batch in graph_batches:
        tasks = []
        for node in batch:
            async def run_with_tracking(n=node):
                await coordinator.start(n.node_id)
                try:
                    result = await run_subagent(...)
                    await coordinator.complete(n.node_id)
                    return result
                except Exception as e:
                    await coordinator.fail(n.node_id, str(e))
                    raise
            
            tasks.append(run_with_tracking())
        
        results.extend(await asyncio.gather(*tasks))
    
    # User can now check: coordinator.get_status()
    return results
```

**Effort**: 1 hour  
**Testing**: Parallel queries → should show progress

---

## PART 3: NEW FEATURE IMPLEMENTATIONS

### Feature 1: Information Gain Calculation (Tier 3 Enhancement)

**New File**: `agent/core/information_theory.py`

**Responsibility**: Score hypotheses by information-theoretic merit

```python
"""Information-theoretic utilities for hypothesis scoring.

Implements entropy and information gain calculations for Bayesian branching.
"""

import math
from dataclasses import dataclass
from typing import List, Dict, Optional

@dataclass
class HypothesisScore:
    """Score for one hypothesis."""
    hypothesis_name: str
    information_gain: float  # bits
    entropy_reduction: float  # 0-1
    confidence: float  # from hypothesis.prior
    rank: int  # 1 = best

def calculate_entropy(probability_distribution: Dict[str, float]) -> float:
    """
    Calculate Shannon entropy of probability distribution.
    
    H(X) = -sum(p(x) * log2(p(x)))
    
    High entropy = high uncertainty
    Low entropy = high confidence
    """
    entropy = 0.0
    for prob in probability_distribution.values():
        if prob > 0:
            entropy -= prob * math.log2(prob)
    return entropy

def calculate_information_gain(
    hypothesis_name: str,
    current_entropy: float,
    posterior_probabilities: Dict[str, float],
) -> float:
    """
    Information gain = current_entropy - expected_entropy_after_observation
    
    High IG = this observation highly reduces uncertainty
    Low IG = this observation barely changes anything
    """
    posterior_entropy = calculate_entropy(posterior_probabilities)
    ig = current_entropy - posterior_entropy
    return ig

def score_hypotheses(
    hypotheses: List['Hypothesis'],  # from core/types.py
    query_ambiguity: float = 0.7,  # 0-1, how ambiguous is the query?
) -> List[HypothesisScore]:
    """
    Score each hypothesis by how much it reduces uncertainty.
    
    Takes into account:
    - Hypothesis prior (what model thinks most likely)
    - Query ambiguity (how much uncertainty exists)
    - Supporting evidence (confidence boosters)
    
    Returns sorted by information_gain (highest first)
    """
    # Build probability distribution from hypotheses
    total_prior = sum(h.prior for h in hypotheses)
    probs = {h.name: h.prior / total_prior for h in hypotheses}
    
    current_entropy = calculate_entropy(probs)
    
    scores = []
    for hypothesis in hypotheses:
        # Simulate observing this hypothesis
        posterior = probs.copy()
        posterior[hypothesis.name] *= 1.5  # Updated to higher prob
        posterior = {k: v / sum(posterior.values()) for k, v in posterior.items()}
        
        ig = calculate_information_gain(
            hypothesis.name,
            current_entropy,
            posterior
        )
        
        score = HypothesisScore(
            hypothesis_name=hypothesis.name,
            information_gain=ig,
            entropy_reduction=ig / current_entropy if current_entropy > 0 else 0,
            confidence=hypothesis.prior,
            rank=0  # Will be set after sorting
        )
        scores.append(score)
    
    # Sort by info gain
    scores.sort(key=lambda s: s.information_gain, reverse=True)
    
    # Assign ranks
    for i, score in enumerate(scores):
        score.rank = i + 1
    
    return scores
```

**Effort**: 1-2 hours  
**Testing**: Score hypotheses for ambiguous query → should order by information gain

---

### Feature 2: Speculative Question Generation (Tier 3 Feature)

**New File**: `agent/llm/speculative.py`

**Responsibility**: Generate user-facing branching questions

```python
"""Generate speculative/clarifying questions for Bayesian branching.

When the agent is uncertain between competing hypotheses, it asks
the user clarifying questions instead of guessing.
"""

from typing import List, Optional
from dataclasses import dataclass
from .client import NIMClient, get_client
from ..core.types import Hypothesis
from ..core.pivot import BranchingOption

@dataclass
class SpeculativeQuestion:
    """A question to help user clarify ambiguity."""
    question_text: str
    context: str  # Why are we asking this?
    options: List[BranchingOption]  # Choices presented
    information_value: float  # How much does answer reduce uncertainty?

async def generate_speculative_question(
    query: str,
    branching_options: List[BranchingOption],
    client: Optional[NIMClient] = None,
) -> SpeculativeQuestion:
    """
    Convert list of BranchingOption into a user-friendly question.
    
    Input: BranchingOption list from pivot loop
    Output: SpeculativeQuestion with natural language phrasing
    
    Example:
        Input: [
            BranchingOption(label="OAuth2", explanation="For federated auth"),
            BranchingOption(label="JWT", explanation="For stateless APIs"),
        ]
        
        Output: SpeculativeQuestion(
            question_text=
                "I see your question could mean several things. Which scenario applies to you?"
                "\n\nA) OAuth2 — Multiple services, federated login"
                "\nB) JWT — Stateless tokens for APIs"
                "\n\nWhich one best matches what you're trying to do?"
            ,
            options=[...],
        )
    """
    client = client or get_client()
    
    # Build prompt for LLM to generate natural language question
    system_prompt = (
        "You are an expert at clarifying ambiguous user queries. "
        "Given a query and competing interpretations, generate a natural, "
        "friendly question that helps the user specify their intent."
    )
    
    options_text = "\n".join([
        f"{chr(65+i)}) {opt.label} — {opt.explanation}"
        for i, opt in enumerate(branching_options[:4])  # Max 4 options
    ])
    
    user_prompt = f"""
Original query: "{query}"

The agent has narrowed this down to these possible interpretations:

{options_text}

Generate a natural, friendly question (2-3 sentences) that helps the user 
specify which one they mean. Make it conversational, not robotic.

Format your response as:
QUESTION: [your question here]
CONTEXT: [brief explanation why we're asking]
"""
    
    # Call LLM
    response = await client.chat.completions.create(
        model=client.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.7,
        max_tokens=200,
    )
    
    # Parse response
    text = response.choices[0].message.content
    lines = text.split("\n")
    
    question_text = ""
    context = ""
    
    for line in lines:
        if line.startswith("QUESTION:"):
            question_text = line.replace("QUESTION:", "").strip()
        elif line.startswith("CONTEXT:"):
            context = line.replace("CONTEXT:", "").strip()
    
    return SpeculativeQuestion(
        question_text=question_text or "Which of these applies to you?",
        context=context or "Clarifying your intent",
        options=branching_options,
        information_value=0.8,  # High value clarification
    )
```

**Effort**: 2 hours  
**Testing**: Given branching options → should generate natural question

---

### Feature 3: Token Cutoff Handling (Tier 2/3 Enhancement)

**File**: `agent/llm/synthesis.py`

**Change**: `global_synthesis_llm()` and streaming version

```python
# BEFORE:
async def global_synthesis_llm(...):
    max_tokens = _calculate_adaptive_max_tokens(query, learnings, specificity)
    
    response = await client.chat.completions.create(
        model=...,
        max_tokens=max_tokens,  # Fixed limit
        ...
    )
    return response.choices[0].message.content

# AFTER:
async def global_synthesis_llm(...):
    # Estimate answer length
    query_tokens = _estimate_tokens(query)
    learnings_tokens = sum(_estimate_tokens(l.text) for l in learnings)
    estimated_answer_tokens = min(4096, query_tokens + learnings_tokens)
    
    # Start with zoom budget, escalate if needed
    max_tokens = _calculate_adaptive_max_tokens(query, learnings, specificity)
    
    # Safety: ensure we have room for answer
    if estimated_answer_tokens > max_tokens * 0.8:
        # Budget too tight - request more or indicate continuation
        max_tokens = min(4096, int(estimated_answer_tokens * 1.2))
    
    response = await client.chat.completions.create(
        model=...,
        max_tokens=max_tokens,
        ...
    )
    
    answer = response.choices[0].message.content
    
    # Check if response hit token limit (typical signs: ends mid-sentence, "...")
    if _appears_truncated(answer):
        # Add continuation marker
        answer += "\n\n[Response truncated due to length. Ask for continuation with '/continue' or '/more']"
        
        # Optionally: store truncation point for continuation
        # metadata["needs_continuation"] = True
        # metadata["last_token_position"] = ...
    
    return answer

def _appears_truncated(text: str) -> bool:
    """Heuristic: check if response seems cut off."""
    # Ends with incomplete sentence
    if text.rstrip().endswith((".", "!", "?", ":", "-", "—")):
        return False
    # Ends mid-word
    if text and not text.rstrip().endswith((" ", ".")):
        return True
    # Suspiciously short
    if len(text) < 100:
        return False
    return False
```

**Effort**: 2 hours  
**Testing**: Long query → should handle gracefully

---

## PART 4: IMPLEMENTATION SEQUENCING

### Week 1 (Sessions 3-4)

**Day 1 - Fixes** (4-6 hours)
1. Wire comparison detection → orchestrator (30 min)
2. Integrate ParallelStateCoordinator (1 hour)
3. Wire branching UI flow (2 hours)
4. Test & verify (1 hour)

**Day 2 - New Features** (6-8 hours)
1. Implement information_theory.py (2 hours)
2. Implement speculative.py (2 hours)
3. Implement token cutoff handling (2 hours)
4. Test & verify (1-2 hours)

**Day 3 - Tools** (6-8 hours)
1. Implement ExecutePythonTool (2 hours)
2. Implement CreateFileTool (1.5 hours)
3. Register tools in tool_registry (1 hour)
4. Wire tools into orchestrator (2 hours)
5. Test & verify (1-1.5 hours)

**Day 4 - Integration & Testing** (6-8 hours)
1. End-to-end flow testing (3 hours)
2. Edge case handling (2 hours)
3. Documentation (2-3 hours)

### Success Criteria

**All problems from problems.txt resolved**:
- ✅ Comparison queries retrieve ALL entities in parallel
- ✅ Agent can create files and execute code
- ✅ Speculative questions generated and presented
- ✅ Parallel operations tracked and visible
- ✅ Responses complete without truncation

**All tiers working**:
- ✅ Tier 1: Connectivity + learning
- ✅ Tier 2: Progressive revelation with zoom
- ✅ Tier 3: Bayesian branching with speculative questions
- ✅ Tier 4: Code execution + file creation

**Test Coverage**:
- ✅ 9/9 Tier 1 tests pass
- ✅ 6/6 Tier 2 tests pass
- ✅ NEW: 6/6 Tier 3 tests pass
- ✅ NEW: 5/5 Tier 4 tests pass

---

## PART 5: FILES TO CREATE/MODIFY

### Create (New Files)
- `agent/core/information_theory.py` (170 lines)
- `agent/llm/speculative.py` (180 lines)
- `agent/routing/branching_orchestrator.py` (120 lines)
- `test_tier3_integration.py` (150 lines)
- `test_tier4_integration.py` (150 lines)

### Modify (Existing)
- `agent/orchestrator/orchestrator.py` (+100 lines in decompose_task)
- `agent/query.py` (+80 lines in run_query)
- `agent/llm/synthesis.py` (+60 lines for token handling)
- `agent/tools/tool_registry.py` (+40 lines, registrations)
- `agent/tools/agent_tools.py` (+200 lines, tool implementations)

### Total Effort: ~1500 lines of new/modified code
### Timeline: 4-5 days for 1 developer

---

## READY TO IMPLEMENT?

Yes, all analysis complete. No more "design questions" needed.

Next step: User says "proceed" → Start Phase A (comparison wiring).

