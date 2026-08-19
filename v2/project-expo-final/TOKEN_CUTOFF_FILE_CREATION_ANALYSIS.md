# TOKEN CUTOFF & FILE CREATION WORKFLOW ANALYSIS
**Focus**: Why answers get cut mid-message, how file creation works  
**Status**: Analysis only — NO EDITS  

---

## TOKEN TRUNCATION ROOT CAUSE ANALYSIS

### The Chain of Limits

```
User Query (100 tokens)
  ↓
Entry Gate + Clarify (200 tokens)
  ↓
Orchestrator task (100 tokens)
  ↓
Semantic Retriever runs (30s timeout)
  ↓
Collect learnings (N tokens, often 3000-5000)
  ↓
CRAG grading (500 tokens)
  ↓
SYNTHESIS PROMPT BUILD:
  - Query: ~100 tokens
  - Learnings: ~4000 tokens (after selecting top_k)
  - Synthesis instructions: ~300 tokens
  ────────────────
  Total prompt: ~4400 tokens

  ↓
  
SYNTHESIS LLM CALL:
  - Input context: 4400 tokens
  - Max tokens output: 2048 tokens ← **BOTTLENECK**
  - Model response: Exactly 2048 tokens (cut off)
```

### The 2048 Token Limit

**File**: `agent/config/settings.py`

```python
class Settings:
    # LLM Configuration
    NIM_MODEL = "nvidia/llama-3.1-70b-instruct"
    NIM_API_BASE = os.getenv("NIM_API_BASE", "http://localhost:8888/v1")
    
    # Token limits for responses
    NIM_MAX_TOKENS = 2048  # ← THE CUTOFF POINT
    
    # Context window (total, for input + output)
    CONTEXT_WINDOW = 16000
    
    # Temperature
    NIM_TEMPERATURE = 0.7
    
    # These are applied in llm/client.py
```

**Usage**:

```python
# llm/client.py L100-150
async def chat_fast(
    self,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = None,
    response_format_json: bool = False,
) -> str:
    """Quick chat call to NIM."""
    
    # If not specified, use default
    if max_tokens is None:
        max_tokens = settings.NIM_MAX_TOKENS  # 2048
    
    response = await self.aclient.chat.completions.create(
        model=settings.NIM_MODEL,
        messages=messages,
        max_tokens=max_tokens,  # ← Passed here
        temperature=temperature,
        # ...
    )
    
    return response.choices[0].message.content
```

**Applied in synthesis**:

```python
# llm/synthesis.py L50-120
async def global_synthesis_llm(
    query: str,
    learnings: list[Learning],
    client: Optional[NIMClient] = None,
    prompt_specificity: str = "standard",
) -> str:
    """Synthesize learnings into a final answer."""
    
    client = client or get_client()
    
    # Build synthesis prompt
    prompt = build_synthesis_prompt(query, learnings, prompt_specificity)
    
    # LLM call
    raw = await client.chat_fast(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=2048,  # ← HARDCODED CUTOFF
    )
    
    return raw  # Could be incomplete!
```

---

## WHAT HAPPENS WHEN ANSWER HITS 2048 TOKENS

### Example Query

**Query**: "Explain machine learning with examples in detail"

**Learnings Retrieved** (4000 tokens):
```
- ML is a subset of AI that enables systems to learn from data
- Supervised learning: training on labeled examples like cat/dog classification
- Unsupervised learning: clustering similar items without labels
- Reinforcement learning: agent learns through rewards/penalties like game playing
- Deep learning: neural networks with many layers for complex patterns
- Transfer learning: reuse pre-trained models for new tasks
- [... continues for 4000 tokens total]
```

**Synthesis Prompt** (4400 tokens input):
```
User query: "Explain machine learning with examples in detail"

Based on these sources:
[4000 tokens of learnings]

Provide a comprehensive explanation covering:
1. What is machine learning?
2. Main types and approaches
3. Real-world examples for each type
4. How to get started
```

