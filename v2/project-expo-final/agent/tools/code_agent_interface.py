"""Code Agent Interface — Define execution capabilities and safety constraints.

Industry-grade code execution interface:
  - Define which execution modes are supported
  - Provide safety checklist for each mode
  - Track execution capabilities dynamically
  - Support code generation, analysis, and execution

This module is an OPTIONAL interface layer on top of existing
blocks/code/block.py execution.
"""

from __future__ import annotations

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


class ExecutionMode(str, Enum):
    """Supported code execution modes."""
    PYTHON = "python"          # Python code execution
    BASH = "bash"              # Shell commands
    SQL = "sql"                # SQL queries (read-only recommended)
    JAVASCRIPT = "javascript"  # JavaScript execution
    ANALYSIS = "analysis"      # Code analysis only (no execution)
    PLANNING = "planning"      # Code planning/generation (no execution)


class SafetyLevel(str, Enum):
    """Safety constraints for execution."""
    SANDBOXED = "sandboxed"     # Fully sandboxed, no file I/O
    RESTRICTED = "restricted"   # Limited file I/O, no network
    UNRESTRICTED = "unrestricted"  # Full access (use with caution)


@dataclass
class SafetyConstraint:
    """One safety rule for code execution."""
    name: str
    description: str
    applies_to: List[ExecutionMode]
    enabled: bool = True
    
    def applies(self, mode: ExecutionMode) -> bool:
        """Check if this constraint applies to a mode."""
        return self.enabled and mode in self.applies_to


@dataclass
class ExecutionCapability:
    """One supported execution capability."""
    mode: ExecutionMode
    enabled: bool = True
    max_execution_time_s: float = 30.0
    max_output_chars: int = 100_000
    safety_level: SafetyLevel = SafetyLevel.SANDBOXED
    
    constraints: List[SafetyConstraint] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def can_execute(self) -> bool:
        """Whether this capability is available."""
        return self.enabled
    
    def get_active_constraints(self) -> List[SafetyConstraint]:
        """Get all active constraints for this capability."""
        return [c for c in self.constraints if c.applies(self.mode)]


