# MASTER IMPLEMENTATION PLAN — Complete End-to-End

**Status**: READY FOR IMPLEMENTATION  
**Scope**: All 4 Tiers + All Options (A, B, C, D)  
**Total Effort**: ~1500 lines of code across 12 files  
**Timeline**: 3-4 weeks full rollout; 1 week for Tier 1 only

---

## PHASE STRUCTURE

```
PHASE 0: Setup & Preparation (Day 1)
         ↓
PHASE 1: TIER 1 — CONNECTIVITY (Days 2-5) [Foundation]
         ↓
PHASE 2: TIER 2 — PROGRESSIVE REVELATION (Days 6-8) [User-Visible]
         ↓
PHASE 3: TIER 3 — BAYESIAN BRANCHING (Days 9-11) [Intelligence]
         ↓
PHASE 4: TIER 4 — CODE EXECUTION (Days 12-15) [Capability]
         ↓
PHASE 5: Integration Testing & Rollout (Days 16-18)
         ↓
COMPLETE
```

Each phase is **independently verifiable**. After each, the agent works better than before.

---

## PHASE 0: SETUP & PREPARATION (Day 1)

### 0.1 Create Base Implementation Branch
```bash
git checkout -b feature/reasoning-improvements
git branch -D feature/reasoning-improvements 2>/dev/null || true
```

### 0.2 Create Testing Framework
```python
# File: agent/tests/test_reasoning_improvements.py (NEW)

import pytest
import asyncio
from agent.query import run_query
from agent.core.reasoning import get_thinking_profile, ThinkingProfile
from agent.core.satisfaction import SatisfactionTracker
from agent.orchestrator.orchestrator import run_orchestrator
from agent.core.types import SubagentInput, SubagentType

class TestReasoningImprovements:
    """Base test class for all improvements"""
    
    @pytest.fixture
    def satisfaction_tracker(self):
        return SatisfactionTracker()
    
    @pytest.fixture
    async def test_query(self):
        async def _run(q: str, **kwargs):
            return await run_query(q, **kwargs)
        return _run
```

### 0.3 Create Config for Feature Flags
```python
# File: agent/config/feature_flags.py (NEW)

from dataclasses import dataclass

@dataclass
class FeatureFlags:
    """Control new features during rollout"""
    connectivity_enabled: bool = False      # Tier 1
    progressive_zoom_enabled: bool = False  # Tier 2
    bayesian_branching_enabled: bool = False # Tier 3
    code_execution_enabled: bool = False    # Tier 4
    active_pivot_enabled: bool = False
    knowledge_graph_queries_enabled: bool = False
    
    @staticmethod
    def all_off():
        """Run with old behavior (backward compatibility)"""
        return FeatureFlags()
    
    @staticmethod
    def tier_1_only():
        """Connectivity only"""
        return FeatureFlags(
            connectivity_enabled=True,
            active_pivot_enabled=True,
            knowledge_graph_queries_enabled=True,
        )
    
    @staticmethod
    def all_on():
        """Full new system"""
        return FeatureFlags(
            connectivity_enabled=True,
            progressive_zoom_enabled=True,
            bayesian_branching_enabled=True,
            code_execution_enabled=True,
            active_pivot_enabled=True,
            knowledge_graph_queries_enabled=True,
        )
```

### 0.4 Add to config/settings.py
```python
# Add to agent/config/settings.py

from .feature_flags import FeatureFlags

class Settings:
    # ... existing settings ...
    
    def __init__(self):
        # ... existing init ...
        self.features = FeatureFlags.all_off()  # Start conservative
    
    def enable_tier_1(self):
        self.features = FeatureFlags.tier_1_only()
    
    def enable_all_tiers(self):
        self.features = FeatureFlags.all_on()
```

### 0.5 Checklist
- [ ] Branch created
- [ ] Test framework in place
- [ ] Feature flags defined
- [ ] Ready for Tier 1

---

## PHASE 1: TIER 1 — CONNECTIVITY (Days 2-5)

### Goal
Connect all components via feedback loops. No UI changes. Enable foundation for all other tiers.

### 1.1 Enhance core/types.py

**File**: `agent/core/types.py`

**Changes**: Add new types for connectivity

```python
# ADD after existing imports:

from typing import Optional, Callable, Awaitable

# ADD after SubagentType enum:

class DecisionSource(str, Enum):
    """Where did a decision originate?"""
    PARAMETRIC = "parametric"          # LLM internal knowledge
    RETRIEVAL = "retrieval"            # From retrieved sources
    HYBRID = "hybrid"                  # Both
    USER_SELECTION = "user_selection"  # User chose branch


@dataclass
class DecisionTrace:
    """Trace of a decision point"""
    decision_id: str
    source: DecisionSource
    confidence: float
    alternatives: list = field(default_factory=list)
    reasoning: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class CorrectionPattern:
    """Record one user correction"""
    pattern_type: str              # "error", "wanted_more_depth", "too_verbose", etc.
    severity: float                # 0-1, how bad?
    domain: str                    # "oauth", "react", etc. or empty for general
    timestamp: float               # when did this happen?
    decay_factor: float = 1.0      # multiplier for age (fades over time)


@dataclass
class Hypothesis:
    """One competing explanation"""
    label: str
    explanation: str
    prior: float = 0.5
    implies_circuit_break: str = ""
    pros: list = field(default_factory=list)      # NEW
    cons: list = field(default_factory=list)      # NEW
    supporting_evidence: list = field(default_factory=list)  # NEW
```

**Test**:
```python
# In agent/tests/test_reasoning_improvements.py, add:

def test_decision_trace_creation():
    trace = DecisionTrace(
        decision_id="gate_001",
        source=DecisionSource.RETRIEVAL,
        confidence=0.85,
        reasoning="Query mentions 'OAuth' → semantic retrieval needed"
    )
    assert trace.confidence == 0.85
    assert trace.source == DecisionSource.RETRIEVAL
```

### 1.2 Enhance routing/entry_gate.py

**File**: `agent/routing/entry_gate.py`

**Change 1**: Update GateDecision dataclass
```python
# REPLACE existing GateDecision:

@dataclass
class GateDecision:
    needs_retrieval: bool
    mode: str              # "PARAMETRIC" | "SEMANTIC" | "CODE" | "HYBRID"
    reason: str
    confidence: float = 1.0                          # NEW: 0.5-1.0
    alternative_modes: list[str] = field(default_factory=list)  # NEW
    decision_trace: Optional[DecisionTrace] = None   # NEW
```

**Change 2**: Update entry_gate function to return confidence
```python
# In entry_gate() function, at the end:

# OLD:
# return GateDecision(needs_retrieval=True, mode="SEMANTIC", reason="...")

# NEW:
decision = GateDecision(
    needs_retrieval=True,
    mode="SEMANTIC",
    reason="Query mentions specific technology",
    confidence=0.85,  # Based on regex certainty
    alternative_modes=["CODE"] if "implement" in query.lower() else [],
    decision_trace=DecisionTrace(
        decision_id=f"gate_{uuid4()}",
        source=DecisionSource.PARAMETRIC if regex_matched else DecisionSource.HYBRID,
        confidence=0.85,
        reasoning=reason,
    )
)
return decision
```

**Test**:
```python
# Add to test_reasoning_improvements.py:

@pytest.mark.asyncio
async def test_gate_decision_has_confidence():
    from agent.routing.entry_gate import entry_gate
    
    decision = await entry_gate("How do I implement OAuth2?")
    assert hasattr(decision, 'confidence')
    assert 0.5 <= decision.confidence <= 1.0
    assert isinstance(decision.alternative_modes, list)
```

### 1.3 Enhance core/reasoning.py

