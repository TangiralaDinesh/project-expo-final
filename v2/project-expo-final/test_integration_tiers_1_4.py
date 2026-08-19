#!/usr/bin/env python3
"""
Integration test for Tiers 1-4 working together.

Verifies that all tiers can work independently and in combination
without regressions or conflicts.
"""

import asyncio
import sys


def test_tier1_core():
    """Test Tier 1 core components."""
    print("\nTesting Tier 1 core (Feedback Loops)...")
    from agent.core.satisfaction import SatisfactionTracker
    from agent.core.reasoning import get_thinking_profile
    
    # Create tracker
    tracker = SatisfactionTracker()
    tracker.record_query("How does OAuth work?")
    tracker.record_correction("wanted_more_depth", "oauth", 0.8)
    
    corrections = tracker.get_recent_corrections()
    assert len(corrections) > 0
    
    # Get thinking profile
    profile = get_thinking_profile("SEMANTIC", "test query")
    assert profile is not None
    
    print("   ✓ Satisfaction tracking works")
    print("   ✓ Thinking profile generation works")
    print("✅ Tier 1 core working")
    return True


def test_tier2_zoom_levels():
    """Test Tier 2 zoom level system."""
    print("\nTesting Tier 2 (Progressive Revelation)...")
    from agent.llm.synthesis_levels import (
        ZoomLevel, get_zoom_config, get_zoom_options, default_zoom_level
    )
    
    # Test zoom configs
    config_0 = get_zoom_config(ZoomLevel.LEVEL_0)
    config_1 = get_zoom_config(ZoomLevel.LEVEL_1)
    config_2 = get_zoom_config(ZoomLevel.LEVEL_2)
    
    assert config_0.token_budget == 300
    assert config_1.token_budget == 800
    assert config_2.token_budget == 2000
    
    # Test zoom options (using ZoomLevel enum)
    opts_0 = get_zoom_options(ZoomLevel.LEVEL_0)
    assert opts_0.can_zoom_in == True
    assert opts_0.can_zoom_out == False
    
    opts_2 = get_zoom_options(ZoomLevel.LEVEL_2)
    assert opts_2.can_zoom_in == False
    assert opts_2.can_zoom_out == True
    
    opts_1 = get_zoom_options(ZoomLevel.LEVEL_1)
    assert opts_1.can_zoom_in == True
    assert opts_1.can_zoom_out == True
    
    # Test default
    default = default_zoom_level()
    assert default == ZoomLevel.LEVEL_0
    
    print("   ✓ Zoom level configs work")
    print("   ✓ Zoom navigation works (ZoomOptions dataclass)")
    print("✅ Tier 2 working")
    return True


def test_tier3_branching_logic():
    """Test Tier 3 branching option handling."""
    print("\nTesting Tier 3 (Bayesian Branching)...")
    from agent.core.branching import should_present_branching, format_branching_for_user, parse_user_branch_selection
    from agent.core.branching_session import get_branching_session_manager, reset_branching_sessions
    from agent.core.pivot import BranchingOption
    
    # Test decision logic
    assert should_present_branching(0.7, 0.5) == True
    assert should_present_branching(0.95, 0.75) == False
    
    # Test formatting
    options = [
        BranchingOption(label="A", explanation="Option A", confidence=0.7),
        BranchingOption(label="B", explanation="Option B", confidence=0.65),
    ]
    formatted = format_branching_for_user(options)
    assert "Option 1" in formatted
    assert "Option 2" in formatted
    
    # Test user selection parsing
    idx = parse_user_branch_selection("Option 1", 2)
    assert idx == 0
    
    # Test session management
    reset_branching_sessions()
    manager = get_branching_session_manager()
    session = manager.create_session("test", "query", options)
    assert session.session_id == "test"
    manager.resolve_session("test", selection=0)
    assert session.resolved
    
    print("   ✓ Branching decisions work")
    print("   ✓ Option formatting works")
    print("   ✓ Session management works")
    print("✅ Tier 3 working")
    return True


async def test_tier4_code_execution():
    """Test Tier 4 code execution."""
    print("\nTesting Tier 4 (Code Execution)...")
    from agent.core.code_execution import (
        ExecutionRequest, ExecutionLanguage, execute_code
    )
    
    # Test Python execution
    py_req = ExecutionRequest(
        language=ExecutionLanguage.PYTHON,
        code="print('integration test')",
        description="Test Python",
    )
    result = await execute_code(py_req)
    assert result.success
    assert "integration test" in result.stdout
    
    # Test Bash execution
    bash_req = ExecutionRequest(
        language=ExecutionLanguage.BASH,
        code="echo 'bash works'",
    )
    result = await execute_code(bash_req)
    assert result.success
    assert "bash works" in result.stdout
    
    # Test security (dangerous command blocked)
    dangerous_req = ExecutionRequest(
        language=ExecutionLanguage.BASH,
        code="rm -rf /tmp/test",
    )
    result = await execute_code(dangerous_req)
    assert not result.success
    assert "blocked" in result.stderr.lower()
    
    print("   ✓ Python execution works")
    print("   ✓ Bash execution works")
    print("   ✓ Security restrictions work")
    print("✅ Tier 4 working")
    return True


