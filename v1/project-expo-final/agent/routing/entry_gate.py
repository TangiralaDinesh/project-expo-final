"""
Entry gate — the original "hallucination threshold."

Decides: answer directly from parametric knowledge, or retrieve first?
Combines regex fast-path (0ms for 90% of queries) from github_researchtool.py
with LLM fallback for ambiguous cases.

Six-way classification:
  - PARAMETRIC: stable algorithms, established concepts — no retrieval
  - SEMANTIC: needs conceptual docs, articles — semantic retrieval
  - CODE: needs real-world implementation examples — code retrieval
  - HYBRID: needs both code + conceptual grounding
  - SKILL: matches a Jarvis skill (deck builder, report builder, etc.)
  - URL_DIRECT: user gave a URL directly — skip search, fetch directly

Fails TOWARD retrieval on ambiguity — unnecessary search costs latency,
a skipped necessary one costs a hallucinated answer.
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass
from typing import Optional

from ..llm.client import NIMClient, get_client
from ..cache.llm_cache import LLMCache, get_llm_cache

logger = logging.getLogger(__name__)


@dataclass
class GateDecision:
    needs_retrieval: bool
    mode: str              # "PARAMETRIC" | "SEMANTIC" | "CODE" | "HYBRID"
    reason: str
    confidence: float = 0.8          # NEW: how confident is this classification?
    alternative_modes: list[str] = None  # NEW: if uncertain, which alternatives?
    decision_trace: Optional[dict] = None  # NEW: for transparency
    
    def __post_init__(self):
        if self.alternative_modes is None:
            self.alternative_modes = []


# URL detection — user gave a direct URL to process
_URL_PATTERN = re.compile(
    r"https?://[^\s]+",
    re.IGNORECASE,
)

# ── FAST-PATH REGEX PATTERNS (0ms classification) ──
# Math/arithmetic — LLM knows this
_MATH_PATTERN = re.compile(
    r"""
    (?:^|\b)(?:
        \d+\s*[\+\-\*/\%\^]\s*\d+              # arithmetic: 2+2, 3*4, etc
        |what\s+(?:is|are)\s+\d+\s*[\+\-\*/]  # "what is 2+2"
        |calculate|compute|solve              # action words
        |(?:square|cube|sqrt|log|sin|cos|tan|integral|derivative)  # math functions
    )
    """,
    re.IGNORECASE | re.VERBOSE
)

# Definition/explanation of common concepts — LLM knows
_DEFINITION_PATTERN = re.compile(
    r"""
    (?:
        (?:what|define|explain|describe)\s+(?:is|are|the|a)  # question starters
        |(?:difference\s+between|how\s+(?:do|does|is))  # comparative/how
        |(?:meaning\s+of|definition\s+of|concept\s+of)
    )
    """,
    re.IGNORECASE | re.VERBOSE
)

# Programming syntax/language concepts — need CODE or SEMANTIC
_SYNTAX_PATTERN = re.compile(
    r"""
    (?:
        python\s+syntax|javascript\s+(?:syntax|how\s+to)
        |what\s+(?:is|are|does)\s+(?:a|an)?\s*(?:loop|function|class|async|await|var|const)
        |how\s+(?:to|do\s+i)\s+(?:write|define|create|declare)\s+a
        |(?:for|while|if|else|try|catch|function|def|class|interface)\s+(?:in|syntax)
    )
    """,
    re.IGNORECASE | re.VERBOSE
)

# Current events / time-sensitive queries → SEMANTIC
_CURRENT_EVENTS_PATTERN = re.compile(
    r"""
    (?:
        today|tonight|right\s+now|now|latest|recent|current|live
        |(?:latest|recent|current|live)\s+(?:news|events|updates|information|data|prices?|rates?|quotes?|scores?)
        |what\s+(?:is|are|happened)\s+(?:today|right\s+now)
        |2024|2025|2026|2027|2028|2029          # recent/future years
        |yesterday|tomorrow|this\s+(?:week|month|year)
        |real-time|real\s+time|realtime
    )
    """,
    re.IGNORECASE | re.VERBOSE
)

# Real-time data queries (market, weather, news, sports, crypto)
_REAL_TIME_DATA_PATTERN = re.compile(
    r"""
    (?:
        (?:stock|crypto|bitcoin|ethereum|forex|currency|exchange|rate|weather|temperature|precipitation|news|sports?|game|match|score)
        (?:\s+(?:price|rate|trend|forecast|today|now|live|current|update|data|quote))?
        |(?:weather|temperature|rain|snow|wind|forecast)\s+(?:today|tonight|tomorrow|now|this\s+(?:week|weekend))
        |(?:sports?|game|match|score|league)\s+(?:today|tonight|tomorrow|live|score|result|update)
        |(?:news|headline|event|update|breaking)\s+(?:today|now|latest|recent)
        |(?:cryptocurrency|crypto|coin|token|altcoin)\s+(?:price|trend|trading|market)
        |(?:oil|gold|silver|copper|commodity)\s+(?:price|rate|trend|market)
        |(?:forex|currency|exchange)\s+(?:rate|pair|trading)
    )
    """,
    re.IGNORECASE | re.VERBOSE
)

# Financial/market queries (needs current data)
_FINANCIAL_PATTERN = re.compile(
    r"""
    (?:
        (?:stock|crypto|bitcoin|ethereum|forex|currency|bond|commodity|gold|oil|silver|platinum|precious)\b
        |(?:price|rate|valuation|dividend|ipo|market|trading|broker|exchange|nasdaq|nyse)\b
        |(?:portfolio|investment|hedge|futures?|options?|etf|mutual\s+fund)\b
        |(?:gdp|inflation|interest|unemployment|recession|economic|bull\s+market|bear\s+market)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE
)

