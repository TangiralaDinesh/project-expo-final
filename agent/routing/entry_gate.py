"""
Entry gate — the original "hallucination threshold."

Decides: answer directly from parametric knowledge, or retrieve first?
Combines regex fast-path (0ms for 90% of queries) from github_researchtool.py
with Groq+LLM fallback for ambiguous cases.

Six-way classification:
  - PARAMETRIC: stable algorithms, established concepts — no retrieval
  - SEMANTIC: needs conceptual docs, articles — semantic retrieval
  - CODE: needs real-world implementation examples — code retrieval
  - HYBRID: needs both code + conceptual grounding
  - SKILL: matches a Jarvis skill (deck builder, report builder, etc.)
  - URL_DIRECT: user gave a URL directly — skip search, fetch directly

Fails TOWARD retrieval on ambiguity — unnecessary search costs latency,
a skipped necessary one costs a hallucinated answer.

REGEX FAST-PATHS (0ms, no API call):
  - Math/arithmetic → PARAMETRIC
  - Definition/explanation → PARAMETRIC
  - Syntax/language → PARAMETRIC
  - Current events/time-sensitive → SEMANTIC
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
    """
    query_lower = query.lower()
    
    # Use dynamic intent classifier
    intent = _dynamic_intent_classifier(query)
    
    if intent == "PARAMETRIC":
        return GateDecision(
            needs_retrieval=False,
            mode="PARAMETRIC",
            reason="parametric_regex",
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
        )
    
    if intent == "CODE":
        return GateDecision(
            needs_retrieval=True,
            mode="CODE",
            reason="code_regex",
        )

    # No fast-path match — fall through to LLM
    return None

_LLM_SYSTEM_TEMPLATE = """Decide how to route a user query. Respond with ONLY a JSON object:

- "needs_retrieval": true/false
- "mode": "PARAMETRIC" | "SEMANTIC" | "CODE" | "HYBRID" | "SKILL" | "URL_DIRECT"
- "reason": one short phrase

Classification rules (PRIORITY ORDER):

1. SKILL MODE (user wants to CREATE/GENERATE something):
   - Examples: "create a presentation", "build a website", "generate a report", "make a deck"
   - Available skills: {skills}

2. URL_DIRECT (user provided a specific URL):
   - Fetch and analyze the URL directly

3. SEMANTIC MODE (needs current/real-time information or conceptual research):
   - Real-time data: stock prices, crypto rates, weather, news, sports scores, breaking events
   - Financial queries: "bitcoin price", "S&P 500 today", "oil prices", "forex rates", "interest rates"
   - Market data: commodities, stocks, forex, crypto, ETFs, bonds
   - Current events: "what happened today", "latest news", "breaking news", "recent events"
   - Time-sensitive: queries with "live", "today", "current", "latest", "recent", "now"
   - Research needs: articles, papers, explanations of complex topics
   - Examples:
     * "cdsl live stock price" → SEMANTIC (market data needs real-time lookup)
     * "bitcoin price" → SEMANTIC (volatile, needs current rates)
     * "weather today" → SEMANTIC (current forecast needed)
     * "latest tech news" → SEMANTIC (needs current sources)

4. PARAMETRIC MODE (LLM already knows this, no retrieval needed):
   - Established concepts: "what is machine learning", "define recursion", "explain photosynthesis"
   - Math/logic: arithmetic, basic algorithms, language fundamentals
   - Historical facts with stable knowledge: "who was Einstein", "what caused WW2"
   - Note: Use PARAMETRIC ONLY for things that don't change. If it involves current data, prices, trends, or recent events, use SEMANTIC

5. CODE MODE (needs code examples and implementation):
   - Programming syntax: "how to write a loop", "javascript async/await"
   - Libraries/frameworks: "python numpy example", "react hooks tutorial"
   - Real-world implementation: "github ssh setup", "build a REST API"
   - Code patterns: "design patterns", "optimization techniques"

6. HYBRID MODE (needs both code AND conceptual grounding):
   - Example: "implement OAuth2 with S256" (needs BOTH concepts AND working code)

CRITICAL: 
- If ANY query involves real-time data, current prices, live markets, recent events, or "today/now" → use SEMANTIC
- If query mentions "price", "rate", "stock", "crypto", "market", "live", "current" → likely SEMANTIC
- Default to retrieval when uncertain (fail toward search, not hallucination)
"""


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
    use_groq_for_semantic: bool = True,
) -> GateDecision:
    """
    Classify query complexity. Uses fast-paths in order:
    1. URL detection (most specific)
    2. Skill matching (user wants deliverable)
    3. Regex patterns (math, definitions, current events)
    4. Groq fast semantic classification (cheap, non-thinking)
    5. NIM LLM fallback (expensive, for truly ambiguous cases)
    """

    # ── URL direct fast-path (FIRST — most specific) ──
    url_match = _URL_PATTERN.search(query)
    if url_match:
        return GateDecision(
            needs_retrieval=True,
            mode="URL_DIRECT",
            reason=f"Direct URL detected: {url_match.group()[:60]}",
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
                reason=f"Skill matched: {skill_match.skill.name} (score={skill_match.score:.2f})",
            )
    except Exception as e:
        logger.debug("Skill matching failed: %s", e)

    # ── Regex fast-paths (0ms, no API call) ──
    regex_decision = _regex_fast_path(query)
    if regex_decision:
        return regex_decision

    # ── Groq fast semantic classification (cheap, non-thinking) ──
    client = client or get_client()
    cache = cache or get_llm_cache()

    if use_groq_for_semantic and client._cfg.groq_api_keys:
        try:
            return await _classify_via_groq(query, client, cache)
        except Exception as e:
            logger.debug("Groq classification failed: %s, falling back to NIM", e)

    # ── NIM LLM fallback for truly ambiguous queries ──
    try:
        return await _classify_via_nim(query, client, cache)
    except Exception as e:
        logger.warning("Entry gate LLM failed: %s, defaulting to retrieval", e)
        return GateDecision(
            needs_retrieval=True,
            mode="SEMANTIC",
            reason="gate_llm_failed_safe",
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


async def _classify_via_groq(
    query: str,
    client: NIMClient,
    cache: LLMCache,
) -> GateDecision:
    """Fast semantic classification via Groq (cheap, non-thinking LLM)."""
    system_prompt = _build_llm_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    # Check cache first
    cached = cache.get(query, "entry_gate_groq", system_prompt)
    if cached:
        parsed = _parse(cached)
        return _to_decision(parsed)

    try:
        # Use Groq worker for fast, cheap classification
        raw = await client.chat_worker(
            messages, temperature=0.0, response_format_json=True, max_tokens=100,
        )
        cache.set(query, "entry_gate_groq", raw, system_prompt)
        parsed = _parse(raw)
        return _to_decision(parsed)
    except Exception as e:
        logger.debug("Groq classification failed: %s", e)
        raise


async def _classify_via_nim(
    query: str,
    client: NIMClient,
    cache: LLMCache,
) -> GateDecision:
    """Fallback semantic classification via NVIDIA NIM (expensive, for ambiguous cases)."""
    system_prompt = _build_llm_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    # Check cache
    cached = cache.get(query, "entry_gate_nim", system_prompt)
    if cached:
        parsed = _parse(cached)
        return _to_decision(parsed)

    # Use NIM fast chat (shorter timeout)
    raw = await client.chat_fast(
        messages, temperature=0.0, response_format_json=True, max_tokens=100,
    )
    cache.set(query, "entry_gate_nim", raw, system_prompt)
    parsed = _parse(raw)
    return _to_decision(parsed)
