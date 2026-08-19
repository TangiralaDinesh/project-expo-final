"""Dynamic Tool Registry — Runtime tool registration and discovery.

Industry-grade tool management:
  - Discover available tools at runtime
  - Register new tools dynamically
  - Track tool capabilities and constraints
  - Query tools by capability

This enables the agent to adapt to different tool environments
without hardcoding tool definitions.
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class ToolCategory(str, Enum):
    """Categories of tools."""
    RETRIEVAL = "retrieval"           # Web search, knowledge base access
    COMPUTATION = "computation"       # Calculations, data processing
    GENERATION = "generation"         # Code/text generation
    ANALYSIS = "analysis"             # Code/data analysis
    FILE_OPERATION = "file_operation" # File reading/writing
    WEB = "web"                       # HTTP requests, APIs
    DATABASE = "database"             # Database queries
    EXECUTION = "execution"           # Code/script execution


class ToolAvailability(str, Enum):
    """Availability status of a tool."""
    AVAILABLE = "available"       # Ready to use
    DISABLED = "disabled"         # Disabled but can be enabled
    UNAVAILABLE = "unavailable"   # Not available in this environment
    DEGRADED = "degraded"         # Available but with limitations


@dataclass
class ToolRequirement:
    """A requirement for using a tool."""
    name: str
    description: str
    required: bool = True
    value: Optional[str] = None


@dataclass
class ToolCapability:
    """One capability of a tool."""
    name: str
    description: str
    complexity: str  # "simple", "moderate", "complex"
    tags: List[str] = field(default_factory=list)


@dataclass
class ToolDefinition:
    """Definition of a tool."""
    tool_id: str
    name: str
    description: str
    category: ToolCategory
    
    # Availability and status
    availability: ToolAvailability = ToolAvailability.AVAILABLE
    version: str = "1.0"
    
    # Capabilities and requirements
    capabilities: List[ToolCapability] = field(default_factory=list)
    requirements: List[ToolRequirement] = field(default_factory=list)
    
    # Execution info
    execution_time_estimate_s: float = 5.0
    supports_async: bool = True
    supports_streaming: bool = False
    
    # Metadata
    author: str = "unknown"
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Last registered/updated
    registered_at: float = field(default_factory=time.time)
    last_used: Optional[float] = None
    
    # For integration
    handler_fn: Optional[Callable] = None  # Async function to execute tool
    
    def mark_used(self):
        """Mark tool as recently used."""
        self.last_used = time.time()
    
    def is_available(self) -> bool:
        """Check if tool is available for use."""
        return self.availability == ToolAvailability.AVAILABLE
    
    def has_capability(self, capability_name: str) -> bool:
        """Check if tool has a capability."""
        return any(c.name == capability_name for c in self.capabilities)


class DynamicToolRegistry:
    """Registry for dynamically discovering and managing tools."""
    
    def __init__(self):
        """Initialize empty registry."""
        self.tools: Dict[str, ToolDefinition] = {}
        self._handlers: Dict[str, Callable] = {}
    
    def register_tool(
        self,
        tool_id: str,
        name: str,
        description: str,
        category: ToolCategory,
        capabilities: List[ToolCapability] = None,
        handler_fn: Optional[Callable] = None,
        **kwargs
    ) -> ToolDefinition:
        """Register a new tool.
        
        Args:
            tool_id: Unique tool identifier
            name: Human-readable name
            description: What the tool does
            category: Tool category
            capabilities: List of capabilities
            handler_fn: Async function to execute tool
            **kwargs: Additional metadata
        
        Returns:
            ToolDefinition for the registered tool
        """
        if tool_id in self.tools:
            logger.warning(f"Tool {tool_id} already registered, updating")
        
        tool = ToolDefinition(
            tool_id=tool_id,
            name=name,
            description=description,
            category=category,
            capabilities=capabilities or [],
            handler_fn=handler_fn,
            **kwargs
        )
        
        self.tools[tool_id] = tool
        if handler_fn:
            self._handlers[tool_id] = handler_fn
        
        logger.info(f"Registered tool: {tool_id} ({name})")
        return tool
    
    def unregister_tool(self, tool_id: str) -> bool:
        """Unregister a tool."""
        if tool_id in self.tools:
            del self.tools[tool_id]
            if tool_id in self._handlers:
                del self._handlers[tool_id]
            logger.info(f"Unregistered tool: {tool_id}")
            return True
        return False
    
    def get_tool(self, tool_id: str) -> Optional[ToolDefinition]:
        """Get a tool by ID."""
        return self.tools.get(tool_id)
    
    def list_tools(
        self,
        category: Optional[ToolCategory] = None,
        available_only: bool = False,
        capability: Optional[str] = None,
    ) -> List[ToolDefinition]:
        """List tools with optional filters.
        
        Args:
            category: Filter by category
            available_only: Only show available tools
            capability: Filter by capability name
        
        Returns:
            List of matching tools
        """
        tools = list(self.tools.values())
        
        if category:
            tools = [t for t in tools if t.category == category]
        
        if available_only:
            tools = [t for t in tools if t.is_available()]
        
        if capability:
            tools = [t for t in tools if t.has_capability(capability)]
        
        return tools
    
    def get_tools_by_category(self, category: ToolCategory) -> List[ToolDefinition]:
        """Get all tools in a category."""
        return self.list_tools(category=category)
    
    def get_available_tools(self) -> List[ToolDefinition]:
        """Get all available tools."""
        return self.list_tools(available_only=True)
    
    def get_tools_with_capability(self, capability: str) -> List[ToolDefinition]:
        """Get tools that have a specific capability."""
        return self.list_tools(capability=capability)
    
    def enable_tool(self, tool_id: str) -> bool:
        """Enable a tool."""
        tool = self.get_tool(tool_id)
        if tool:
            tool.availability = ToolAvailability.AVAILABLE
            logger.info(f"Enabled tool: {tool_id}")
            return True
        return False
    
    def disable_tool(self, tool_id: str, reason: str = "") -> bool:
        """Disable a tool."""
        tool = self.get_tool(tool_id)
        if tool:
            tool.availability = ToolAvailability.DISABLED
            if reason:
                tool.metadata["disabled_reason"] = reason
            logger.info(f"Disabled tool: {tool_id} ({reason})")
            return True
        return False
    
    def mark_tool_degraded(self, tool_id: str, reason: str = "") -> bool:
        """Mark a tool as degraded (available but with limitations)."""
        tool = self.get_tool(tool_id)
        if tool:
            tool.availability = ToolAvailability.DEGRADED
            if reason:
                tool.metadata["degraded_reason"] = reason
            logger.warning(f"Tool degraded: {tool_id} ({reason})")
            return True
        return False
    
    async def execute_tool(
        self,
        tool_id: str,
        *args,
        **kwargs
    ) -> Any:
        """Execute a tool.
        
        Args:
            tool_id: Tool to execute
            *args, **kwargs: Arguments to pass to handler
        
        Returns:
            Result from tool execution
        """
        tool = self.get_tool(tool_id)
        if not tool:
            raise ValueError(f"Tool not found: {tool_id}")
        
        if not tool.is_available():
            raise RuntimeError(f"Tool not available: {tool_id}")
        
        handler = self._handlers.get(tool_id)
        if not handler:
            raise RuntimeError(f"No handler for tool: {tool_id}")
        
        tool.mark_used()
        
        try:
            if tool.supports_async:
                return await handler(*args, **kwargs)
            else:
                return handler(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error executing tool {tool_id}: {e}")
            raise
    
    def get_registry_summary(self) -> Dict[str, Any]:
        """Get summary of registry state."""
        tools_by_category = {}
        for category in ToolCategory:
            tools = self.list_tools(category=category)
            tools_by_category[category.value] = len(tools)
        
        return {
            "total_tools": len(self.tools),
            "available_tools": len(self.list_tools(available_only=True)),
            "tools_by_category": tools_by_category,
            "tools": {
                t.tool_id: {
                    "name": t.name,
                    "availability": t.availability.value,
                    "category": t.category.value,
                    "capabilities": len(t.capabilities),
                }
                for t in self.tools.values()
            }
        }


# Global singleton registry
_default_registry: Optional[DynamicToolRegistry] = None


def get_tool_registry() -> DynamicToolRegistry:
    """Get the global tool registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = DynamicToolRegistry()
        _setup_default_tools(_default_registry)
    return _default_registry