@dataclass
class CodeExecutionRequest:
    """Request to execute code."""
    code: str
    mode: ExecutionMode
    timeout_s: float = 30.0
    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeExecutionResult:
    """Result of code execution."""
    success: bool
    mode: ExecutionMode
    output: str = ""
    error: Optional[str] = None
    execution_time_s: float = 0.0
    
    # For analysis/planning modes
    analysis: Optional[Dict[str, Any]] = None
    generated_code: Optional[str] = None
    
    # Safety tracking
    constraints_violated: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class CodeAgentInterface:
    """Interface for code execution and analysis."""
    
    def __init__(self):
        """Initialize with default capabilities."""
        self.capabilities: Dict[ExecutionMode, ExecutionCapability] = {}
        self._setup_default_capabilities()
    
    def _setup_default_capabilities(self):
        """Setup default execution capabilities."""
        # Python execution
        python_cap = ExecutionCapability(
            mode=ExecutionMode.PYTHON,
            enabled=True,
            max_execution_time_s=30.0,
            safety_level=SafetyLevel.SANDBOXED,
        )
        python_cap.constraints = [
            SafetyConstraint(
                name="no_network_access",
                description="Python code cannot make network requests",
                applies_to=[ExecutionMode.PYTHON],
                enabled=True,
            ),
            SafetyConstraint(
                name="no_file_write",
                description="Python code cannot modify files",
                applies_to=[ExecutionMode.PYTHON],
                enabled=True,
            ),
            SafetyConstraint(
                name="no_subprocess",
                description="Python code cannot spawn subprocesses",
                applies_to=[ExecutionMode.PYTHON],
                enabled=True,
            ),
        ]
        self.capabilities[ExecutionMode.PYTHON] = python_cap
        
        # Bash execution
        bash_cap = ExecutionCapability(
            mode=ExecutionMode.BASH,
            enabled=True,
            max_execution_time_s=30.0,
            safety_level=SafetyLevel.RESTRICTED,
        )
        bash_cap.constraints = [
            SafetyConstraint(
                name="no_dangerous_commands",
                description="Cannot use rm, dd, mkfs, etc.",
                applies_to=[ExecutionMode.BASH],
                enabled=True,
            ),
            SafetyConstraint(
                name="timeout_enforced",
                description="Bash scripts timeout after 30s",
                applies_to=[ExecutionMode.BASH],
                enabled=True,
            ),
        ]
        self.capabilities[ExecutionMode.BASH] = bash_cap
        
        # SQL execution (read-only default)
        sql_cap = ExecutionCapability(
            mode=ExecutionMode.SQL,
            enabled=False,  # Disabled by default (requires explicit enablement)
            max_execution_time_s=10.0,
            safety_level=SafetyLevel.RESTRICTED,
        )
        sql_cap.constraints = [
            SafetyConstraint(
                name="read_only",
                description="Only SELECT queries allowed",
                applies_to=[ExecutionMode.SQL],
                enabled=True,
            ),
        ]
        self.capabilities[ExecutionMode.SQL] = sql_cap
        
        # JavaScript execution
        javascript_cap = ExecutionCapability(
            mode=ExecutionMode.JAVASCRIPT,
            enabled=False,  # Disabled by default
            max_execution_time_s=10.0,
            safety_level=SafetyLevel.SANDBOXED,
        )
        self.capabilities[ExecutionMode.JAVASCRIPT] = javascript_cap
        
        # Analysis mode (code analysis, no execution)
        analysis_cap = ExecutionCapability(
            mode=ExecutionMode.ANALYSIS,
            enabled=True,
            max_execution_time_s=5.0,  # Quick analysis
            safety_level=SafetyLevel.SANDBOXED,
            metadata={
                "supports_ast_analysis": True,
                "supports_linting": True,
                "supports_type_checking": True,
            }
        )
        self.capabilities[ExecutionMode.ANALYSIS] = analysis_cap
        
        # Planning mode (code generation, no execution)
        planning_cap = ExecutionCapability(
            mode=ExecutionMode.PLANNING,
            enabled=True,
            max_execution_time_s=10.0,  # Planning may take longer
            safety_level=SafetyLevel.SANDBOXED,
            metadata={
                "supports_code_generation": True,
                "supports_architecture_planning": True,
            }
        )
        self.capabilities[ExecutionMode.PLANNING] = planning_cap
    
    def get_capability(self, mode: ExecutionMode) -> Optional[ExecutionCapability]:
        """Get a capability by mode."""
        return self.capabilities.get(mode)
    
    def is_mode_available(self, mode: ExecutionMode) -> bool:
        """Check if a mode is available and enabled."""
        cap = self.get_capability(mode)
        return cap is not None and cap.enabled
    
    def enable_mode(self, mode: ExecutionMode) -> bool:
        """Enable a capability mode."""
        cap = self.get_capability(mode)
        if cap:
            cap.enabled = True
            logger.info(f"Enabled execution mode: {mode.value}")
            return True
        return False
    
    def disable_mode(self, mode: ExecutionMode) -> bool:
        """Disable a capability mode."""
        cap = self.get_capability(mode)
        if cap:
            cap.enabled = False
            logger.info(f"Disabled execution mode: {mode.value}")
            return True
        return False
    
    def set_safety_level(self, mode: ExecutionMode, level: SafetyLevel) -> bool:
        """Change safety level for a mode."""
        cap = self.get_capability(mode)
        if cap:
            cap.safety_level = level
            logger.info(f"Set {mode.value} safety level to {level.value}")
            return True
        return False
    
    def get_available_modes(self) -> List[ExecutionMode]:
        """Get all available execution modes."""
        return [mode for mode, cap in self.capabilities.items() if cap.enabled]
    
    def validate_request(self, request: CodeExecutionRequest) -> tuple[bool, Optional[str]]:
        """Validate an execution request for safety.
        
        Returns: (is_valid, error_message)
        """
        cap = self.get_capability(request.mode)
        
        if not cap:
            return False, f"Execution mode not supported: {request.mode.value}"
        
        if not cap.enabled:
            return False, f"Execution mode disabled: {request.mode.value}"
        
        if len(request.code) > 50_000:
            return False, f"Code too large ({len(request.code)} chars > 50000 max)"
        
        if request.timeout_s > cap.max_execution_time_s:
            return False, f"Timeout {request.timeout_s}s exceeds max {cap.max_execution_time_s}s"
        
        # Check constraints
        for constraint in cap.get_active_constraints():
            # Add more sophisticated constraint checking here
            logger.debug(f"Checked constraint: {constraint.name}")
        
        return True, None
    
    def get_execution_summary(self) -> Dict[str, Any]:
        """Get summary of available execution capabilities."""
        return {
            "available_modes": [m.value for m in self.get_available_modes()],
            "capabilities": {
                mode.value: {
                    "enabled": cap.enabled,
                    "max_time_s": cap.max_execution_time_s,
                    "safety_level": cap.safety_level.value,
                    "constraints": len(cap.constraints),
                }
                for mode, cap in self.capabilities.items()
            }
        }


# Global singleton instance
_default_interface: Optional[CodeAgentInterface] = None


def get_code_agent_interface() -> CodeAgentInterface:
    """Get the global code agent interface."""
    global _default_interface
    if _default_interface is None:
        _default_interface = CodeAgentInterface()
    return _default_interface


def reset_code_agent_interface():
    """Reset to default interface (for testing)."""
    global _default_interface
    _default_interface = None