**LLM Response** (Limited to 2048 tokens):
```
Machine learning is a subset of artificial intelligence that enables 
computer systems to learn from data without being explicitly programmed. 
Instead of following hard-coded instructions, ML systems identify patterns 
in training data and use these patterns to make predictions or decisions 
on new data.

## Types of Machine Learning

**1. Supervised Learning**
When you have labeled training data, supervised learning algorithms learn 
the relationship between input features and target output labels.

Example: Email spam classification
- Training data: thousands of emails labeled as "spam" or "not spam"
- Model learns: patterns that distinguish spam from legitimate emails
- Application: filter incoming emails into inbox or spam folder

**2. Unsupervised Learning**
These algorithms find hidden patterns in unlabeled data without knowing 
what to look for in advance.

Example: Customer segmentation
- Training data: customer purchase history without predefined segments
- Model learns: groups customers with similar behavior
- Application: target marketing campaigns to each segment

**3. Reinforcement Learning**
An agent learns by interacting with an environment, receiving rewards for 
good actions and penalties for bad ones.

Example: Game playing (like AlphaGo)
- Agent learns: through thousands of games against itself
- Rewards: winning the game, penalties: losing moves
- Application: optimal decision-making in complex environments

## Real-World Applications

- Healthcare: predicting patient outcomes, drug discovery
- Finance: fraud detection, algorithmic trading
- Transportation: autonomous vehicles, route optimization
- Retail: [TRUNCATED AT 2048 TOKENS - MESSAGE CUT HERE]
```

**What User Sees**:
```
"- Retail: [TRUNCATED AT 2048 TOKENS - MESSAGE CUT HERE]"
```

**User's Reaction**: "Wait, the answer was cut off? Why didn't it finish?"

---

## Why No Fallback Exists

### No Detection of Truncation

**File**: `llm/synthesis.py` L50-120

```python
async def global_synthesis_llm(...) -> str:
    # ... build prompt ...
    raw = await client.chat_fast(
        messages=[...],
        max_tokens=2048,
    )
    
    return raw  # ❌ No check if raw ends with complete sentence
    
    # Missing checks:
    # if raw.endswith("..."):  # Indicates truncation
    # if raw[-20:] == "TRUNCATED":  # Check for LLM's self-truncation marker
    # if len(raw.split()) < expected_words:  # Check word count
```

### No Retry with Higher Limit

```python
# Missing logic:
# if is_truncated(raw):
#     # Try again with higher limit
#     raw = await client.chat_fast(
#         messages=[...],
#         max_tokens=4096,  # Double the limit
#     )
```

### No Streaming to Avoid Cutoff

```python
# Could stream response and collect full content:
async def global_synthesis_llm_stream(...):
    full_response = ""
    async for chunk in client.chat_stream(messages, max_tokens=4096):
        full_response += chunk
        yield chunk  # Stream to user in real-time
    return full_response
```

### Actually EXISTS but UNUSED

**File**: `llm/synthesis.py` L200-250 (Streaming variant)

```python
async def global_synthesis_llm_stream(
    query: str,
    learnings: list[Learning],
    client: Optional[NIMClient] = None,
    prompt_specificity: str = "standard",
) -> AsyncIterator[str]:
    """Stream synthesis output."""
    
    client = client or get_client()
    prompt = build_synthesis_prompt(query, learnings, prompt_specificity)
    
    async for chunk in client.chat_stream(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        # ❌ Note: Still uses default max_tokens somewhere
    ):
        yield chunk
```

**Where it's called**:
```python
# query.py L400-450 (run_query_stream)
async def run_query_stream(...) -> AsyncIterator[StreamEvent]:
    """Streaming variant."""
    
    # ... orchestrator runs ...
    
    async for delta in global_synthesis_llm_stream(
        effective_query, all_learnings, client=client,
    ):
        yield StreamEvent(type="answer_delta", data=delta)
```

**Problem**: Streaming version exists but:
1. Not used by default (sync `run_query()` is more common)
2. Still hits same 2048 token limit
3. Just spreads truncation over time

---

## WHY 2048 IS TOO SMALL

### Budget Analysis

| Type of Query | Typical Output Size |
|---|---|
| Simple factual | 500-800 tokens |
| Medium explanation | 1200-1800 tokens |
| Complex comparison (CDSL vs EMVEE) | 2500-3500 tokens |
| Tutorial/guide | 3000-5000 tokens |
| Comprehensive research | 4000-8000 tokens |

**Result**: Any query with 2+ concepts, or requesting "detailed/comprehensive" output will exceed 2048 tokens.

### Example: CDSL vs EMVEE

**Ideal answer breakdown**:
```
CDSL Overview:        400 tokens
CDSL Strengths:       300 tokens
CDSL Weaknesses:      300 tokens
────────────────
EMVEE Overview:       400 tokens
EMVEE Strengths:      300 tokens
EMVEE Weaknesses:     300 tokens
────────────────
Comparison Matrix:    400 tokens
Recommendation:       300 tokens
When to choose each:  400 tokens
────────────────
TOTAL:               2800 tokens  ← EXCEEDS LIMIT
```

