"""
Multi-perspective critique — for reviewing something that "succeeded"
but might not actually be good.

Two hard constraints from the research:

1. GENUINELY DIFFERENT ROLES — not N copies of "please be critical."
   Undifferentiated multi-agent debate actively DECREASES accuracy as
   agents drift toward agreement under peer pressure.

2. ISOLATED calls — each persona gets ONLY the artifact and the goal,
   never the generation's own reasoning trace.

CRITICAL CAVEAT: unanimous approval across all four personas is NOT
proof of correctness. Where a more objective check exists (run the tests,
verify against a real source), USE IT alongside this.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..llm.client import NIMClient, get_client
from ..config.budgets import CRITIQUE_TEMPERATURE

logger = logging.getLogger(__name__)


@dataclass
class CritiquePersona:
    name: str
    system_prompt: str


_BRUTAL_CRITIC = CritiquePersona(
    name="brutal_critic",
    system_prompt=(
        "You are reviewing a piece of work against the goal it was meant to "
        "achieve. Find what is actually wrong with it — concrete errors, "
        "inconsistencies, unsupported claims, gaps. Do not soften findings, "
        "do not open with something positive first. If there is nothing wrong, "
        "say so plainly — do not manufacture a criticism to seem thorough."
    ),
)

_BRUTAL_EXPECTATIONIST = CritiquePersona(
    name="brutal_expectationist",
    system_prompt=(
        "You represent the highest reasonable bar for this work. Would someone "
        "genuinely expert in this domain call this excellent, or merely adequate? "
        "What is the gap between what was delivered and what would actually "
        "impress someone who knows this space well? Be specific about the gap."
    ),
)

_BRUTAL_REALIST = CritiquePersona(
    name="brutal_realist",
    system_prompt=(
        "Evaluate whether this actually works, not whether the ambition behind "
        "it is good. Are the claims actually true? Given real constraints, will "
        "this approach actually function as described? Do not evaluate the idea "
        "— evaluate this specific execution of it."
    ),
)

_OVERTHINKER = CritiquePersona(
    name="overthinker",
    system_prompt=(
        "Assume this WILL fail. Your only job is finding how. Edge cases, "
        "race conditions, what happens when an assumption this relies on turns "
        "out false, what breaks under scale or adversarial input. Be paranoid "
        "and specific — a concrete failure scenario, not a vague concern."
    ),
)

ALL_PERSONAS = [_BRUTAL_CRITIC, _BRUTAL_EXPECTATIONIST, _BRUTAL_REALIST, _OVERTHINKER]


@dataclass
class CritiqueResult:
    persona_name: str
    verdict: str              # "approve" | "reject" | "approve_with_concerns"
    concerns: list[str] = field(default_factory=list)


@dataclass
class MultiCritiqueResult:
    results: list[CritiqueResult]
    consensus: bool           # True only if every persona approved outright
    disagreement: list[str] = field(default_factory=list)


def _safe_parse_json_dict(raw: str) -> dict:
    """Safely parse JSON dict from LLM output, handling markdown fences and surrounding text."""
    if not raw or not isinstance(raw, str):
        return {}
    raw_str = raw.strip()
    try:
        data = json.loads(raw_str)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Strip markdown code fences if present
    if "```" in raw_str:
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_str, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    # Search for first { ... } block
    m = re.search(r"\{.*\}", raw_str, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _parse(raw: str) -> dict:
    parsed = _safe_parse_json_dict(raw)
    if parsed:
        return parsed
    return {"verdict": "reject", "concerns": ["critique response was not valid JSON"]}


async def _run_one_persona(
    persona: CritiquePersona, goal: str, artifact: str, client: NIMClient,
) -> CritiqueResult:
    messages = [
        {"role": "system", "content": persona.system_prompt + (
            '\n\nRespond with ONLY a JSON object: {"verdict": "approve" | '
            '"reject" | "approve_with_concerns", "concerns": [list of strings]}'
        )},
        {"role": "user", "content": f"Goal: {goal}\n\nArtifact to review:\n{artifact}"},
    ]
    raw = await client.chat_worker(messages, temperature=CRITIQUE_TEMPERATURE, response_format_json=True)
    parsed = _parse(raw)
    return CritiqueResult(
        persona_name=persona.name,
        verdict=parsed.get("verdict", "reject"),
        concerns=parsed.get("concerns", []),
    )


async def run_multi_critique(
    goal: str,
    artifact: str,
    *,
    client: Optional[NIMClient] = None,
    personas: Optional[list[CritiquePersona]] = None,
) -> MultiCritiqueResult:
    """
    Runs every persona as an independent, isolated call — true parallel
    independence, same reason the orchestrator fires independent layers
    concurrently rather than in sequence.
    """
    client = client or get_client()
    personas = personas or ALL_PERSONAS

    results = await asyncio.gather(
        *(_run_one_persona(p, goal, artifact, client) for p in personas)
    )

    consensus = all(r.verdict == "approve" for r in results)
    disagreement = [
        f"{r.persona_name}: {r.verdict} -- {'; '.join(r.concerns)}"
        if r.concerns else f"{r.persona_name}: {r.verdict}"
        for r in results if r.verdict != "approve"
    ]

    return MultiCritiqueResult(
        results=list(results),
        consensus=consensus,
        disagreement=disagreement,
    )


# ── Plan 2: Critique-Guided Retrieval ──

@dataclass
class RetrievalGapAnalysis:
    """Result of critiquing partial retrieval results."""
    gaps_found: list[str]          # Concepts/aspects not yet explored
    suggested_queries: list[str]   # Specific follow-up retrieval queries
    consensus_strength: float      # 0-1: how many personas agree gaps exist?
    persona_verdicts: list[dict]   # Per-persona breakdown
    confidence: float              # Overall confidence in gap analysis


async def run_critique_on_retrieval(
    query: str,
    learnings: list[str],
    depth: int = 1,
    max_depth: int = 3,
    *,
    client: Optional[NIMClient] = None,
) -> RetrievalGapAnalysis:
    """
    Run multi-persona critique on partial retrieval results (Plan 2, Phase 2).
    
    Identifies gaps and suggests next queries to fill them. Each persona
    independently analyzes the current learnings to detect what's missing.
    
    Args:
        query: Original user query
        learnings: List of learned facts/summaries so far
        depth: Current search depth
        max_depth: Maximum depth allowed
        client: LLM client
    
    Returns:
        RetrievalGapAnalysis with gaps and suggested follow-up queries
    """
    client = client or get_client()
    
    # Prepare learnings summary for critique (up to 8 learnings, truncated for prompt efficiency)
    cleaned_learnings = [
        getattr(l, "text", str(l))[:250].strip() for l in learnings[:8] if l
    ]
    learnings_summary = "\n- ".join(cleaned_learnings) if cleaned_learnings else "(no learnings yet)"
    if cleaned_learnings:
        learnings_summary = "- " + learnings_summary
    
    # Critique prompt for each persona
    critique_system = (
        "You are critiquing partial retrieval results for a research query. "
        "Your job: identify what's MISSING or UNEXPLORED that would improve the answer. "
        "Return JSON with: gaps (list of missing concepts), suggested_queries (list of follow-up searches)."
    )
    
    persona_prompts = {
        "brutal_critic": (
            "Goal: Identify factual gaps or contradictions in what's been found so far. "
            "What obvious pieces of information are missing or contradicted?"
        ),
        "expectationist": (
            "Goal: Would an expert in this domain consider the retrieval comprehensive? "
            "What would an expert expect to find but don't see?"
        ),
        "realist": (
            "Goal: Ignore ambition. What's the practical next question you'd ask "
            "to actually answer the user's problem?"
        ),
        "overthinker": (
            "Goal: Find edge cases or nuances not covered. "
            "What scenarios or use cases aren't represented in the findings?"
        ),
    }
    
    async def run_one_persona_critique(persona_name: str, prompt_hint: str) -> dict:
        """Run one persona's critique on partial results."""
        full_prompt = f"""Query: {query}
        
Current findings (depth {depth}/{max_depth}):
{learnings_summary}

{prompt_hint}

Respond with JSON:
{{
  "gaps": ["gap1", "gap2", ...],  // Concepts/aspects not yet explored
  "suggested_queries": ["query1", "query2", ...],  // Specific follow-ups
  "confidence": 0-1  // How confident you are these gaps matter
}}"""
        
        try:
            raw = await client.chat_worker(
                [{"role": "user", "content": full_prompt}],
                temperature=CRITIQUE_TEMPERATURE,
                response_format_json=True,
            )
            parsed = _safe_parse_json_dict(raw)
            return {
                "persona": persona_name,
                "gaps": parsed.get("gaps", []),
                "suggested_queries": parsed.get("suggested_queries", []),
                "confidence": parsed.get("confidence", 0.0),
                "verdict": "gap_found" if parsed.get("gaps") else "sufficient",
            }
        except Exception as e:
            logger.warning(f"Critique failed for {persona_name}: {e}")
            return {
                "persona": persona_name,
                "gaps": [],
                "suggested_queries": [],
                "confidence": 0.0,
                "verdict": "error",
            }
    
    # Run all personas in parallel
    persona_results = await asyncio.gather(
        *[
            run_one_persona_critique(name, hint)
            for name, hint in persona_prompts.items()
        ]
    )
    
    # Aggregate results
    all_gaps = set()
    all_queries = []
    verdicts_with_gaps = 0
    confidences = []
    
    for result in persona_results:
        all_gaps.update(result.get("gaps", []))
        all_queries.extend(result.get("suggested_queries", []))
        if result.get("verdict") == "gap_found":
            verdicts_with_gaps += 1
        confidences.append(result.get("confidence", 0.0))
    
    # Consensus: if 3+ personas agree there are gaps
    consensus_strength = verdicts_with_gaps / len(persona_results) if persona_results else 0.0
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    
    # Deduplicate and limit queries
    unique_queries = list(dict.fromkeys(all_queries))[:5]
    unique_gaps = list(dict.fromkeys(all_gaps))[:5]
    
    logger.info(
        f"Critique-guided retrieval: {len(unique_gaps)} gaps identified, "
        f"consensus_strength={consensus_strength:.2f}, "
        f"suggesting {len(unique_queries)} follow-up queries"
    )
    
    return RetrievalGapAnalysis(
        gaps_found=unique_gaps,
        suggested_queries=unique_queries,
        consensus_strength=consensus_strength,
        persona_verdicts=persona_results,
        confidence=avg_confidence,
    )
