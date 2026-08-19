# PERSONA SYSTEM & AMBIGUITY HANDLING ANALYSIS
**Focus**: How does system handle disagreement, 2-2 splits, ambiguous branches?  
**Status**: Analysis only — NO EDITS  

---

## PERSONA VOTING SYSTEM IN DEPTH

### The 4 Personas at a Glance

```python
# From core/critique.py - These are the independent evaluators

PERSONAS = {
    "Brutal Critic": {
        "mission": "Find everything that's wrong, incomplete, or misleading",
        "asks": "What's obviously wrong or missing?",
        "nature": "Adversarial, skeptical, fault-finding",
        "example_critique": "You only found info on CDSL, completely ignored EMVEE",
    },
    "Expectationist": {
        "mission": "Evaluate against what SHOULD be expected",
        "asks": "What did you expect to find but didn't?",
        "nature": "Standards-based, ambitious",
        "example_critique": "I expected detailed pricing comparison; you only have CDSL pricing",
    },
    "Realist": {
        "mission": "Pragmatic evaluation of what's actionable",
        "asks": "What's the realistic next question?",
        "nature": "Practical, grounded, resource-aware",
        "example_critique": "Pricing gap matters most. Should have focused there more.",
    },
    "Overthinker": {
        "mission": "Explore edge cases, nuances, assumptions",
        "asks": "What subtleties or edge cases were missed?",
        "nature": "Deep-diving, thorough, philosophical",
        "example_critique": "Didn't explore deployment scenario or regulatory compliance angles",
    },
}
```

### How Critique Currently Works

**File**: `core/critique.py` L200-300

```python
async def run_critique(
    goal: str,
    artifacts: list[str],  # What to evaluate (retrieved learnings, answers, etc.)
    client: Optional[NIMClient] = None,
) -> CritiqueResult:
    """Run 4-persona critique."""
    
    client = client or get_client()
    results = []
    
    # STEP 1: Each persona evaluates INDEPENDENTLY
    for persona_name, persona_config in PERSONA_SYSTEM.items():
        system_prompt = f"""You are the {persona_name}.
        
Your role: {persona_config['mission']}
Your key question: {persona_config['asks']}

Evaluate the following work/answer:
"""
        
        user_prompt = f"""Goal: {goal}

Current findings/answer: {'; '.join(artifacts[:5])}

Be critical and thorough. Return a JSON object:
{{
  "gaps": ["gap1", "gap2", ...],
  "confidence": 0.0-1.0,
  "reasoning": "Why do you think these gaps exist?",
  "recommendation": "What should be done next?"
}}"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        response = await client.chat_fast(
            messages,
            temperature=0.7,  # Some variation for independence
            response_format_json=True,
        )
        
        result = json.loads(response)
        results.append({
            "persona": persona_name,
            "gaps": result.get("gaps", []),
            "confidence": result.get("confidence", 0.5),
            "reasoning": result.get("reasoning", ""),
            "recommendation": result.get("recommendation", ""),
        })
    
    # STEP 2: AGGREGATE (Simple averaging)
    all_gaps = set()
    confidence_sum = 0
    
    for result in results:
        all_gaps.update(result["gaps"])
        confidence_sum += result["confidence"]
    
    avg_confidence = confidence_sum / 4
    
    return CritiqueResult(
        gaps_found=list(all_gaps),
        average_confidence=avg_confidence,
        persona_results=results,  # All 4 results stored
    )

@dataclass
class CritiqueResult:
    gaps_found: list[str]
    average_confidence: float
    persona_results: list[dict]  # Individual verdicts
```

---

## THE PROBLEM: 2-2 SPLIT HANDLING

### Scenario: "CDSL vs EMVEE" Query

**Persona Votes**:
```
Brutal Critic:    "WRONG - Missing EMVEE analysis completely"  (confidence: 0.95)
Expectationist:   "WRONG - Expected both options equally explored" (confidence: 0.90)
Realist:          "OK - CDSL info sufficient for most users"  (confidence: 0.60)
Overthinker:      "OK - Edge cases unexplored but query doesn't require depth" (confidence: 0.55)
```

**Vote Split**: 2-2 (Brutal Critic + Expectationist vs Realist + Overthinker)

### What SHOULD Happen ❌

