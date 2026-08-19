"""Speculative Bayesian questioning during retrieval.

Phase 4: Generates speculative questions while retrieving information,
asks user which matter most, and uses responses to guide subsequent searches.
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum

from agent.llm.client import NIMClient, get_client

logger = logging.getLogger(__name__)


class SpeculativeQuestionType(Enum):
    """Types of speculative questions."""
    ASPECT = "aspect"           # Missing aspect or dimension
    COMPARISON = "comparison"   # Compare two entities
    EVIDENCE = "evidence"       # More evidence for a claim
    EDGE_CASE = "edge_case"     # Edge cases or exceptions
    IMPLEMENTATION = "how_to"   # How to implement or apply
    RISK = "risk"               # Risks or downsides
    ALTERNATIVE = "alternative" # Alternatives to consider


@dataclass
class SpeculativeQuestion:
    """One speculative question generated during retrieval."""
    question: str
    question_type: SpeculativeQuestionType
    importance: float          # 0.0-1.0, Bayesian prior
    why_relevant: str          # Why this question matters
    estimated_answer_tokens: int  # How many tokens to answer


@dataclass
class SpeculativeQuestionSet:
    """Set of questions generated for one retrieval."""
    original_query: str
    questions: List[SpeculativeQuestion]
    confidence: float         # 0.0-1.0, how confident in these questions


class SpeculativeQuestionGenerator:
    """Generate Bayesian speculative questions during retrieval."""
    
    # Question generation system prompt
    _SYSTEM_PROMPT = """You are a Bayesian question generator. Given a query and initial retrieval results,
generate 3-5 speculative questions that would strengthen the user's understanding.

Each question should:
1. Represent a plausible gap in current understanding
2. Have prior probability (how likely this matters)
3. Be answerable from typical sources
4. Not be trivial or already-obvious

Return JSON array of objects:
[
  {
    "question": "specific question text",
    "type": "aspect" | "comparison" | "evidence" | "edge_case" | "how_to" | "risk" | "alternative",
    "importance": 0.0-1.0,
    "why_relevant": "explanation of relevance",
    "estimated_tokens": 100-500
  }
]"""
    
    def __init__(self, client: Optional[NIMClient] = None):
        self.client = client or get_client()
    
    async def generate(
        self,
        query: str,
        initial_learnings: List[str],
        query_type: str = "semantic",
    ) -> SpeculativeQuestionSet:
        """Generate speculative questions based on query and initial learnings (Phase 4).
        
        Uses Bayesian principles to identify high-value clarification questions that:
        1. Disambiguate between competing hypotheses
        2. Reveal user preferences/constraints not yet elicited
        3. Guide information gathering toward user needs
        
        Args:
            query: Original user query
            initial_learnings: Initial retrieved learnings/snippets
            query_type: Type of query (semantic, code, etc.)
        
        Returns:
            SpeculativeQuestionSet with generated questions ranked by importance
        """
        try:
            # Build context for question generation
            learnings_summary = "\n".join(
                [f"- {l[:200]}" for l in initial_learnings[:8]]
            )
            
            # Enhanced system prompt with Bayesian language
            system_prompt = (
                "You are a Bayesian questioner. Your role is to generate clarification questions "
                "that maximize information gain for user decision-making.\n\n"
                
                "Principles:\n"
                "1. INFORMATION GAIN: Questions should disambiguate competing interpretations\n"
                "2. USER PREFERENCES: Reveal unstated constraints (budget, risk tolerance, timeline)\n"
                "3. HIDDEN ASSUMPTIONS: Question assumptions in the query\n"
                "4. ACTIONABILITY: Each question should guide next retrieval step\n\n"
                
                "Generate 3-5 speculative questions ranked by importance.\n"
                "Be specific: 'How risk-averse are you?' is better than 'What matters to you?'\n\n"
                
                "Return ONLY valid JSON array with no markdown."
            )
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"""Query: "{query}"

Current findings (from {len(initial_learnings)} sources):
{learnings_summary}

