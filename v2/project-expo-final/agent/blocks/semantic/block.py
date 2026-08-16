"""
Recursive Semantic Retriever Block — the core engine.

NOT a tool the top LLM calls once — it's a self-contained subsystem that
takes a query OR a direct URL and returns synthesized learnings, recursing
when the per-node decision LLM decides more information is needed.

Pipeline per node:
    input → resolve_sources → chunk → embed → rerank →
    decision_llm → (terminate | spawn children)

Two operating modes:
    - PUBLIC: Brave/DDG search + web fetch only
    - KB: internal knowledge base AND public web in parallel, merged

Recursion gated by TWO signals, not one:
    1. Depth budget (hard ceiling)
    2. Information gain (dynamic — node only recurses if retrieved info
       actually shifts the answer; otherwise terminates early)

Parallel sibling execution: children run concurrently via asyncio.gather.
Global deadline enforcement: checked at every node entry.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from ...llm.client import NIMClient, get_client
from ...config.budgets import (
    DEFAULT_MAX_DEPTH, NODE_TIMEOUT_S, GLOBAL_BUDGET_S,
    MIN_TIME_MARGIN_S, MAX_EXTENSIONS_PER_BRANCH,
    EXTENSION_INCREMENT, TOP_K_RERANK,
)
from .types import BlockInput, Chunk, Decision, NodeResult, Learning, Mode
from .sources import resolve_sources
from .embed import embed_chunks, embed_query
from .rerank import rerank_chunks
from .decision import decision_llm
from ...core.critique import run_multi_critique

logger = logging.getLogger(__name__)


async def _run_critique_on_retrieval(
    query: str,
    top_chunks: list[Chunk],
    *,
    client: Optional[NIMClient] = None,
) -> list[str]:
    """
    Phase 2: Run multi-persona critique on retrieved chunks to identify gaps.
    Returns list of gap-related follow-up queries to explore.
    
    If critique identifies missing concepts, comparison entities, or incomplete coverage,
    this spawns new retrieval queries to fill those gaps.
    """
    client = client or get_client()
    
    if not top_chunks:
        return []
    
    try:
        # Build artifact (retrieved content) for critique
        artifact = "\n\n".join([
            f"[{c.source_url or 'internal'}] {c.text[:500]}"
            for c in top_chunks[:5]
        ])
        
        # Run multi-persona critique
        critique_result = await run_multi_critique(
            goal=f"Answer the query: {query}",
            artifact=artifact,
            client=client,
        )
        
        # If consensus approved, no gaps to explore
        if critique_result.consensus:
            logger.debug(f"Critique consensus: retrieval complete for query '{query[:50]}'")
            return []
        
        # Extract actionable gaps from disagreements (Phase 2)
        gap_queries = []
        gap_patterns = {
            "missing_entity": ["missing", "entity", "missing entity", "incomplete comparison", "both"],
            "incomplete_depth": ["shallow", "lacks depth", "insufficient detail", "more detail"],
            "missing_perspective": ["perspective", "angle", "viewpoint", "doesn't address"],
            "missing_examples": ["example", "concrete", "case study", "practical"],
            "missing_risks": ["risk", "downside", "drawback", "limitation", "danger"],
            "missing_comparison": ["compare", "comparison", "vs", "versus", "alternative"],
        }
        
        gap_categories = {}
        all_concerns = []
        
        # Aggregate concerns by persona
        for result in critique_result.results:
            all_concerns.extend(result.concerns)
        
        # Categorize gaps and deduplicate
        for concern in all_concerns:
            concern_lower = concern.lower()
            categorized = False
            
            for category, keywords in gap_patterns.items():
                if any(kw in concern_lower for kw in keywords):
                    if category not in gap_categories:
                        gap_categories[category] = []
                    gap_categories[category].append(concern)
                    categorized = True
                    break
            
            if not categorized and any(pattern in concern_lower 
                                      for pattern in ["missing", "incomplete", "without", "lacks", 
                                                     "not covered", "overlooked", "not addressed"]):
                gap_categories["other"] = gap_categories.get("other", [])
                gap_categories["other"].append(concern)
        
        # Phase 2: Convert gaps to follow-up queries
        for category, concerns in gap_categories.items():
            # Take top concern for this category
            if concerns:
                top_concern = concerns[0][:150]
                
                if category == "missing_entity":
                    gap_queries.append(
                        f"Explore missing entity: {top_concern}. "
                        f"Ensure fair coverage of all comparison options."
                    )
                elif category == "incomplete_depth":
                    gap_queries.append(
                        f"Provide deeper details on: {top_concern}"
                    )
                elif category == "missing_perspective":
                    gap_queries.append(
                        f"Explore additional perspective: {top_concern}"
                    )
                elif category == "missing_examples":
                    gap_queries.append(
                        f"Find concrete examples and case studies for: {top_concern}"
                    )
                elif category == "missing_risks":
                    gap_queries.append(
                        f"Identify risks, downsides and limitations of: {top_concern}"
                    )
                elif category == "missing_comparison":
                    gap_queries.append(
                        f"Compare and contrast: {top_concern}"
                    )
                else:
                    gap_queries.append(f"Address gap: {top_concern}")
                
                logger.info(f"Critique identified {category} gap: {top_concern[:80]}")
        
        # Phase 2: Limit to avoid infinite loops
        return gap_queries[:3]
    
    except Exception as e:
        logger.debug(f"Critique-guided retrieval failed: {e}")
        return []


async def semantic_retriever_block(
    inp: BlockInput,
    *,
    client: Optional[NIMClient] = None,
    kb_search_fn=None,
) -> NodeResult:
    """
    One recursive node. Enforces:
      - Per-node timeout (node_timeout_s)
      - Global deadline across the whole tree
      - Depth budget AND information-gain gate before spawning children
      - Parallel sibling execution (children run concurrently)
    """
    client = client or get_client()
    query_label = inp.query or inp.url or "?"

    # Global deadline check
    if inp.global_deadline and time.monotonic() > inp.global_deadline:
        return NodeResult(query_label, [], [], terminated_reason="global_deadline")

    # ----- Stage 1: Source resolution + chunking -----
    try:
        chunks = await asyncio.wait_for(
            resolve_sources(inp, kb_search_fn=kb_search_fn),
            timeout=inp.node_timeout_s,
        )
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning("Node '%s' source resolution failed: %s", query_label[:50], e)
        return NodeResult(query_label, [], [], terminated_reason="timeout_or_error")

    if not chunks:
        return NodeResult(query_label, [], [], terminated_reason="no_results")

    # ----- Stage 2: Embed chunks -----
    try:
        chunks = await embed_chunks(chunks, client=client)
    except Exception as e:
        logger.warning("Node '%s' embedding failed: %s", query_label[:50], e)
        # Proceed with un-embedded chunks — reranking will fall back to BM25 only
        pass

    # ----- Stage 3: Embed query + rerank -----
    try:
        query_vec = await embed_query(inp.query or query_label, client=client)

        # ── Knowledge graph enhancement (if beneficial) ──
        try:
            from ...knowledge.graph_rag import graph_enhanced_retrieval, should_use_graph
            if should_use_graph(inp.query or query_label):
                graph_chunks = await graph_enhanced_retrieval(
                    inp.query or query_label,
                    query_vec,
                    client=client,
                    top_k=4,  # Small set from graph
                )
                # Merge graph chunks with vector chunks (before rerank)
                existing_texts = {c.text[:100] for c in chunks}
                for gc in graph_chunks:
                    if gc.text[:100] not in existing_texts:
                        chunks.append(gc)
        except ImportError:
            pass  # Graph not available, continue with vector-only
        except Exception as e:
            logger.debug("Graph enhancement skipped: %s", e)

        top_chunks = await rerank_chunks(
            inp.query or query_label,
            [query_vec],
            chunks,
            client=client,
        )
    except Exception as e:
        logger.warning("Node '%s' reranking failed: %s", query_label[:50], e)
        top_chunks = chunks[:TOP_K_RERANK]

    # ----- Stage 4b: Critique-guided retrieval (Phase 2) -----
    # Run critique on reranked chunks to identify gaps
    critique_gaps = []
    if inp.depth < inp.max_depth - 1:  # Don't critique near depth limit
        try:
            critique_gaps = await _run_critique_on_retrieval(
                query=inp.query or query_label,
                top_chunks=top_chunks,
                client=client,
            )
        except Exception as e:
            logger.debug(f"Critique-guided retrieval skipped: {e}")

    # ----- Stage 5: Decision LLM -----
    try:
        decision = await decision_llm(
            query=inp.query or query_label,
            mode=inp.mode,
            reranked_chunks=top_chunks,
            depth=inp.depth,
            max_depth=inp.max_depth,
            client=client,
        )
    except Exception as e:
        logger.warning("Node '%s' decision LLM failed: %s", query_label[:50], e)
        # On decision failure, return what we have (don't lose retrieved data)
        learnings = [c.text for c in top_chunks]
        source_urls = list({c.source_url for c in top_chunks})
        return NodeResult(query_label, learnings, source_urls, terminated_reason="decision_error")

    # Extract learnings from top chunks
    learnings = [c.text for c in top_chunks]
    source_urls = list({c.source_url for c in top_chunks})

    # ----- Gate: depth budget + dynamic extension -----
    at_depth_limit = inp.depth >= inp.max_depth
    effective_max_depth = inp.max_depth
    extensions_for_children = inp.extensions_used

    if at_depth_limit and decision.request_extension:
        # Rare escape valve — all three conditions must hold
        branch_has_budget = inp.extensions_used < inp.max_extensions_per_branch
        time_remaining_ok = (
            inp.global_deadline is None
            or (inp.global_deadline - time.monotonic()) > inp.min_time_margin_s
        )
        if branch_has_budget and time_remaining_ok:
            effective_max_depth = inp.max_depth + inp.extension_increment
            extensions_for_children = inp.extensions_used + 1
            at_depth_limit = False
            logger.info(
                "Node '%s' granted depth extension: %d→%d (%s)",
                query_label[:50], inp.max_depth, effective_max_depth,
                decision.extension_justification[:100],
            )

    if decision.sufficient or at_depth_limit:
        reason = "leaf_answered" if decision.sufficient else "depth_budget"
        return NodeResult(query_label, learnings, source_urls, terminated_reason=reason)

    # ----- Spawn children in parallel -----
    # Merge critique gaps into next_queries (Phase 2 enhancement)
    all_next_queries = list(decision.next_queries) + critique_gaps
    
    if not all_next_queries:
        return NodeResult(query_label, learnings, source_urls, terminated_reason="leaf_answered")

    child_coros = []
    for child_query in all_next_queries:
        if decision.needs_code_retriever and inp.code_tool_fn:
            # Route to code retriever block
            child_coros.append(inp.code_tool_fn(child_query))
        else:
            child_inp = BlockInput(
                query=child_query,
                mode=decision.next_mode or inp.mode,
                depth=inp.depth + 1,
                max_depth=effective_max_depth,
                node_timeout_s=inp.node_timeout_s,
                global_deadline=inp.global_deadline,
                extensions_used=extensions_for_children,
                max_extensions_per_branch=inp.max_extensions_per_branch,
                extension_increment=inp.extension_increment,
                min_time_margin_s=inp.min_time_margin_s,
                fetch_fn=inp.fetch_fn,
                code_tool_fn=inp.code_tool_fn,
            )
            child_coros.append(
                semantic_retriever_block(child_inp, client=client, kb_search_fn=kb_search_fn)
            )

    children = await asyncio.gather(*child_coros, return_exceptions=True)
    valid_children = [c for c in children if isinstance(c, NodeResult)]

    return NodeResult(
        query_label,
        learnings,
        source_urls,
        children=valid_children,
        terminated_reason="expanded",
    )


def collect_tree(node: NodeResult) -> tuple[list[Learning], list[str]]:
    """Walk the recursion tree and collect all learnings + source URLs."""
    learnings = [Learning(text=t) for t in node.learnings]
    urls = list(node.source_urls)

    for child in node.children:
        child_learnings, child_urls = collect_tree(child)
        learnings.extend(child_learnings)
        urls.extend(child_urls)

    # Deduplicate URLs
    seen = set()
    unique_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    return learnings, unique_urls


async def run_deep_search(
    query: str,
    *,
    mode: Mode = Mode.PUBLIC,
    max_depth: int = DEFAULT_MAX_DEPTH,
    budget_s: float = GLOBAL_BUDGET_S,
    fetch_fn=None,
    code_tool_fn=None,
    client: Optional[NIMClient] = None,
    kb_search_fn=None,
) -> NodeResult:
    """
    Entry point for the semantic retriever. Runs the recursive tree
    under a global time budget. Returns the root NodeResult with all
    children populated.

    The caller (query.py) is responsible for the final synthesis LLM call
    over the collected learnings.
    """
    deadline = time.monotonic() + budget_s
    root_input = BlockInput(
        query=query,
        mode=mode,
        depth=0,
        max_depth=max_depth,
        global_deadline=deadline,
        fetch_fn=fetch_fn,
        code_tool_fn=code_tool_fn,
    )

    return await semantic_retriever_block(
        root_input, client=client, kb_search_fn=kb_search_fn,
    )
