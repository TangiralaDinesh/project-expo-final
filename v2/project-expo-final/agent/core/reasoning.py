"""
Adaptive thinking — decides how deep the system should reason.

TWO sources of adaptation (from chats L320, L344):

1. GATE MODE → base parameters (depth, budget, feature flags)
2. PROMPT SPECIFICITY → depth calibration
   - Expert prompt ("implement OAuth2 PKCE with S256 code verifier") → deeper
   - Casual prompt ("what is oauth") → broader, more accessible
   This is NOT a gate decision — it's a presentation/depth decision

3. EFFORT BIAS from correction history (L327)
   - If user keeps asking for more depth → increase thinking
   - If user keeps correcting errors → increase self-consistency
   - Per-category, decaying, not a single accumulating counter

Also: classify prompt specificity using cheap heuristics (no LLM call).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .satisfaction import SatisfactionTracker
    from ..config.feature_flags import FeatureFlags

from ..config.budgets import DEFAULT_MAX_DEPTH, GLOBAL_BUDGET_S


@dataclass
class ThinkingProfile:
    """Execution parameters derived from query complexity + user history."""
    max_depth: int
    budget_s: float
    use_deep_propositions: bool
    use_critique: bool
    use_multi_query_expansion: bool
    prompt_specificity: str          # "expert" | "standard" | "casual"
    self_consistency_calls: int      # 1 = normal, 2 = anti-sycophancy check
    
    # NEW TIER 1 FIELDS:
    correction_history_active: bool = True       # Apply correction patterns?
    uncertainty_tolerance: float = 0.6           # 0.3=stop early, 0.9=explore lots
    branching_enabled: bool = True               # Show user choice points? (for Tier 3)
    confidence_target: float = 0.75              # Target confidence level
    knowledge_graph_enabled: bool = True         # Query graph for related concepts?
    active_pivot_enabled: bool = True            # Use pivot loop actively?
    
    # Tracking fields:
    applied_corrections: list[str] = field(default_factory=list)  # Which patterns were applied?


import json
import urllib.request
from ..config.settings import settings


def classify_prompt_specificity(query: str) -> str:
    """Classify whether the query comes from an expert or casual user.
    Uses a fast synchronous LLM call to evaluate semantic complexity.
    """
    word_count = len(query.split())
    if not settings.nim.api_keys:
        return "expert" if word_count > 30 else "standard"

    prompt = (
        "Analyze this user query to an AI. Classify the user's technical expertise level.\n"
        "Categories:\n"
        "- expert: uses advanced technical jargon, specific architectures, code patterns, or asks for complex implementations\n"
        "- casual: simple, high-level questions (e.g. 'what is python', 'explain X')\n"
        "- standard: typical queries that don't fit the extremes\n\n"
        "User query: " + query + "\n\n"
        "Output ONLY the category name."
    )

    req_data = {
        "model": settings.nim.fast_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 5
    }
    req = urllib.request.Request(
        f"{settings.nim.base_url}/chat/completions",
        data=json.dumps(req_data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.nim.api_keys[0]}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            ans = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"].strip().lower()
            if ans in ("expert", "casual", "standard"):
                return ans
    except Exception:
        pass

    return "expert" if word_count > 30 else "standard"


# ── Effort bias from correction history ──

@dataclass
class EffortBias:
    """Per-category effort bias with decay. From chat L327:
    'per-category effort_bias, decaying, not a single accumulating counter'"""
    depth_bias: float = 0.0      # User wants MORE depth (positive = deeper)
    accuracy_bias: float = 0.0   # User keeps correcting errors (positive = more careful)
    speed_bias: float = 0.0      # User wants FASTER responses (positive = shallower)

    def apply_decay(self, factor: float = 0.85):
        """Decay biases over time/turns so they don't overcorrect."""
        self.depth_bias *= factor
        self.accuracy_bias *= factor
        self.speed_bias *= factor

    def record_correction(self, correction_type: str):
        """Record a user correction and adjust the right bias."""
        if correction_type == "wanted_more_depth":
            self.depth_bias += 1.0
        elif correction_type == "error_correction":
            self.accuracy_bias += 1.0
        elif correction_type == "too_slow":
            self.speed_bias += 1.0
        elif correction_type == "too_verbose":
            self.depth_bias -= 0.5


# ── Profile computation ──