def _setup_default_tools(registry: DynamicToolRegistry):
    """Setup default tools in registry."""
    # Semantic retrieval tool
    registry.register_tool(
        tool_id="semantic_retriever",
        name="Semantic Retriever",
        description="Search for information in knowledge base and web",
        category=ToolCategory.RETRIEVAL,
        capabilities=[
            ToolCapability(
                name="web_search",
                description="Search the web",
                complexity="simple",
            ),
            ToolCapability(
                name="knowledge_base_search",
                description="Search knowledge base",
                complexity="simple",
            ),
        ],
        version="2.0",
        tags=["retrieval", "search"],
    )
    
    # Code execution tool
    registry.register_tool(
        tool_id="code_executor",
        name="Code Executor",
        description="Execute Python, Bash, or JavaScript code",
        category=ToolCategory.EXECUTION,
        capabilities=[
            ToolCapability(
                name="python_execution",
                description="Execute Python code",
                complexity="moderate",
            ),
            ToolCapability(
                name="bash_execution",
                description="Execute Bash commands",
                complexity="moderate",
            ),
        ],
        version="1.5",
        tags=["execution", "computation"],
    )
    
    # Code generation tool
    registry.register_tool(
        tool_id="code_generator",
        name="Code Generator",
        description="Generate code for various languages",
        category=ToolCategory.GENERATION,
        capabilities=[
            ToolCapability(
                name="python_generation",
                description="Generate Python code",
                complexity="moderate",
            ),
            ToolCapability(
                name="sql_generation",
                description="Generate SQL queries",
                complexity="moderate",
            ),
        ],
        version="1.0",
        tags=["generation", "coding"],
    )
    
    # Code analysis tool
    registry.register_tool(
        tool_id="code_analyzer",
        name="Code Analyzer",
        description="Analyze code structure, complexity, and quality",
        category=ToolCategory.ANALYSIS,
        capabilities=[
            ToolCapability(
                name="ast_analysis",
                description="Analyze code AST",
                complexity="moderate",
            ),
            ToolCapability(
                name="complexity_analysis",
                description="Calculate code complexity",
                complexity="simple",
            ),
        ],
        version="1.2",
        tags=["analysis", "code_quality"],
    )
    
    logger.info("Initialized default tools in registry")


def reset_tool_registry():
    """Reset to default registry (for testing)."""
    global _default_registry
    _default_registry = None
