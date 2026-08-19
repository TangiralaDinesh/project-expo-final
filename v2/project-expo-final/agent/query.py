"""
query.py — THE single entry point for the entire agent backend.

Every query flows through run_query(). This orchestrates the full pipeline:

    entry_gate → clarify (parallel) → thinking_profile → orchestrator → synthesis

KEY PHILOSOPHIES from chats implemented here:
  1. Speculative streaming (L72): start streaming answer parts while
     retrieval still runs in background
  2. Parallel clarify+gate (L48-49): fire both concurrently, only
     sequence when clarification changes the search target
  3. Prompt-adaptive depth (L344): expert prompts get deeper treatment
  4. Correction-pattern feedback (L327): effort_bias feeds into profiles
  5. CRAG-style (L30): if retrieval comes back thin, fall back to
     direct answer + disclaimer rather than empty response

Streaming: run_query_stream() yields structured events for real-time UI.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from .llm.client import NIMClient, get_client
from .llm.clarify import generate_clarifying_question, ClarifyDecision
from .llm.synthesis import (
    global_synthesis_llm, global_synthesis_llm_stream,
    direct_answer_llm, direct_answer_llm_stream,
)
from .routing.entry_gate import entry_gate, GateDecision
from .orchestrator.orchestrator import run_orchestrator
from .blocks.base import run_subagent
from .blocks.semantic.corrective import (
    grade_retrieval, RetrievalGrade, get_correction_stats,
)
from .core.types import Learning
from .core.reasoning import get_thinking_profile, get_thinking_profile_with_history, EffortBias, classify_prompt_specificity
from .core.satisfaction import SatisfactionTracker
from .core.intent_classifier import IntentClassifier
from .core.resonance import should_continue_retrieval
from .core.tracing import start_query_trace, end_query_trace, get_current_trace
from .core.query_fanout import fan_out_query, score_pilot_results, select_queries_by_eig
from .core.gap_scanner import scan_for_gaps, format_gaps_for_user, _should_run_gap_scan
from .core.progressive import GeoHashDecisionTree
from .retrieval.knowledge_coordinator import get_knowledge_coordinator
from .cache.semantic_cache import get_semantic_cache
from .blocks.semantic.embed import embed_query
from .config.feature_flags import FeatureFlags
from .config.settings import settings
from .config.budgets import (
    MAX_SATISFACTION_ROUNDS, MIN_CONCEPT_SATISFACTION,
    GAP_SCAN_MIN_LEARNINGS, GAP_SCAN_MIN_DEPTH,
)

logger = logging.getLogger(__name__)


def _suggest_file_creation(
    answer: str,
    num_learnings: int,
    query: str,
) -> tuple[bool, list[str], str]:
    """
    Detect if answer would benefit from file creation (Tier 4 UX enhancement).
    Returns: (should_suggest, file_types, reason)
    """
    file_types = []
    reason = ""
    
    # Heuristics for file creation suggestions
    answer_tokens = len(answer) // 4  # Rough token count
    
    # Complex comparison → Excel matrix
    if any(word in query.lower() for word in ["compare", "vs", "versus", "difference", "comparison"]):
        if num_learnings > 5 and answer_tokens > 800:
            file_types.append("excel")
            reason = "Complex comparison data can be organized in a spreadsheet"
    
    # Large research answer → PDF report
    if answer_tokens > 1500 and num_learnings > 8:
        file_types.append("pdf")
        reason = "Large detailed answer can be saved as a professional report"
    
    # Presentation query → PowerPoint
    if any(word in query.lower() for word in ["presentation", "slide", "deck", "pitch", "proposal"]):
        if answer_tokens > 500:
            file_types.append("pptx")
            reason = "Content is suited for a presentation deck"
    
    # Data-heavy or structured → Excel
    if any(word in query.lower() for word in ["data", "stats", "numbers", "table", "matrix", "breakdown"]):
        if answer_tokens > 600:
            file_types.append("excel")
            if "excel" not in file_types[:1]:
                reason = "Structured data works well in spreadsheets"
    
    # Interactive content → HTML
    if answer_tokens > 1200 and "interactive" not in query.lower():
        file_types.append("html")
        if not reason:
            reason = "Can create an interactive HTML document"
    
    # Deduplicate and prioritize
    file_types = list(dict.fromkeys(file_types))[:3]  # Max 3 suggestions
    
    return len(file_types) > 0, file_types, reason


@dataclass
class QueryResult:
    """Complete result of a query."""
    answer: str
    learnings: list[Learning] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    gate_decision: Optional[GateDecision] = None
    clarify_decision: Optional[ClarifyDecision] = None
    timing_ms: float = 0.0
    from_cache: bool = False
    prompt_specificity: str = "standard"
    
    # Tier 2: Progressive Revelation (zoom levels)
    current_zoom_level: int = 0  # 0=overview, 1=focused, 2=comprehensive
    zoom_options: dict = field(default_factory=dict)  # Zoom navigation options
    can_zoom_in: bool = False
    can_zoom_out: bool = False
    
    # Tier 3: Bayesian Branching
    branching_options: list = field(default_factory=list)  # BranchingOption objects
    branching_session_id: Optional[str] = None  # Track multi-turn branching
    
    # Tier 4: Code Execution (metadata)
    code_executed: bool = False
    code_execution_results: list = field(default_factory=list)
    
    # Tier 4: File Creation Suggestions
    can_create_file: bool = False  # Whether agent identified file creation opportunity
    suggested_file_types: list = field(default_factory=list)  # ["excel", "pdf", "pptx", "html"]
    file_creation_reason: str = ""  # Why file creation is recommended
    
    # Plan 2: Comparison Query Analysis (Phase 1)
    comparison_analysis: Optional[dict] = None  # For "should I buy X or Y?" type queries
    # Structure: {"is_comparison": bool, "entities": [{"name": str, "learnings": []}], "comparison_verdict": str}


@dataclass
class StreamEvent:
    """One event in the streaming response."""
    type: str       # "gate" | "clarify" | "thinking" | "tool" | "answer_delta" | "answer_end" | "sources" | "error"
    data: str = ""
    metadata: dict = field(default_factory=dict)


async def run_query(
    query: str,
    *,
    memory_context: Optional[list[str]] = None,
    fetch_fn=None,
    code_tool_fn=None,
    client: Optional[NIMClient] = None,
    effort_bias: Optional[EffortBias] = None,
    satisfaction: Optional[SatisfactionTracker] = None,
    zoom_level: int = 0,  # Tier 2: 0=overview, 1=focused, 2=comprehensive
    branch_selection: Optional[int] = None,  # Tier 3: user selected branch index
    branching_session_id: Optional[str] = None,  # Tier 3: resume branching session
    use_code_execution: Optional[bool] = None,  # Tier 4: enable code-based validation
) -> QueryResult:
    """
    Non-streaming entry point with Tier 1-4 features.
    
    Args:
        query: User's question
        memory_context: Prior conversation context
        fetch_fn: Function to fetch web content
        code_tool_fn: Function for tool execution
        client: LLM client
        effort_bias: Effort/cost preferences
        satisfaction: User satisfaction tracker (Tier 1)
        zoom_level: Progressive revelation depth (Tier 2)
        branch_selection: User's choice from branching options (Tier 3)
        branching_session_id: Continue multi-turn branching (Tier 3)
        use_code_execution: Enable code validation (Tier 4). If None, uses feature flag default.
    
    Returns:
        QueryResult with answer + metadata
    """
    t0 = time.time()
    client = client or get_client()
    
    # Determine use_code_execution from feature flags if not explicitly provided
    if use_code_execution is None:
        features = settings.features if hasattr(settings, 'features') else FeatureFlags.all_off()
        use_code_execution = features.code_execution_enabled

    # ── Semantic cache check ──
    sem_cache = get_semantic_cache()

    # ── Stage 1: Entry gate + Clarify + Embed (ALL parallel — saves 2-5s) ──
    gate_task = entry_gate(query, client=client)
    clarify_task = generate_clarifying_question(
        query, client=client, memory_context=memory_context,
    )
    embed_task = embed_query(query, client=client)

    results = await asyncio.gather(
        gate_task, clarify_task, embed_task, return_exceptions=True,
    )

    gate_result = results[0]
    clarify_result = results[1]
    query_vec = results[2] if not isinstance(results[2], Exception) else []

    # Check cache with embedding result
    if query_vec:
        try:
            cached = sem_cache.get(query_vec)
            if cached:
                return QueryResult(
                    answer=cached.get("answer", ""),
                    learnings=cached.get("learnings", []),
                    source_urls=cached.get("source_urls", []),
                    timing_ms=(time.time() - t0) * 1000,
                    from_cache=True,
                )
        except Exception:
            pass

    if isinstance(gate_result, Exception):
        gate_result = GateDecision(needs_retrieval=True, mode="SEMANTIC", reason="gate_error")
    if isinstance(clarify_result, Exception):
        clarify_result = ClarifyDecision(should_ask=False, question="", depends_on_search_target=False)

    # ── Thinking profile (adapts to prompt + user history) ──
    specificity = classify_prompt_specificity(query)
    
    # NEW Tier 1: Build thinking profile with satisfaction history if enabled
    features = settings.features if hasattr(settings, 'features') else FeatureFlags.all_off()
    
    if satisfaction and features.connectivity_enabled:
        profile = await get_thinking_profile_with_history(
            query=query,
            prompt_specificity=specificity,
            gate_mode=gate_result.mode,
            satisfaction_tracker=satisfaction,
            features_enabled=features,
        )
    else:
        profile = get_thinking_profile(gate_result.mode, query, effort_bias)

    # ── Satisfaction adjustments (reward/punishment) ──
    if satisfaction:
        satisfaction.record_query(query)
        adjustments = satisfaction.get_thinking_adjustments()
        if adjustments.get("depth_boost"):
            profile.max_depth = min(profile.max_depth + adjustments["depth_boost"], 5)

    # ── Case 0: Skill matched → execute skill ──
    if gate_result.mode == "SKILL":
        try:
            from .skills.registry import get_skill_registry
            from .skills.executor import execute_skill
            registry = get_skill_registry()
            skill_match = registry.match(query)
            if skill_match:
                skill_result = await execute_skill(skill_match, query, client=client)
                return QueryResult(
                    answer=skill_result.output,
                    gate_decision=gate_result,
                    timing_ms=(time.time() - t0) * 1000,
                    prompt_specificity="skill",
                )
        except Exception as e:
            logger.warning("Skill execution failed: %s, falling back to standard", e)

    # ── Case 0b: URL direct → skip search, fetch directly ──
    if gate_result.mode == "URL_DIRECT":
        import re
        url_match = re.search(r"https?://[^\s]+", query)
        if url_match:
            from .tools.web_fetch import fetch_url
            url = url_match.group()
            raw = await fetch_url(url)
            if raw:
                from .blocks.semantic.chunk import chunk_text
                chunks = chunk_text(raw, url, is_html=True)
                all_learnings = [Learning(text=c.text, source_url=url) for c in chunks[:5]]
                answer = await global_synthesis_llm(
                    query, all_learnings, client=client,
                    prompt_specificity=profile.prompt_specificity,
                )
                return QueryResult(
                    answer=answer,
                    learnings=all_learnings,
                    source_urls=[url],
                    gate_decision=gate_result,
                    timing_ms=(time.time() - t0) * 1000,
                    prompt_specificity=profile.prompt_specificity,
                )

    # ── Case 1: No retrieval needed ──
    if not gate_result.needs_retrieval:
        answer = await direct_answer_llm(
            query, client=client, prompt_specificity=profile.prompt_specificity,
        )
        result = QueryResult(
            answer=answer,
            gate_decision=gate_result,
            clarify_decision=clarify_result,
            timing_ms=(time.time() - t0) * 1000,
            prompt_specificity=profile.prompt_specificity,
        )
        if query_vec:
            sem_cache.set(query, query_vec, {"answer": answer, "learnings": [], "source_urls": []})
        return result

    # ── Case 4: Clarification blocks search ──
    if clarify_result.should_ask and clarify_result.depends_on_search_target:
        return QueryResult(
            answer="",
            gate_decision=gate_result,
            clarify_decision=clarify_result,
            timing_ms=(time.time() - t0) * 1000,
            prompt_specificity=profile.prompt_specificity,
        )

    # ── Adjust query if memory resolved ambiguity ──
    effective_query = query
    if clarify_result.resolved_by_memory:
        effective_query = f"{query} (context: {clarify_result.resolved_by_memory})"

    # ── STAGE 1.5: INTENT CLASSIFICATION (NEW - for dynamic multi-task handling) ──
    # Analyze query intent and focus areas BEFORE orchestration
    intent_analysis = None
    try:
        classifier = IntentClassifier(client=client)
        intent_analysis = await classifier.analyze(
            query,
            satisfaction_tracker=satisfaction,
            use_llm=False,  # Start with fast heuristic
        )
        logger.debug(
            f"Intent: {intent_analysis.intent.value}, "
            f"focus_areas: {[fa.name for fa in intent_analysis.focus_areas]}"
        )
    except Exception as e:
        logger.debug(f"Intent classification failed: {e}, continuing without it")
    
    # ── Stage 2: Run orchestrator to decompose and execute ──
    # Start tracing
    trace = start_query_trace(effective_query)
    root_span = trace.start_span("run_query", mode=gate_result.mode, specificity=specificity)

    # ── Phase 12: Initialize GeoHash Decision Tree ──
    geohash_tree = GeoHashDecisionTree(effective_query, max_depth=profile.max_depth)
    # Set strategy based on query type (comparison = breadth_first)
    if intent_analysis and hasattr(intent_analysis, 'intent'):
        intent_val = intent_analysis.intent.value if hasattr(intent_analysis.intent, 'value') else str(intent_analysis.intent)
        if 'compare' in intent_val.lower() or 'compare' in effective_query.lower():
            geohash_tree.set_strategy("comparison")
        elif 'explain' in intent_val.lower() or 'how' in effective_query.lower():
            geohash_tree.set_strategy("focused")

    # ── Phase 2 + Orchestrator: Run fan-out IN PARALLEL with orchestrator ──
    # Fan-out generates variant queries; orchestrator uses them IF they arrive in time.
    # This eliminates the 5-10s fan-out blocking time.
    
    fan_out_plan = None
    pipeline_deadline = time.time() + 75.0  # 75s total budget for retrieval

    async def _do_fan_out():
        """Generate fan-out queries (runs in parallel with orchestrator)."""
        nonlocal fan_out_plan
        try:
            fan_out_plan = await fan_out_query(
                effective_query, client=client,
                existing_aspect_queries=None,
            )
            if fan_out_plan:
                trace.add_attribute(root_span, "fanout_queries", len(fan_out_plan.selected_queries))
                logger.info(
                    "Fan-out: %d queries selected (removed %d dupes)",
                    len(fan_out_plan.selected_queries), len(fan_out_plan.dedup_removed),
                )
        except Exception as e:
            logger.warning("Fan-out failed: %s, continuing with original query", e)

    async def _do_orchestrator():
        """Run orchestrator (main retrieval)."""
        return await run_orchestrator(
            effective_query,
            run_subagent=run_subagent,
            gate_mode=gate_result.mode,
            client=client,
            thinking_profile=profile,
            satisfaction=satisfaction,
            intent_analysis=intent_analysis,
        )

    # Fire BOTH in parallel — orchestrator doesn't need fan-out results
    async with trace.span("parallel_fanout_orchestrator") as parallel_span:
        fan_out_task = asyncio.create_task(_do_fan_out())
        orch_results = await _do_orchestrator()
        
        # Wait for fan-out to finish (it usually finishes before orchestrator)
        try:
            await asyncio.wait_for(fan_out_task, timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass  # Fan-out didn't finish in time, that's fine
        
        trace.add_attribute(parallel_span, "subagents", len(orch_results))

    # ── Collect all learnings ──
    all_learnings, all_urls = _collect_results(orch_results)

    # ── LLM-POWERED REASONING LOOP ──
    # The LLM is now IN the loop — it evaluates learnings, identifies gaps,
    # generates targeted follow-up queries, and decides when to stop.
    # This replaces the heuristic satisfaction checking.
    from .core.llm_reasoning import evaluate_learnings, decide_next_action, explore_connected_dots
    
    satisfaction_scores = {}
    unsatisfied_concepts = []
    wave_2_learnings = []
    llm_quality_score = 0.0

    for retrieval_round in range(MAX_SATISFACTION_ROUNDS):
        # Time budget check
        elapsed = time.time() - t0
        if elapsed > 55.0:  # Hard budget: 55s for retrieval phases
            logger.info("Reasoning loop: time budget exhausted (%.0fs), stopping", elapsed)
            break

        async with trace.span(
            f"reasoning_round_{retrieval_round + 1}",
            round=retrieval_round + 1,
        ) as reason_span:
            # ── STEP 1: LLM evaluates current learnings ──
            # This is the KEY difference — LLM reads the learnings and judges
            # whether they actually answer the query (not just text overlap)
            llm_eval_task = evaluate_learnings(
                effective_query, all_learnings, client=client,
            )
            
            # Run LLM evaluation + heuristic satisfaction IN PARALLEL
            heuristic_task = None
            if satisfaction:
                concepts = satisfaction.extract_concepts(effective_query)
                heuristic_task = satisfaction.evaluate_satisfaction(
                    effective_query, all_learnings, concepts
                )
            
            if heuristic_task:
                llm_eval, heuristic_scores = await asyncio.gather(
                    llm_eval_task, heuristic_task, return_exceptions=True,
                )
                if isinstance(heuristic_scores, dict):
                    satisfaction_scores = heuristic_scores
            else:
                llm_eval = await llm_eval_task
            
            if isinstance(llm_eval, Exception):
                logger.warning("LLM evaluation failed: %s", llm_eval)
                break
            
            llm_quality_score = llm_eval.quality_score
            trace.add_attribute(reason_span, "llm_quality", llm_eval.quality_score)
            trace.add_attribute(reason_span, "llm_sufficient", llm_eval.sufficient)
            trace.add_attribute(reason_span, "missing_aspects", llm_eval.missing_aspects)
            
            logger.info(
                "Reasoning round %d: quality=%.2f, sufficient=%s, missing=%s",
                retrieval_round + 1, llm_eval.quality_score, llm_eval.sufficient,
                llm_eval.missing_aspects[:3],
            )
            
            # If LLM says sufficient, stop
            if llm_eval.sufficient and llm_eval.confidence > 0.6:
                logger.info("LLM says sufficient (conf=%.2f), stopping", llm_eval.confidence)
                break
            
            # ── STEP 2: Check resonance (are we getting new info?) ──
            if retrieval_round > 0 and wave_2_learnings:
                continue_retrieval, reason = should_continue_retrieval(
                    existing_learnings=all_learnings[:-len(wave_2_learnings)] if wave_2_learnings else all_learnings,
                    new_learnings=wave_2_learnings,
                    satisfaction_scores=satisfaction_scores,
                    min_satisfaction=MIN_CONCEPT_SATISFACTION,
                )
                trace.add_attribute(reason_span, "resonance_decision", reason)
                if not continue_retrieval:
                    logger.info("Resonance says stop: %s", reason)
                    break
            
            # ── STEP 3: KG exploration + LLM decision IN PARALLEL ──
            # Fire KG exploration and decision concurrently — decision uses
            # whatever KG data arrives in time. Both are I/O-bound LLM calls.
            
            entities_for_kg = satisfaction.extract_concepts(effective_query) if satisfaction else []
            
            async def _kg_explore():
                try:
                    return await asyncio.wait_for(
                        explore_connected_dots(
                            effective_query, entities_for_kg, all_learnings, client=client,
                        ),
                        timeout=3.0,
                    )
                except Exception:
                    return []
            
            async def _decide(kg_ctx):
                return await decide_next_action(
                    effective_query, all_learnings,
                    client=client,
                    quality_score=llm_eval.quality_score,
                    round_num=retrieval_round,
                    elapsed_s=time.time() - t0,
                    missing_aspects=llm_eval.missing_aspects,
                    kg_context=kg_ctx,
                )
            
            # Run both in parallel
            kg_result, decision = await asyncio.gather(
                _kg_explore(),
                _decide([]),  # Decision starts immediately with empty KG context
                return_exceptions=True,
            )
            
            kg_context = kg_result if isinstance(kg_result, list) else []
            if isinstance(decision, Exception):
                logger.warning("Decision failed: %s", decision)
                break
            
            trace.add_attribute(reason_span, "decision", decision.action)
            trace.add_attribute(reason_span, "decision_reasoning", decision.reasoning)
            
            logger.info(
                "LLM decision: %s (conf=%.2f) — %s",
                decision.action, decision.confidence, decision.reasoning[:80],
            )
            
            if decision.action == "stop":
                break
            
            # ── STEP 4: Build follow-up queries from LLM eval + LLM decision + critique ──
            gap_queries = []
            
            # Priority 1: LLM decision queries (most targeted)
            gap_queries.extend(decision.queries[:3])
            
            # Priority 2: LLM evaluation follow-up queries
            for q in llm_eval.follow_up_queries[:2]:
                if not any(q.lower()[:20] in existing.lower() for existing in gap_queries):
                    gap_queries.append(q)
            
            # Priority 3: KG connected-dot queries
            for q in kg_context[:2]:
                if not any(q.lower()[:20] in existing.lower() for existing in gap_queries):
                    gap_queries.append(q)
            
            if not gap_queries:
                # Fallback: use missing aspects as queries
                for aspect in llm_eval.missing_aspects[:3]:
                    gap_queries.append(f"{aspect} {effective_query}")
            
            if not gap_queries:
                break
            
            gap_queries = gap_queries[:4]  # Cap at 4 queries
            
            logger.info(
                "Progressive retrieval round %d: %d gap queries: %s",
                retrieval_round + 1, len(gap_queries), gap_queries[:3],
            )
            
            # ── STEP 5: Execute re-retrieval with LLM-generated queries ──
            wave_2_learnings = []
            wave_2_urls = []
            try:
                wave_2_results = await run_orchestrator(
                    " | ".join(gap_queries),
                    run_subagent=run_subagent,
                    gate_mode=gate_result.mode,
                    client=client,
                    thinking_profile=profile,
                )
                wave_2_learnings, wave_2_urls = _collect_results(wave_2_results)
                all_learnings.extend(wave_2_learnings)
                all_urls.extend(wave_2_urls)
                logger.info(
                    "Wave %d complete: +%d learnings, +%d URLs",
                    retrieval_round + 2, len(wave_2_learnings), len(wave_2_urls),
                )
            except Exception as e:
                logger.warning("Re-retrieval round %d failed: %s", retrieval_round + 1, e)
                break

    # ── Phase 12: Record GeoHash exploration path ──
    if satisfaction_scores:
        geohash_tree.next_character(
            branches=list(satisfaction_scores.keys()),
            branch_scores=satisfaction_scores,
            satisfaction_scores=satisfaction_scores,
        )
        trace.add_attribute(root_span, "geohash_path", geohash_tree.geohash_string)

    # ── Stage 3: CRAG grading + synthesis + gap scanning IN PARALLEL ──
    # Tier 2: Zoom filtering is OPT-IN (v1 behavior: use all learnings by default)
    # Only apply zoom filtering if explicitly requested via user params
    learnings_for_synthesis = all_learnings
    zoom_metadata = {}
    
    # Track zoom level metadata even if not filtering
    if zoom_level == 0:
        zoom_metadata["level"] = "default"
        zoom_metadata["description"] = "Complete information (all learnings used)"
    elif zoom_level == 1:  # User explicitly requested focused view
        learnings_for_synthesis = sorted(
            all_learnings, key=lambda l: l.score, reverse=True
        )[:6]
        zoom_metadata["level"] = "focused"
        zoom_metadata["description"] = "Detailed analysis (top 6 learnings)"
    elif zoom_level >= 2:  # User explicitly requested comprehensive
        learnings_for_synthesis = sorted(
            all_learnings, key=lambda l: l.score, reverse=True
        )[:15]
        zoom_metadata["level"] = "comprehensive"
        zoom_metadata["description"] = "Deep analysis (top 15 learnings)"
    
    # ── Phase 2: Entropy-based learning selection (remove noise) ──
    if learnings_for_synthesis:
        from .core.reasoning import select_top_learnings
        
        if len(learnings_for_synthesis) > 10:
            learnings_for_synthesis = select_top_learnings(
                learnings_for_synthesis,
                effective_query,
                top_k=10,
            )
            logger.debug(
                "Entropy filtering applied: selected %d learnings from %d total",
                len(learnings_for_synthesis),
                len(all_learnings),
            )
    
    # ── Phase 9: Assign citation IDs before synthesis ──
    for i, learning in enumerate(learnings_for_synthesis):
        learning.citation_id = f"[{i + 1}]"

    # ── Run gap scanning IN PARALLEL with CRAG grading (both are I/O-bound LLM calls) ──
    gap_scan_task = None
    if _should_run_gap_scan(
        learnings_count=len(all_learnings),
        max_depth=profile.max_depth,
        gate_mode=gate_result.mode,
    ):
        async def _do_gap_scan():
            try:
                return await scan_for_gaps(
                    effective_query, all_learnings, client=client,
                )
            except Exception as e:
                logger.debug("Gap scanning failed: %s", e)
                return None
        gap_scan_task = asyncio.create_task(_do_gap_scan())

    if learnings_for_synthesis:
        grade = await grade_retrieval(effective_query, learnings_for_synthesis, client=client)
        stats = get_correction_stats()
        stats.total_queries += 1

        if grade.grade == RetrievalGrade.CORRECT:
            answer = await global_synthesis_llm(
                effective_query, learnings_for_synthesis, client=client,
                prompt_specificity=profile.prompt_specificity,
            )
        elif grade.grade == RetrievalGrade.INCORRECT:
            stats.corrected_count += 1
            logger.warning("CRAG: retrieval graded INCORRECT, discarding. Reason: %s", grade.reason)
            answer = await direct_answer_llm(
                effective_query, client=client,
                prompt_specificity=profile.prompt_specificity,
            )
            answer = (
                "⚠️ Note: Retrieved sources were not relevant to your query. "
                "This answer is based on general knowledge.\n\n" + answer
            )
            learnings_for_synthesis = []
            all_urls = []
        else:
            stats.corrected_count += 1
            answer = await global_synthesis_llm(
                effective_query, learnings_for_synthesis, client=client,
                prompt_specificity=profile.prompt_specificity,
            )
    else:
        logger.warning("No learnings from retrieval, CRAG fallback to direct answer")
        answer = await direct_answer_llm(
            effective_query, client=client,
            prompt_specificity=profile.prompt_specificity,
        )
        answer = (
            "⚠️ Note: Live search returned limited results. This answer is based on "
            "general knowledge and may not reflect the most current information.\n\n" + answer
        )

    # ── Phase 9: Build citation reference list ──
    if learnings_for_synthesis:
        citation_lines = []
        seen_urls = set()
        for learning in learnings_for_synthesis:
            if learning.citation_id and learning.source_url and learning.source_url not in seen_urls:
                title = learning.title or learning.source_url
                citation_lines.append(f"{learning.citation_id} {title} — {learning.source_url}")
                seen_urls.add(learning.source_url)
        if citation_lines:
            answer += "\n\n---\n**Sources:**\n" + "\n".join(citation_lines)

    # ── Collect gap scan result (ran in parallel with grading+synthesis) ──
    if gap_scan_task:
        try:
            gap_result = await asyncio.wait_for(gap_scan_task, timeout=3.0)
            if gap_result and gap_result.should_present:
                gap_text = format_gaps_for_user(gap_result)
                if gap_text:
                    answer += gap_text
                trace.add_attribute(root_span, "gaps_found", len(gap_result.gaps))
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug("Gap scan collection failed: %s", e)

    # ── End tracing ──
    trace.end_span(root_span, status="ok",
                   learnings_count=len(all_learnings),
                   sources_count=len(all_urls))
    final_trace = end_query_trace()

    # Detect file creation opportunities (Tier 4 UX enhancement)
    can_create_file, file_types, file_reason = _suggest_file_creation(
        answer, len(learnings_for_synthesis), effective_query
    )

    result = QueryResult(
        answer=answer,
        learnings=learnings_for_synthesis,
        source_urls=all_urls,
        gate_decision=gate_result,
        clarify_decision=clarify_result,
        timing_ms=(time.time() - t0) * 1000,
        prompt_specificity=profile.prompt_specificity,
        current_zoom_level=zoom_level,
        zoom_options=zoom_metadata,
        can_zoom_in=(zoom_level < 2),
        can_zoom_out=(zoom_level > 0),
        can_create_file=can_create_file,
        suggested_file_types=file_types,
        file_creation_reason=file_reason,
    )

    if query_vec:
        sem_cache.set(query, query_vec, {
            "answer": answer,
            "learnings": [{"text": l.text, "url": l.source_url} for l in all_learnings],
            "source_urls": all_urls,
        })

    return result


async def run_query_stream(
    query: str,
    *,
    memory_context: Optional[list[str]] = None,
    fetch_fn=None,
    code_tool_fn=None,
    client: Optional[NIMClient] = None,
    effort_bias: Optional[EffortBias] = None,
) -> AsyncIterator[StreamEvent]:
    """Streaming entry point — yields StreamEvent for real-time UI.

    SPECULATIVE STREAMING (L72): starts synthesis the moment ANY learnings
    are available, doesn't wait for ALL retrieval to finish.
    """
    t0 = time.time()
    client = client or get_client()

    # ── Gate + Clarify (parallel) ──
    yield StreamEvent(type="thinking", data="Analyzing query complexity...")

    gate_task = entry_gate(query, client=client)
    clarify_task = generate_clarifying_question(
        query, client=client, memory_context=memory_context,
    )

    gate_result, clarify_result = await asyncio.gather(
        gate_task, clarify_task, return_exceptions=True,
    )

    if isinstance(gate_result, Exception):
        gate_result = GateDecision(needs_retrieval=True, mode="SEMANTIC", reason="gate_error")
    if isinstance(clarify_result, Exception):
        clarify_result = ClarifyDecision(should_ask=False, question="", depends_on_search_target=False)

    profile = get_thinking_profile(gate_result.mode, query, effort_bias)

    yield StreamEvent(type="gate", data=gate_result.mode, metadata={
        "needs_retrieval": gate_result.needs_retrieval,
        "reason": gate_result.reason,
        "specificity": profile.prompt_specificity,
    })

    # ── No retrieval ──
    if not gate_result.needs_retrieval:
        async for delta in direct_answer_llm_stream(
            query, client=client, prompt_specificity=profile.prompt_specificity,
        ):
            yield StreamEvent(type="answer_delta", data=delta)
        yield StreamEvent(type="answer_end", metadata={
            "timing_ms": round((time.time() - t0) * 1000),
        })
        return

    # ── Clarify blocks ──
    if clarify_result.should_ask and clarify_result.depends_on_search_target:
        yield StreamEvent(type="clarify", data=clarify_result.question)
        return

    # ── SPECULATIVE STREAMING (L72): ──
    # Fire retrieval as background task, start streaming LLM's own knowledge
    # immediately. If retrieval adds new info, emit supplementary event.
    yield StreamEvent(type="thinking", data=f"Deep searching ({gate_result.mode}, depth={profile.max_depth})...")

    # Fire retrieval in background
    retrieval_task = asyncio.create_task(run_orchestrator(
        effective_query,
        run_subagent=run_subagent,
        gate_mode=gate_result.mode,
        client=client,
    ))

    # Start speculative streaming from LLM's own knowledge
    # This gives the user SOMETHING immediately while retrieval runs
    speculative_buffer = []
    yield StreamEvent(type="thinking", data="Drafting initial response...")
    async for delta in direct_answer_llm_stream(
        effective_query, client=client,
        prompt_specificity=profile.prompt_specificity,
    ):
        speculative_buffer.append(delta)
        yield StreamEvent(type="answer_delta", data=delta)

    # Wait for retrieval to complete
    try:
        orch_results = await asyncio.wait_for(retrieval_task, timeout=25.0)
    except asyncio.TimeoutError:
        orch_results = {}
        yield StreamEvent(type="thinking", data="Search timed out, using initial answer")

    all_learnings, all_urls = _collect_results(orch_results)

    for node_id, sub_result in orch_results.items():
        yield StreamEvent(type="tool", data=f"{sub_result.subagent_type.value}: {'✓' if sub_result.success else '✗'}")

    # ── If retrieval found substantial new info, emit enhanced answer ──
    if all_learnings and len(all_learnings) >= 2:
        yield StreamEvent(type="thinking", data="Enhancing with retrieved sources...")
        yield StreamEvent(type="answer_delta", data="\n\n---\n**Enhanced with live sources:**\n\n")
        async for delta in global_synthesis_llm_stream(
            effective_query, all_learnings, client=client,
            prompt_specificity=profile.prompt_specificity,
        ):
            yield StreamEvent(type="answer_delta", data=delta)

    yield StreamEvent(type="sources", metadata={"urls": all_urls[:10]})
    yield StreamEvent(type="answer_end", metadata={
        "timing_ms": round((time.time() - t0) * 1000),
        "learnings_count": len(all_learnings),
        "sources_count": len(all_urls),
        "prompt_specificity": profile.prompt_specificity,
        "thinking_depth": profile.max_depth,
        "speculative": True,
    })


def _collect_results(
    orch_results: dict,
) -> tuple[list[Learning], list[str]]:
    """Extract learnings and URLs from orchestrator results."""
    all_learnings: list[Learning] = []
    all_urls: list[str] = []

    for node_id, sub_result in orch_results.items():
        if sub_result.success:
            for l in sub_result.learnings:
                if isinstance(l, Learning):
                    all_learnings.append(l)
                elif isinstance(l, str):
                    all_learnings.append(Learning(text=l))
            all_urls.extend(sub_result.source_urls)

    # Deduplicate URLs
    seen = set()
    unique_urls = [u for u in all_urls if u and u not in seen and not seen.add(u)]

    return all_learnings, unique_urls
