"""
Global synthesis LLM — the single accumulator over all leaf learnings.

KEY PHILOSOPHY from chats:
  1. NOT a flat list — connect related facts into narrative
  2. Hybrid ordering: chronological/logical structure FIRST, then
     flag genuinely surprising facts within that structure
  3. DOB/age redundancy: prefer fundamental facts over derivable ones
     (if you know DOB, don't separately state age — it's derivable)
  4. Fill gaps: if learnings have holes, the LLM can use its own
     knowledge to bridge them, but MUST flag what's from retrieval
     vs what's from its own knowledge
  5. Presentation quality matters — this is what the user sees

Two delivery modes:
  - Non-streaming: returns full answer at once
  - Streaming: yields token deltas (for WebSocket/SSE)
"""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator, Optional

from .client import NIMClient, get_client
from .persona import build_persona_prompt
from ..core.types import Learning
from ..config.settings import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are the final synthesis step of a recursive research agent. You "
    "receive the original query and every learning gathered across all "
    "search branches, tagged with sources and ordered by relevance.\n\n"
    "Your job is to write ONE coherent, well-structured answer that "
    "directly addresses the query. Follow these principles:\n\n"
    "1. NARRATIVE OVER LIST: Connect related facts into a real story. "
    "If several learnings relate to the same event, entity, or timeline, "
    "present them together in logical/chronological order — not as "
    "disconnected bullet points.\n\n"
    "2. HYBRID SURPRISAL: Use a logical structure as the backbone, but "
    "explicitly flag genuinely surprising or counterintuitive findings "
    "within that structure. Don't lead with ALL the surprises and don't "
    "bury them — weave them naturally where they fit chronologically or "
    "logically, but call them out (e.g., 'Unexpectedly, ...' or "
    "'Contrary to common belief, ...').\n\n"
    "3. REDUNDANCY ELIMINATION: Prefer fundamental facts over derivable "
    "ones. If you state a date of birth, don't separately state the age "
    "— it's derivable. If you explain a mechanism, don't also restate "
    "the obvious consequence. Every sentence should add NEW information.\n\n"
    "4. GAP BRIDGING: If the learnings have gaps that your own knowledge "
    "can fill to make the narrative coherent, you may do so, but you "
    "MUST mark what came from the retrieved learnings vs your own "
    "knowledge (e.g., 'Based on the retrieved sources...' vs "
    "'From general knowledge...'). Never present your own knowledge "
    "as if it came from the sources.\n\n"
    "5. DEPTH MATCHING: Match the depth and technicality of your answer "
    "to what the query itself signals. A detailed technical prompt "
    "deserves a detailed technical answer. A casual question gets a "
    "concise, accessible answer. Don't over-explain to experts or "
    "under-explain to beginners.\n\n"
    "6. SOURCE ATTRIBUTION: When making specific factual claims, "
    "attribute them to their source where possible. This builds trust "
    "and lets the user verify."
)