_BASE_PROFILES = {
    "PARAMETRIC": {
        "max_depth": 0,
        "budget_s": 5.0,
        "use_deep_propositions": False,
        "use_critique": False,
        "use_multi_query_expansion": False,
    },
    "SEMANTIC": {
        "max_depth": DEFAULT_MAX_DEPTH,
        "budget_s": GLOBAL_BUDGET_S,
        "use_deep_propositions": True,
        "use_critique": False,
        "use_multi_query_expansion": True,
    },
    "CODE": {
        "max_depth": 2,
        "budget_s": GLOBAL_BUDGET_S,
        "use_deep_propositions": True,
        "use_critique": False,
        "use_multi_query_expansion": True,
    },
    "HYBRID": {
        "max_depth": DEFAULT_MAX_DEPTH,
        "budget_s": GLOBAL_BUDGET_S + 10.0,
        "use_deep_propositions": True,
        "use_critique": True,
        "use_multi_query_expansion": True,
    },
}


def get_thinking_profile(
    gate_mode: str,
    query: str = "",
    effort_bias: EffortBias = None,
) -> ThinkingProfile:
    """Build execution parameters from gate mode + prompt specificity + user history.

    Three adaptation layers:
    1. Gate mode → base parameters
    2. Prompt specificity → presentation depth
    3. Effort bias → per-user adjustments from correction history
    """
    base = _BASE_PROFILES.get(gate_mode, _BASE_PROFILES["SEMANTIC"]).copy()
    specificity = classify_prompt_specificity(query) if query else "standard"

    # Apply effort bias
    self_consistency = 1
    if effort_bias:
        # Depth bias: user wants more/less depth
        if effort_bias.depth_bias > 1.0:
            base["max_depth"] = min(base["max_depth"] + 1, 5)
            base["use_critique"] = True
        elif effort_bias.depth_bias < -1.0:
            base["max_depth"] = max(base["max_depth"] - 1, 0)

        # Accuracy bias: user keeps finding errors → more self-consistency
        if effort_bias.accuracy_bias > 1.0:
            self_consistency = 2
            base["use_critique"] = True

        # Speed bias: user wants faster
        if effort_bias.speed_bias > 1.0:
            base["budget_s"] = max(base["budget_s"] * 0.7, 5.0)
            base["max_depth"] = max(base["max_depth"] - 1, 0)

    # Expert prompts get deeper treatment
    if specificity == "expert" and base["max_depth"] > 0:
        base["max_depth"] = min(base["max_depth"] + 1, 5)
        base["use_deep_propositions"] = True

    return ThinkingProfile(
        max_depth=base["max_depth"],
        budget_s=base["budget_s"],
        use_deep_propositions=base["use_deep_propositions"],
        use_critique=base["use_critique"],
        use_multi_query_expansion=base["use_multi_query_expansion"],
        prompt_specificity=specificity,
        self_consistency_calls=self_consistency,
    )


async def get_thinking_profile_with_history(
    query: str,
    prompt_specificity: str,
    gate_mode: str = "SEMANTIC",
    satisfaction_tracker: Optional[SatisfactionTracker] = None,
    features_enabled: Optional[FeatureFlags] = None,
) -> ThinkingProfile:
    """
    Build thinking profile incorporating:
    1. Prompt specificity (existing)
    2. Gate mode (existing)
    3. Correction history (NEW - Tier 1 feature)
    4. Feature flags (NEW - Tier 1 control)
    """
    from .observability import get_observability_tracker, MetricType
    
    # Start with base profile
    base_profile = get_thinking_profile(gate_mode, query)
    
    if not features_enabled:
        from ..config.feature_flags import FeatureFlags
        features_enabled = FeatureFlags.all_off()
    
    # Apply correction history if enabled
    applied_corrections = []
    domain = _extract_domain(query)
    tracker = get_observability_tracker()
    
    if satisfaction_tracker and features_enabled.connectivity_enabled:
        corrections = satisfaction_tracker.get_recent_corrections(
            domain_hint=domain,
            decay_days=7
        )
        
        # Adjust thinking based on corrections
        for correction_type, severity in corrections:
            if correction_type == "wanted_more_depth":
                # Increase depth for similar queries
                base_profile.max_depth = min(
                    base_profile.max_depth + int(severity),
                    5
                )
                base_profile.uncertainty_tolerance = min(0.9, base_profile.uncertainty_tolerance + 0.1)
                applied_corrections.append("increased_depth")
                tracker.record_event(
                    MetricType.THINKING_DEPTH_ADJUSTED,
                    value=severity,
                    domain=domain,
                    adjustment="depth_increase"
                )
            
            elif correction_type == "error_correction":
                # More self-consistency checks
                base_profile.self_consistency_calls = min(
                    base_profile.self_consistency_calls + 1,
                    3
                )
                applied_corrections.append("increased_consistency")
                tracker.record_event(
                    MetricType.CORRECTION_APPLIED,
                    value=severity,
                    domain=domain,
                    correction_type="error_correction"
                )
            
            elif correction_type == "too_verbose":
                # Less exploration
                base_profile.use_multi_query_expansion = False
                applied_corrections.append("reduced_expansion")
                tracker.record_event(
                    MetricType.CORRECTION_APPLIED,
                    value=severity,
                    domain=domain,
                    correction_type="too_verbose"
                )
            
            elif correction_type == "incomplete_work":
                # More thorough
                base_profile.budget_s = min(base_profile.budget_s * 1.3, 60)
                applied_corrections.append("increased_budget")
                tracker.record_event(
                    MetricType.CORRECTION_APPLIED,
                    value=severity,
                    domain=domain,
                    correction_type="incomplete_work"
                )

    
    # Apply feature flags
    base_profile.branching_enabled = features_enabled.bayesian_branching_enabled
    base_profile.knowledge_graph_enabled = features_enabled.knowledge_graph_queries_enabled
    base_profile.active_pivot_enabled = features_enabled.active_pivot_enabled
    base_profile.correction_history_active = features_enabled.connectivity_enabled
    base_profile.applied_corrections = applied_corrections
    
    return base_profile


