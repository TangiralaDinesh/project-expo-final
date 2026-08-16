#!/usr/bin/env python3
"""
Test script for Tier 2 (Progressive Revelation) implementation.

Verifies that zoom levels work correctly and can be used progressively.
"""

import asyncio
import sys


def test_zoom_level_config():
    """Test zoom level configuration system."""
    print("\nTesting zoom level configs...")
    from agent.llm.synthesis_levels import (
        ZoomLevel, get_zoom_config, get_zoom_options,
        default_zoom_level
    )
    
    # Test Level 0
    config_l0 = get_zoom_config(ZoomLevel.LEVEL_0)
    assert config_l0.level == ZoomLevel.LEVEL_0
    assert config_l0.token_budget == 300
    assert config_l0.token_budget < 500
    print(f"   Level 0: {config_l0.token_budget} tokens")
    
    # Test Level 1
    config_l1 = get_zoom_config(ZoomLevel.LEVEL_1)
    assert config_l1.level == ZoomLevel.LEVEL_1
    assert config_l1.token_budget == 800
    assert config_l0.token_budget < config_l1.token_budget
    print(f"   Level 1: {config_l1.token_budget} tokens")
    
    # Test Level 2
    config_l2 = get_zoom_config(ZoomLevel.LEVEL_2)
    assert config_l2.level == ZoomLevel.LEVEL_2
    assert config_l2.token_budget == 2000
    assert config_l1.token_budget < config_l2.token_budget
    print(f"   Level 2: {config_l2.token_budget} tokens")
    
    # Test progression
    assert config_l0.token_budget < config_l1.token_budget < config_l2.token_budget
    
    # Test default
    default = default_zoom_level()
    assert default == ZoomLevel.LEVEL_0
    
    print("✅ Zoom level configs working")
    return True


def test_zoom_options():
    """Test zoom option calculations."""
    print("\nTesting zoom options...")
    from agent.llm.synthesis_levels import (
        ZoomLevel, get_zoom_options
    )
    
    # At Level 0: can zoom in, can't zoom out
    opts_l0 = get_zoom_options(ZoomLevel.LEVEL_0)
    assert opts_l0.can_zoom_in == True
    assert opts_l0.can_zoom_out == False
    print(f"   Level 0: can_zoom_in={opts_l0.can_zoom_in}, can_zoom_out={opts_l0.can_zoom_out}")
    
    # At Level 1: can zoom in and out
    opts_l1 = get_zoom_options(ZoomLevel.LEVEL_1)
    assert opts_l1.can_zoom_in == True
    assert opts_l1.can_zoom_out == True
    print(f"   Level 1: can_zoom_in={opts_l1.can_zoom_in}, can_zoom_out={opts_l1.can_zoom_out}")
    
    # At Level 2: can't zoom in, can zoom out
    opts_l2 = get_zoom_options(ZoomLevel.LEVEL_2)
    assert opts_l2.can_zoom_in == False
    assert opts_l2.can_zoom_out == True
    print(f"   Level 2: can_zoom_in={opts_l2.can_zoom_in}, can_zoom_out={opts_l2.can_zoom_out}")
    
    print("✅ Zoom options working")
    return True


def test_synthesis_levels_import():
    """Test that synthesis_levels module is importable."""
    print("\nTesting synthesis_levels import...")
    try:
        from agent.llm.synthesis_levels import (
            ZoomLevel, ZoomLevelConfig, ZoomOptions,
            get_zoom_config, get_zoom_options, default_zoom_level
        )
        print("✅ synthesis_levels imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False


async def test_zoom_synthesis_function():
    """Test that synthesis_at_zoom_level function exists and is callable."""
    print("\nTesting synthesis_at_zoom_level function...")
    try:
        from agent.llm.synthesis import synthesis_at_zoom_level
        from agent.core.types import Learning
        
        # Just verify function exists and has correct signature
        assert callable(synthesis_at_zoom_level)
        
        # Verify it's async
        import inspect
        assert inspect.iscoroutinefunction(synthesis_at_zoom_level)
        
        print("✅ synthesis_at_zoom_level function present and async")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False
    except AssertionError as e:
        print(f"❌ Assertion failed: {e}")
        return False


def test_observability_module():
    """Test new observability module."""
    print("\nTesting observability module...")
    try:
        from agent.core.observability import (
            ObservabilityTracker, MetricType, MetricEvent,
            get_observability_tracker, reset_observability
        )
        
        # Get tracker
        tracker = get_observability_tracker()
        assert tracker is not None
        
        # Record an event
        tracker.record_event(
            MetricType.CORRECTION_APPLIED,
            value=0.8,
            domain="oauth",
            correction_type="error_correction"
        )
        
        # Get stats
        stats = tracker.get_stats()
        assert stats["total_events"] > 0
        
        # Get domain stats
        domain_stats = tracker.get_domain_stats("oauth")
        assert domain_stats["event_count"] > 0
        
        print("✅ Observability module working")
        return True
    except Exception as e:
        print(f"❌ Observability test failed: {e}")
        return False


def test_correction_history_cap():
    """Test that correction history has a cap."""
    print("\nTesting correction history cap...")
    try:
        from agent.core.satisfaction import SatisfactionTracker, MAX_CORRECTIONS_HISTORY
        
        # Verify cap is defined
        assert MAX_CORRECTIONS_HISTORY > 0
        print(f"   MAX_CORRECTIONS_HISTORY = {MAX_CORRECTIONS_HISTORY}")
        
        # Create tracker and add more corrections than cap
        tracker = SatisfactionTracker()
        
        for i in range(MAX_CORRECTIONS_HISTORY + 10):
            tracker.record_correction(
                f"q{i}",
                "error_correction",
                0.5,
                "oauth"
            )
        
        # Verify it's capped
        assert len(tracker.corrections) <= MAX_CORRECTIONS_HISTORY
        print(f"   Corrections capped at {len(tracker.corrections)} (max={MAX_CORRECTIONS_HISTORY})")
        
        print("✅ Correction history cap working")
        return True
    except Exception as e:
        print(f"❌ History cap test failed: {e}")
        return False


async def run_all_tests():
    """Run all test functions."""
    print("=" * 60)
    print("TIER 2 IMPLEMENTATION TEST SUITE")
    print("=" * 60)
    
    tests = [
        ("zoom_level_config", test_zoom_level_config),
        ("zoom_options", test_zoom_options),
        ("synthesis_levels_import", test_synthesis_levels_import),
        ("zoom_synthesis_function", test_zoom_synthesis_function),
        ("observability_module", test_observability_module),
        ("correction_history_cap", test_correction_history_cap),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func()
            else:
                result = test_func()
            
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ {test_name} test failed with exception: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
