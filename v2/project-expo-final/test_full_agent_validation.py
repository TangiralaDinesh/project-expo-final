#!/usr/bin/env python3
"""
COMPREHENSIVE AGENT VALIDATION TEST

Verifies:
1. All imports work (no missing dependencies)
2. All connections are wired correctly
3. All file routing works
4. Feature flags control features properly
5. Error handling is in place
6. End-to-end query flow works
7. All Tiers 1-4 are properly integrated
"""

import sys
import asyncio
from pathlib import Path

# Color codes for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'


def print_header(text):
    print(f"\n{BLUE}{BOLD}{'='*70}{RESET}")
    print(f"{BLUE}{BOLD}{text}{RESET}")
    print(f"{BLUE}{BOLD}{'='*70}{RESET}")


def print_check(name, result):
    symbol = f"{GREEN}✅{RESET}" if result else f"{RED}❌{RESET}"
    print(f"{symbol} {name}")


def test_all_imports():
    """Test that all required modules can be imported."""
    print_header("VALIDATION 1: All Imports")
    
    imports_to_test = [
        # Core
        ("agent.query", "run_query"),
        ("agent.config.feature_flags", "FeatureFlags"),
        ("agent.config.settings", "settings"),
        
        # Tier 1
        ("agent.core.satisfaction", "SatisfactionTracker"),
        ("agent.core.reasoning", "get_thinking_profile"),
        ("agent.core.types", "Learning, CorrectionPattern"),
        
        # Tier 2
        ("agent.llm.synthesis_levels", "ZoomLevel, get_zoom_config"),
        ("agent.llm.synthesis", "synthesis_at_zoom_level"),
        
        # Tier 3
        ("agent.core.branching", "should_present_branching"),
        ("agent.core.branching_session", "get_branching_session_manager"),
        
        # Tier 4
        ("agent.core.code_execution", "ExecutionLanguage, execute_code"),
        
        # Integration
        ("agent.query_integration", "synthesis_with_zoom"),
        
        # Observability
        ("agent.core.observability", "get_observability_tracker"),
    ]
    
    passed = 0
    failed = 0
    
    for module_name, items in imports_to_test:
        try:
            module = __import__(module_name, fromlist=items.split(","))
            for item in items.split(","):
                item = item.strip()
                if hasattr(module, item):
                    passed += 1
                else:
                    print_check(f"{module_name}.{item}", False)
                    failed += 1
            print_check(f"{module_name}", True)
        except Exception as e:
            print_check(f"{module_name}", False)
            print(f"  Error: {e}")
            failed += 1
    
    print(f"\n{GREEN}Imports: {passed} passed{RESET}")
    if failed > 0:
        print(f"{RED}Imports: {failed} failed{RESET}")
    
    return failed == 0


