"""
Query Fan-Out — AI Mode-inspired parallel sub-query generation.

Instead of one search, breaks the question into several parallel sub-queries:
  1. Semantic variants (synonyms, rephrasings)
  2. Aspect-driven (existing dimension decomposition)
  3. Angle variants (same concept, different perspective)

Key innovation: Pilot-then-expand with Information Entropy scoring.
  - Fire ALL sub-queries at depth=1 (shallow pilot, ~2 results each)
  - Score each pilot by information gain (new info vs what we already know)
  - Expand only top-50% by EIG into full-depth retrieval
  - Saves 40-50% token budget on complex queries

Deduplication: embed sub-queries, cluster by cosine similarity (>0.85), keep one per cluster.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..llm.client import NIMClient, get_client

logger = logging.getLogger(__name__)


@dataclass
class FanOutPlan:
    """Complete fan-out plan with all sub-query variants."""
    original_query: str
    semantic_variants: list[str] = field(default_factory=list)
    aspect_queries: list[str] = field(default_factory=list)
    angle_queries: list[str] = field(default_factory=list)
    # After pilot:
    pilot_scores: dict[str, float] = field(default_factory=dict)
    selected_queries: list[str] = field(default_factory=list)
    dedup_removed: list[str] = field(default_factory=list)

    @property
    def all_queries(self) -> list[str]:
        """All unique queries before dedup."""
        seen = set()
        result = []
        for q in self.semantic_variants + self.aspect_queries + self.angle_queries:
            key = q.lower().strip()
            if key not in seen and key:
                seen.add(key)
                result.append(q)
        return result


async def generate_semantic_variants(
    query: str,
    *,
    client: Optional[NIMClient] = None,
    num_variants: int = 3,
) -> list[str]:
    """Generate semantic variants (synonyms, rephrasings) of a query.

    Example:
        "OAuth2 PKCE" → ["OAuth2 code verifier", "PKCE S256 challenge",
                          "authorization code flow with proof key"]
    """
    client = client or get_client()

    prompt = f"""Generate {num_variants} alternative search queries that would find the SAME information as the original query but using different words.

Original query: "{query}"

Example: If query is "best laptop for programming 2024"
Answer: ["top developer laptops 2024 coding", "programmer notebook recommendations", "software development laptop reviews"]

Now generate for MY query. Return ONLY a JSON array of strings:"""

    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a search query expansion engine. Output strictly a raw JSON array of search strings. "
                    "CRITICAL: Do NOT output conversational monologue, 'Here\\'s a thinking process', or preamble. "
                    "Do NOT use markdown code blocks. Start your response IMMEDIATELY with '[' and end with ']'."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = await client.chat_worker(
            messages,
            temperature=0.3,
            max_tokens=256,
            response_format_json=True,
        )

        import json
        match = re.search(r'\[.*?\]', response, re.DOTALL)
        if match:
            variants = json.loads(match.group())
            # Filter out template placeholders
            clean = [str(v).strip() for v in variants if v and str(v).strip() and len(str(v)) > 5]
            template_words = {"variant 1", "variant 2", "variant 3", "query 1", "query 2"}
            clean = [v for v in clean if v.lower() not in template_words]
            return clean[:num_variants]
    except Exception as e:
        logger.warning("Semantic variant generation failed: %s", e)

    return []


async def generate_angle_queries(
    query: str,
    *,
    client: Optional[NIMClient] = None,
    num_angles: int = 3,
) -> list[str]:
    """Generate angle variants — same concept, different perspectives.

    Example:
        "Should I use React?" → ["React advantages and benefits",
                                   "React limitations and drawbacks",
                                   "React alternatives comparison"]
    """
    client = client or get_client()

    prompt = f"""For this query, generate {num_angles} follow-up search queries that explore DIFFERENT ANGLES of the same topic.

Query: "{query}"

Example: If query is "Should I use React for my project?"
Answer: ["React framework advantages benefits 2024", "React limitations performance issues", "React vs Vue vs Angular comparison"]