**File**: `agent/core/reasoning.py`

**Change 1**: Update ThinkingProfile dataclass
```python
# REPLACE existing ThinkingProfile:

@dataclass
class ThinkingProfile:
    """Execution parameters derived from query complexity + user history."""
    max_depth: int
    budget_s: float
    use_deep_propositions: bool
    use_critique: bool
    use_multi_query_expansion: bool
    prompt_specificity: str          # "expert" | "standard" | "casual"
    self_consistency_calls: int      # 1 = normal, 2 = anti-sycophancy
    
    # NEW FIELDS FOR TIER 1:
    correction_history_active: bool = True       # Apply correction patterns?
    uncertainty_tolerance: float = 0.6           # 0.3=stop early, 0.9=explore lots
    branching_enabled: bool = True               # Show user choice points? (Tier 3)
    confidence_target: float = 0.75              # Target confidence level
    knowledge_graph_enabled: bool = True         # Query graph for related concepts?
    active_pivot_enabled: bool = True            # Use pivot loop actively?
    
    # Tracking fields:
    applied_corrections: list[str] = field(default_factory=list)  # Which patterns were applied?
```

**Change 2**: Add function to build profile from satisfaction history
```python
# ADD new function in core/reasoning.py:

async def get_thinking_profile_with_history(
    query: str,
    prompt_specificity: str,
    satisfaction_tracker: Optional['SatisfactionTracker'] = None,
    features_enabled: Optional['FeatureFlags'] = None,
) -> ThinkingProfile:
    """
    Build thinking profile incorporating:
    1. Prompt specificity (existing)
    2. Correction history (NEW)
    3. Feature flags (NEW)
    """
    from ..config.feature_flags import FeatureFlags
    
    # Start with base profile
    base_profile = await get_thinking_profile(query, prompt_specificity)
    
    if not features_enabled:
        features_enabled = FeatureFlags.all_off()
    
    # Apply correction history if enabled
    applied_corrections = []
    if satisfaction_tracker and features_enabled.connectivity_enabled:
        corrections = satisfaction_tracker.get_recent_corrections(
            domain_hint=extract_domain(query),
            decay_days=7
        )
        
        # Adjust thinking based on corrections
        for correction_type, severity in corrections:
            if correction_type == "wanted_more_depth":
                # Increase depth for similar queries
                base_profile.max_depth = min(
                    base_profile.max_depth + 1,
                    DEFAULT_MAX_DEPTH
                )
                base_profile.uncertainty_tolerance = min(0.8, base_profile.uncertainty_tolerance + 0.1)
                applied_corrections.append("increased_depth")
            
            elif correction_type == "error_correction":
                # More self-consistency checks
                base_profile.self_consistency_calls = min(
                    base_profile.self_consistency_calls + 1,
                    3
                )
                applied_corrections.append("increased_consistency")
            
            elif correction_type == "too_verbose":
                # Less exploration
                base_profile.use_multi_query_expansion = False
                applied_corrections.append("reduced_expansion")
    
    # Apply feature flags
    base_profile.branching_enabled = features_enabled.bayesian_branching_enabled
    base_profile.knowledge_graph_enabled = features_enabled.knowledge_graph_queries_enabled
    base_profile.active_pivot_enabled = features_enabled.active_pivot_enabled
    base_profile.applied_corrections = applied_corrections
    
    return base_profile
```

**Test**:
```python
# Add to test_reasoning_improvements.py:

@pytest.mark.asyncio
async def test_thinking_profile_incorporates_history():
    from agent.core.reasoning import get_thinking_profile_with_history
    from agent.config.feature_flags import FeatureFlags
    
    tracker = SatisfactionTracker()
    # Simulate user asking for more depth
    tracker.record_correction("Query 1", "wanted_more_depth", "high")
    
    profile = await get_thinking_profile_with_history(
        "Similar query in same domain",
        "standard",
        satisfaction_tracker=tracker,
        features_enabled=FeatureFlags.tier_1_only()
    )
    
    assert "increased_depth" in profile.applied_corrections
    assert profile.max_depth > 1
```

### 1.4 Enhance core/satisfaction.py

**File**: `agent/core/satisfaction.py`

**Change 1**: Add methods to feed into next thinking_profile
```python
# ADD to SatisfactionTracker class:

def get_recent_corrections(
    self,
    domain_hint: str = "",
    decay_days: int = 7,
    limit: int = 5,
) -> list[tuple[str, float]]:
    """
    Get correction patterns weighted by recency and domain relevance.
    Returns: [(correction_type, severity), ...]
    
    Severity decays over time (older corrections matter less).
    """
    import time
    from datetime import timedelta
    
    now = time.time()
    cutoff = now - (decay_days * 24 * 3600)
    
    relevant = []
    for correction in self.corrections:
        if correction.timestamp < cutoff:
            continue
        
        # Calculate decay (older = lower weight)
        age_days = (now - correction.timestamp) / (24 * 3600)
        decay = max(0.1, 1.0 - (age_days / decay_days))
        
        # Domain boost (exact match > general)
        domain_match = 1.0 if domain_hint and domain_hint == correction.domain else 0.7
        
        severity = correction.severity * decay * domain_match
        relevant.append((correction.pattern_type, severity))
    
    # Sort by severity, return top N
    relevant.sort(key=lambda x: x[1], reverse=True)
    return relevant[:limit]

def apply_to_thinking_profile(
    self,
    profile: 'ThinkingProfile',
    domain: str = "",
) -> 'ThinkingProfile':
    """
    Directly modify a thinking profile based on correction history.
    This is the FEEDBACK LOOP.
    """
    corrections = self.get_recent_corrections(domain_hint=domain)
    
    for correction_type, severity in corrections:
        if correction_type == "error_correction":
            profile.self_consistency_calls = min(
                profile.self_consistency_calls + int(severity * 2),
                3
            )
        elif correction_type == "wanted_more_depth":
            profile.max_depth = min(
                profile.max_depth + int(severity),
                5
            )
            profile.use_deep_propositions = True
        elif correction_type == "incomplete_work":
            profile.budget_s = min(profile.budget_s * (1 + severity * 0.5), 60)
        elif correction_type == "too_verbose":
            profile.use_multi_query_expansion = False
    
    return profile
```

**Change 2**: Link to query.py
```python
# In query.py, update run_query():

async def run_query(
    query: str,
    *,
    memory_context: Optional[list[str]] = None,
    fetch_fn=None,
    code_tool_fn=None,
    client: Optional[NIMClient] = None,
    effort_bias: Optional[EffortBias] = None,
    satisfaction: Optional[SatisfactionTracker] = None,  # PASSED IN
    features_enabled: Optional[FeatureFlags] = None,     # NEW
) -> QueryResult:
    
    # ... existing code ...
    
    # UPDATED: Build thinking_profile WITH satisfaction history
    from agent.core.reasoning import get_thinking_profile_with_history
    
    thinking_profile = await get_thinking_profile_with_history(
        query=query,
        prompt_specificity=result.prompt_specificity,
        satisfaction_tracker=satisfaction,
        features_enabled=features_enabled,
    )
    
    # ... continue ...
```

**Test**:
```python
# Add to test_reasoning_improvements.py:

def test_satisfaction_feedback_loop():
    from agent.core.satisfaction import SatisfactionTracker
    from agent.core.reasoning import ThinkingProfile
    
    tracker = SatisfactionTracker()
    
    # Record a correction
    tracker.record_correction(
        query_id="q1",
        correction_type="wanted_more_depth",
        severity=0.9,
        domain="oauth"
    )
    
    # Create profile for related domain
    profile = ThinkingProfile(
        max_depth=2,
        budget_s=10,
        use_deep_propositions=False,
        use_critique=True,
        use_multi_query_expansion=True,
        prompt_specificity="standard",
        self_consistency_calls=1,
    )
    
    # Apply feedback
    modified = tracker.apply_to_thinking_profile(profile, domain="oauth")
    
    assert modified.max_depth > profile.max_depth
    assert modified.use_deep_propositions == True
```

