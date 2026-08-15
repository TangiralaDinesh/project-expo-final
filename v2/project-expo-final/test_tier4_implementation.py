#!/usr/bin/env python3
"""
Test script for Tier 4 (Code Execution) implementation.

Verifies safe execution of Python and Bash code with timeouts and restrictions.
"""

import asyncio
import sys


def test_execution_language_enum():
    """Test ExecutionLanguage enum."""
    print("\nTesting ExecutionLanguage enum...")
    from agent.core.code_execution import ExecutionLanguage
    
    assert ExecutionLanguage.PYTHON.value == "python"
    assert ExecutionLanguage.BASH.value == "bash"
    assert ExecutionLanguage.JAVASCRIPT.value == "javascript"
    
    print("   ✓ ExecutionLanguage enum valid")
    print("✅ ExecutionLanguage working")
    return True


def test_execution_request_creation():
    """Test ExecutionRequest dataclass."""
    print("\nTesting ExecutionRequest creation...")
    from agent.core.code_execution import (
        ExecutionRequest, ExecutionLanguage, ExecutionSafety
    )
    
    req = ExecutionRequest(
        language=ExecutionLanguage.PYTHON,
        code="print('hello')",
        timeout_s=5.0,
        description="Test print",
    )
    
    assert req.language == ExecutionLanguage.PYTHON
    assert req.code == "print('hello')"
    assert req.timeout_s == 5.0
    assert req.safety == ExecutionSafety.SANDBOXED  # Default
    
    print("   ✓ Created ExecutionRequest")
    print("✅ ExecutionRequest working")
    return True


def test_execution_result_summary():
    """Test ExecutionResult summary generation."""
    print("\nTesting ExecutionResult summary...")
    from agent.core.code_execution import ExecutionResult
    
    # Success case
    result = ExecutionResult(
        success=True,
        stdout="Hello, world!",
        exit_code=0,
        execution_time_ms=125.5,
    )
    
    assert result.success
    assert not result.is_error()
    summary = result.get_summary()
    assert "✅" in summary
    assert "125.5" in summary
    print("   ✓ Success summary: " + summary[:30] + "...")
    
    # Error case
    result = ExecutionResult(
        success=False,
        stderr="Division by zero",
        exit_code=1,
        execution_time_ms=50.0,
    )
    
    assert not result.success
    assert result.is_error()
    summary = result.get_summary()
    assert "❌" in summary
    assert "exit 1" in summary
    print("   ✓ Error summary: " + summary[:30] + "...")
    
    print("✅ ExecutionResult working")
    return True


async def test_python_executor_success():
    """Test Python executor with simple code."""
    print("\nTesting Python executor with successful code...")
    from agent.core.code_execution import (
        ExecutionRequest, ExecutionLanguage, PythonExecutor
    )
    
    req = ExecutionRequest(
        language=ExecutionLanguage.PYTHON,
        code="print('test output')\nprint('line 2')",
        timeout_s=5.0,
        description="Test print output",
    )
    
    result = await PythonExecutor.execute(req)
    
    assert result.success
    assert result.exit_code == 0
    assert "test output" in result.stdout
    assert "line 2" in result.stdout
    
    print(f"   ✓ Executed successfully ({result.execution_time_ms:.1f}ms)")
    print(f"   ✓ Output: {result.stdout[:50]}")
    print("✅ Python executor working")
    return True


async def test_python_executor_error():
    """Test Python executor with error."""
    print("\nTesting Python executor with error code...")
    from agent.core.code_execution import (
        ExecutionRequest, ExecutionLanguage, PythonExecutor
    )
    
    req = ExecutionRequest(
        language=ExecutionLanguage.PYTHON,
        code="raise ValueError('test error')",
        timeout_s=5.0,
        description="Test error handling",
    )
    
    result = await PythonExecutor.execute(req)
    
    assert not result.success
    assert result.exit_code != 0
    assert "ValueError" in result.stderr or "test error" in result.stderr
    
    print(f"   ✓ Caught error ({result.execution_time_ms:.1f}ms)")
    print(f"   ✓ Error message: {result.stderr[:50]}")
    print("✅ Python error handling working")
    return True