# Code implementation/libraries — need CODE retrieval
_CODE_PATTERN = re.compile(
    r"""
    (?:
        (?:example|sample|code|implementation|library|package|module|npm|pip|cargo)
        (?:\s+of)?|\bhow\s+to\s+implement\b
        |github|repository|npm\s+package|python\s+library
        |(?:write|build|create)\s+(?:a|an|the)?\s*(?:api|server|client|function|class)
    )
    """,
    re.IGNORECASE | re.VERBOSE
)


def _dynamic_intent_classifier(query: str) -> Optional[str]:
    """Dynamic semantic intent detection (no LLM). Returns mode or None if ambiguous.
    
    Uses multi-layer pattern matching with priority ordering to classify queries accurately.
    Returns: "PARAMETRIC" | "SEMANTIC" | "CODE" | None (for LLM fallback)
    """
    # LAYER 1: PARAMETRIC (no retrieval)
    if _MATH_PATTERN.search(query):
        return "PARAMETRIC"
    if _DEFINITION_PATTERN.search(query) and not _CODE_PATTERN.search(query):
        return "PARAMETRIC"
    
    # LAYER 2: SEMANTIC (must retrieve) — checked BEFORE CODE to prioritize real-time data
    # Real-time data takes highest priority
    if _REAL_TIME_DATA_PATTERN.search(query):
        return "SEMANTIC"  # Market, weather, news, sports ALWAYS need current data
    
    if _FINANCIAL_PATTERN.search(query):
        # Financial queries need retrieval unless asking pure conceptual question
        if not _DEFINITION_PATTERN.search(query):
            return "SEMANTIC"
    
    if _CURRENT_EVENTS_PATTERN.search(query):
        return "SEMANTIC"
    
    # LAYER 3: CODE (code examples, implementations)
    if _SYNTAX_PATTERN.search(query):
        return "CODE"
    if _CODE_PATTERN.search(query):
        return "CODE"
    
    # Ambiguous — return None for LLM fallback
    return None


def _regex_fast_path(query: str) -> Optional[GateDecision]:
    """Fast-path classification using regex patterns (0ms, no API call).
    
    Uses dynamic intent classifier for accurate multi-layer classification.
    Returns decision with high confidence (regex match is reliable).
    """
    # Use dynamic intent classifier
    intent = _dynamic_intent_classifier(query)
    
    if intent == "PARAMETRIC":
        return GateDecision(
            needs_retrieval=False,
            mode="PARAMETRIC",
            reason="parametric_regex",
            confidence=0.95,  # Regex match is very reliable
        )
    
    if intent == "SEMANTIC":
        # Determine specific reason
        if _REAL_TIME_DATA_PATTERN.search(query):
            reason = "realtime_data_regex"
        elif _FINANCIAL_PATTERN.search(query):
            reason = "financial_regex"
        else:
            reason = "semantic_regex"
        
        return GateDecision(
            needs_retrieval=True,
            mode="SEMANTIC",
            reason=reason,
            confidence=0.92,  # Regex match is very reliable
        )
    
    if intent == "CODE":
        return GateDecision(
            needs_retrieval=True,
            mode="CODE",
            reason="code_regex",
            confidence=0.90,  # Regex match is very reliable
        )

    # No fast-path match — fall through to LLM
    return None