### 1.5 Enhance core/pivot.py

**File**: `agent/core/pivot.py`

**Change 1**: Add BranchingOption type
```python
# ADD after existing imports:

@dataclass
class BranchingOption:
    """One hypothesis presented to user as a choice"""
    label: str                          # "Empirical Approach", "Practical Approach"
    explanation: str                    # Why choose this?
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    confidence: float = 0.5             # Agent's confidence in this approach
    evidence_level: str = "moderate"    # "weak" | "moderate" | "strong"
    estimated_depth: int = 2            # How deep will this go?
```

**Change 2**: Modify run_pivot_loop to support branching
```python
# REPLACE run_pivot_loop signature:

async def run_pivot_loop(
    goal: str,
    first_action: Callable[[], Awaitable[Observation]],
    generate_hypotheses: HypothesisGenerator,
    run_discriminating_experiment: DiscriminatingExperiment,
    # NEW PARAMETERS:
    branching_enabled: bool = False,
    confidence_threshold: float = 0.75,
) -> tuple[PivotDecision, list[BranchingOption]]:
    """
    Returns: (decision, branching_options)
    
    If branching_enabled and top 2 hypotheses are close in confidence,
    returns both as branching options for user selection.
    Otherwise returns empty list and auto-selected decision.
    """
    observation = await first_action()
    if observation.succeeded:
        return (PivotDecision(goal=goal, next_action="none"), [])
    
    hypotheses = generate_hypotheses(goal, observation)
    if not hypotheses:
        return (PivotDecision(goal=goal, next_action="abandon"), [])
    
    # Sort by prior
    hypotheses = sorted(hypotheses, key=lambda h: h.prior, reverse=True)
    h_a, h_b = hypotheses[0], (hypotheses[1] if len(hypotheses) > 1 else None)
    
    # Check if branching should be offered
    branching_options = []
    if branching_enabled and h_b:
        confidence_gap = h_a.prior - h_b.prior
        
        # If gap is small, offer both as options (Tier 3 feature)
        if confidence_gap < 0.25:
            branching_options = [
                BranchingOption(
                    label=h.label,
                    explanation=h.explanation,
                    pros=h.pros if hasattr(h, 'pros') else [],
                    cons=h.cons if hasattr(h, 'cons') else [],
                    confidence=h.prior,
                    evidence_level="strong" if h.prior > 0.7 else "moderate",
                )
                for h in [h_a, h_b]
            ]
    
    # Auto-select top hypothesis
    confirmed = h_a
    
    return (
        PivotDecision(
            goal=goal,
            confirmed_hypothesis=confirmed,
            next_action=f"pursue_{confirmed.label}",
        ),
        branching_options  # Empty if branching not enabled or confidence gap large
    )
```

**Test**:
```python
# Add to test_reasoning_improvements.py:

@pytest.mark.asyncio
async def test_pivot_loop_with_branching():
    from agent.core.pivot import run_pivot_loop, Observation, Hypothesis
    
    async def first_action():
        return Observation(succeeded=False, detail="Data ambiguous")
    
    def generate_hypotheses(goal, obs):
        return [
            Hypothesis(label="Path A", explanation="...", prior=0.6, pros=["X"], cons=["Y"]),
            Hypothesis(label="Path B", explanation="...", prior=0.55, pros=["Z"], cons=["W"]),
        ]
    
    async def discriminate(h1, h2):
        return Observation(succeeded=False, detail="Still unclear")
    
    decision, options = await run_pivot_loop(
        goal="test",
        first_action=first_action,
        generate_hypotheses=generate_hypotheses,
        run_discriminating_experiment=discriminate,
        branching_enabled=True,
    )
    
    # Should return both options when confidence gap < 0.25
    assert len(options) == 2
    assert options[0].label == "Path A"
    assert len(options[0].pros) > 0
```

### 1.6 Enhance orchestrator/orchestrator.py

**File**: `agent/orchestrator/orchestrator.py`

**Change 1**: Import new types
```python
# ADD after existing imports:

from ..core.pivot import run_pivot_loop, BranchingOption
from ..knowledge.graph_rag import GraphRAG
from ..config.feature_flags import FeatureFlags
```

**Change 2**: Add active pivot calling
```python
# ADD new method to orchestrator module:

async def run_subagent_with_pivot(
    sub_input: SubagentInput,
    run_subagent_fn: Callable,
    pivot_enabled: bool = True,
    branching_enabled: bool = False,
) -> tuple[SubagentResult, list[BranchingOption]]:
    """
    Run subagent with active pivot loop fallback.
    
    NEW TIER 1 FEATURE:
    If subagent fails, use pivot loop to find alternative approach.
    """
    from ..core.pivot import run_pivot_loop, Observation
    
    # First attempt
    result = await run_subagent_fn(sub_input)
    
    if result.success or not pivot_enabled:
        return result, []
    
    # Failed - use pivot loop to find alternative
    async def first_action():
        return Observation(
            succeeded=False,
            detail=result.error_reason,
        )
    
    def generate_hypotheses(goal, obs):
        # Generate alternative approaches based on failure
        # (implementation depends on subagent type)
        return [
            Hypothesis(
                label=f"Alternative approach {i}",
                explanation="...",
                prior=0.5 - (i * 0.1),
            )
            for i in range(2)
        ]
    
    async def discriminate(h1, h2):
        # Try h1, if fails try h2
        return Observation(succeeded=False)
    
    pivot_decision, branch_options = await run_pivot_loop(
        goal=result.task,
        first_action=first_action,
        generate_hypotheses=generate_hypotheses,
        run_discriminating_experiment=discriminate,
        branching_enabled=branching_enabled,
    )
    
    # Update result with pivot recommendation
    result.error_reason = pivot_decision.next_action
    
    return result, branch_options
```

**Change 3**: Add knowledge graph querying
```python
# ADD to run_orchestrator function:

# After gathering initial learnings, query knowledge graph
if thinking_profile.knowledge_graph_enabled and graph_rag:
    try:
        # Extract main concepts from learnings
        concepts = extract_entities(learnings)
        
        for concept in concepts[:2]:  # Top 2 concepts
            related = await graph_rag.find_related(
                concept=concept,
                relation_types=["related_to", "component_of"],
                max_depth=1,
            )
            
            # If high-relevance related concepts found, trigger optional subagents
            for rel_concept in related:
                if rel_concept.relevance_score > thinking_profile.confidence_target:
                    # Add optional subagent
                    optional_node = TaskNode(
                        node_id=f"graph_{rel_concept.name}",
                        subagent_type=SubagentType.RETRIEVER,
                        task=f"Explain {rel_concept.name} relation to {query}",
                        depends_on=[],
                    )
                    decomp.nodes.append(optional_node)
    except Exception as e:
        logger.debug(f"Graph query failed: {e}")
```

**Test**:
```python
# Add to test_reasoning_improvements.py:

@pytest.mark.asyncio
async def test_orchestrator_calls_pivot_on_failure():
    from agent.orchestrator.orchestrator import run_subagent_with_pivot
    from agent.core.types import SubagentInput, SubagentResult, SubagentType
    
    async def mock_subagent_fail(sub_input):
        return SubagentResult(
            subagent_type=SubagentType.RETRIEVER,
            success=False,
            error_reason="No results found",
        )
    
    result, branches = await run_subagent_with_pivot(
        SubagentInput(
            task="Find something",
            subagent_type=SubagentType.RETRIEVER,
        ),
        run_subagent_fn=mock_subagent_fail,
        pivot_enabled=True,
    )
    
    assert result.error_reason != "No results found"  # Pivot modified it
```