**Result**: Gets truncated at ~2048, missing "When to choose each" section

---

## FILE CREATION WORKFLOW ANALYSIS

### How File Creation Works Currently

#### Step 1: User Makes Request

**Query Types That Trigger File Creation**:
```
1. "Create a Python script that..."
2. "Generate an Excel spreadsheet with..."
3. "Build a website for..."
4. "Write a report on..."
5. "Make a PowerPoint presentation about..."
```

#### Step 2: Intent Classification

**File**: `routing/intent_classifier.py`

```python
class Intent(Enum):
    BUILD_DOCUMENT = "build_document"    # Create PPTX, DOCX, HTML
    CODE_TASK = "code_task"              # Create .py, .js, .sh
    DATA_TASK = "data_task"              # Analyze, transform data
    SEARCH_TASK = "search_task"          # Research online
    RESEARCH = "research"                # Deep exploration
    PLANNING = "planning"                # Todo, outline
    QUERY = "query"                      # General question

async def classify_intent(query: str, client: Optional[NIMClient] = None) -> Intent:
    """Classify query intent."""
    
    # Pattern matching
    patterns = {
        Intent.BUILD_DOCUMENT: [
            r"create.*(?:powerpoint|deck|pptx|presentation|report|docx|html|website)",
            r"build.*(?:website|app|dashboard)",
            r"write.*(?:article|report|proposal)",
        ],
        Intent.CODE_TASK: [
            r"write.*(?:python|code|script|function|class)",
            r"create.*(?:api|server|client)",
            r"implement.*(?:algorithm|feature)",
        ],
        # ... more patterns ...
    }
    
    for intent, patterns_list in patterns.items():
        for pattern in patterns_list:
            if re.search(pattern, query, re.IGNORECASE):
                return intent  # Found!
    
    # LLM fallback
    result = await client.chat_fast(...)
    return Intent(result)
```

#### Step 3: Tool Selection

**File**: `routing/intent_classifier.py` L200-250

```python
def get_tools_for_intent(intent: Intent) -> list[str]:
    """Return tools appropriate for intent."""
    
    tools_by_intent = {
        Intent.BUILD_DOCUMENT: [
            "deck_builder",      # PowerPoint creator
            "report_builder",    # Word/PDF creator
            "website_builder",   # HTML/CSS/JS creator
        ],
        Intent.CODE_TASK: [
            "python_execute",    # Run Python
            "bash_execute",      # Run shell commands
            "file_write",        # Create files
            "file_read",         # Read files
        ],
        # ...
    }
    
    return tools_by_intent.get(intent, ["search_task"])  # Default: search
```

#### Step 4: Tool Execution

**Option A: Code File Creation**

```python
# Query: "Create a Python script that downloads a CSV file"

# Tool: file_write
{
    "tool": "file_write",
    "params": {
        "filename": "download_csv.py",
        "content": """import requests\n\ndef download_csv(url):\n    ...\n\nif __name__ == '__main__':\n    download_csv('https://...')\n""",
        "directory": "/workspaces/project-expo/v2/project-expo-final/",
    }
}
```

**Option B: Builder Tool**

```python
# Query: "Create a PowerPoint presentation about machine learning"

# Tool: deck_builder (invokes skill)
{
    "tool": "deck_builder",
    "params": {
        "title": "Machine Learning",
        "sections": [
            {"title": "What is ML?", "content": "..."},
            {"title": "Types of ML", "content": "..."},
            {"title": "Applications", "content": "..."},
        ],
        "style": "professional",
    }
}

# Result: Creates PPTX file via skill execution
```

#### Step 5: Tool Executor Runs

**File**: `tools/executor.py`

```python
async def execute_tool(
    tool_name: str,
    params: dict,
    timeout_s: float = 30.0,
) -> ToolResult:
    """Execute a single tool."""
    
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        raise ValueError(f"Unknown tool: {tool_name}")
    
    try:
        result = await asyncio.wait_for(
            handler(params),
            timeout=timeout_s,
        )
        return ToolResult(success=True, output=result)
    except asyncio.TimeoutError:
        return ToolResult(success=False, error="Tool timeout")
    except Exception as e:
        return ToolResult(success=False, error=str(e))
```

#### Step 6: File Created or Skill Invoked

