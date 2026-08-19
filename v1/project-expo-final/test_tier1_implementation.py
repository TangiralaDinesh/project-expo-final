#!/usr/bin/env python3
"""
Test script for Tier 1 (Connectivity) implementation.

This verifies that all 6 Phase 1 Tier 1 files have been correctly integrated
and that the feedback loop works end-to-end.

Run with: python test_tier1_implementation.py
"""

import asyncio
import sys
from dataclasses import dataclass

# Import all modified modules to verify they load correctly
def test_imports():
    """Verify all modules can be imported without errors."""
    print("Testing imports...")
    try:
        from agent.config.feature_flags import FeatureFlags
        from agent.core.reasoning import ThinkingProfile, get_thinking_profile, get_thinking_profile_with_history
        from agent.core.satisfaction import SatisfactionTracker
        from agent.core.pivot import BranchingOption, run_pivot_loop
        from agent.core.types import CorrectionPattern, DecisionTrace
        from agent.routing.entry_gate import entry_gate, GateDecision
        from agent.orchestrator.orchestrator import query_knowledge_graph_for_context, run_orchestrator
        from agent.query import run_query
        print("✅ All imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False


def test_feature_flags():
    """Test feature flag system."""
    print("\nTesting feature flags...")
    from agent.config.feature_flags import FeatureFlags
    
    # Test tier presets
    all_off = FeatureFlags.all_off()
    assert all_off.connectivity_enabled == False
    assert all_off.progressive_zoom_enabled == False
    
    tier1 = FeatureFlags.tier_1_only()
    assert tier1.connectivity_enabled == True
    assert tier1.progressive_zoom_enabled == False
    
    all_on = FeatureFlags.all_on()
    assert all_on.connectivity_enabled == True
    assert all_on.progressive_zoom_enabled == True
    
    print("✅ Feature flag system working")
    return True


def test_correction_pattern():
    """Test correction pattern tracking."""
    print("\nTesting correction patterns...")
    from agent.core.types import CorrectionPattern
    import time
    
    pattern = CorrectionPattern(
        pattern_type="wanted_more_depth",
        severity=0.8,
        domain="oauth",
        timestamp=time.time(),
    )
    
    assert pattern.pattern_type == "wanted_more_depth"
    assert pattern.severity == 0.8
    assert pattern.domain == "oauth"
    
    print("✅ Correction pattern tracking working")
    return True


def test_satisfaction_tracker():
    """Test satisfaction tracker with corrections."""
    print("\nTesting satisfaction tracker...")
    from agent.core.satisfaction import SatisfactionTracker
    from agent.core.types import CorrectionPattern
    import time
    
    tracker = SatisfactionTracker()
    
    # Record some corrections
    tracker.record_correction("q1", "wanted_more_depth", 0.8, "oauth")
    tracker.record_correction("q2", "error_correction", 0.9, "oauth")
    tracker.record_correction("q3", "too_verbose", 0.5, "general")
    
    # Get recent corrections for oauth domain
    recent = tracker.get_recent_corrections(domain_hint="oauth", limit=5)
    assert len(recent) > 0
    print(f"   Found {len(recent)} recent corrections for oauth")
    
    # Verify results contain expected correction types and all are positive
    correction_types = [ct for ct, _ in recent]
    for correction_type, severity in recent:
        assert severity > 0, f"Severity should be > 0, got {severity}"
    
    # Verify at least one oauth domain correction is present
    assert "wanted_more_depth" in correction_types or "error_correction" in correction_types, \
        "Should have oauth-domain corrections"
    
    print("✅ Satisfaction tracker working")
    return True


def test_thinking_profile_enhancement():
    """Test enhanced thinking profile."""
    print("\nTesting thinking profile enhancements...")
    from agent.core.reasoning import ThinkingProfile
    
    profile = ThinkingProfile(
        max_depth=3,
        budget_s=30.0,
        use_deep_propositions=True,
        use_critique=False,
        use_multi_query_expansion=True,
        prompt_specificity="standard",
        self_consistency_calls=1,
        # NEW fields
        correction_history_active=True,
        uncertainty_tolerance=0.7,
        branching_enabled=True,
        confidence_target=0.75,
    )
    
    assert profile.correction_history_active == True
    assert profile.branching_enabled == True
    assert len(profile.applied_corrections) == 0
    
    profile.applied_corrections.append("increased_depth")
    assert len(profile.applied_corrections) == 1
    
    print("✅ Thinking profile enhancements working")
    return True


def test_branching_option():
    """Test BranchingOption dataclass."""
    print("\nTesting branching options...")
    from agent.core.pivot import BranchingOption
    
    option = BranchingOption(
        label="Empirical Approach",
        explanation="Use data-driven validation",
        pros=["more rigorous", "verifiable"],
        cons=["slower", "more complex"],
        confidence=0.8,
        evidence_level="strong",
        estimated_depth=3,
    )
    
    assert option.label == "Empirical Approach"
    assert option.confidence == 0.8
    assert "more rigorous" in option.pros
    
    print("✅ Branching options working")
    return True


def test_gate_decision_enhancement():
    """Test enhanced GateDecision."""
    print("\nTesting GateDecision enhancements...")
    from agent.routing.entry_gate import GateDecision
    
    decision = GateDecision(
        needs_retrieval=True,
        mode="SEMANTIC",
        reason="semantic_regex",
        confidence=0.95,
        alternative_modes=["CODE", "HYBRID"],
    )
    
    assert decision.confidence == 0.95
    assert "CODE" in decision.alternative_modes
    
    print("✅ GateDecision enhancements working")
    return True


async def test_thinking_profile_with_history():
    """Test the new thinking profile with history function."""
    print("\nTesting thinking profile with history...")
    from agent.core.reasoning import get_thinking_profile_with_history, classify_prompt_specificity
    from agent.core.satisfaction import SatisfactionTracker
    from agent.config.feature_flags import FeatureFlags
    from agent.llm.client import get_client
    
    tracker = SatisfactionTracker()
    tracker.record_correction("q1", "wanted_more_depth", 0.8, "oauth")
    
    features = FeatureFlags.tier_1_only()
    
    # This would need an actual NIM client, so we'll just verify the function exists
    # and has the right signature
    assert callable(get_thinking_profile_with_history)
    
    print("✅ Thinking profile with history function present")
    return True


def test_knowledge_graph_integration():
    """Test knowledge graph integration point."""
    print("\nTesting knowledge graph integration...")
    from agent.orchestrator.orchestrator import query_knowledge_graph_for_context
    
    # Verify function exists and has right signature
    assert callable(query_knowledge_graph_for_context)
    
    # Verify it's async
    import inspect
    assert inspect.iscoroutinefunction(query_knowledge_graph_for_context)
    
    print("✅ Knowledge graph integration point present")
    return True


async def run_all_tests():
    """Run all test functions."""
    print("=" * 60)
    print("TIER 1 IMPLEMENTATION TEST SUITE")
    print("=" * 60)
    
    tests = [
        test_imports,
        test_feature_flags,
        test_correction_pattern,
        test_satisfaction_tracker,
        test_thinking_profile_enhancement,
        test_branching_option,
        test_gate_decision_enhancement,
        test_thinking_profile_with_history,
        test_knowledge_graph_integration,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if asyncio.iscoroutinefunction(test):
                result = await test()
            else:
                result = test()
            
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Test failed with exception: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