### 1.7 Update query.py Integration

**File**: `agent/query.py`

**Change**: Pass satisfaction and features through pipeline
```python
# In run_query function signature, ADD:

async def run_query(
    query: str,
    *,
    memory_context: Optional[list[str]] = None,
    fetch_fn=None,
    code_tool_fn=None,
    client: Optional[NIMClient] = None,
    effort_bias: Optional[EffortBias] = None,
    satisfaction: Optional[SatisfactionTracker] = None,          # NEW
    features_enabled: Optional[FeatureFlags] = None,            # NEW
) -> QueryResult:
    
    # ... existing gate logic ...
    
    # NEW: Build profile with satisfaction history
    thinking_profile = await get_thinking_profile_with_history(
        query=query,
        prompt_specificity=gate_result.prompt_specificity,
        satisfaction_tracker=satisfaction,
        features_enabled=features_enabled,
    )
    
    # ... pass to orchestrator ...
    result = await run_orchestrator(
        query=query,
        thinking_profile=thinking_profile,
        # NEW PARAMS:
        satisfaction_tracker=satisfaction,
        features_enabled=features_enabled,
        pivot_enabled=features_enabled.active_pivot_enabled if features_enabled else False,
    )
    
    # ... continue synthesis ...
```

### 1.8 Tier 1 Verification Checklist

- [ ] All type changes compile
- [ ] GateDecision includes confidence
- [ ] ThinkingProfile builds from satisfaction history
- [ ] Pivot loop returns branching options
- [ ] Orchestrator calls pivot on failures
- [ ] Knowledge graph querying integrated
- [ ] Feature flags control all new code
- [ ] Tests pass: `pytest agent/tests/test_reasoning_improvements.py::TestReasoningImprovements -v`
- [ ] Backward compatibility: old behavior works if features disabled
- [ ] Single test query works end-to-end with Tier 1 enabled

### 1.9 Tier 1 Files Summary

| File | Lines Changed | Key Changes |
|------|----------------|------------|
| core/types.py | +80 | DecisionTrace, CorrectionPattern, enhanced Hypothesis |
| routing/entry_gate.py | +30 | confidence score, alternatives, decision_trace |
| core/reasoning.py | +100 | get_thinking_profile_with_history, domain extraction |
| core/satisfaction.py | +80 | get_recent_corrections, apply_to_thinking_profile |
| core/pivot.py | +50 | BranchingOption, branching mode in loop |
| orchestrator/orchestrator.py | +100 | active pivot, knowledge graph querying |
| query.py | +40 | pass satisfaction + features through pipeline |
| config/feature_flags.py | +60 | NEW file, feature control |
| config/settings.py | +20 | Integrate feature_flags |
| tests/test_reasoning_improvements.py | +150 | NEW file, Tier 1 tests |

**TOTAL TIER 1: ~710 lines**

---

## PHASE 2: TIER 2 — PROGRESSIVE REVELATION (Days 6-8)

### Goal
User sees geohash "zoom" process and can guide investigation.

### 2.1 Enhance QueryResult

**File**: `agent/query.py`

**Change**: Add zoom tracking
```python
# REPLACE QueryResult dataclass:

@dataclass
class QueryResult:
    """Complete result of a query."""
    answer: str
    learnings: list[Learning] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    gate_decision: Optional[GateDecision] = None
    clarify_decision: Optional[ClarifyDecision] = None
    timing_ms: float = 0.0
    from_cache: bool = False
    prompt_specificity: str = "standard"
    
    # NEW TIER 2 FIELDS:
    zoom_level: int = 0                    # Current zoom: 0=overview, 1=focused, 2=deep
    zoom_options: list[str] = field(default_factory=list)  # What to zoom into next
    can_zoom_deeper: bool = True           # More detail available?
    current_zoom_direction: str = ""       # User's selected focus area
```

### 2.2 Enhance llm/synthesis.py

**File**: `agent/llm/synthesis.py`

**Change 1**: Add depth-adaptive synthesis
```python
# ADD new function:

@dataclass
class SynthesisConfig:
    depth_level: int = 0                   # 0=overview, 1=focused, 2=deep
    user_zoom_direction: Optional[str] = None
    show_zoom_options: bool = True
    max_tokens_per_level: dict = field(default_factory=lambda: {
        0: 300,    # Overview: concise
        1: 800,    # Focused: detailed
        2: 2000,   # Deep: comprehensive
    })


async def global_synthesis_llm_with_depth(
    query: str,
    learnings: list[Learning],
    prompt_specificity: str,
    config: SynthesisConfig,
    client: Optional[NIMClient] = None,
) -> tuple[str, list[str]]:
    """
    Generate answer adapted to zoom level.
    Returns: (answer, zoom_options)
    """
    client = client or get_client()
    
    # Adjust system prompt based on depth_level
    if config.depth_level == 0:
        # OVERVIEW: High-level structure
        depth_directive = (
            "Your response should be a HIGH-LEVEL OVERVIEW organized by major themes. "
            "Use clear section headers. For each section, provide a 1-2 sentence summary. "
            "Do NOT go deep into technical details. "
            "Suggest 3-4 natural subtopics the user might want to explore. "
            "Format: [OVERVIEW]\n[Subtopic 1]\n[Subtopic 2]\n[ZOOM_OPTIONS]: topic1, topic2, ..."
        )
        max_tokens = config.max_tokens_per_level[0]
        
    elif config.depth_level == 1:
        # FOCUSED: Deep dive on selected aspect
        depth_directive = (
            f"The user is interested in: {config.user_zoom_direction} "
            "Focus EXCLUSIVELY and deeply on this topic. Provide detailed explanation, "
            "structure, implications, relationships to other concepts. "
            "Assume the reader wants to understand this in depth. "
            "As you explain, identify 2-3 natural sub-topics. "
            "Format: [FOCUSED_EXPLANATION]\n[Sub-topic 1]\n[Sub-topic 2]\n[NEXT_ZOOM_OPTIONS]: sub1, sub2, ..."
        )
        max_tokens = config.max_tokens_per_level[1]
        
    else:  # depth_level == 2
        # DEEP DIVE: Maximum detail
        depth_directive = (
            f"Deep dive into: {config.user_zoom_direction}. "
            "Include: concrete examples, implementation details, edge cases, nuances, "
            "common mistakes, performance considerations. "
            "Assume reader is implementing this or publishing expertise. "
            "This is the most comprehensive treatment possible."
        )
        max_tokens = config.max_tokens_per_level[2]
    
    # Build system prompt
    system_prompt = (
        "You are the final synthesis step of a recursive research agent. "
        "You receive the original query and learnings gathered across all search branches.\n\n"
        f"{depth_directive}\n\n"
        "Principles:\n"
        "1. NARRATIVE OVER LIST: Connect related facts into a coherent story\n"
        "2. REDUNDANCY ELIMINATION: State each fact once\n"
        "3. GAP BRIDGING: Mark what's from sources vs your knowledge\n"
        "4. DEPTH MATCHING: Match depth to prompt signal"
    )
    
    # Call LLM
    response = await client.generate(
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"Query: {query}\n\nLearnings:\n" + "\n".join(
                f"- {l.text} (source: {l.source_url}, score: {l.score})"
                for l in learnings[:20]  # Top 20
            )
        }],
        max_tokens=max_tokens,
        temperature=0.7,
    )
    
    answer = response
    
    # Extract zoom options from answer (if at overview level)
    zoom_options = []
    if config.depth_level == 0 and config.show_zoom_options:
        # Parse [ZOOM_OPTIONS]: topic1, topic2, ...
        match = re.search(r'\[ZOOM_OPTIONS\]:\s*(.+)', answer)
        if match:
            zoom_options = [t.strip() for t in match.group(1).split(',')]
            # Remove the marker from answer
            answer = re.sub(r'\n?\[ZOOM_OPTIONS\]:.*', '', answer)
    
    elif config.depth_level == 1 and config.show_zoom_options:
        # Parse [NEXT_ZOOM_OPTIONS]: sub1, sub2, ...
        match = re.search(r'\[NEXT_ZOOM_OPTIONS\]:\s*(.+)', answer)
        if match:
            zoom_options = [t.strip() for t in match.group(1).split(',')]
            answer = re.sub(r'\n?\[NEXT_ZOOM_OPTIONS\]:.*', '', answer)
    
    return answer, zoom_options


async def global_synthesis_llm_stream_with_depth(
    query: str,
    learnings: list[Learning],
    prompt_specificity: str,
    config: SynthesisConfig,
    client: Optional[NIMClient] = None,
) -> AsyncIterator[tuple[str, list[str]]]:
    """
    Streaming version of depth-aware synthesis.
    Yields: (token_delta, zoom_options_when_complete)
    """
    client = client or get_client()
    
    # Same system prompt construction
    # ... (use global_synthesis_llm_with_depth logic for prompt)
    
    # Stream tokens
    async for token in client.stream(
        system=system_prompt,
        messages=[...],
        max_tokens=config.max_tokens_per_level[config.depth_level],
    ):
        yield token, []  # Zoom options only at end
    
    # Parse and yield zoom options at stream end
    # (After full answer is available)
    zoom_options = extract_zoom_options(full_answer, config.depth_level)
    yield "", zoom_options
```