Based on these findings, what 3-5 clarification questions would most help guide deeper research?
Focus on:
- User preferences (cost sensitivity, risk tolerance, timeline)
- Hidden assumptions or constraints
- Comparison dimensions not yet explored
- Implementation/practical concerns

Respond with ONLY JSON array:
[
  {{
    "question": "specific question text",
    "type": "aspect|comparison|evidence|edge_case|how_to|risk|alternative",
    "importance": 0.0-1.0,
    "why_relevant": "one sentence explaining relevance",
    "estimated_tokens": 50-300
  }}
]
"""},
            ]
            
            raw = await self.client.chat_fast(
                messages,
                temperature=0.6,  # Some creativity for question generation
                response_format_json=True,
            )
            
            # Parse response
            parsed_questions = []
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    for q in data:
                        parsed_questions.append(SpeculativeQuestion(
                            question=q.get("question", ""),
                            question_type=SpeculativeQuestionType(
                                q.get("type", "aspect").lower()
                            ),
                            importance=float(q.get("importance", 0.5)),
                            why_relevant=q.get("why_relevant", ""),
                            estimated_answer_tokens=int(q.get("estimated_tokens", 100)),
                        ))
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning(f"Failed to parse speculative questions: {e}")
                return SpeculativeQuestionSet(
                    original_query=query,
                    questions=[],
                    confidence=0.0,
                )
            
            # Sort by importance (descending) and confidence
            parsed_questions.sort(key=lambda q: q.importance, reverse=True)
            
            # Calculate confidence based on question quality
            avg_importance = sum(q.importance for q in parsed_questions) / len(parsed_questions) if parsed_questions else 0.5
            
            return SpeculativeQuestionSet(
                original_query=query,
                questions=parsed_questions,
                confidence=min(0.95, avg_importance + 0.2),  # Higher avg importance = higher confidence
            )
        
        except Exception as e:
            logger.exception(f"Speculative question generation failed: {e}")
            return SpeculativeQuestionSet(
                original_query=query,
                questions=[],
                confidence=0.0,
            )
    
    def _parse_questions(self, response: str) -> List[SpeculativeQuestion]:
        """Parse LLM response into SpeculativeQuestion objects."""
        try:
            data = json.loads(response)
            if not isinstance(data, list):
                return []
            
            questions = []
            for item in data:
                try:
                    q_type = SpeculativeQuestionType(item.get("type", "aspect"))
                except ValueError:
                    q_type = SpeculativeQuestionType.ASPECT
                
                questions.append(SpeculativeQuestion(
                    question=item.get("question", ""),
                    question_type=q_type,
                    importance=float(item.get("importance", 0.5)),
                    why_relevant=item.get("why_relevant", ""),
                    estimated_answer_tokens=int(item.get("estimated_tokens", 200)),
                ))
            
            return questions
        
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug(f"Failed to parse speculative questions: {e}")
            return []


async def filter_speculative_questions(
    questions: List[SpeculativeQuestion],
    user_priorities: Optional[List[str]] = None,
    max_to_explore: int = 3,
) -> List[str]:
    """Filter speculative questions based on user priorities (Phase 4).
    
    Args:
        questions: Generated speculative questions
        user_priorities: Types user cares about ["comparison", "edge_case", etc.]
        max_to_explore: Max questions to convert to follow-up searches
    
    Returns:
        List of follow-up queries to execute
    """
    if not questions:
        return []
    
    # Sort by importance and filter by user priorities
    sorted_qs = sorted(questions, key=lambda q: q.importance, reverse=True)
    
    if user_priorities:
        # Prioritize questions matching user's interests
        filtered = [
            q for q in sorted_qs 
            if any(priority.lower() in q.question_type.value.lower() 
                   for priority in user_priorities)
        ]
        if not filtered:
            filtered = sorted_qs
    else:
        filtered = sorted_qs
    
    # Convert top questions to follow-up queries
    follow_ups = []
    for q in filtered[:max_to_explore]:
        follow_ups.append(f"Follow up: {q.question}")
    
    return follow_ups