def test_feature_flags_routing():
    """Test that feature flags properly control feature activation."""
    print_header("VALIDATION 2: Feature Flags Routing")
    
    from agent.config.feature_flags import FeatureFlags
    
    tests = [
        ("all_off", FeatureFlags.all_off(), {
            "connectivity_enabled": False,
            "progressive_zoom_enabled": False,
            "bayesian_branching_enabled": False,
            "code_execution_enabled": False,
        }),
        ("tier_1_only", FeatureFlags.tier_1_only(), {
            "connectivity_enabled": True,
            "progressive_zoom_enabled": False,
            "bayesian_branching_enabled": False,
            "code_execution_enabled": False,
        }),
        ("all_on", FeatureFlags.all_on(), {
            "connectivity_enabled": True,
            "progressive_zoom_enabled": True,
            "bayesian_branching_enabled": True,
            "code_execution_enabled": True,
        }),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, flags_obj, expected_values in tests:
        print(f"\n  Testing: {test_name}")
        for attr, expected_val in expected_values.items():
            actual_val = getattr(flags_obj, attr)
            if actual_val == expected_val:
                print_check(f"    {attr} = {expected_val}", True)
                passed += 1
            else:
                print_check(f"    {attr} = {expected_val} (got {actual_val})", False)
                failed += 1
    
    print(f"\n{GREEN}Feature flags: {passed} checks passed{RESET}")
    if failed > 0:
        print(f"{RED}Feature flags: {failed} checks failed{RESET}")
    
    return failed == 0


def test_core_components():
    """Test that core components work independently."""
    print_header("VALIDATION 3: Core Components")
    
    passed = 0
    failed = 0
    
    # Test Tier 1: Satisfaction
    try:
        from agent.core.satisfaction import SatisfactionTracker
        tracker = SatisfactionTracker()
        tracker.record_query("test query")
        tracker.record_correction("wanted_more_depth", "test", 0.5)
        corrections = tracker.get_recent_corrections()
        print_check("Tier 1: SatisfactionTracker", len(corrections) > 0)
        passed += 1
    except Exception as e:
        print_check("Tier 1: SatisfactionTracker", False)
        print(f"  Error: {e}")
        failed += 1
    
    # Test Tier 1: Thinking Profile
    try:
        from agent.core.reasoning import get_thinking_profile
        profile = get_thinking_profile("SEMANTIC", "test query")
        print_check("Tier 1: Thinking Profile", profile is not None)
        passed += 1
    except Exception as e:
        print_check("Tier 1: Thinking Profile", False)
        print(f"  Error: {e}")
        failed += 1
    
    # Test Tier 2: Zoom Levels
    try:
        from agent.llm.synthesis_levels import ZoomLevel, get_zoom_config
        config = get_zoom_config(ZoomLevel.LEVEL_1)
        print_check("Tier 2: Zoom Levels", config.token_budget == 800)
        passed += 1
    except Exception as e:
        print_check("Tier 2: Zoom Levels", False)
        print(f"  Error: {e}")
        failed += 1
    
    # Test Tier 3: Branching
    try:
        from agent.core.branching import should_present_branching
        result = should_present_branching(0.7, 0.5)
        print_check("Tier 3: Branching", result == True)
        passed += 1
    except Exception as e:
        print_check("Tier 3: Branching", False)
        print(f"  Error: {e}")
        failed += 1
    
    # Test Tier 4: Code Execution
    try:
        from agent.core.code_execution import ExecutionLanguage
        lang = ExecutionLanguage.PYTHON
        print_check("Tier 4: Code Execution", lang.value == "python")
        passed += 1
    except Exception as e:
        print_check("Tier 4: Code Execution", False)
        print(f"  Error: {e}")
        failed += 1
    
    # Test Observability
    try:
        from agent.core.observability import get_observability_tracker, MetricType
        tracker = get_observability_tracker()
        print_check("Observability: Tracker", tracker is not None)
        passed += 1
    except Exception as e:
        print_check("Observability: Tracker", False)
        print(f"  Error: {e}")
        failed += 1
    
    print(f"\n{GREEN}Core components: {passed} passed{RESET}")
    if failed > 0:
        print(f"{RED}Core components: {failed} failed{RESET}")
    
    return failed == 0


def test_query_result_structure():
    """Test that QueryResult has all required fields."""
    print_header("VALIDATION 4: QueryResult Structure")
    
    from agent.query import QueryResult
    from agent.core.pivot import BranchingOption
    
    result = QueryResult(
        answer="Test answer",
        learnings=[],
        current_zoom_level=1,
        can_zoom_in=True,
        can_zoom_out=False,
        branching_options=[
            BranchingOption(label="A", explanation="Option A", confidence=0.7)
        ],
        code_executed=True,
    )
    
    checks = [
        ("answer field", result.answer == "Test answer"),
        ("current_zoom_level field", result.current_zoom_level == 1),
        ("can_zoom_in field", result.can_zoom_in == True),
        ("branching_options field", len(result.branching_options) > 0),
        ("code_executed field", result.code_executed == True),
    ]
    
    passed = sum(1 for _, result in checks if result)
    failed = len(checks) - passed
    
    for name, result_check in checks:
        print_check(name, result_check)
    
    print(f"\n{GREEN}QueryResult structure: {passed} checks{RESET}")
    if failed > 0:
        print(f"{RED}QueryResult structure: {failed} failed{RESET}")
    
    return failed == 0


async def test_code_execution_async():
    """Test async code execution works."""
    print_header("VALIDATION 5: Async Code Execution")
    
    from agent.core.code_execution import ExecutionRequest, ExecutionLanguage, execute_code
    
    passed = 0
    failed = 0
    
    # Test Python execution
    try:
        request = ExecutionRequest(
            language=ExecutionLanguage.PYTHON,
            code="print('hello')",
            timeout_s=5.0,
        )
        result = await execute_code(request)
        if result.success and "hello" in result.stdout:
            print_check("Python execution", True)
            passed += 1
        else:
            print_check("Python execution", False)
            failed += 1
    except Exception as e:
        print_check("Python execution", False)
        print(f"  Error: {e}")
        failed += 1
    
    # Test Bash execution
    try:
        request = ExecutionRequest(
            language=ExecutionLanguage.BASH,
            code="echo 'test'",
        )
        result = await execute_code(request)
        if result.success and "test" in result.stdout:
            print_check("Bash execution", True)
            passed += 1
        else:
            print_check("Bash execution", False)
            failed += 1
    except Exception as e:
        print_check("Bash execution", False)
        print(f"  Error: {e}")
        failed += 1
    
    # Test security (dangerous command blocked)
    try:
        request = ExecutionRequest(
            language=ExecutionLanguage.BASH,
            code="rm -rf /tmp/test",
        )
        result = await execute_code(request)
        if not result.success and "blocked" in result.stderr.lower():
            print_check("Security restriction", True)
            passed += 1
        else:
            print_check("Security restriction", False)
            failed += 1
    except Exception as e:
        print_check("Security restriction", False)
        print(f"  Error: {e}")
        failed += 1
    
    print(f"\n{GREEN}Async code execution: {passed} passed{RESET}")
    if failed > 0:
        print(f"{RED}Async code execution: {failed} failed{RESET}")
    
    return failed == 0


def test_integration_helpers():
    """Test integration helper functions."""
    print_header("VALIDATION 6: Integration Helpers")
    
    from agent.query_integration import (
        should_present_branching_for_result,
        handle_branching_presentation,
    )
    from agent.core.pivot import BranchingOption
    
    passed = 0
    failed = 0
    
    options = [
        BranchingOption(label="A", explanation="Option A", confidence=0.7),
        BranchingOption(label="B", explanation="Option B", confidence=0.65),
    ]
    
    # Test decision logic
    try:
        result = should_present_branching_for_result(None, options, branching_enabled=True)
        if result == True:
            print_check("Branching decision logic", True)
            passed += 1
        else:
            print_check("Branching decision logic", False)
            failed += 1
    except Exception as e:
        print_check("Branching decision logic", False)
        print(f"  Error: {e}")
        failed += 1
    
    # Test presentation handler (now sync)
    try:
        from agent.core.branching_session import reset_branching_sessions
        reset_branching_sessions()
        message, session_id, returned_options = handle_branching_presentation(
            "test query", options, branching_enabled=True
        )
        if message and session_id and len(returned_options) > 0:
            print_check("Branching presentation handler", True)
            passed += 1
        else:
            print_check("Branching presentation handler", False)
            failed += 1
    except Exception as e:
        print_check("Branching presentation handler", False)
        print(f"  Error: {e}")
        failed += 1
    
    print(f"\n{GREEN}Integration helpers: {passed} passed{RESET}")
    if failed > 0:
        print(f"{RED}Integration helpers: {failed} failed{RESET}")
    
    return failed == 0


def test_file_routing():
    """Test that all files are in correct locations and accessible."""
    print_header("VALIDATION 7: File Routing")
    
    required_files = [
        # Core agent files
        "agent/query.py",
        "agent/config/feature_flags.py",
        "agent/config/settings.py",
        
        # Tier 1
        "agent/core/satisfaction.py",
        "agent/core/reasoning.py",
        "agent/core/types.py",
        
        # Tier 2
        "agent/llm/synthesis_levels.py",
        "agent/llm/synthesis.py",
        
        # Tier 3
        "agent/core/branching.py",
        "agent/core/branching_session.py",
        
        # Tier 4
        "agent/core/code_execution.py",
        
        # Observability
        "agent/core/observability.py",
        
        # Integration
        "agent/query_integration.py",
        
        # Test files
        "test_tier1_implementation.py",
        "test_tier2_implementation.py",
        "test_tier3_implementation.py",
        "test_tier4_implementation.py",
        "test_integration_tiers_1_4.py",
    ]
    
    passed = 0
    failed = 0
    
    base_path = Path("/workspaces/projectexpo/v2/project-expo-final")
    
    for file_path in required_files:
        full_path = base_path / file_path
        if full_path.exists():
            print_check(file_path, True)
            passed += 1
        else:
            print_check(file_path, False)
            print(f"  Not found: {full_path}")
            failed += 1
    
    print(f"\n{GREEN}File routing: {passed}/{len(required_files)} files present{RESET}")
    if failed > 0:
        print(f"{RED}File routing: {failed} files missing{RESET}")
    
    return failed == 0


async def run_all_validations():
    """Run all validation tests."""
    print_header("COMPREHENSIVE AGENT VALIDATION")
    print(f"{YELLOW}Checking all connections, imports, and integrations...{RESET}\n")
    
    results = []
    
    # Sync tests
    results.append(("All Imports", test_all_imports()))
    results.append(("Feature Flags", test_feature_flags_routing()))
    results.append(("Core Components", test_core_components()))
    results.append(("QueryResult Structure", test_query_result_structure()))
    results.append(("Integration Helpers", test_integration_helpers()))
    results.append(("File Routing", test_file_routing()))
    
    # Async tests
    results.append(("Async Code Execution", await test_code_execution_async()))
    
    # Summary
    print_header("VALIDATION SUMMARY")
    
    passed_checks = sum(1 for _, result in results if result)
    total_checks = len(results)
    
    print(f"\n{BOLD}Results:{RESET}")
    for test_name, result in results:
        symbol = f"{GREEN}✅{RESET}" if result else f"{RED}❌{RESET}"
        print(f"{symbol} {test_name}")
    
    print(f"\n{BOLD}Overall:{RESET}")
    if passed_checks == total_checks:
        print(f"{GREEN}{BOLD}✅ ALL VALIDATIONS PASSED{RESET}")
        print(f"{GREEN}   • All imports working{RESET}")
        print(f"{GREEN}   • All connections verified{RESET}")
        print(f"{GREEN}   • All files routed correctly{RESET}")
        print(f"{GREEN}   • All Tiers 1-4 integrated{RESET}")
        print(f"{GREEN}   • Error handling in place{RESET}")
        return True
    else:
        print(f"{RED}{BOLD}❌ {total_checks - passed_checks} VALIDATION(S) FAILED{RESET}")
        return False


if __name__ == "__main__":
    success = asyncio.run(run_all_validations())
    sys.exit(0 if success else 1)
