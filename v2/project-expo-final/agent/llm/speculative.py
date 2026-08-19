"""
Speculative Question Generation (Tier 3) — Generate compelling follow-up questions
that guide users toward deeper exploration when the agent is uncertain.

Uses information-theoretic principles to prioritize questions that would:
  1. Disambiguate between competing hypotheses (high information gain)
  2. Reveal new dimensions the user hasn't considered
  3. Connect to the user's implicit mental model
"""

from __future__ import annotations

import asyncio
import logging
import json
from dataclasses import dataclass
from typing import Optional

from .client import NIMClient, get_client
from ..core.types import Learning

logger = logging.getLogger(__name__)


@dataclass
class SpeculativeQuestion:
    """One speculative follow-up question"""
    question: str
    reasoning: str        # Why this question?
    information_gain: float  # 0-1, how much would it disambiguate?
    connects_to: list[str]   # Which hypotheses would it clarify?
    difficulty: str       # "beginner" | "intermediate" | "expert"


async def generate_speculative_questions(
    query: str,
    learnings: list[Learning],
    competing_hypotheses: list[dict],
    *,
    client: Optional[NIMClient] = None,
    num_questions: int = 3,
) -> list[SpeculativeQuestion]:
    """
    Generate speculative questions for user exploration (Tier 3).
    
    Args:
        query: Original user query
        learnings: Retrieved information
        competing_hypotheses: List of competing interpretations/hypotheses
                Each: {hypothesis: str, confidence: float, requires_clarification: list[str]}
        client: LLM client
        num_questions: Number of questions to generate
    
    Returns:
        List of SpeculativeQuestion ranked by information gain
    """
    client = client or get_client()
    
    if not competing_hypotheses or len(competing_hypotheses) < 2:
        # Not enough ambiguity for speculative questions
        return []
    
    # Build prompt for question generation
    hypotheses_str = "\n".join(
        f"  {i+1}. [{h.get('confidence', 0):.2f}] {h['hypothesis']}"
        for i, h in enumerate(competing_hypotheses[:3])  # Top 3
    )
    
    learnings_str = "\n".join(
        f"  - [{l.source_url or 'source'}] {l.text[:200]}..."
        for l in learnings[:5]
    )
    
    prompt = f"""You are an expert at generating speculative questions that disambiguate between competing interpretations.

Original Query: {query}

Competing Hypotheses (ranked by confidence):
{hypotheses_str}

Available Information:
{learnings_str}

Generate {num_questions} speculative questions that would:
1. Help clarify which hypothesis is most relevant
2. Reveal dimensions the user hasn't considered
3. Be engaging and thought-provoking

Return JSON array with this structure:
[
  {{
    "question": "What specific aspect matters most to you?",
    "reasoning": "This disambiguates between hypotheses 1 and 2 by...",
    "information_gain": 0.75,
    "connects_to": ["hypothesis_1", "hypothesis_2"],
    "difficulty": "beginner"
  }},
  ...
]

Ensure questions are:
- Clear and specific (not vague)
- Actionable (answerable by the user)
- Genuinely informative (would change the answer)
- Respectful (not condescending)
"""
    
    try:
        response = await client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024,
        )
        
        # Extract JSON from response
        import re
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if not json_match:
            logger.warning("Could not extract JSON from speculative questions response")
            return []
        
        questions_data = json.loads(json_match.group())
        
        # Convert to SpeculativeQuestion objects
        questions = [
            SpeculativeQuestion(
                question=q.get("question", ""),
                reasoning=q.get("reasoning", ""),
                information_gain=float(q.get("information_gain", 0.5)),
                connects_to=q.get("connects_to", []),
                difficulty=q.get("difficulty", "intermediate"),
            )
            for q in questions_data
            if q.get("question")
        ]
        
        # Sort by information gain
        questions.sort(key=lambda q: q.information_gain, reverse=True)
        
        logger.info(f"Generated {len(questions)} speculative questions")
        return questions[:num_questions]
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse speculative questions JSON: {e}")
        return []
    except Exception as e:
        logger.error(f"Error generating speculative questions: {e}")
        return []


async def generate_simple_clarifying_questions(
    query: str,
    *,
    client: Optional[NIMClient] = None,
    num_questions: int = 2,
) -> list[str]:
    """
    Simpler version: just ask what's unclear about the query.
    Used when there's no clear competing hypothesis yet.
    """
    client = client or get_client()
    
    prompt = f"""Given this query: "{query}"

Generate {num_questions} simple, specific follow-up questions that would help provide a better answer.
These should clarify ambiguities or reveal what matters most to the user.

Return a JSON array of strings:
["question 1", "question 2", ...]
"""
    
    try:
        response = await client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=256,
        )
        
        import re
        import json
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            questions = json.loads(json_match.group())
            return [str(q) for q in questions if q][:num_questions]
    except Exception as e:
        logger.error(f"Error generating clarifying questions: {e}")
    
    return []
