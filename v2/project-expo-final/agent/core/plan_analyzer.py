"""Plan Analyzer — Decompose queries into execution plans.

Analyzes a user query and generates an ExecutionPlan with tasks,
dependencies, and estimated duration. Integrates with existing
orchestration without replacing it.

Industry-grade patterns:
  - Detect query type (research, implementation, analysis, comparison)
  - Auto-generate task graph based on type
  - Validate task dependencies
  - Provide human-readable task descriptions
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .execution_plan import ExecutionPlan, TaskItem, TaskPriority, TaskStatus
from ..llm.client import get_client, NIMClient

logger = logging.getLogger(__name__)


class QueryType:
    """Classification of query intent."""
    RESEARCH = "research"           # Information gathering
    IMPLEMENTATION = "implementation"  # "How to implement X"
    ANALYSIS = "analysis"           # "What is", "Explain X"
    COMPARISON = "comparison"        # "Compare X vs Y"
    DECISION = "decision"           # "Should I do X"
    TROUBLESHOOTING = "troubleshooting"  # "Fix issue X"
    CREATIVE = "creative"           # "Write X", "Create X"
    CALCULATION = "calculation"      # "Calculate X", "Compute X"


class PlanAnalyzer:
    """Analyze queries and generate execution plans."""
    
    def __init__(self, client: Optional[NIMClient] = None):
        self.client = client or get_client()
    
    async def analyze(
        self,
        query: str,
        enable_llm: bool = False,
    ) -> ExecutionPlan:
        """Analyze a query and generate an execution plan.
        
        Args:
            query: User query
            enable_llm: If True, use LLM for intelligent decomposition.
                       If False, use heuristics (faster, works offline).
        
        Returns:
            ExecutionPlan with tasks and dependencies
        """
        # Detect query type
        query_type = self._classify_query(query)
        logger.debug(f"Query classified as: {query_type}")
        
        # Generate plan based on type
        if enable_llm:
            plan = await self._generate_plan_llm(query, query_type)
        else:
            plan = self._generate_plan_heuristic(query, query_type)
        
        return plan
    
    def _classify_query(self, query: str) -> str:
        """Classify query intent using heuristics."""
        query_lower = query.lower()
        
        # Comparison patterns
        if any(kw in query_lower for kw in ["vs", "versus", "compare", "difference", "better", "vs."]):
            return QueryType.COMPARISON
        
        # Decision patterns
        if any(kw in query_lower for kw in ["should", "should i", "should we", "best", "recommend"]):
            return QueryType.DECISION
        
        # How-to / implementation patterns
        if any(kw in query_lower for kw in ["how to", "how do", "implement", "create", "build", "write", "make"]):
            return QueryType.IMPLEMENTATION
        
        # Explain / analysis patterns
        if any(kw in query_lower for kw in ["explain", "what is", "what are", "describe", "tell me about", "about"]):
            return QueryType.ANALYSIS
        
        # Calculate patterns
        if any(kw in query_lower for kw in ["calculate", "compute", "how much", "how many", "count", "sum"]):
            return QueryType.CALCULATION
        
        # Troubleshoot patterns
        if any(kw in query_lower for kw in ["fix", "error", "bug", "problem", "issue", "troubleshoot", "why is"]):
            return QueryType.TROUBLESHOOTING
        
        # Default to research
        return QueryType.RESEARCH
    
    def _generate_plan_heuristic(self, query: str, query_type: str) -> ExecutionPlan:
        """Generate execution plan using heuristics (fast path)."""
        plan = ExecutionPlan(query=query, plan_id=f"plan_{hash(query) & 0xffffffff:08x}")
        
        if query_type == QueryType.COMPARISON:
            self._plan_comparison(query, plan)
        elif query_type == QueryType.DECISION:
            self._plan_decision(query, plan)
        elif query_type == QueryType.IMPLEMENTATION:
            self._plan_implementation(query, plan)
        elif query_type == QueryType.ANALYSIS:
            self._plan_analysis(query, plan)
        elif query_type == QueryType.TROUBLESHOOTING:
            self._plan_troubleshooting(query, plan)
        elif query_type == QueryType.CALCULATION:
            self._plan_calculation(query, plan)
        else:
            self._plan_research(query, plan)
        
        return plan
    
    async def _generate_plan_llm(self, query: str, query_type: str) -> ExecutionPlan:
        """Generate execution plan using LLM (more intelligent)."""
        # For now, fall back to heuristic
        # In production, this would call LLM to decompose
        return self._generate_plan_heuristic(query, query_type)
    
    def _plan_comparison(self, query: str, plan: ExecutionPlan):
        """Generate plan for comparison queries (X vs Y)."""
        # Extract entities to compare
        parts = query.split(" vs ")
        if len(parts) == 2:
            entity_a = parts[0].strip().split()[-1]  # Last word before vs
            entity_b = parts[1].strip().split()[0]   # First word after vs
        else:
            entity_a = "Option A"
            entity_b = "Option B"
        
        # Task 1: Research Entity A
        task_a = TaskItem(
            task_id="research_a",
            title=f"Research {entity_a}",
            description=f"Gather information about {entity_a}",
            priority=TaskPriority.HIGH,
            subagent_type="retriever",
        )
        plan.add_task(task_a)
        
        # Task 2: Research Entity B
        task_b = TaskItem(
            task_id="research_b",
            title=f"Research {entity_b}",
            description=f"Gather information about {entity_b}",
            priority=TaskPriority.HIGH,
            subagent_type="retriever",
        )
        plan.add_task(task_b)
        
        # Task 3: Compare (depends on both research tasks)
        task_compare = TaskItem(
            task_id="compare",
            title="Synthesize Comparison",
            description=f"Analyze and compare {entity_a} vs {entity_b}",
            priority=TaskPriority.HIGH,
            depends_on=["research_a", "research_b"],
        )
        plan.add_task(task_compare)
        
        plan.reasoning = f"Parallel research on both entities, then synthesis"
        plan.total_estimat_duration_s = 60.0
    
    def _plan_decision(self, query: str, plan: ExecutionPlan):
        """Generate plan for decision queries (Should I buy/use X)."""
        # Task 1: Gather information
        task_gather = TaskItem(
            task_id="gather",
            title="Gather Information",
            description="Collect relevant information and options",
            priority=TaskPriority.HIGH,
            subagent_type="retriever",
        )
        plan.add_task(task_gather)
        
        # Task 2: Identify decision factors
        task_factors = TaskItem(
            task_id="factors",
            title="Identify Decision Factors",
            description="What are the key factors that matter?",
            priority=TaskPriority.NORMAL,
            depends_on=["gather"],
        )
        plan.add_task(task_factors)
        
        # Task 3: Ask user for priorities
        task_ask = TaskItem(
            task_id="ask_user",
            title="Clarify Priorities",
            description="Ask user which factors matter most",
            priority=TaskPriority.NORMAL,
            depends_on=["factors"],
            metadata={"requires_user_input": True}
        )
        plan.add_task(task_ask)
        
        # Task 4: Deep dive on priorities
        task_deep = TaskItem(
            task_id="deep_dive",
            title="Deep Dive Analysis",
            description="Analyze the priority areas in detail",
            priority=TaskPriority.HIGH,
            depends_on=["ask_user"],
            subagent_type="retriever",
        )
        plan.add_task(task_deep)
        
        plan.reasoning = "Gather info → identify factors → ask user → deep dive on priorities"
        plan.total_estimat_duration_s = 120.0
    
    def _plan_implementation(self, query: str, plan: ExecutionPlan):
        """Generate plan for implementation/how-to queries."""
        # Task 1: Research existing solutions
        task_research = TaskItem(
            task_id="research",
            title="Research Existing Solutions",
            description="Find existing implementations and best practices",
            priority=TaskPriority.HIGH,
            subagent_type="code_retriever",
        )
        plan.add_task(task_research)
        
        # Task 2: Identify requirements
        task_requirements = TaskItem(
            task_id="requirements",
            title="Define Requirements",
            description="Clarify technical requirements and constraints",
            priority=TaskPriority.NORMAL,
            depends_on=["research"],
        )
        plan.add_task(task_requirements)
        
        # Task 3: Generate implementation
        task_generate = TaskItem(
            task_id="generate",
            title="Generate Implementation",
            description="Create code/solution based on requirements",
            priority=TaskPriority.HIGH,
            depends_on=["requirements"],
            subagent_type="code_gen_executor",
        )
        plan.add_task(task_generate)
        
        # Task 4: Validate and test
        task_validate = TaskItem(
            task_id="validate",
            title="Validate Solution",
            description="Test and validate the implementation",
            priority=TaskPriority.HIGH,
            depends_on=["generate"],
            subagent_type="code_gen_executor",
        )
        plan.add_task(task_validate)
        
        plan.reasoning = "Research → define requirements → generate → validate"
        plan.total_estimat_duration_s = 180.0
    
    def _plan_analysis(self, query: str, plan: ExecutionPlan):
        """Generate plan for analysis queries (What is X, Explain X)."""
        # Task 1: Retrieve information
        task_retrieve = TaskItem(
            task_id="retrieve",
            title="Retrieve Information",
            description="Gather comprehensive information on the topic",
            priority=TaskPriority.HIGH,
            subagent_type="retriever",
        )
        plan.add_task(task_retrieve)
        
        # Task 2: Synthesize explanation
        task_synthesize = TaskItem(
            task_id="synthesize",
            title="Synthesize Explanation",
            description="Create clear, comprehensive explanation",
            priority=TaskPriority.HIGH,
            depends_on=["retrieve"],
        )
        plan.add_task(task_synthesize)
        
        plan.reasoning = "Simple: retrieve → synthesize"
        plan.total_estimat_duration_s = 45.0
    
    def _plan_troubleshooting(self, query: str, plan: ExecutionPlan):
        """Generate plan for troubleshooting/debugging queries."""
        # Task 1: Understand the problem
        task_understand = TaskItem(
            task_id="understand",
            title="Understand Problem",
            description="Gather details about the error/issue",
            priority=TaskPriority.HIGH,
            metadata={"requires_user_input": True}
        )
        plan.add_task(task_understand)
        
        # Task 2: Search for solutions
        task_search = TaskItem(
            task_id="search",
            title="Search for Solutions",
            description="Find similar issues and solutions",
            priority=TaskPriority.HIGH,
            depends_on=["understand"],
            subagent_type="retriever",
        )
        plan.add_task(task_search)
        
        # Task 3: Diagnose root cause
        task_diagnose = TaskItem(
            task_id="diagnose",
            title="Diagnose Root Cause",
            description="Identify the underlying cause",
            priority=TaskPriority.HIGH,
            depends_on=["search"],
        )
        plan.add_task(task_diagnose)
        
        # Task 4: Provide solution
        task_solution = TaskItem(
            task_id="solution",
            title="Generate Solution",
            description="Provide step-by-step fix",
            priority=TaskPriority.HIGH,
            depends_on=["diagnose"],
        )
        plan.add_task(task_solution)
        
        plan.reasoning = "Understand → search → diagnose → solve"
        plan.total_estimat_duration_s = 90.0
    
    def _plan_calculation(self, query: str, plan: ExecutionPlan):
        """Generate plan for calculation/computation queries."""
        # Task 1: Prepare input
        task_prepare = TaskItem(
            task_id="prepare",
            title="Prepare Calculation",
            description="Gather input data and parameters",
            priority=TaskPriority.HIGH,
        )
        plan.add_task(task_prepare)
        
        # Task 2: Execute calculation
        task_execute = TaskItem(
            task_id="execute",
            title="Execute Calculation",
            description="Run the computation",
            priority=TaskPriority.HIGH,
            depends_on=["prepare"],
            subagent_type="code_gen_executor",
        )
        plan.add_task(task_execute)
        
        # Task 3: Verify and present
        task_verify = TaskItem(
            task_id="verify",
            title="Verify Results",
            description="Check results and present clearly",
            priority=TaskPriority.NORMAL,
            depends_on=["execute"],
        )
        plan.add_task(task_verify)
        
        plan.reasoning = "Simple computational flow"
        plan.total_estimat_duration_s = 30.0
    
    def _plan_research(self, query: str, plan: ExecutionPlan):
        """Generate plan for general research queries."""
        # Task 1: Retrieve information
        task_retrieve = TaskItem(
            task_id="retrieve",
            title="Retrieve Information",
            description="Search for relevant information",
            priority=TaskPriority.HIGH,
            subagent_type="retriever",
        )
        plan.add_task(task_retrieve)
        
        # Task 2: Synthesize answer
        task_synthesize = TaskItem(
            task_id="synthesize",
            title="Synthesize Answer",
            description="Compile and organize findings",
            priority=TaskPriority.HIGH,
            depends_on=["retrieve"],
        )
        plan.add_task(task_synthesize)
        
        plan.reasoning = "Standard retrieval → synthesis"
        plan.total_estimat_duration_s = 60.0