```python
# Expected logic (NOT IMPLEMENTED):

if count_agree("WRONG") == 2 and count_agree("OK") == 2:
    # TIEBREAKER NEEDED
    option_1 = "Trust critics (2 votes): get more data on EMVEE"
    option_2 = "Trust realists (2 votes): answer is sufficient"
    
    # ASK USER:
    if in_interactive_mode:
        await ask_user(
            question=f"Critics and realists disagree. Which matters more to you:\n{option_1}\n{option_2}",
            option_1=option_1,
            option_2=option_2,
        )
    else:
        # Default: trust critics (higher confidence avg = 0.925)
        return "WRONG - More retrieval needed"
```

### What ACTUALLY Happens ✅ (For Now, But Suboptimal)

**File**: `query.py` L230-280

```python
# After retrieval completes:
all_learnings, all_urls = _collect_results(orch_results)

# CRAG grading (separate system)
if all_learnings:
    grade = await grade_retrieval(effective_query, all_learnings, client=client)
    
    if grade.grade == RetrievalGrade.INCORRECT:
        # Discard learnings, direct answer + disclaimer
        answer = await direct_answer_llm(...)
        answer = "⚠️ Note: Retrieved sources were not relevant..."
    else:
        # Use learnings as-is
        answer = await global_synthesis_llm(...)

# ❌ WHAT'S MISSING:
# 1. No check: "Are we at a decision point where personas disagree?"
# 2. No tiebreaker: "Which persona's concern is more important?"
# 3. No user involvement: "Should we explore EMVEE further? (yes/no)"
# 4. Just uses CRAG grading (binary: correct/incorrect/ambiguous)
```

**Persona votes collected but not used in main flow**:
```python
# core/critique.py (called ONLY on pivot failure)
# Result: Persona consensus = "2-2 split, 0.75 avg confidence"
# System: "Uncertain, proceed with available info"
# User: "Why didn't you explore both options equally?"
```

---

## USER GUIDANCE: When User SHOULD Decide

### Scenario 1: Ambiguous Query
```
Query: "best database for my project"
Personas disagree on:
- Brutal Critic: "Missing requirements! Can't recommend without knowing: size, latency, cost"
- Realist: "For most projects, PostgreSQL works fine"
```

**System SHOULD do**:
```python
await ask_user(
    "Your question is ambiguous. Before I answer, which matters most?",
    options=[
        "Scalability at any cost",
        "Simplicity and standard choice",
        "Specific use case (tell me)",
    ]
)
```

**System ACTUALLY does**:
- Proceeds with available info
- User gets generic answer: "PostgreSQL is good for most uses"
- User disappointed: "But my project needs high-throughput, low-latency..."

### Scenario 2: Comparison Query
```
Query: "CDSL vs EMVEE"
Personas disagree on:
- Brutal Critic: "Incomplete - need to explore both equally"
- Expectationist: "Incomplete - missing comparison framework"
- Realist: "Sufficient for decision-making"
- Overthinker: "OK but could explore deployment complexity"
```

**System SHOULD ask**:
```
"I found CDSL info but less on EMVEE. Before I synthesize:
 1. Should I do equal-depth research on both? (yes/no)
 2. Which factors matter most? (price/features/adoption/support)
 3. What's your timeline? (need answer in 5 min / can wait)"
```

**System ACTUALLY does**:
- Returns answer based on available CDSL info
- Never asks about EMVEE parity
- User left wondering: "Why didn't you research the second option?"

---

## HOW TO DETECT TIEBREAKER SCENARIOS

### Detection Logic (NOT IMPLEMENTED)

```python
async def detect_tiebreaker_scenario(persona_results: list[dict]) -> Optional[TiebreakerCase]:
    """Detect when personas disagree evenly."""
    
    # Aggregate verdicts
    verdicts = {}
    for result in persona_results:
        verdict = classify_verdict(result["reasoning"])  # "WRONG" or "OK"
        if verdict not in verdicts:
            verdicts[verdict] = []
        verdicts[verdict].append({
            "persona": result["persona"],
            "confidence": result["confidence"],
            "reasoning": result["reasoning"],
        })
    
    # Check for tie scenarios
    if len(verdicts) == 2:
        sides = list(verdicts.values())
        if len(sides[0]) == len(sides[1]) == 2:
            # Perfect 2-2 split
            return TiebreakerCase(
                type="perfect_tie",
                side_a=sides[0],
                side_b=sides[1],
                confidence_a=sum(p["confidence"] for p in sides[0]) / 2,
                confidence_b=sum(p["confidence"] for p in sides[1]) / 2,
            )
        elif len(sides[0]) == 3 and len(sides[1]) == 1:
            # 3-1 (majority clear)
            return TiebreakerCase(
                type="clear_majority",
                majority=sides[0],
                minority=sides[1],
                minority_concern=sides[1][0]["reasoning"],  # Listen to the 1
            )
    
    return None
```

