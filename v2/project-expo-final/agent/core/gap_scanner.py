"""
Proactive Gap Scanner — surfaces what users didn't know to ask.

Categorically different from everything else in this agent:
  - Entry gate, decision_llm, critique: answer the QUERY in front of them
  - Gap scanner: answers the FIELD, using what's been discussed as boundary

"Given everything covered so far in [domain], what would someone genuinely
expert in this field flag as standard and relevant, that hasn't come up yet?"

Honest limits:
  - Bounded by documented/known info — surfaces STANDARD adjacent considerations
  - NOT a crystal ball — genuinely unprecedented things will still be missed
  - Gated, not on every query — expensive, fires only on deep sessions
"""

from __future__ import annotations

import asyncio
import logging
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from ..llm.client import NIMClient, get_client

logger = logging.getLogger(__name__)


@dataclass
class GapItem:
    """One gap identified by the scanner."""
    topic: str              # What's missing
    relevance: str          # Why it matters for this query
    confidence: float       # How confident we are this is a real gap (0-1)
    category: str           # "risk", "context", "alternative", "prerequisite", "dependency"


@dataclass
class GapScanResult:
    """Result of proactive gap scanning."""
    gaps: list[GapItem]
    domain: str
    learnings_analyzed: int
    should_present: bool    # Whether gaps are significant enough to show user


def _should_run_gap_scan(
    learnings_count: int,
    max_depth: int,
    gate_mode: str,
    gap_scanning_enabled: bool = True,
) -> bool:
    """Gate: decide if gap scanning should fire.

    Only runs when:
    1. Feature flag enabled
    2. 5+ learnings accumulated (deep enough session)
    3. Complex query (max_depth >= 3)
    4. Not a casual/PARAMETRIC query
    """
    if not gap_scanning_enabled:
        return False
    if learnings_count < 5:
        return False
    if max_depth < 3:
        return False
    if gate_mode == "PARAMETRIC":
        return False
    return True


async def scan_for_gaps(
    query: str,
    learnings: list,
    domain: str = "",
    *,
    client: Optional[NIMClient] = None,
    max_gaps: int = 3,
) -> GapScanResult:
    """Run proactive gap scanning on accumulated learnings.

    Asks: "What would an expert flag as missing from this coverage?"

    Args:
        query: Original user query
        learnings: All learnings accumulated so far
        domain: Detected domain (e.g., "python", "finance")
        client: LLM client
        max_gaps: Maximum gaps to return

    Returns:
        GapScanResult with identified gaps
    """
    client = client or get_client()

    # Build learnings summary
    learnings_summary = "\n".join(
        f"- {getattr(l, 'text', str(l))[:150]}"
        for l in learnings[:10]
    )

    prompt = f"""You are an expert reviewer performing a COVERAGE CHECK — not answering the query, but checking what a domain expert would expect to find that ISN'T here yet.

Original query: "{query}"
{"Domain: " + domain if domain else ""}

Information gathered so far:
{learnings_summary}

Task: Identify {max_gaps} things that a genuine expert in this field would flag as STANDARD AND RELEVANT that haven't been covered. Focus on:
- Risks or caveats that are commonly known but not mentioned
- Prerequisites or dependencies that are assumed but not stated
- Alternative approaches that experts would consider
- Important context that changes interpretation of the findings

Do NOT:
- Suggest obscure or niche topics
- Repeat what's already covered
- Suggest things only loosely related
- Be vague — each gap must be specific and actionable

Return JSON array:
[
  {{
    "topic": "specific missing topic",
    "relevance": "why this matters for the query",
    "confidence": 0.8,
    "category": "risk|context|alternative|prerequisite|dependency"
  }}
]

If everything important IS covered, return an empty array: []"""

    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a research gap analysis engine. Output strictly a raw JSON array of gap objects. "
                    "CRITICAL: Do NOT output conversational monologue, 'Here\\'s a thinking process', or preamble. "
                    "Do NOT use markdown code blocks. Start your response IMMEDIATELY with '[' and end with ']'."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = await client.chat_worker(
            messages,
            temperature=0.2,
            max_tokens=512,
            response_format_json=True,
        )

        # Parse JSON
        match = re.search(r'\[.*\]', response, re.DOTALL)
        if not match:
            return GapScanResult(
                gaps=[], domain=domain,
                learnings_analyzed=len(learnings),
                should_present=False,
            )

        items = json.loads(match.group())
        gaps = []
        for item in items:
            if isinstance(item, dict) and item.get("topic"):
                gaps.append(GapItem(
                    topic=item.get("topic", ""),
                    relevance=item.get("relevance", ""),
                    confidence=float(item.get("confidence", 0.5)),
                    category=item.get("category", "context"),
                ))

        # Only present if we have high-confidence gaps
        should_present = any(g.confidence >= 0.6 for g in gaps)

        logger.info(
            "Gap scan: %d gaps found (present=%s) for domain='%s'",
            len(gaps), should_present, domain,
        )

        return GapScanResult(
            gaps=gaps[:max_gaps],
            domain=domain,
            learnings_analyzed=len(learnings),
            should_present=should_present,
        )

    except Exception as e:
        logger.warning("Gap scanning failed: %s", e)
        return GapScanResult(
            gaps=[], domain=domain,
            learnings_analyzed=len(learnings),
            should_present=False,
        )


def format_gaps_for_user(result: GapScanResult) -> str:
    """Format gap scan results for user-facing display.

    Only called when should_present is True.
    """
    if not result.gaps or not result.should_present:
        return ""

    lines = ["\n---", "💡 **You might also want to know:**\n"]

    for gap in result.gaps:
        icon = {
            "risk": "⚠️",
            "context": "📋",
            "alternative": "🔄",
            "prerequisite": "📌",
            "dependency": "🔗",
        }.get(gap.category, "💡")

        lines.append(f"{icon} **{gap.topic}** — {gap.relevance}")

    lines.append("\n*These items were identified by gap-scanning, not from your query.*")
    return "\n".join(lines)
