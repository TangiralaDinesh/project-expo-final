"""
Speculative Questioning — EIG-optimal probing to resolve branch ambiguity.

When the pivot loop generates competing hypotheses and confidence gap is small,
this module generates the ONE question that would most reduce uncertainty
about which hypothesis is correct.

This is Bayesian optimal experiment design applied to conversations:
  - Each hypothesis is a "theory"
  - Each question is an "experiment"
  - Pick the experiment that maximizes Expected Information Gain (EIG)

Unlike clarify.py (which asks about the USER'S intent), speculative questioning
asks about the DOMAIN to discriminate between the agent's OWN hypotheses.

Example:
  Hypotheses: ["The user needs OAuth2 for a SPA", "The user needs OAuth2 for a backend API"]
  Speculative question: "Is your application running in the browser (client-side)
  or on a server (backend)?"
  → Whichever the user answers, one hypothesis is eliminated.

The question is DESIGNED so any answer discriminates. If the question doesn't
help distinguish, it's a bad question and shouldn't be asked.
"""

from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass
from typing import Optional

from ..llm.client import NIMClient, get_client
from .types import Hypothesis

logger = logging.getLogger(__name__)


@dataclass
class SpeculativeQuestion:
    """One question designed to discriminate between competing hypotheses."""
    question: str
    target_hypotheses: list[str]  # Which hypotheses this discriminates
    expected_information_gain: float  # 0-1: how much uncertainty this resolves
    answer_mapping: dict[str, str]  # {"if user says X": "hypothesis A wins", ...}


async def generate_speculative_questions(
    hypotheses: list[Hypothesis],
    context: str = "",
    *,
    client: Optional[NIMClient] = None,
    max_questions: int = 2,
) -> list[SpeculativeQuestion]:
    """Generate questions that maximally discriminate between hypotheses.

    Args:
        hypotheses: Competing hypotheses from the pivot loop
        context: Query context for relevance
        client: LLM client
        max_questions: Maximum questions to generate

    Returns:
        List of SpeculativeQuestion, sorted by expected information gain
    """
    if len(hypotheses) < 2:
        return []

    client = client or get_client()

    hypothesis_descriptions = "\n".join(
        f"Hypothesis {i+1}: {h.label} — {h.explanation} (confidence: {h.prior:.0%})"
        for i, h in enumerate(hypotheses[:4])  # Cap at 4
    )

    prompt = f"""I'm an AI agent trying to decide between these competing hypotheses:

{hypothesis_descriptions}

{"Context: " + context if context else ""}

Generate {max_questions} questions I could ask the user where:
1. ANY answer the user gives would CLEARLY favor one hypothesis over the others
2. The question is specific and answerable (not vague like "what do you want?")
3. Each question discriminates between DIFFERENT pairs of hypotheses

Return JSON array:
[
  {{
    "question": "the discriminating question",
    "if_yes": "which hypothesis wins and why",
    "if_no": "which hypothesis wins and why",
    "eig": 0.8
  }}
]"""

    try:
        response = await client.chat_worker(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=400,
        )

        match = re.search(r'\[.*\]', response, re.DOTALL)
        if not match:
            return []

        items = json.loads(match.group())
        questions = []

        for item in items:
            if isinstance(item, dict) and item.get("question"):
                questions.append(SpeculativeQuestion(
                    question=item["question"],
                    target_hypotheses=[h.label for h in hypotheses[:2]],
                    expected_information_gain=float(item.get("eig", 0.5)),
                    answer_mapping={
                        "yes": item.get("if_yes", ""),
                        "no": item.get("if_no", ""),
                    },
                ))

        # Sort by EIG
        questions.sort(key=lambda q: q.expected_information_gain, reverse=True)
        return questions[:max_questions]

    except Exception as e:
        logger.warning("Speculative question generation failed: %s", e)
        return []


def select_best_question(
    questions: list[SpeculativeQuestion],
    already_asked: Optional[set[str]] = None,
) -> Optional[SpeculativeQuestion]:
    """Select the highest-EIG question that hasn't been asked before.

    Args:
        questions: Available speculative questions
        already_asked: Set of question strings already asked in this session

    Returns:
        Best question, or None if all have been asked
    """
    already_asked = already_asked or set()

    for q in questions:
        if q.question not in already_asked and q.expected_information_gain > 0.3:
            return q

    return None