async def test_python_executor_timeout():
    """Test Python executor timeout."""
    print("\nTesting Python executor timeout...")
    from agent.core.code_execution import (
        ExecutionRequest, ExecutionLanguage, PythonExecutor
    )
    
    req = ExecutionRequest(
        language=ExecutionLanguage.PYTHON,
        code="import time; time.sleep(10)",
        timeout_s=0.5,  # Very short timeout
        description="Test timeout",
    )
    
    result = await PythonExecutor.execute(req)
    
    assert not result.success
    assert "timeout" in result.stderr.lower()
    
    print(f"   ✓ Timeout caught ({result.execution_time_ms:.1f}ms)")
    print("✅ Python timeout handling working")
    return True


async def test_bash_executor_success():
    """Test Bash executor with simple command."""
    print("\nTesting Bash executor with successful command...")
    from agent.core.code_execution import (
        ExecutionRequest, ExecutionLanguage, BashExecutor
    )
    
    req = ExecutionRequest(
        language=ExecutionLanguage.BASH,
        code="echo 'hello from bash'",
        timeout_s=5.0,
        description="Test echo",
    )
    
    result = await BashExecutor.execute(req)
    
    assert result.success
    assert result.exit_code == 0
    assert "hello from bash" in result.stdout
    
    print(f"   ✓ Executed successfully ({result.execution_time_ms:.1f}ms)")
    print(f"   ✓ Output: {result.stdout.strip()}")
    print("✅ Bash executor working")
    return True


async def test_bash_executor_dangerous_blocked():
    """Test Bash executor blocks dangerous commands."""
    print("\nTesting Bash executor blocking dangerous commands...")
    from agent.core.code_execution import (
        ExecutionRequest, ExecutionLanguage, BashExecutor
    )
    
    dangerous_cmds = [
        "rm -rf /tmp/*",
        "sudo reboot",
        "ssh admin@server",
    ]
    
    for cmd in dangerous_cmds:
        req = ExecutionRequest(
            language=ExecutionLanguage.BASH,
            code=cmd,
            timeout_s=5.0,
        )
        
        result = await BashExecutor.execute(req)
        assert not result.success
        assert "blocked" in result.stderr.lower()
        print(f"   ✓ Blocked: {cmd}")
    
    print("✅ Bash security restrictions working")
    return True


async def test_execute_code_dispatcher():
    """Test execute_code dispatcher."""
    print("\nTesting execute_code dispatcher...")
    from agent.core.code_execution import (
        execute_code, ExecutionRequest, ExecutionLanguage
    )
    
    # Python dispatch
    req = ExecutionRequest(
        language=ExecutionLanguage.PYTHON,
        code="print('dispatch test')",
        description="Test dispatcher",
    )
    result = await execute_code(req)
    assert result.success
    assert "dispatch test" in result.stdout
    print("   ✓ Python dispatch working")
    
    # Bash dispatch
    req = ExecutionRequest(
        language=ExecutionLanguage.BASH,
        code="echo 'bash test'",
    )
    result = await execute_code(req)
    assert result.success
    assert "bash test" in result.stdout
    print("   ✓ Bash dispatch working")
    
    # Unsupported language
    req = ExecutionRequest(
        language=ExecutionLanguage.JAVASCRIPT,
        code="console.log('test')",
    )
    result = await execute_code(req)
    assert not result.success
    assert "Unsupported" in result.stderr
    print("   ✓ Unsupported language error")
    
    print("✅ execute_code dispatcher working")
    return True


def test_code_execution_imports():
    """Test that all code execution modules import successfully."""
    print("\nTesting code execution module imports...")
    try:
        from agent.core.code_execution import (
            ExecutionLanguage, ExecutionSafety, ExecutionRequest,
            ExecutionResult, PythonExecutor, BashExecutor,
            execute_code
        )
        print("✅ All code execution imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False


async def run_all_tests():
    """Run all Tier 4 tests."""
    print("=" * 60)
    print("TIER 4 (CODE EXECUTION) TEST SUITE")
    print("=" * 60)
    
    # Synchronous tests
    sync_tests = [
        ("execution_language_enum", test_execution_language_enum),
        ("execution_request_creation", test_execution_request_creation),
        ("execution_result_summary", test_execution_result_summary),
        ("code_execution_imports", test_code_execution_imports),
    ]
    
    # Async tests
    async_tests = [
        ("python_executor_success", test_python_executor_success),
        ("python_executor_error", test_python_executor_error),
        ("python_executor_timeout", test_python_executor_timeout),
        ("bash_executor_success", test_bash_executor_success),
        ("bash_executor_dangerous_blocked", test_bash_executor_dangerous_blocked),
        ("execute_code_dispatcher", test_execute_code_dispatcher),
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
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