---

## CURRENT TIEBREAKER HANDLING: DEFAULT BEHAVIOR

### When No Tiebreaker Logic Exists

**File**: `orchestrator.py` L280-320 (Pivot recovery only)

```python
async def discriminate(_h_a, _h_b):
    """When hypotheses conflict, how do we choose?"""
    # Run subagent again with different approach
    last_result[0] = await run_subagent(sub_input)
    return Observation(succeeded=last_result[0].success, detail=...)

# But: This only runs if subagent already failed
# In main retrieval path: No tiebreaker logic at all
```

### Fallback in Main Query Path

**File**: `query.py` L240-260

```python
# No explicit tiebreaker
# Defaults to:
grade = await grade_retrieval(effective_query, all_learnings, client=client)

if grade.grade == RetrievalGrade.CORRECT:
    # Assume CRAG grading is right
    answer = await global_synthesis_llm(query, all_learnings, ...)
elif grade.grade == RetrievalGrade.INCORRECT:
    # CRAG said wrong, discard
    answer = await direct_answer_llm(query, ...)
else:  # AMBIGUOUS
    # Keep learnings but note uncertainty
    answer = await global_synthesis_llm(query, all_learnings, ...)
    # ❌ No special handling for ambiguous case
```

---

## HOW AMBIGUITY HANDLING SHOULD WORK (Vision)

### Ideal Flow for Comparison Queries

**Step 1: Detect Ambiguity**
```
Query: "CDSL vs EMVEE"
System recognizes: "Comparison query" → "Needs both entities"
```

**Step 2: Parallel Retrieval** (Phase 1 of earlier plan)
```
Retrieve CDSL
Retrieve EMVEE
Retrieve comparison aspects
```

**Step 3: Critique Check**
```
Brutal Critic: "Both explored?" → YES ✓
Expectationist: "Equal depth?" → YES ✓
Realist: "Actionable comparison?" → YES ✓
Overthinker: "Edge cases covered?" → Partially, but sufficient
```

**Step 4: If 2-2 Split**
```
Personas: 2 say "Yes, good" | 2 say "Could be better"

Ask User: "I found both options. Should I dive deeper into:
  - Cost analysis
  - Feature comparison  
  - Community/support
  - Deployment complexity
  - All of the above
  - Answer as is"
```

**Step 5: Synthesize with User Input**
```
User: "Cost and community"
System: Deep dive on cost + community only
Synthesis: Compare on those dimensions
```

---

## MULTIPLE AMBIGUOUS BRANCHES (User CAN'T Always Decide)

### When User Can't Choose

**Scenario**: "Recommend a deep learning framework"
```
Personas:
- Brutal Critic: "For what use case?"
- Expectationist: "Should cover TensorFlow, PyTorch, JAX"
- Realist: "TensorFlow for production, PyTorch for research"
- Overthinker: "What's your compute environment?"
```

**System WOULD ask**: "For what use case? (image/NLP/RL/all)" 
**But user says**: "I don't know yet, explore all?"

**Then system should**:
```python
# Instead of asking user (who can't decide):
# 1. Retrieve all branches (TensorFlow, PyTorch, JAX)
# 2. Ask: "Which aspect matters most?" (learning curve, speed, community)
# 3. Create comparison table
# 4. User picks based on matrix, not upfront decision
```

### Current Limitation ❌

System assumes user can always clarify upfront, but:
- User might say "I don't know" (can't guide)
- Query might have 3-5 competing interpretations (too many branches)
- User might learn what matters DURING research (dynamic)

**System needs**: Progressive disambiguation (explore all, then ask which matters)

---

## SUMMARY: Persona System Gaps

| Scenario | Current Behavior | Desired Behavior | Gap |
|----------|-----------------|------------------|-----|
| **Perfect Tie (2-2)** | Return all verdicts, proceed with available info | Ask user which concern matters more | ❌ Tiebreaker missing |
| **Majority (3-1)** | Proceed with majority verdict | Note minority concern, optionally explore | ⚠️ Minority ignored |
| **Ambiguous Query** | Proceed with generic answer | Ask which angle to prioritize | ❌ No clarification trigger |
| **Too Many Branches** | Can't handle | Retrieve all, then ask which dimension to optimize | ❌ No branch management |
| **User Can't Decide** | Block, ask again | Explore all options, create comparison matrix | ❌ No progressive disambiguation |

---