def _extract_domain(query: str) -> str:
    """Extract domain hint from query (oauth, react, etc.)"""
    domains = ["oauth", "react", "python", "javascript", "go", "rust", "typescript",
               "kubernetes", "docker", "aws", "gcp", "azure", "sql", "nosql",
               "graphql", "rest", "authentication", "security", "performance"]
    
    query_lower = query.lower()
    for domain in domains:
        if domain in query_lower:
            return domain
    return ""


# ── Phase 2: Entropy-Based Learning Selection ──

def calculate_entropy_score(
    learning,
    all_learnings: list,
    query: str,
) -> float:
    """
    Calculate information-gain entropy score for a learning.
    
    Score combines:
    1. Relevance: semantic similarity to query
    2. Redundancy: overlap with other learnings (remove duplicates)
    3. Novelty: how much new info does it add
    
    Formula: score = relevance × (1 - redundancy) × novelty
    
    Args:
        learning: Individual learning object with .text, .title, .score
        all_learnings: List of all learnings for redundancy calc
        query: Original query for relevance calc
    
    Returns:
        Entropy score (0-1, higher = more information gain)
    """
    # Use existing learning score as base relevance (already computed by retrieval)
    relevance = min(getattr(learning, 'score', 0.5) / 10, 1.0)  # Normalize to 0-1
    
    # Calculate redundancy (how much overlap with other learnings)
    text_lower = learning.text.lower() if hasattr(learning, 'text') else ""
    query_lower = query.lower()
    
    redundancy = 0.0
    if text_lower and len(all_learnings) > 1:
        # Count how many other learnings have similar content
        learning_terms = set(text_lower.split())
        similar_count = 0
        
        for other in all_learnings:
            if other is learning:
                continue
            other_text = other.text.lower() if hasattr(other, 'text') else ""
            other_terms = set(other_text.split())
            
            # Jaccard similarity
            if learning_terms or other_terms:
                intersection = len(learning_terms & other_terms)
                union = len(learning_terms | other_terms)
                similarity = intersection / union if union > 0 else 0
                
                if similarity > 0.5:  # High similarity threshold
                    similar_count += 1
        
        # Redundancy score: 0 = unique, 1 = very redundant
        redundancy = min(similar_count / max(1, len(all_learnings) * 0.3), 1.0)
    
    # Novelty: presence of query terms (high novelty = directly answers query)
    novelty = 0.5
    if query_lower and text_lower:
        query_terms = set(w for w in query_lower.split() if len(w) > 3)
        text_terms = set(w for w in text_lower.split() if len(w) > 3)
        
        if query_terms:
            matched_terms = len(query_terms & text_terms)
            novelty = min(matched_terms / len(query_terms), 1.0)
    
    # Combined entropy score
    entropy_score = relevance * (1.0 - redundancy * 0.5) * novelty
    return entropy_score


def select_top_learnings(
    learnings: list,
    query: str,
    top_k: int = 8,
) -> list:
    """
    Select top-K learnings by entropy score.
    
    Removes noise and redundant learnings while keeping high-signal information.
    Useful before synthesis to prevent token bloat.
    
    Args:
        learnings: All retrieved learnings
        query: Original query (for relevance context)
        top_k: How many learnings to keep (default 8)
    
    Returns:
        Sorted list of top-K learnings by entropy score
    """
    if len(learnings) <= top_k:
        # Not enough to filter, return all sorted by score
        return sorted(learnings, key=lambda l: getattr(l, 'score', 0), reverse=True)
    
    # Score each learning
    scored_learnings = []
    for learning in learnings:
        entropy_score = calculate_entropy_score(learning, learnings, query)
        scored_learnings.append((learning, entropy_score))
    
    # Sort by entropy and return top K
    scored_learnings.sort(key=lambda x: x[1], reverse=True)
    return [l[0] for l in scored_learnings[:top_k]]