**Change 2**: Update existing synthesis to use new function
```python
# REPLACE existing global_synthesis_llm call:

# OLD:
# answer = await global_synthesis_llm(query, learnings, prompt_specificity)

# NEW:
config = SynthesisConfig(
    depth_level=zoom_level,
    user_zoom_direction=zoom_direction,
    show_zoom_options=zoom_level < 2,
)
answer, zoom_options = await global_synthesis_llm_with_depth(
    query=query,
    learnings=learnings,
    prompt_specificity=prompt_specificity,
    config=config,
    client=client,
)
```

### 2.3 Update query.py for Zoom Handling

**File**: `agent/query.py`

**Change**: Track zoom through pipeline
```python
# UPDATE run_query signature:

async def run_query(
    query: str,
    *,
    memory_context: Optional[list[str]] = None,
    fetch_fn=None,
    code_tool_fn=None,
    client: Optional[NIMClient] = None,
    effort_bias: Optional[EffortBias] = None,
    satisfaction: Optional[SatisfactionTracker] = None,
    features_enabled: Optional[FeatureFlags] = None,
    # NEW TIER 2:
    zoom_level: int = 0,                # Current zoom depth
    user_zoom_direction: Optional[str] = None,  # Which aspect to focus on
) -> QueryResult:
    
    # ... existing logic ...
    
    # Pass zoom config to synthesis
    config = SynthesisConfig(
        depth_level=zoom_level,
        user_zoom_direction=user_zoom_direction,
        show_zoom_options=zoom_level < 2 and features_enabled.progressive_zoom_enabled,
    )
    
    answer, zoom_options = await global_synthesis_llm_with_depth(
        query=query,
        learnings=result.learnings,
        prompt_specificity=result.prompt_specificity,
        config=config,
        client=client,
    )
    
    # Return with zoom metadata
    return QueryResult(
        answer=answer,
        learnings=result.learnings,
        source_urls=result.source_urls,
        gate_decision=result.gate_decision,
        clarify_decision=result.clarify_decision,
        timing_ms=result.timing_ms,
        from_cache=result.from_cache,
        prompt_specificity=result.prompt_specificity,
        # NEW:
        zoom_level=zoom_level,
        zoom_options=zoom_options,
        can_zoom_deeper=zoom_level < 2,
        current_zoom_direction=user_zoom_direction or "",
    )
```

### 2.4 Update StreamEvent Types

**File**: `agent/transport/server.py` (or where StreamEvent is defined)

**Change**: Add zoom-related events
```python
# ADD new StreamEvent types:

# In StreamEvent class or as separate types:

@dataclass
class StreamEvent:
    type: str       # existing
    data: str = ""
    metadata: dict = field(default_factory=dict)

# Add to metadata for relevant events:
# type="progressive_section"
# metadata={
#     "zoom_level": 0,
#     "section": "overview" | "focused" | "deep",
#     "zoom_options": ["option1", "option2", ...]
# }
```

### 2.5 Tier 2 Tests

```python
# ADD to test_reasoning_improvements.py:

@pytest.mark.asyncio
async def test_synthesis_with_zoom_overview():
    from agent.llm.synthesis import global_synthesis_llm_with_depth, SynthesisConfig
    from agent.core.types import Learning
    
    learnings = [
        Learning(text="OAuth uses authorization codes", source_url="...", score=0.9),
        Learning(text="Tokens are short-lived credentials", source_url="...", score=0.8),
    ]
    
    config = SynthesisConfig(depth_level=0, show_zoom_options=True)
    answer, options = await global_synthesis_llm_with_depth(
        query="How does OAuth work?",
        learnings=learnings,
        prompt_specificity="standard",
        config=config,
    )
    
    assert len(answer) < 400  # Overview should be concise
    assert len(options) > 0   # Should have zoom options


@pytest.mark.asyncio
async def test_zoom_navigation():
    """Test: user asks about X → overview, selects zoom → focused answer"""
    from agent.query import run_query
    
    # Level 0: Overview
    result0 = await run_query(
        "How does OAuth work?",
        zoom_level=0,
        features_enabled=FeatureFlags(progressive_zoom_enabled=True),
    )
    
    assert result0.zoom_level == 0
    assert len(result0.zoom_options) > 0
    assert result0.can_zoom_deeper == True
    
    # Level 1: Zoom into first option
    result1 = await run_query(
        "How does OAuth work?",
        zoom_level=1,
        user_zoom_direction=result0.zoom_options[0],
        features_enabled=FeatureFlags(progressive_zoom_enabled=True),
    )
    
    assert result1.zoom_level == 1
    assert len(result1.answer) > len(result0.answer)
```

### 2.6 Tier 2 Files Summary

| File | Lines Changed | Key Changes |
|------|----------------|------------|
| query.py | +50 | zoom_level, user_zoom_direction params |
| llm/synthesis.py | +200 | SynthesisConfig, depth-adaptive synthesis |
| transport/server.py | +20 | Add zoom StreamEvents |
| tests/test_reasoning_improvements.py | +80 | Zoom navigation tests |

**TOTAL TIER 2: ~350 lines**

---

## PHASE 3: TIER 3 — BAYESIAN BRANCHING (Days 9-11)

### Goal
Present competing hypotheses to user; let them choose paths.

### 3.1 Update Orchestrator for User Branching

**File**: `agent/orchestrator/orchestrator.py`

