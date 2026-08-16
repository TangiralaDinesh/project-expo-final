"""
Task Reflection Engine (Plan 1, Phase C)

LLM-powered analysis of learnings to detect knowledge gaps and suggest new tasks.
Drives the adaptive behavior: after each wave, reflection engine analyzes what was
discovered and recommends what should be explored next.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Any

from .task_queue import TaskQueueItem, TaskStatus
from ..llm.client import NIMClient, get_client

logger = logging.getLogger(__name__)


@dataclass
class ReflectionAnalysis:
    """Output of reflection engine analysis."""
    new_tasks_detected: list[dict] = field(default_factory=list)  # [{"task": str, "priority": int, "reasoning": str}]
    reprioritization_suggestions: list[dict] = field(default_factory=list)  # [{"task_id": str, "new_priority": int, "reason": str}]
    knowledge_gaps: list[str] = field(default_factory=list)  # Gaps in current understanding
    confidence: float = 0.7  # How confident is this analysis (0-1)
    reasoning: str = ""  # Explanation of reflection results
    
    def has_new_tasks(self) -> bool:
        return len(self.new_tasks_detected) > 0
    
    def has_reprioritizations(self) -> bool:
        return len(self.reprioritization_suggestions) > 0


class TaskReflectionEngine:
    """Analyzes learnings to suggest new tasks and reprioritizations.
    
    This is the "thinking" component of Plan 1's adaptive system. After each wave
    executes, the engine looks at what was learned and decides what should be
    explored next.
    """
    
    def __init__(self, client: Optional[NIMClient] = None):
        self.client = client or get_client()
    
    async def analyze_learnings(
        self,
        current_learnings: list,
        original_query: str,
        domain: str = "research",
        context: Optional[dict] = None,
    ) -> ReflectionAnalysis:
        """
        Analyze learnings from wave execution and recommend next steps.
        
        Args:
            current_learnings: List of Learning objects from this wave
            original_query: User's original question
            domain: Domain of query (research, finance, investment, etc.)
            context: Additional context (user_profile, satisfaction_score, etc.)
        
        Returns:
            ReflectionAnalysis with new tasks, reprioritizations, gaps
        """
        if not current_learnings:
            logger.warning("No learnings to analyze")
            return ReflectionAnalysis(confidence=0.0, reasoning="No learnings provided")
        
        # Format learnings for LLM
        learnings_str = "\n".join([
            f"- {self._format_learning(l)}"
            for l in current_learnings[:10]  # Use top 10 to avoid context overflow
        ])
        
        context_str = self._format_context(context)
        
        prompt = f"""You are an adaptive task planning engine. Analyze these findings and recommend what to explore next.

ORIGINAL QUERY:
{original_query}

FINDINGS FROM THIS WAVE:
{learnings_str}

{context_str}

TASK: Based on these findings, what knowledge gaps remain? What should be explored next?