_LLM_SYSTEM_TEMPLATE = """Decide how to route a user query. Respond with ONLY a JSON object:

- "needs_retrieval": true/false
- "mode": "PARAMETRIC" | "SEMANTIC" | "CODE" | "HYBRID" | "SKILL" | "URL_DIRECT"
- "reason": one short phrase

Classification rules:
- PARAMETRIC: Standard algorithms, language syntax, common patterns, math/logic. LLM already knows these.
- SEMANTIC: Needs conceptual explanation, documentation, research papers, current events.
- CODE: Needs real-world code examples, niche libraries, domain-specific tools.
- HYBRID: Needs both code examples AND conceptual grounding.
- SKILL: User wants to CREATE/GENERATE a deliverable (presentation, report, website, analysis, review). Available skills: {skills}
- URL_DIRECT: User provided a specific URL to fetch/scrape/read.

IMPORTANT: If the user asks to "create", "make", "build", "generate" a document/presentation/website/report — use SKILL mode."""


def _build_llm_prompt() -> str:
    """Build gate LLM prompt dynamically with available skill names."""
    try:
        from ..skills.registry import get_skill_registry
        registry = get_skill_registry()
        skills_str = ", ".join(registry.skill_names) if registry.skill_names else "none registered"
    except Exception:
        skills_str = "deck_builder, report_builder, website_builder, data_analyzer, code_reviewer"
    return _LLM_SYSTEM_TEMPLATE.format(skills=skills_str)


async def entry_gate(
    query: str,
    *,
    client: Optional[NIMClient] = None,
    cache: Optional[LLMCache] = None,
) -> GateDecision:
    """Classify query complexity. Uses fast-paths in order:
    1. URL detection (most specific)
    2. Skill matching (user wants deliverable)
    3. Regex patterns (math, definitions, current events) ← RESTORED
    4. LLM fallback (for truly ambiguous cases)
    """

    # ── URL direct fast-path (FIRST — most specific) ──
    url_match = _URL_PATTERN.search(query)
    if url_match:
        return GateDecision(
            needs_retrieval=True,
            mode="URL_DIRECT",
            reason=f"Direct URL detected: {url_match.group()[:60]}",
            confidence=0.99,  # URL match is definitive
        )

    # ── Skill matching (before LLM — user wants deliverable) ──
    try:
        from ..skills.registry import get_skill_registry
        registry = get_skill_registry()
        skill_match = registry.match(query)
        if skill_match and skill_match.score >= 0.15:
            return GateDecision(
                needs_retrieval=False,
                mode="SKILL",
                reason=f"Skill matched: {skill_match.name} (score={skill_match.score:.2f})",
                confidence=min(0.95, skill_match.score),  # Confidence based on skill match score
            )
    except Exception as e:
        logger.debug("Skill matching failed: %s", e)

    # ── Regex fast-paths (0ms, no API call) — RESTORED ──
    regex_decision = _regex_fast_path(query)
    if regex_decision:
        return regex_decision

    # ── LLM fallback for truly ambiguous queries ──
    client = client or get_client()
    cache = cache or get_llm_cache()

    system_prompt = _build_llm_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    # Check cache
    cached = cache.get(query, "entry_gate", system_prompt)
    if cached:
        parsed = _parse(cached)
        return _to_decision(parsed)

    try:
        raw = await client.chat_fast(
            messages, temperature=0.0, response_format_json=True, max_tokens=100,
        )
        cache.set(query, "entry_gate", raw, system_prompt)
        parsed = _parse(raw)
        decision = _to_decision(parsed)
        # LLM decisions have moderate confidence (lower than regex)
        decision.confidence = parsed.get("confidence", 0.75)
        return decision
    except Exception as e:
        logger.warning("Entry gate LLM failed: %s, defaulting to retrieval", e)
        return GateDecision(
            needs_retrieval=True,
            mode="SEMANTIC",
            reason="gate_llm_failed_safe",
            confidence=0.5,  # Low confidence on fallback
        )


def _parse(raw: str) -> dict:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"needs_retrieval": True, "mode": "SEMANTIC", "reason": "parse_error_failed_safe"}


def _to_decision(parsed: dict) -> GateDecision:
    mode = parsed.get("mode", "SEMANTIC")
    if mode not in ("PARAMETRIC", "SEMANTIC", "CODE", "HYBRID", "SKILL", "URL_DIRECT"):
        mode = "SEMANTIC"
    return GateDecision(
        needs_retrieval=parsed.get("needs_retrieval", True),
        mode=mode,
        reason=parsed.get("reason", ""),
    )
