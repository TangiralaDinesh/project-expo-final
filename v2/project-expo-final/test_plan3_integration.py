#!/usr/bin/env python3
"""
Quick integration test for Plan 3 — COMPUTATION mode and code execution.

Tests:
1. Entry gate recognizes COMPUTATION queries
2. Orchestrator creates CODE_GEN_EXECUTOR nodes for COMPUTATION mode
3. CODE_GEN_EXECUTOR handler is registered in dispatcher
4. Feature flags enable code execution by default
"""

import asyncio
import sys
from agent.routing.entry_gate import entry_gate, _dynamic_intent_classifier
from agent.orchestrator.orchestrator import decompose_task
from agent.blocks.base import SUBAGENT_DISPATCH
from agent.core.types import SubagentType
from agent.config.feature_flags import FeatureFlags


def test_entry_gate_classification():
    """Test that entry gate classifies COMPUTATION queries correctly."""
    print("\n[TEST 1] Entry Gate COMPUTATION Classification")
    print("=" * 60)
    
    test_cases = [
        ("how many r's in strawberry", "COMPUTATION"),
        ("what files are in this directory", "COMPUTATION"),
        ("list all files", "COMPUTATION"),
        ("run bash command to list files", "COMPUTATION"),
        ("9.9 vs 9.11", "COMPUTATION"),
        ("count occurrences of a in hello", "COMPUTATION"),
        # Verify non-COMPUTATION still work
        ("what is a function", "PARAMETRIC"),
        ("latest stock prices", "SEMANTIC"),
        ("how to implement async await", "CODE"),
    ]
    
    all_passed = True
    for query, expected_intent in test_cases:
        intent = _dynamic_intent_classifier(query)
        status = "✓ PASS" if intent == expected_intent else "✗ FAIL"
        if intent != expected_intent:
            all_passed = False
        print(f"{status}: '{query}' → {intent} (expected {expected_intent})")
    
    return all_passed


async def test_orchestrator_routing():
    """Test that orchestrator routes COMPUTATION to CODE_GEN_EXECUTOR."""
    print("\n[TEST 2] Orchestrator COMPUTATION Routing")
    print("=" * 60)
    
    test_queries = [
        ("how many r's in strawberry", "COMPUTATION"),
        ("count the vowels in hello", "COMPUTATION"),
    ]
    
    all_passed = True
    for query, gate_mode in test_queries:
        try:
            decomposition = await decompose_task(query, gate_mode=gate_mode)
            
            if decomposition.nodes:
                node = decomposition.nodes[0]
                expected_type = SubagentType.CODE_GEN_EXECUTOR
                status = "✓ PASS" if node.subagent_type == expected_type else "✗ FAIL"
                if node.subagent_type != expected_type:
                    all_passed = False
                print(f"{status}: '{query}' → {node.subagent_type.value} (expected {expected_type.value})")
            else:
                print(f"✗ FAIL: No nodes returned for '{query}'")
                all_passed = False
        except Exception as e:
            print(f"✗ FAIL: Exception for '{query}': {e}")
            all_passed = False
    
    return all_passed


def test_code_executor_registration():
    """Test that CODE_GEN_EXECUTOR handler is registered."""
    print("\n[TEST 3] CODE_GEN_EXECUTOR Handler Registration")
    print("=" * 60)
    
    handler = SUBAGENT_DISPATCH.get(SubagentType.CODE_GEN_EXECUTOR)
    if handler:
        print(f"✓ PASS: CODE_GEN_EXECUTOR handler registered")
        print(f"         Handler: {handler.__name__}")
        return True
    else:
        print(f"✗ FAIL: CODE_GEN_EXECUTOR not in SUBAGENT_DISPATCH")
        print(f"         Available: {list(SUBAGENT_DISPATCH.keys())}")
        return False


def test_feature_flags():
    """Test that code execution is enabled by default."""
    print("\n[TEST 4] Feature Flags Default")
    print("=" * 60)
    
    flags = FeatureFlags()
    if flags.code_execution_enabled:
        print(f"✓ PASS: code_execution_enabled=True by default")
        return True
    else:
        print(f"✗ FAIL: code_execution_enabled=False (expected True)")
        return False


async def main():
    """Run all integration tests."""
    print("\n" + "=" * 60)
    print("PLAN 3 INTEGRATION TEST SUITE")
    print("=" * 60)
    
    results = []
    
    # Test 1: Entry gate classification
    results.append(("Entry Gate Classification", test_entry_gate_classification()))
    
    # Test 2: Orchestrator routing
    results.append(("Orchestrator Routing", await test_orchestrator_routing()))
    
    # Test 3: Handler registration
    results.append(("Handler Registration", test_code_executor_registration()))
    
    # Test 4: Feature flags
    results.append(("Feature Flags", test_feature_flags()))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} passed")
    
    if passed == total:
        print("\n✓ All tests passed! Plan 3 implementation is complete.")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