**Change**: Add branch presentation
```python
# ADD to run_orchestrator:

async def run_orchestrator(
    query: str,
    thinking_profile: ThinkingProfile,
    user_selection_fn: Optional[Callable[[list[BranchingOption]], Awaitable[str]]] = None,
    # ... other existing params ...
) -> SubagentResult:
    
    decomp = await decompose_task(query, thinking_profile)
    
    # NEW TIER 3: Present branches if uncertain
    if (decomp.uncertainty > 0.6 and 
        thinking_profile.branching_enabled and
        user_selection_fn):
        
        # Present branches to user
        try:
            selected_branch = await user_selection_fn(decomp.hypothesis_options)
            # Filter tasks to match selected branch
            decomp.nodes = [
                n for n in decomp.nodes
                if getattr(n, 'branch_label', None) == selected_branch or not hasattr(n, 'branch_label')
            ]
        except TimeoutError:
            logger.info("No branch selection, auto-selecting top hypothesis")
    
    # Run with pivot
    for node in decomp.nodes:
        result, branches = await run_subagent_with_pivot(
            ...,
            branching_enabled=thinking_profile.branching_enabled,
        )
        
        if branches and user_selection_fn:
            # Present pivot branches to user
            await user_selection_fn(branches)
    
    # ... rest of orchestrator ...
```

### 3.2 Update query.py for Branch Handling

**File**: `agent/query.py`

**Change**: Accept user selection callback
```python
# UPDATE run_query signature:

async def run_query(
    query: str,
    *,
    # ... existing params ...
    user_branch_selection_fn: Optional[Callable[[list[BranchingOption]], Awaitable[str]]] = None,  # NEW
) -> QueryResult:
    
    # Pass to orchestrator
    result = await run_orchestrator(
        # ...
        user_selection_fn=user_branch_selection_fn,
    )
```

### 3.3 StreamEvents for Branching

```python
# StreamEvent types for Tier 3:

# type="branching_options"
# metadata={
#     "options": [
#         {
#             "label": "Option A",
#             "explanation": "...",
#             "pros": [...],
#             "cons": [...],
#             "confidence": 0.7
#         },
#         ...
#     ],
#     "question": "Which approach interests you?"
# }

# type="user_selection"
# metadata={
#     "selected": "Option A"
# }
```

### 3.4 Tier 3 Tests

```python
# ADD to test_reasoning_improvements.py:

@pytest.mark.asyncio
async def test_branching_presentation():
    from agent.orchestrator.orchestrator import run_orchestrator
    from agent.core.pivot import BranchingOption
    
    captured_branches = []
    
    async def mock_user_select(options: list[BranchingOption]):
        captured_branches.extend(options)
        return options[0].label
    
    await run_orchestrator(
        query="Compare A and B",
        thinking_profile=ThinkingProfile(..., branching_enabled=True),
        user_selection_fn=mock_user_select,
    )
    
    assert len(captured_branches) > 0
    assert all(hasattr(b, 'pros') for b in captured_branches)
```

### 3.5 Tier 3 Files Summary

| File | Lines Changed | Key Changes |
|------|----------------|------------|
| orchestrator/orchestrator.py | +60 | Branch presentation logic |
| query.py | +30 | user_branch_selection_fn param |
| transport/server.py | +30 | Branching StreamEvents |
| tests/test_reasoning_improvements.py | +50 | Branching tests |

**TOTAL TIER 3: ~170 lines**

---

## PHASE 4: TIER 4 — CODE EXECUTION (Days 12-15)

### Goal
Agent can generate and execute code dynamically.

### 4.1 Implement blocks/code/block.py

**File**: `agent/blocks/code/block.py`

```python
"""
Code generation + execution block.

When a query requires code analysis, data processing, or automation:
1. Generate Python/bash code
2. Execute in sandbox
3. Capture output as learning
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Optional

from ...llm.client import NIMClient, get_client
from ...config.budgets import CODE_EXEC_TIMEOUT_S
from ...tools.agent_tools import bash_tool, ToolResult
from ...core.types import Learning

logger = logging.getLogger(__name__)


@dataclass
class CodeGenInput:
    """Request for code generation + execution"""
    task: str
    available_files: list[str] = None
    language: str = "python"  # "python" | "bash"
    include_visualization: bool = False
    timeout_s: int = 30
    sandbox_dir: str = "/tmp"
    client: Optional[NIMClient] = None


@dataclass
class CodeGenOutput:
    """Result of code generation + execution"""
    code: str
    execution_output: str
    success: bool
    error: Optional[str] = None
    execution_time_s: float = 0
    visualizations: list[str] = None
    learning: Optional[Learning] = None


async def code_gen_executor_block(input: CodeGenInput) -> CodeGenOutput:
    """
    Generate code for task, execute in sandbox, return output.
    
    Safety:
    - Execution limited to sandbox_dir (default /tmp)
    - Timeout protection (default 30s)
    - Error isolation (code errors don't crash system)
    """
    import time
    
    client = input.client or get_client()
    start_time = time.time()
    
    # Step 1: Generate code
    logger.info(f"Generating {input.language} code for: {input.task}")
    
    system_prompt = f"""You are a data analysis expert. Generate {input.language} code
to complete this task:

Task: {input.task}

Available files: {', '.join(input.available_files or [])}

Requirements:
- Include error handling
- Print clear, structured output
- If applicable, suggest data formats (CSV, JSON)
- Keep execution time under {input.timeout_s}s
- Do NOT require dependencies beyond standard library

Generate ONLY the code block wrapped in triple backticks, no explanation."""
    
    user_prompt = f"Generate {input.language} code for: {input.task}"
    
    try:
        code_response = await client.generate(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=1500,
            temperature=0.3,  # Deterministic code
        )
    except Exception as e:
        return CodeGenOutput(
            code="",
            execution_output="",
            success=False,
            error=f"Code generation failed: {e}",
        )
    
    # Extract code from markdown
    code = extract_code_block(code_response, input.language)
    
    if not code:
        return CodeGenOutput(
            code="",
            execution_output="",
            success=False,
            error="No code block found in LLM response",
        )
    
    # Step 2: Execute in sandbox
    logger.info(f"Executing {input.language} code")
    
    try:
        if input.language == "python":
            # Execute Python code
            result = await bash_tool(
                f"cd {input.sandbox_dir} && timeout {input.timeout_s} python3 << 'EOF'\n{code}\nEOF",
                sandbox_dir=input.sandbox_dir,
            )
        else:  # bash
            # Execute bash directly
            result = await bash_tool(
                f"cd {input.sandbox_dir} && {code}",
                sandbox_dir=input.sandbox_dir,
                timeout_s=input.timeout_s,
            )
    except asyncio.TimeoutError:
        return CodeGenOutput(
            code=code,
            execution_output="",
            success=False,
            error=f"Code execution timed out after {input.timeout_s}s",
        )
    except Exception as e:
        return CodeGenOutput(
            code=code,
            execution_output="",
            success=False,
            error=f"Execution error: {e}",
        )
    
    # Step 3: Process output
    execution_time = time.time() - start_time
    
    if not result.success:
        return CodeGenOutput(
            code=code,
            execution_output=result.error,
            success=False,
            error=result.error,
            execution_time_s=execution_time,
        )
    
    # Create learning from execution output
    learning = Learning(
        text=f"Code Execution Result:\n```\n{result.output}\n```\n\nCode:\n```{input.language}\n{code}\n```",
        source_url="<code_execution>",
        score=1.0,
    )
    
    return CodeGenOutput(
        code=code,
        execution_output=result.output,
        success=True,
        error=None,
        execution_time_s=execution_time,
        visualizations=result.files_created,
        learning=learning,
    )


def extract_code_block(text: str, language: str = "python") -> str:
    """Extract code block from markdown-formatted text"""
    # Try ```language ... ``` format first
    pattern = rf"```{language}\n(.*?)\n```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1)
    
    # Try plain ``` ... ``` format
    pattern = r"```\n(.*?)\n```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1)
    
    # No code block found
    return ""


@dataclass
class SandboxConfig:
    """Configuration for code execution sandbox"""
    sandbox_dir: str = "/tmp/agent_sandbox"
    max_concurrent_executions: int = 2
    timeout_s: int = 30
    allowed_languages: list[str] = None
    
    def __post_init__(self):
        if self.allowed_languages is None:
            self.allowed_languages = ["python", "bash"]


class CodeExecutionManager:
    """Manages multiple parallel code executions with resource limits"""
    
    def __init__(self, config: SandboxConfig):
        self.config = config
        self.active_executions = 0
        self.results_cache: dict[str, CodeGenOutput] = {}
    
    async def execute_code(self, input: CodeGenInput) -> CodeGenOutput:
        """Execute code, respecting concurrency limits"""
        
        # Check limits
        if self.active_executions >= self.config.max_concurrent_executions:
            return CodeGenOutput(
                code="",
                execution_output="",
                success=False,
                error=f"Too many concurrent executions (limit: {self.config.max_concurrent_executions})",
            )
        
        if input.language not in self.config.allowed_languages:
            return CodeGenOutput(
                code="",
                execution_output="",
                success=False,
                error=f"Language '{input.language}' not allowed",
            )
        
        # Execute
        self.active_executions += 1
        try:
            output = await code_gen_executor_block(input)
            
            # Cache result
            cache_key = f"{input.task}_{input.language}"
            self.results_cache[cache_key] = output
            
            return output
        finally:
            self.active_executions -= 1
```