Now generate for MY query. Return ONLY a JSON array of strings:"""

    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a multi-angle research query generator. Output strictly a raw JSON array of search strings. "
                    "CRITICAL: Do NOT output conversational monologue, 'Here\\'s a thinking process', or preamble. "
                    "Do NOT use markdown code blocks. Start your response IMMEDIATELY with '[' and end with ']'."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response = await client.chat_worker(
            messages,
            temperature=0.3,
            max_tokens=256,
            response_format_json=True,
        )

        import json
        match = re.search(r'\[.*?\]', response, re.DOTALL)
        if match:
            angles = json.loads(match.group())
            # Filter out template placeholders
            clean = [str(a).strip() for a in angles if a and str(a).strip() and len(str(a)) > 5]
            template_words = {"angle query 1", "angle query 2", "angle query 3", "query 1", "query 2"}
            clean = [a for a in clean if a.lower() not in template_words]
            return clean[:num_angles]
    except Exception as e:
        logger.warning("Angle query generation failed: %s", e)

    return []


def deduplicate_queries(
    queries: list[str],
    threshold: float = 0.85,
) -> tuple[list[str], list[str]]:
    """Deduplicate queries by term-overlap similarity.

    Uses Jaccard similarity on word sets (fast, no embeddings needed).
    Keeps the first occurrence in each cluster.

    Args:
        queries: List of query strings
        threshold: Similarity threshold for dedup (0.85 = very similar)

    Returns:
        (kept_queries, removed_queries)
    """
    if len(queries) <= 1:
        return queries, []

    def _term_set(q: str) -> set[str]:
        return set(w.lower().strip(".,!?\"'") for w in q.split() if len(w) > 2)

    kept: list[str] = []
    removed: list[str] = []

    for query in queries:
        query_terms = _term_set(query)
        is_dup = False

        for existing in kept:
            existing_terms = _term_set(existing)
            # Jaccard similarity
            if query_terms and existing_terms:
                intersection = len(query_terms & existing_terms)
                union = len(query_terms | existing_terms)
                similarity = intersection / union if union > 0 else 0
                if similarity >= threshold:
                    is_dup = True
                    break

        if is_dup:
            removed.append(query)
        else:
            kept.append(query)

    if removed:
        logger.info("Dedup removed %d/%d queries: %s", len(removed), len(queries), removed[:3])

    return kept, removed


async def fan_out_query(
    query: str,
    *,
    client: Optional[NIMClient] = None,
    max_total_queries: int = 7,
    existing_aspect_queries: Optional[list[str]] = None,
) -> FanOutPlan:
    """Generate diverse sub-queries for parallel retrieval.

    Three strategies run in parallel:
    1. Semantic variants (synonyms, rephrasings)
    2. Aspect queries (from existing SubqueryGenerator, passed in)
    3. Angle queries (pros/cons/alternatives/examples)

    After generation, deduplicates by term overlap.

    Args:
        query: Original user query
        client: LLM client
        max_total_queries: Maximum total sub-queries after dedup
        existing_aspect_queries: Aspect/dimension queries from SubqueryGenerator

    Returns:
        FanOutPlan with all variants + dedup info
    """
    client = client or get_client()

    plan = FanOutPlan(original_query=query)

    # Generate all variants in parallel
    semantic_task = generate_semantic_variants(query, client=client, num_variants=3)
    angle_task = generate_angle_queries(query, client=client, num_angles=3)

    results = await asyncio.gather(semantic_task, angle_task, return_exceptions=True)

    if isinstance(results[0], list):
        plan.semantic_variants = results[0]
    else:
        logger.warning("Semantic variant generation failed: %s", results[0])
    if isinstance(results[1], list):
        plan.angle_queries = results[1]
    else:
        logger.warning("Angle query generation failed: %s", results[1])

    # Fallback: if BOTH LLM calls failed, generate simple rule-based variants
    if not plan.semantic_variants and not plan.angle_queries:
        logger.info("Fan-out fallback: generating rule-based variants for '%s'", query[:50])
        words = query.split()
        if len(words) > 3:
            # Simple angle variants
            plan.angle_queries = [
                f"{query} detailed analysis",
                f"{query} examples and data",
            ]
            # If it looks like a comparison, add per-entity queries
            if any(w.lower() in ('vs', 'versus', 'compared', 'or') for w in words):
                entities = [w for w in words if w[0].isupper() and len(w) > 2]
                for entity in entities[:2]:
                    plan.angle_queries.append(f"{entity} filmography box office results")

    # Include existing aspect queries from SubqueryGenerator
    if existing_aspect_queries:
        plan.aspect_queries = existing_aspect_queries

    # Deduplicate all queries together
    all_queries = [query] + plan.all_queries  # Original always first
    kept, removed = deduplicate_queries(all_queries, threshold=0.75)
    plan.dedup_removed = removed

    # Cap at max_total_queries
    plan.selected_queries = kept[:max_total_queries]

    logger.info(
        "Fan-out: %d semantic + %d angle + %d aspect = %d total → %d after dedup",
        len(plan.semantic_variants), len(plan.angle_queries),
        len(plan.aspect_queries), len(all_queries), len(plan.selected_queries),
    )

    return plan


def score_pilot_results(
    query: str,
    pilot_results: dict[str, list],
    existing_knowledge_terms: Optional[set[str]] = None,
) -> dict[str, float]:
    """Score pilot retrieval results by Expected Information Gain.

    For each sub-query's pilot results, compute how much NEW information
    it adds vs what we already know.

    Args:
        query: Original query
        pilot_results: {sub_query: [learning_texts]} from shallow retrieval
        existing_knowledge_terms: Terms we already know (from previous rounds)

    Returns:
        {sub_query: EIG_score} — higher = more valuable to expand
    """
    if not pilot_results:
        return {}

    existing = existing_knowledge_terms or set()

    scores: dict[str, float] = {}
    all_pilot_terms: set[str] = set()

    for sub_query, results in pilot_results.items():
        if not results:
            scores[sub_query] = 0.0
            continue

        # Extract terms from this sub-query's results
        sub_terms: set[str] = set()
        for result_text in results:
            text = result_text if isinstance(result_text, str) else getattr(result_text, "text", str(result_text))
            for w in text.lower().split():
                clean = "".join(c for c in w if c.isalnum())
                if len(clean) > 3:
                    sub_terms.add(clean)

        # Novelty: how many terms are NOT in existing knowledge
        if sub_terms:
            novel = sub_terms - existing - all_pilot_terms
            novelty_ratio = len(novel) / len(sub_terms)
        else:
            novelty_ratio = 0.0

        # Relevance: overlap with original query terms
        query_terms = set(w.lower() for w in query.split() if len(w) > 3)
        if query_terms and sub_terms:
            relevance = len(query_terms & sub_terms) / len(query_terms)
        else:
            relevance = 0.5

        # EIG = novelty × relevance (novel AND relevant = high value)
        eig = novelty_ratio * 0.6 + relevance * 0.4
        scores[sub_query] = eig

        # Track cumulative terms for inter-query novelty
        all_pilot_terms.update(sub_terms)

    logger.info(
        "Pilot EIG scores: %s",
        {q[:40]: f"{s:.2f}" for q, s in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]},
    )

    return scores


def select_queries_by_eig(
    scores: dict[str, float],
    top_fraction: float = 0.5,
    min_queries: int = 2,
) -> list[str]:
    """Select top sub-queries by EIG score for full-depth expansion.

    Args:
        scores: {sub_query: EIG_score}
        top_fraction: Fraction of queries to keep (0.5 = top 50%)
        min_queries: Minimum queries to keep even if scores are low

    Returns:
        List of selected sub-queries, sorted by EIG descending
    """
    sorted_queries = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    n_keep = max(min_queries, int(len(sorted_queries) * top_fraction))
    selected = [q for q, _ in sorted_queries[:n_keep]]

    logger.info("EIG selection: kept %d/%d queries", len(selected), len(sorted_queries))
    return selected