**For code files** (`file_write` tool):
```python
# Directly writes to filesystem
with open(filename, 'w') as f:
    f.write(content)
# Result: File created at specified path
```

**For builder files** (PowerPoint, Word, HTML):
```python
# Invokes skill system
from skills.registry import get_skill_registry
registry = get_skill_registry()
skill = registry.match("deck_builder")
result = await execute_skill(skill, params, client)
# Result: .PPTX file created via skill
```

---

## FILE CREATION: CURRENT LIMITATIONS

### Limitation 1: Agent Doesn't Proactively Offer

**Current Behavior**:
```
User: "I want to learn about machine learning"
System: Returns text answer about ML
❌ System doesn't say: "Would you like me to create a PowerPoint presentation?"
```

**Ideal Behavior**:
```
User: "I want to learn about machine learning"
System: Returns text answer, then asks:
  "I can also create:
   - PowerPoint presentation with diagrams
   - Python notebook with examples
   - HTML interactive guide
  Would any of these help?"
```

### Limitation 2: File Creation Requires Explicit Request

**Works**:
```
"Create a Python script that..."
"Generate a PowerPoint presentation on..."
"Build a website for..."
```

**Doesn't Work**:
```
"I need a report on market analysis"
→ System creates text, doesn't offer "Create DOCX report?"

"Analyze this dataset and show me results"
→ System shows text results, doesn't offer "Create Excel spreadsheet?"
```

### Limitation 3: No Awareness of What Files Can Represent

**Intent Classification** (regex-based):
- Catches: "create Python script", "build website"
- Misses: "I need documentation" (could be DOCX or HTML)
- Misses: "Analyze this data" (could be Excel or notebook)

**Gap**: Intent classifier has **no semantic understanding** of when files are beneficial

### Limitation 4: No File Path Suggestions

```python
# Current flow:
tool_write_params = {
    "filename": params.get("filename", "output.py"),  # User must specify
    "content": generated_code,
}

# Better flow would be:
# 1. Suggest: "I'll create a file at: /workspaces/project-expo/scripts/analysis.py"
# 2. Ask: "Is this location OK?" 
# 3. Only create if user approves (or auto-create for trusted queries)
```

---

## SUMMARY: Token & File Handling Gaps

| Issue | Current | Needed | Gap |
|-------|---------|--------|-----|
| **Max tokens** | 2048 hardcoded | Dynamic based on query complexity | ❌ Fixed limit |
| **Truncation detection** | No check | Detect incomplete sentences | ❌ Missing |
| **Truncation recovery** | No retry | Retry with higher limit | ❌ Missing |
| **Streaming** | Exists but unused | Use by default for long answers | ⚠️ Underutilized |
| **File awareness** | Only on explicit request | Proactively suggest files | ❌ Not offered |
| **File types** | Known (PPTX, DOCX, HTML, .py) | Suggest appropriate type | ⚠️ No smart suggestion |
| **File paths** | Hardcoded or requested | Ask for user confirmation | ⚠️ Minimal UX |
| **Builder skills** | Registered and callable | More promotion + discovery | ⚠️ Hidden |

---

## EXAMPLE: CDSL/EMVEE FILE CREATION FLOW

### What SHOULD Happen (Not Implemented)

```
User: "Compare CDSL and EMVEE for me"

System:
  1. Recognizes: Comparison query (Phase 1)
  2. Retrieves CDSL and EMVEE info (parallel)
  3. Offers file creation:
     "I found detailed comparison info. Would you like:
      - Excel spreadsheet with side-by-side comparison?
      - PowerPoint presentation with visualizations?
      - PDF report with analysis?
      - Interactive HTML tool to explore factors?"
  4. User selects: "Excel spreadsheet"
  5. System: Creates comparison_matrix.xlsx with:
     - Sheet 1: Feature comparison
     - Sheet 2: Pricing analysis
     - Sheet 3: Pros/Cons summary
     - Sheet 4: Decision matrix
  6. File saved: /workspaces/project-expo/v2/project-expo-final/reports/CDSL_vs_EMVEE.xlsx
  7. User downloads and uses for decision-making
```

### What ACTUALLY Happens

```
User: "Compare CDSL and EMVEE for me"

System:
  1. Retrieves mostly CDSL info (no comparison detection)
  2. Returns text comparison (if any)
  3. ❌ Never offers to create Excel
  4. ❌ Never offers to create PowerPoint
  5. ❌ User stuck with text answer
  6. User has to manually create their own comparison sheet
```

---