Respond with ONLY valid JSON (no markdown, no code blocks):
{{
  "new_tasks": [
    {{"task": "specific next task", "priority": 85, "reasoning": "why this matters"}}
  ],
  "reprioritizations": [
    {{"task_name": "original_task_id", "new_priority": 75, "reason": "new importance"}}
  ],
  "gaps": [
    "what we still don't know",
    "missing information"
  ],
  "confidence": 0.85,
  "reasoning": "overall analysis explanation"
}}"""
        
        try:
            response = await self.client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=512,
            )
            
            # Parse JSON response
            parsed = json.loads(response)
            
            # Extract new tasks
            new_tasks = []
            for task_dict in parsed.get("new_tasks", []):
                new_tasks.append({
                    "task": task_dict.get("task", ""),
                    "priority": min(100, max(0, task_dict.get("priority", 50))),
                    "reasoning": task_dict.get("reasoning", ""),
                })
            
            # Extract reprioritizations
            reprioritizations = []
            for repo_dict in parsed.get("reprioritizations", []):
                reprioritizations.append({
                    "task_id": repo_dict.get("task_name", repo_dict.get("task_id", "")),
                    "new_priority": min(100, max(0, repo_dict.get("new_priority", 50))),
                    "reason": repo_dict.get("reason", ""),
                })
            
            # Extract gaps
            gaps = parsed.get("gaps", [])
            
            confidence = min(1.0, max(0.0, parsed.get("confidence", 0.7)))
            reasoning = parsed.get("reasoning", "")
            
            analysis = ReflectionAnalysis(
                new_tasks_detected=new_tasks,
                reprioritization_suggestions=reprioritizations,
                knowledge_gaps=gaps,
                confidence=confidence,
                reasoning=reasoning,
            )
            
            logger.info(f"Reflection analysis: {len(new_tasks)} new tasks, "
                       f"{len(reprioritizations)} reprioritizations, confidence={confidence:.2f}")
            
            return analysis
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Failed to parse reflection response: {e}")
            return ReflectionAnalysis(
                confidence=0.0,
                reasoning=f"Failed to analyze learnings: {e}",
            )
        except Exception as e:
            logger.error(f"Reflection analysis failed: {e}")
            return ReflectionAnalysis(
                confidence=0.0,
                reasoning=f"Exception during reflection: {e}",
            )
    
    async def suggest_new_tasks(
        self,
        learnings: list,
        original_query: str,
    ) -> list[TaskQueueItem]:
        """
        Generate new TaskQueueItem objects based on learnings.
        
        Returns list of ready-to-enqueue TaskQueueItem.
        """
        analysis = await self.analyze_learnings(learnings, original_query)
        
        new_items = []
        for i, task_dict in enumerate(analysis.new_tasks_detected):
            item = TaskQueueItem(
                task_id=f"reflect_{len(new_items)}_{task_dict['task'][:20]}".replace(" ", "_"),
                task_description=task_dict["task"],
                priority=task_dict["priority"],
                dependencies=[],  # No deps on newly discovered tasks
            )
            new_items.append(item)
        
        return new_items
    
    async def detect_knowledge_gaps(
        self,
        learnings: list,
        original_query: str,
    ) -> list[str]:
        """
        Detect what knowledge is still missing.
        
        Returns list of gap descriptions.
        """
        analysis = await self.analyze_learnings(learnings, original_query)
        return analysis.knowledge_gaps
    
    def _format_learning(self, learning_obj: Any) -> str:
        """Format a Learning object for LLM consumption."""
        # Handle different learning object types
        if hasattr(learning_obj, 'text'):
            text = learning_obj.text
        elif isinstance(learning_obj, dict):
            text = learning_obj.get('text', str(learning_obj))
        elif isinstance(learning_obj, str):
            text = learning_obj
        else:
            text = str(learning_obj)
        
        # Truncate long text
        if len(text) > 300:
            text = text[:297] + "..."
        
        return text
    
    def _format_context(self, context: Optional[dict]) -> str:
        """Format context dict for LLM."""
        if not context:
            return ""
        
        lines = ["CONTEXT:"]
        if 'user_profile' in context:
            lines.append(f"- User Profile: {context['user_profile']}")
        if 'domain' in context:
            lines.append(f"- Domain: {context['domain']}")
        if 'satisfaction' in context:
            lines.append(f"- User Satisfaction: {context['satisfaction']:.1%}")
        if 'previous_waves' in context:
            lines.append(f"- Previous Waves Executed: {context['previous_waves']}")
        
        return "\n".join(lines)


# Predefined reprioritization rules (non-LLM based)
class ReprioritizationRules:
    """Deterministic rules for reprioritizing tasks based on learnings."""
    
    @staticmethod
    def apply_comparison_rule(queue_items: list[TaskQueueItem], learnings: list) -> list[tuple[str, int]]:
        """If comparison query and only 1 entity explored, prioritize other entity.
        
        Returns list of (task_id, new_priority) tuples.
        """
        reprioritizations = []
        
        # Check if this looks like a comparison (contains "vs", "versus", "compare")
        learnings_text = " ".join([
            getattr(l, 'text', str(l))[:100] for l in learnings
        ]).lower()
        
        if any(word in learnings_text for word in ['vs', 'versus', 'compare', 'comparison']):
            # Find tasks that might be for other entities
            for item in queue_items:
                if item.status.value == 'queued' and 'entity' in item.task_id.lower():
                    # Boost priority for unexplored entity tasks
                    reprioritizations.append((item.task_id, 95))
        
        return reprioritizations
    
    @staticmethod
    def apply_risk_profile_rule(queue_items: list[TaskQueueItem], learnings: list) -> list[tuple[str, int]]:
        """If risk-averse user and defensive stocks not yet analyzed, prioritize them."""
        reprioritizations = []
        
        learnings_text = " ".join([
            getattr(l, 'text', str(l))[:100] for l in learnings
        ]).lower()
        
        if 'risk-averse' in learnings_text or 'low risk' in learnings_text:
            for item in queue_items:
                if item.status.value == 'queued' and any(
                    word in item.task_description.lower()
                    for word in ['defensive', 'low_risk', 'stable', 'bond']
                ):
                    reprioritizations.append((item.task_id, 90))
        
        return reprioritizations
    
    @staticmethod
    def apply_priority_decay(queue_items: list[TaskQueueItem]) -> list[tuple[str, int]]:
        """Tasks waiting in queue lose 5 priority per wave (breadth before depth)."""
        reprioritizations = []
        
        for item in queue_items:
            if item.status.value == 'queued':
                # Decay priority by 5 for each wave it's been waiting
                decay = 5 * max(0, item.wave_added_at - 1)
                new_priority = max(0, item.priority - decay)
                if new_priority != item.priority:
                    reprioritizations.append((item.task_id, new_priority))
        
        return reprioritizations