def test_query_result_structure():
    """Test enhanced QueryResult structure."""
    print("\nTesting QueryResult with all Tier fields...")
    from agent.query import QueryResult
    from agent.core.pivot import BranchingOption
    
    # Create result with all fields
    result = QueryResult(
        answer="Test answer",
        learnings=[],
        source_urls=["http://example.com"],
        timing_ms=150.0,
        current_zoom_level=1,
        can_zoom_in=True,
        can_zoom_out=True,
        branching_options=[
            BranchingOption(label="A", explanation="Option A", confidence=0.7),
        ],
        branching_session_id="session-123",
        code_executed=True,
    )
    
    assert result.answer == "Test answer"
    assert result.current_zoom_level == 1
    assert len(result.branching_options) == 1
    assert result.code_executed == True
    
    print("   ✓ QueryResult includes all Tier 2 fields")
    print("   ✓ QueryResult includes all Tier 3 fields")
    print("   ✓ QueryResult includes all Tier 4 fields")
    print("✅ QueryResult structure working")
    return True


def test_integration_helpers():
    """Test query integration helpers."""
    print("\nTesting query integration helpers...")
    from agent.query_integration import (
        should_present_branching_for_result,
        handle_branching_presentation,
    )
    from agent.core.pivot import BranchingOption
    
    # Test branching decision
    options = [
        BranchingOption(label="A", explanation="A", confidence=0.7),
        BranchingOption(label="B", explanation="B", confidence=0.65),
    ]
    
    should_present = should_present_branching_for_result(None, options, branching_enabled=True)
    assert should_present == True
    
    should_present = should_present_branching_for_result(None, options, branching_enabled=False)
    assert should_present == False
    
    # Test presentation handler (now sync)
    message, session_id, returned_opts = handle_branching_presentation(
        "test query", options, branching_enabled=True
    )
    assert message is not None
    assert session_id is not None
    
    print("   ✓ Branching decision logic works")
    print("   ✓ Branching presentation works (sync)")
    print("✅ Integration helpers working")
    return True


def test_feature_flags_coverage():
    """Test feature flags cover all tiers."""
    print("\nTesting feature flags for all tiers...")
    from agent.config.feature_flags import FeatureFlags
    
    # All tiers off
    flags = FeatureFlags.all_off()
    assert flags.connectivity_enabled == False
    assert flags.progressive_zoom_enabled == False
    assert flags.bayesian_branching_enabled == False
    assert flags.code_execution_enabled == False
    
    # Tier 1 only
    flags = FeatureFlags.tier_1_only()
    assert flags.connectivity_enabled == True
    assert flags.progressive_zoom_enabled == False
    
    # All on
    flags = FeatureFlags.all_on()
    assert flags.connectivity_enabled == True
    assert flags.progressive_zoom_enabled == True
    assert flags.bayesian_branching_enabled == True
    assert flags.code_execution_enabled == True
    
    print("   ✓ Feature flags support all off")
    print("   ✓ Feature flags support Tier 1 only")
    print("   ✓ Feature flags support all tiers")
    print("✅ Feature flags working")
    return True


async def run_all_tests():
    """Run comprehensive integration tests."""
    print("=" * 60)
    print("COMPREHENSIVE INTEGRATION TEST (TIERS 1-4)")
    print("=" * 60)
    
    # Sync tests
    sync_tests = [
        ("tier1_core", test_tier1_core),
        ("tier2_zoom_levels", test_tier2_zoom_levels),
        ("tier3_branching_logic", test_tier3_branching_logic),
        ("query_result_structure", test_query_result_structure),
        ("integration_helpers", test_integration_helpers),
        ("feature_flags_coverage", test_feature_flags_coverage),
    ]
    
    # Async tests
    async_tests = [
        ("tier4_code_execution", test_tier4_code_execution),
    ]
    
    passed = 0
    failed = 0
    
    # Run sync tests
    for test_name, test_func in sync_tests:
        try:
            result = test_func()
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ {test_name} failed: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    # Run async tests
    for test_name, test_func in async_tests:
        try:
            result = await test_func()
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ {test_name} failed: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    if failed == 0:
        print("✅ ALL INTEGRATION TESTS PASSED")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