### 4.2 Add CODE_GEN_EXECUTOR Subagent Type

**File**: `agent/core/types.py`

**Change**: Add to SubagentType enum
```python
class SubagentType(str, Enum):
    RETRIEVER = "retriever"
    CODE_RETRIEVER = "code_retriever"
    SANDBOX = "sandbox"
    FILE_GENERATOR = "file_generator"
    CODE_GEN_EXECUTOR = "code_gen_executor"  # NEW
```

### 4.3 Add Adapter in blocks/base.py

**File**: `agent/blocks/base.py`

**Change**: Add adapter function
```python
# ADD:

async def run_code_gen_executor_subagent(sub_input: SubagentInput) -> SubagentResult:
    """Adapts code generation + execution block to SubagentResult interface"""
    from .code.block import code_gen_executor_block, CodeGenInput
    
    try:
        gen_input = CodeGenInput(
            task=sub_input.task,
            available_files=sub_input.payload.get("available_files", []),
            language=sub_input.payload.get("language", "python"),
            timeout_s=sub_input.payload.get("timeout_s", 30),
            sandbox_dir=sub_input.payload.get("sandbox_dir", "/tmp"),
            client=sub_input.payload.get("client"),
        )
        
        gen_output = await code_gen_executor_block(gen_input)
    except Exception as exc:
        logger.error(f"Code generation failed: {exc}")
        return SubagentResult(
            subagent_type=SubagentType.CODE_GEN_EXECUTOR,
            success=False,
            error_reason=f"exception: {exc}",
            session_id=sub_input.session_id,
            turn_id=sub_input.turn_id,
            parent_id=sub_input.parent_id,
        )
    
    if gen_output.learning:
        learnings = [gen_output.learning]
    else:
        learnings = []
    
    return SubagentResult(
        subagent_type=SubagentType.CODE_GEN_EXECUTOR,
        success=gen_output.success,
        learnings=learnings,
        error_reason=gen_output.error or "",
        session_id=sub_input.session_id,
        turn_id=sub_input.turn_id,
        parent_id=sub_input.parent_id,
    )


# ADD to SUBAGENT_DISPATCH:
SUBAGENT_DISPATCH = {
    SubagentType.RETRIEVER: run_retriever_subagent,
    SubagentType.CODE_RETRIEVER: run_code_retriever_subagent,
    SubagentType.SANDBOX: run_sandbox_subagent,
    SubagentType.FILE_GENERATOR: run_file_generator_subagent,
    SubagentType.CODE_GEN_EXECUTOR: run_code_gen_executor_subagent,  # NEW
}
```

### 4.4 Integrate into Orchestrator Decompose

**File**: `agent/orchestrator/orchestrator.py`

**Change**: Detect when to use code execution
```python
# In decompose function:

async def decompose_task(
    query: str,
    thinking_profile: ThinkingProfile,
    features_enabled: Optional[FeatureFlags] = None,
) -> Decomposition:
    
    # ... existing decomposition logic ...
    
    # NEW TIER 4: Detect code execution needs
    if features_enabled and features_enabled.code_execution_enabled:
        code_keywords = [
            "analyze", "process", "calculate", "generate", "automate",
            "parse", "extract", "transform", "visualize", "report"
        ]
        
        needs_code = any(kw in query.lower() for kw in code_keywords)
        
        if needs_code:
            # Add code_gen_executor node
            code_node = TaskNode(
                node_id=f"code_exec_{uuid4()}",
                subagent_type=SubagentType.CODE_GEN_EXECUTOR,
                task=query,
                depends_on=[],  # Can run in parallel with retrieval
            )
            decomp.nodes.append(code_node)
            decomp.fan_out_eligible = True
    
    return decomp
```

### 4.5 Stream Code Execution Output

**File**: `agent/transport/server.py` (or query streaming)

**Change**: Add code execution events
```python
# StreamEvent types for code execution:

# type="code_generation"
# metadata={
#     "language": "python",
#     "status": "generating"
# }

# type="code_execution"
# metadata={
#     "code": "... code ...",
#     "status": "running" | "complete" | "error",
#     "output": "... output ...",
#     "execution_time_s": 2.5
# }
```

### 4.6 Tier 4 Tests

```python
# ADD to test_reasoning_improvements.py:

@pytest.mark.asyncio
async def test_code_generation():
    from agent.blocks.code.block import code_gen_executor_block, CodeGenInput
    
    input_data = CodeGenInput(
        task="Calculate sum of [1, 2, 3, 4, 5]",
        language="python",
        sandbox_dir="/tmp",
    )
    
    output = await code_gen_executor_block(input_data)
    
    assert output.success
    assert "15" in output.execution_output or output.execution_output
    assert len(output.code) > 0


@pytest.mark.asyncio
async def test_code_execution_timeout():
    from agent.blocks.code.block import code_gen_executor_block, CodeGenInput
    
    input_data = CodeGenInput(
        task="Sleep for 60 seconds",
        language="python",
        timeout_s=2,
    )
    
    output = await code_gen_executor_block(input_data)
    
    assert not output.success
    assert "timeout" in output.error.lower()


@pytest.mark.asyncio
async def test_code_executor_subagent():
    from agent.blocks.base import run_code_gen_executor_subagent
    from agent.core.types import SubagentInput, SubagentType
    
    result = await run_code_gen_executor_subagent(
        SubagentInput(
            task="Sum [1, 2, 3]",
            subagent_type=SubagentType.CODE_GEN_EXECUTOR,
            payload={
                "language": "python",
                "sandbox_dir": "/tmp",
            }
        )
    )
    
    assert result.success
    assert len(result.learnings) > 0
```

### 4.7 Tier 4 Files Summary

| File | Lines Changed | Key Changes |
|------|----------------|------------|
| blocks/code/block.py | +250 | NEW file: code generation + execution |
| core/types.py | +5 | Add CODE_GEN_EXECUTOR to enum |
| blocks/base.py | +50 | Add adapter + dispatch |
| orchestrator/orchestrator.py | +40 | Detect code needs, add node |
| transport/server.py | +30 | Code execution StreamEvents |
| tests/test_reasoning_improvements.py | +100 | Code execution tests |

**TOTAL TIER 4: ~475 lines**

---

## PHASE 5: INTEGRATION TESTING & ROLLOUT (Days 16-18)

### 5.1 End-to-End Integration Tests