def _estimate_tokens(text: str) -> int:
    """Rough token estimation: ~4 characters per token."""
    return max(1, len(text) // 4)


def _calculate_adaptive_max_tokens(
    query: str,
    learnings: list[Learning],
    prompt_specificity: str = "standard",
) -> int:
    """Dynamically calculate max_tokens based on query and learnings complexity (Phase 3).
    
    Returns adaptive token limit:
    - Simple query with few learnings: 1024
    - Medium query with moderate learnings: 2048-3000
    - Complex query with many learnings: 3000-4096
    """
    query_tokens = _estimate_tokens(query)
    learnings_tokens = sum(_estimate_tokens(l.text) for l in learnings)
    total_context_tokens = query_tokens + learnings_tokens
    
    # Base calculation: allocate based on complexity
    if total_context_tokens < 1000:
        base_tokens = 1024  # Simple query
    elif total_context_tokens < 3000:
        base_tokens = 2048  # Medium complexity
    elif total_context_tokens < 6000:
        base_tokens = 3000  # Higher complexity
    else:
        base_tokens = 4096  # Very high complexity (Phase 3 enhancement)
    
    # Technical queries get slightly more tokens for detailed explanations
    if prompt_specificity == "expert":
        return min(4096, base_tokens + 512)
    
    return base_tokens


def _detect_truncation(response: str) -> bool:
    """Detect if response appears to be truncated (Phase 3).
    
    Heuristics:
    - Ends with incomplete sentence (common patterns)
    - Ends with "[" or "(" or "**" suggesting incomplete markup
    - Length suggests max_tokens was hit
    """
    if not response or len(response) < 100:
        return False
    
    # Check for incomplete markup/formatting
    incomplete_patterns = [
        r'\[\s*$',  # Unclosed [
        r'\(\s*$',  # Unclosed (
        r'\*\*\s*$',  # Unclosed **
        r'-\s*$',  # Ends with dash (list item)
        r':\s*$',  # Ends with colon (incomplete statement)
    ]
    
    for pattern in incomplete_patterns:
        if re.search(pattern, response):
            logger.info("Truncation detected: incomplete markup")
            return True
    
    # Check if ends with word fragment (last word seems cut off)
    last_word = response.split()[-1] if response.split() else ""
    if last_word and len(last_word) == 1:  # Single character = fragment
        return True
    
    return False


def _build_synthesis_prompt(
    query: str,
    learnings: list[Learning],
    prompt_specificity: str = "standard",
) -> list[dict]:
    """Build the synthesis prompt. Mechanical ordering by score,
    with prompt-specificity hint for depth calibration."""
    ordered = sorted(learnings, key=lambda l: l.score, reverse=True)
    learnings_block = "\n\n".join(
        f"- [{l.source_url or 'source unknown'}] {l.text}" for l in ordered
    ) or "(no learnings gathered)"

    specificity_hint = ""
    if prompt_specificity == "expert":
        specificity_hint = "\n\nNote: This query appears to be from someone with domain expertise. Provide deep technical detail."
    elif prompt_specificity == "casual":
        specificity_hint = "\n\nNote: This query appears to be a casual/general question. Be concise and accessible."

    # Jarvis persona + synthesis rules combined
    persona = build_persona_prompt(prompt_specificity)
    full_system = persona + "\n\n--- SYNTHESIS RULES ---\n\n" + _SYSTEM_PROMPT

    return [
        {"role": "system", "content": full_system},
        {"role": "user", "content": f"Query: {query}{specificity_hint}\n\nLearnings:\n{learnings_block}"},
    ]


async def global_synthesis_llm(
    query: str,
    learnings: list[Learning],
    *,
    client: Optional[NIMClient] = None,
    prompt_specificity: str = "standard",
) -> str:
    """Non-streaming — returns the full answer at once.
    
    Phase 3 Enhancement: Uses adaptive token limits and truncation detection.
    """
    client = client or get_client()
    
    # Calculate adaptive max tokens (Phase 3)
    adaptive_tokens = _calculate_adaptive_max_tokens(query, learnings, prompt_specificity)
    logger.info(f"Synthesis using adaptive max_tokens: {adaptive_tokens}")
    
    messages = _build_synthesis_prompt(query, learnings, prompt_specificity)
    
    # Try with adaptive tokens
    response = await client.chat(
        messages,
        temperature=0.2,
        max_tokens=adaptive_tokens,
    )
    
    # Check for truncation and retry with higher limit (Phase 3)
    if _detect_truncation(response):
        logger.warning(f"Response truncated at {adaptive_tokens} tokens, retrying with higher limit")
        higher_tokens = min(4096, adaptive_tokens + 1024)
        
        response = await client.chat(
            messages,
            temperature=0.2,
            max_tokens=higher_tokens,
        )
        
        # If still truncated, log but return as-is
        if _detect_truncation(response):
            logger.warning(f"Response still truncated even at {higher_tokens} tokens")
    
    return response



async def global_synthesis_llm_stream(
    query: str,
    learnings: list[Learning],
    *,
    client: Optional[NIMClient] = None,
    prompt_specificity: str = "standard",
) -> AsyncIterator[str]:
    """Streaming — yields text deltas with adaptive token limits (Phase 3)."""
    client = client or get_client()
    
    # Use adaptive tokens for streaming as well
    adaptive_tokens = _calculate_adaptive_max_tokens(query, learnings, prompt_specificity)
    
    async for delta in client.chat_stream(
        _build_synthesis_prompt(query, learnings, prompt_specificity),
        temperature=0.2,
        max_tokens=adaptive_tokens,
    ):
        yield delta


async def direct_answer_llm(
    query: str,
    *,
    client: Optional[NIMClient] = None,
    prompt_specificity: str = "standard",
) -> str:
    """No-retrieval path. Entry_gate decided no grounding needed."""
    client = client or get_client()

    specificity_hint = ""
    if prompt_specificity == "expert":
        specificity_hint = " Provide deep technical detail appropriate for a domain expert."
    elif prompt_specificity == "casual":
        specificity_hint = " Be concise and accessible."

    return await client.chat(
        [{"role": "user", "content": query + specificity_hint}],
        temperature=0.3,
        max_tokens=1024,
    )


async def direct_answer_llm_stream(
    query: str,
    *,
    client: Optional[NIMClient] = None,
    prompt_specificity: str = "standard",
) -> AsyncIterator[str]:
    """Streaming direct answer — no retrieval path."""
    client = client or get_client()

    specificity_hint = ""
    if prompt_specificity == "expert":
        specificity_hint = " Provide deep technical detail appropriate for a domain expert."
    elif prompt_specificity == "casual":
        specificity_hint = " Be concise and accessible."

    async for delta in client.chat_stream(
        [{"role": "user", "content": query + specificity_hint}],
        temperature=0.3,
        max_tokens=1024,
    ):
        yield delta


# ── TIER 2: Zoom level support ──

async def synthesis_at_zoom_level(
    query: str,
    learnings: list[Learning],
    zoom_level: str = "overview",
    *,
    client: Optional[NIMClient] = None,
) -> str:
    """
    Generate synthesis at a specific zoom level (Tier 2 feature).
    
    Zoom levels:
    - "overview" (Level 0): ~300 tokens, high-level summary
    - "focused" (Level 1): ~800 tokens, focused detail with examples
    - "comprehensive" (Level 2): ~2000 tokens, full treatment
    """
    from .synthesis_levels import get_zoom_config, ZoomLevel
    
    if client is None:
        client = get_client()
    
    # Map string to ZoomLevel enum
    level_map = {
        "overview": ZoomLevel.LEVEL_0,
        "focused": ZoomLevel.LEVEL_1,
        "comprehensive": ZoomLevel.LEVEL_2,
    }
    zoom = level_map.get(zoom_level, ZoomLevel.LEVEL_0)
    config = get_zoom_config(zoom)
    
    # Build learnings block with source attribution
    learnings_block = "\n".join(
        f"- [{l.source}] {l.text}" if l.source else f"- {l.text}"
        for l in learnings
    )
    
    # Add zoom-level specific instructions to system prompt
    zoom_prompt = f"{_SYSTEM_PROMPT}\n\n--- ZOOM LEVEL {config.level.value.upper()} ---\n{config.depth_instructions}"
    
    persona = build_persona_prompt("standard")
    full_system = persona + "\n\n" + zoom_prompt
    
    answer = await client.chat(
        [
            {"role": "system", "content": full_system},
            {
                "role": "user",
                "content": f"Query: {query}\n\nLearnings:\n{learnings_block}"
            },
        ],
        temperature=0.3,
        max_tokens=config.token_budget,
    )
    
    return answer

