#!/usr/bin/env python3
"""
Test script for Tier 3 (Bayesian Branching) implementation.

Verifies that branching options are presented correctly and users can select branches.
"""

import asyncio
import sys


def test_branching_logic():
    """Test branching decision logic."""
    print("\nTesting branching decision logic...")
    from agent.core.branching import should_present_branching
    
    # High confidence gap → auto-select
    assert not should_present_branching(0.9, 0.7)
    print("   ✓ High gap (0.2): auto-select")
    
    # Small confidence gap → present
    assert should_present_branching(0.7, 0.5)
    print("   ✓ Small gap (0.2, below 0.75): present")
    
    # Very high top confidence → auto-select even with small gap
    assert not should_present_branching(0.95, 0.75)
    print("   ✓ High top confidence (0.95): auto-select")
    
    print("✅ Branching decision logic working")
    return True


def test_branching_option_formatting():
    """Test formatting of branching options for user."""
    print("\nTesting branching option formatting...")
    from agent.core.branching import format_branching_for_user
    from agent.core.pivot import BranchingOption
    
    options = [
        BranchingOption(
            label="Empirical Approach",
            explanation="Use data-driven validation",
            pros=["rigorous", "verifiable"],
            cons=["slower"],
            confidence=0.7,
        ),
        BranchingOption(
            label="Practical Approach",
            explanation="Use common patterns",
            pros=["faster"],
            cons=["less rigorous"],
            confidence=0.65,
        ),
    ]
    
    formatted = format_branching_for_user(options)
    
    # Verify key content is present
    assert "Option 1" in formatted
    assert "Option 2" in formatted
    assert "Empirical" in formatted
    assert "Practical" in formatted
    assert "rigorous" in formatted
    
    print(f"   Formatted output length: {len(formatted)} chars")
    print("✅ Branching option formatting working")
    return True


def test_user_selection_parsing():
    """Test parsing of user branch selections."""
    print("\nTesting user branch selection parsing...")
    from agent.core.branching import parse_user_branch_selection
    
    # Test various formats
    assert parse_user_branch_selection("Option 1", 2) == 0
    assert parse_user_branch_selection("1", 2) == 0
    assert parse_user_branch_selection("Option 2", 2) == 1
    assert parse_user_branch_selection("second", 2) == 1
    
    # Invalid selections
    assert parse_user_branch_selection("Option 3", 2) is None  # Out of range
    assert parse_user_branch_selection("tell me more", 2) is None  # Clarification
    
    print("   ✓ Parsed: 'Option 1' → 0")
    print("   ✓ Parsed: 'second' → 1")
    print("   ✓ Detected clarification: 'tell me more'")
    print("✅ User selection parsing working")
    return True


def test_branching_session_manager():
    """Test branching session manager."""
    print("\nTesting branching session manager...")
    from agent.core.branching_session import (
        get_branching_session_manager, 
        reset_branching_sessions,
        BranchingSession
    )
    from agent.core.pivot import BranchingOption
    
    # Reset
    reset_branching_sessions()
    manager = get_branching_session_manager()
    
    # Create session
    options = [
        BranchingOption(label="A", explanation="Option A", confidence=0.7),
        BranchingOption(label="B", explanation="Option B", confidence=0.65),
    ]
    
    session = manager.create_session("test-123", "What's the best way?", options)
    assert session.session_id == "test-123"
    assert len(session.branching_options) == 2
    assert not session.resolved
    
    print("   ✓ Created session with 2 options")
    
    # Get active session
    active = manager.get_active_session()
    assert active is not None
    assert active.session_id == "test-123"
    print("   ✓ Retrieved active session")
    
    # Resolve session
    resolved = manager.resolve_session("test-123", selection=0)
    assert resolved.resolved
    assert resolved.user_selection == 0
    print("   ✓ Resolved session with selection")
    
    # Active session should now be None
    active = manager.get_active_session()
    assert active is None
    print("   ✓ Active session cleared after resolution")
    
    print("✅ Branching session manager working")
    return True


def test_branching_imports():
    """Test that all branching modules import successfully."""
    print("\nTesting branching module imports...")
    try:
        from agent.core.branching import (
            BranchingDecisionType, BranchingDecision,
            should_present_branching, format_branching_for_user,
            parse_user_branch_selection, BranchingContext
        )
        from agent.core.branching_session import (
            BranchingSession, BranchingSessionManager,
            get_branching_session_manager, reset_branching_sessions
        )
        print("✅ All branching imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False


async def run_all_tests():
    """Run all Tier 3 tests."""
    print("=" * 60)
    print("TIER 3 (BAYESIAN BRANCHING) TEST SUITE")
    print("=" * 60)
    
    tests = [
        ("branching_logic", test_branching_logic),
        ("branching_option_formatting", test_branching_option_formatting),
        ("user_selection_parsing", test_user_selection_parsing),
        ("branching_session_manager", test_branching_session_manager),
        ("branching_imports", test_branching_imports),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ {test_name} failed with exception: {e}")
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