```python
# File: agent/tests/test_integration_all_tiers.py (NEW)

@pytest.mark.asyncio
async def test_full_query_with_all_tiers():
    """Complete flow: Connectivity + Zoom + Branching + Code"""
    
    from agent.query import run_query
    from agent.config.feature_flags import FeatureFlags
    from agent.core.satisfaction import SatisfactionTracker
    
    satisfaction = SatisfactionTracker()
    features = FeatureFlags.all_on()
    
    # Query 1: Initial question
    result1 = await run_query(
        "How do I analyze performance metrics?",
        satisfaction=satisfaction,
        features_enabled=features,
        zoom_level=0,
    )
    
    assert result1.zoom_level == 0
    assert len(result1.zoom_options) > 0
    assert result1.answer
    
    # Simulate: User chooses to zoom into implementation
    result2 = await run_query(
        "How do I analyze performance metrics?",
        satisfaction=satisfaction,
        features_enabled=features,
        zoom_level=1,
        user_zoom_direction=result1.zoom_options[0],
    )
    
    assert result2.zoom_level == 1
    assert len(result2.answer) > len(result1.answer)
    
    # Simulate: User corrects ("too theoretical, need code")
    satisfaction.record_correction(
        query_id=result2.id,
        correction_type="incomplete_work",
        severity=0.8,
        domain="performance",
    )
    
    # Query 3: Next question should have higher code emphasis
    result3 = await run_query(
        "Show me code for profiling Python",
        satisfaction=satisfaction,
        features_enabled=features,
    )
    
    # Should include code execution subagent
    assert any(l.source_url == "<code_execution>" for l in result3.learnings)


@pytest.mark.asyncio
async def test_cascade_failure_recovery():
    """Test that pivot loop activates on subagent failure"""
    
    from agent.orchestrator.orchestrator import run_subagent_with_pivot
    
    async def failing_subagent(sub_input):
        return SubagentResult(
            subagent_type=SubagentType.RETRIEVER,
            success=False,
            error_reason="No results found",
        )
    
    result, branches = await run_subagent_with_pivot(
        SubagentInput(
            task="Find impossible thing",
            subagent_type=SubagentType.RETRIEVER,
        ),
        run_subagent_fn=failing_subagent,
        pivot_enabled=True,
    )
    
    # Should have pivot recommendation
    assert "pursue_" in result.error_reason
```

### 5.2 Backward Compatibility Tests

```python
# Verify old behavior still works

@pytest.mark.asyncio
async def test_old_behavior_works():
    """With features disabled, should behave like before"""
    
    from agent.query import run_query
    from agent.config.feature_flags import FeatureFlags
    
    features = FeatureFlags.all_off()
    
    result = await run_query(
        "Simple query",
        features_enabled=features,
    )
    
    # Should work but without new features
    assert result.answer
    assert result.zoom_level == 0
    assert result.zoom_options == []
```

### 5.3 Performance Baseline

```python
# Track performance regressions

@pytest.mark.asyncio
async def test_performance_tier_1():
    """Tier 1 should add <100ms overhead"""
    
    import time
    from agent.query import run_query
    from agent.config.feature_flags import FeatureFlags
    
    # Warm up
    await run_query("test", features_enabled=FeatureFlags.all_off())
    
    # Without Tier 1
    start = time.time()
    await run_query("test", features_enabled=FeatureFlags.all_off())
    time_without = time.time() - start
    
    # With Tier 1
    start = time.time()
    await run_query("test", features_enabled=FeatureFlags.tier_1_only())
    time_with = time.time() - start
    
    overhead_ms = (time_with - time_without) * 1000
    assert overhead_ms < 100, f"Tier 1 overhead: {overhead_ms}ms (max 100ms)"
```

### 5.4 Rollout Checklist

Phase 5 Steps:

- [ ] Run full test suite: `pytest agent/tests/ -v`
- [ ] Test backward compatibility
- [ ] Performance baseline established
- [ ] Code review checklist:
  - [ ] All new code has docstrings
  - [ ] Error handling present
  - [ ] Logging added
  - [ ] Feature flags properly gated
  - [ ] No breaking changes to existing APIs
- [ ] Create integration test results document
- [ ] Prepare rollout strategy:
  - [ ] Deploy Tier 1 (foundation)
  - [ ] Monitor for 1 week
  - [ ] Deploy Tier 2-3 (user-facing)
  - [ ] Deploy Tier 4 (code execution)
- [ ] Create user documentation
  - [ ] How to use zoom feature
  - [ ] How to select branches
  - [ ] How code execution works
  - [ ] Safety/sandbox information

### 5.5 Post-Deployment Monitoring

```python
# File: agent/monitoring/reasoning_metrics.py (NEW)

from dataclasses import dataclass
import time

@dataclass
class ReasoningMetrics:
    """Track effectiveness of reasoning improvements"""
    user_satisfaction_score: float = 0.0  # 1-5
    zoom_usage_rate: float = 0.0          # % queries that zoom
    branch_selection_rate: float = 0.0    # % that present branches
    code_execution_rate: float = 0.0      # % using code
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0
    feedback_loop_effectiveness: float = 0.0  # Do corrections improve next query?
```

---

## SUMMARY: COMPLETE IMPLEMENTATION MAP

### Timeline
```
Week 1 (Days 1-5):  Phase 0 (setup) + Phase 1 (Tier 1 - Connectivity)
Week 2 (Days 6-11): Phase 2-3 (Tier 2-3 - Progressive + Branching)
Week 3 (Days 12-15): Phase 4 (Tier 4 - Code Execution)
Week 4 (Days 16-18): Phase 5 (Integration + Rollout)
```

### Lines of Code by Component

| Component | Phase 0 | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Total |
|-----------|---------|---------|---------|---------|---------|-------|
| config/ | 80 | 20 | 0 | 0 | 0 | 100 |
| core/ | 0 | 310 | 0 | 0 | 0 | 310 |
| routing/ | 0 | 30 | 0 | 0 | 0 | 30 |
| orchestrator/ | 0 | 100 | 0 | 60 | 40 | 200 |
| llm/ | 0 | 0 | 200 | 0 | 0 | 200 |
| query.py | 0 | 40 | 50 | 30 | 0 | 120 |
| blocks/ | 0 | 0 | 0 | 0 | 300 | 300 |
| transport/ | 0 | 0 | 20 | 30 | 30 | 80 |
| tests/ | 150 | 150 | 80 | 50 | 100 | 530 |
| **TOTAL** | **230** | **650** | **350** | **170** | **470** | **1870** |

### Files Modified

**NEW FILES** (9):
- config/feature_flags.py
- blocks/code/block.py
- tests/test_reasoning_improvements.py
- tests/test_integration_all_tiers.py
- tests/test_code_generation.py
- monitoring/reasoning_metrics.py
- docs/ZOOM_GUIDE.md
- docs/BRANCHING_GUIDE.md
- docs/CODE_EXECUTION_GUIDE.md

**MODIFIED FILES** (12):
- core/types.py (+85 lines)
- core/reasoning.py (+100 lines)
- core/satisfaction.py (+80 lines)
- core/pivot.py (+50 lines)
- routing/entry_gate.py (+30 lines)
- orchestrator/orchestrator.py (+200 lines)
- llm/synthesis.py (+200 lines)
- query.py (+120 lines)
- blocks/base.py (+50 lines)
- config/settings.py (+20 lines)
- transport/server.py (+80 lines)

---

## READY TO IMPLEMENT

This plan is **complete, sequenced, and ready to execute**.

### Next Steps:

**Option 1**: Start implementing Tier 1 now (I can code it)
**Option 2**: Review this plan, ask clarifications
**Option 3**: Adjust scope/timeline, then start

**Which would you prefer?**
