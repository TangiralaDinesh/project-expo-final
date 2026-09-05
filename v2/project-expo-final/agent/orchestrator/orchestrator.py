"""
Lead agent: decomposes the incoming task into subagent mandates, decides
how many subagents to actually spawn, collects structured SubagentResults,
and runs core.pivot when a subagent fails.

Subagent count and firing order are NOT fixed:
  - decompose() builds a task dependency graph (TDG), not a flat list.
  - Nodes with no unresolved dependencies fire together via asyncio.gather.
  - A node with a dependency waits for its prerequisite's SubagentResult.
  - "No subagents needed" is a valid decompose() outcome.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..core.types import SubagentInput, SubagentResult, SubagentType, Hypothesis, PivotDecision
from ..core.pivot import Observation, run_pivot_loop
from ..core.reasoning import ThinkingProfile
from ..core.satisfaction import SatisfactionTracker
from ..core.parallel_state import get_state_coordinator
from ..core.intent_classifier import IntentClassifier, QueryIntent
from ..core.aspect_extractor import AspectExtractor
from ..core.subquery_generator import SubqueryGenerator
from ..llm.client import NIMClient, get_client
from ..config.budgets import DEFAULT_MAX_SUBAGENTS, FAN_OUT_MAX_SUBAGENTS
from ..routing.comparison_detector import ComparisonQueryDetector

logger = logging.getLogger(__name__)


@dataclass
class TaskNode:
    """One node in the task dependency graph."""
    node_id: str
    subagent_type: SubagentType
    task: str
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Decomposition:
    """Output of the decompose step. Empty nodes = handle directly."""
    nodes: list[TaskNode] = field(default_factory=list)
    fan_out_eligible: bool = False
    is_comparison: bool = False
    comparison_entities: list[str] = field(default_factory=list)


RunSubagentFn = Callable[[SubagentInput], Awaitable[SubagentResult]]


# ── Knowledge graph integration (Tier 1) ──

async def query_knowledge_graph_for_context(
    query: str,
    features_enabled: Optional[object] = None,
) -> list[str]:
    """Query knowledge graph for related concepts (Tier 1 feature).
    
    Returns list of related concept names that should be considered.
    """
    if not features_enabled or not getattr(features_enabled, 'knowledge_graph_queries_enabled', False):
        return []
    
    try:
        from ..knowledge.graph_store import get_graph_store
        graph = get_graph_store()
        if graph.entity_count == 0:
            return []
        
        # Extract key terms from query
        terms = [w for w in query.lower().split() if len(w) > 3][:3]
        
        related = []
        for term in terms:
            triples = graph.query_entity(term, hops=1)
            for t in triples:
                if t.object.lower() != term:
                    related.append(t.object)
                if t.subject.lower() != term:
                    related.append(t.subject)
        
        return list(set(related))[:5]  # Dedupe and limit
    except Exception as e:
        logger.debug(f"Knowledge graph query failed: {e}")
        return []


# ── Circuit breaker registry ──

class SubagentRegistry:
    """Tracks subagent health for circuit-breaking."""

    def __init__(self, break_threshold: int = 3):
        self._failures: dict[str, int] = {}
        self._threshold = break_threshold

    def record_failure(self, subagent_name: str):
        self._failures[subagent_name] = self._failures.get(subagent_name, 0) + 1

    def record_success(self, subagent_name: str):
        self._failures[subagent_name] = 0

    def is_circuit_broken(self, subagent_type: SubagentType) -> bool:
        return self._failures.get(subagent_type.value, 0) >= self._threshold


# ── Decomposition via LLM ──

_DECOMPOSE_SYSTEM = """You are an automated task decomposition engine. Given a research task, decide whether it needs to be broken into parallel sub-tasks for specialized subagents.
Output strictly raw machine-readable JSON only. Zero conversational monologue, zero thinking process preamble, zero markdown fences. Start immediately with '{' and end with '}'.

Available subagent types:
- "retriever": semantic web search + knowledge base retrieval (for factual, comparative, and domain research)
- "code_retriever": GitHub code search + AST analysis (for repository inspection and code implementations)
- "code_gen_executor": direct code execution in a secure sandbox (for computation, file operations, algorithms)

Decomposition Rules & Patterns:
1. Multi-Entity Comparison (e.g. "Tom Holland vs Zendaya", "PostgreSQL vs MongoDB"):
   Decompose into balanced parallel retriever nodes for each subject entity with fan_out_eligible=true.
2. Conceptual + Implementation Research:
   Decompose into a retriever node (concepts/docs) and a code_retriever node (repo/code examples).
3. Complex Multi-Step Tasks:
   Chain dependent sub-tasks using "depends_on": ["n1"].
4. Simple / Single-Faceted Research:
   Return a single retriever node. Do NOT over-decompose simple questions.
5. Direct / Conversational:
   Return empty nodes [] if the task can be answered without retrieval.

Example (Comparison):
Task: "Tom Holland vs Zendaya who has more blockbusters"
{
  "nodes": [
    {"node_id": "n1", "subagent_type": "retriever", "task": "Tom Holland films box office gross and blockbuster career statistics", "depends_on": []},
    {"node_id": "n2", "subagent_type": "retriever", "task": "Zendaya films box office gross and blockbuster career statistics", "depends_on": []}
  ],
  "fan_out_eligible": true
}

Example (Code & Concept):
Task: "How to implement WebSocket authentication in FastAPI"
{
  "nodes": [
    {"node_id": "n1", "subagent_type": "retriever", "task": "FastAPI WebSocket authentication security best practices documentation", "depends_on": []},
    {"node_id": "n2", "subagent_type": "code_retriever", "task": "FastAPI WebSocket auth middleware implementation code examples", "depends_on": []}
  ],
  "fan_out_eligible": false
}

Respond with ONLY a raw JSON object:"""


async def decompose_task(
    task: str,
    gate_mode: str = "SEMANTIC",
    *,
    client: Optional[NIMClient] = None,
    satisfaction_tracker: Optional[SatisfactionTracker] = None,
    intent_analysis=None,
    related_concepts: Optional[list[str]] = None,
) -> Decomposition:
    """
    DYNAMIC task decomposition using intent classification.
    
    Instead of rigid comparison detection, this uses flexible intent analysis
    to understand query purpose and focus areas, then decomposes accordingly.
    
    Supports:
    - Narrative queries with multiple entities (no false-positive comparisons)
    - Multi-task decomposition with dynamic focus area tracking
    - Satisfaction layer guidance for retrieval depth
    - Attention mechanism for multiple focus areas
    """
    client = client or get_client()

    # Fast-path: if gate already decided, skip LLM decomposition
    if gate_mode == "PARAMETRIC":
        return Decomposition()  # no delegation
    
    # COMPUTATION mode: execute code directly, don't retrieve
    if gate_mode == "COMPUTATION":
        return Decomposition(nodes=[
            TaskNode("n1", SubagentType.CODE_GEN_EXECUTOR, task),
        ])

    # ── STAGE 1: INTENT CLASSIFICATION ──
    # Reuse intent_analysis from query.py if provided (avoids duplicate LLM/heuristic call)
    if not intent_analysis and gate_mode == "SEMANTIC":
        try:
            classifier = IntentClassifier(client=client)
            intent_analysis = await classifier.analyze(
                task,
                satisfaction_tracker=satisfaction_tracker,
                use_llm=True,
            )
            logger.debug(
                f"Intent analysis: {intent_analysis.intent.value}, "
                f"focus_areas={[fa.name for fa in intent_analysis.focus_areas]}, "
                f"decomposition={intent_analysis.suggested_decomposition}"
            )
        except Exception as e:
            logger.error(f"Intent classification failed ({type(e).__name__}): {e}")
            logger.warning(f"Falling back to default SEMANTIC decomposition for query: {task[:100]}")
            intent_analysis = None
    
    # ── STAGE 2: DYNAMIC DECOMPOSITION based on intent ──
    
    # If we have intent analysis, use it to guide decomposition
    if intent_analysis:
        focus_areas = intent_analysis.focus_areas
        
        # COMPARISON INTENT or DECISION with multiple entities: Create parallel nodes for each
        if (intent_analysis.intent == QueryIntent.COMPARISON or 
            (intent_analysis.intent == QueryIntent.DECISION and len(focus_areas) >= 2)) and len(focus_areas) >= 2:
            nodes = []
            entity_names = [fa.name for fa in focus_areas]
            
            # ── LLM-POWERED entity research planning ──
            # Instead of "search entity name", LLM generates SPECIFIC questions
            try:
                from ..core.llm_reasoning import generate_entity_research_plans
                research_plans = await generate_entity_research_plans(
                    task, entity_names, client=client,
                )
            except Exception as e:
                logger.warning("LLM research planning failed: %s, using fallback", e)
                research_plans = []
            
            if research_plans:
                # Create nodes from LLM-generated research plans
                for i, plan in enumerate(research_plans):
                    for j, search_query in enumerate(plan.search_queries[:3]):
                        node_id = f"entity_{i}_{plan.entity.replace(' ', '_')}_q{j}"
                        nodes.append(TaskNode(
                            node_id=node_id,
                            subagent_type=SubagentType.RETRIEVER,
                            task=search_query,
                            depends_on=[],
                        ))
                
                logger.info(
                    "LLM research plans: %s → %d search nodes",
                    [(p.entity, p.search_queries[:2]) for p in research_plans],
                    len(nodes),
                )
            else:
                # Fallback: Create one retriever node per entity with generic task
                for i, focus_area in enumerate(focus_areas[:5]):
                    node_id = f"focus_area_{i}_{focus_area.name.replace(' ', '_')}"
                    
                    depth_hint = focus_area.retrieval_depth
                    if satisfaction_tracker:
                        satisfaction_level = satisfaction_tracker.get_concept_satisfaction(focus_area.name)
                        if satisfaction_level < 0.5:
                            depth_hint = "comprehensive"
                        elif satisfaction_level > 0.8:
                            depth_hint = "summary"
                    
                    entity_task = (
                        f"Retrieve {depth_hint} information about {focus_area.name}. "
                        f"Context: {task}\n\n"
                        f"Entity type: {focus_area.entity_type}\n"
                        f"Relevance level: {focus_area.relationship_type}\n"
                        f"Include key facts, properties, metrics, and comparability dimensions."
                    )
                    if related_concepts:
                        entity_task += f"\nRelated concepts: {', '.join(related_concepts[:4])}"
                    
                    nodes.append(TaskNode(
                        node_id=node_id,
                        subagent_type=SubagentType.RETRIEVER,
                        task=entity_task,
                        depends_on=[],
                    ))
            
            logger.info(
                f"Comparison decomposition: intent={intent_analysis.intent.value}, "
                f"entities={entity_names}, nodes={len(nodes)}, confidence={intent_analysis.confidence:.2f}"
            )
            
            return Decomposition(
                nodes=nodes,
                fan_out_eligible=True,
                is_comparison=True,
                comparison_entities=entity_names,
            )
        
        # MULTI_TASK INTENT or multiple focus areas: Parallel independent tasks
        elif intent_analysis.requires_parallel and len(focus_areas) > 1:
            nodes = []
            
            # Create one node per focus area
            for i, focus_area in enumerate(focus_areas[:5]):  # Limit to 5
                node_id = f"task_{i}_{focus_area.name.replace(' ', '_')}"
                task_text = (
                    f"Focus area: {focus_area.name}\n"
                    f"Type: {focus_area.entity_type}\n"
                    f"Relationship: {focus_area.relationship_type}\n"
                    f"Depth: {focus_area.retrieval_depth}\n\n"
                    f"In context of: {task}"
                )
                
                nodes.append(TaskNode(
                    node_id=node_id,
                    subagent_type=SubagentType.RETRIEVER,
                    task=task_text,
                    depends_on=[],  # Parallel
                ))
            
            logger.info(
                f"Multi-task decomposition: {len(nodes)} parallel tasks for "
                f"focus areas {[fa.name for fa in focus_areas[:5]]}"
            )
            
            return Decomposition(
                nodes=nodes,
                fan_out_eligible=True,
                is_comparison=False,
            )
        
        # NARRATIVE or SINGLE-FOCUS: Normal single retriever
        # (Don't force comparison for multi-entity narratives)
        else:
            if len(focus_areas) > 1:
                # Multiple concepts but narrative intent → single retriever with multi-concept guidance
                focus_names = [fa.name for fa in focus_areas[:3]]
                task_with_context = (
                    f"{task}\n\n"
                    f"[NOTE: This query involves multiple concepts: {', '.join(focus_names)}. "
                    f"Retrieve information that covers the relationships and interactions between them.]"
                )
                if related_concepts:
                    task_with_context += f"\n[Related KG context: {', '.join(related_concepts[:4])}]"
            else:
                task_with_context = task
                if related_concepts:
                    task_with_context += f"\n[Related KG context: {', '.join(related_concepts[:4])}]"
            
            logger.info(
                f"Single-retriever decomposition: intent={intent_analysis.intent.value}, "
                f"focus_count={len(focus_areas)}"
            )
            
            return Decomposition(nodes=[
                TaskNode("n1", SubagentType.RETRIEVER, task_with_context),
            ])
    
    # ── PHASE 1 FIX: Aspect Extraction (NEW - replaces entity-based comparison) ──
    
    # Try aspect-based extraction for all queries (works for facts, comparisons, how-tos)
    # This is the PREFERRED approach - more general and flexible than entity comparison
    if gate_mode == "SEMANTIC":
        try:
            extractor = AspectExtractor(client=client)
            aspect_result = await extractor.extract(task, use_llm=False)
            
            # If we have high-confidence aspects, use them for parallel retrieval
            if aspect_result.aspects and aspect_result.confidence > 0.6:
                nodes = []
                aspect_names = [a.name for a in aspect_result.aspects]
                
                # Create one retriever node per aspect (parallel, no dependencies)
                for i, aspect in enumerate(aspect_result.aspects[:6]):  # Limit to 6 aspects
                    node_id = f"aspect_{i}_{aspect.name.replace(' ', '_')[:30]}"
                    
                    aspect_task = (
                        f"Retrieve {aspect.description} (target: ~{aspect.depth_target} tokens).\n"
                        f"Original query: {task}\n\n"
                        f"Focus specifically on this aspect. Be comprehensive and find surprising details."
                    )
                    
                    nodes.append(TaskNode(
                        node_id=node_id,
                        subagent_type=SubagentType.RETRIEVER,
                        task=aspect_task,
                        depends_on=[],  # Parallel execution
                    ))
                
                logger.info(
                    f"Aspect-based extraction (PHASE 1): {len(nodes)} parallel retrievals for "
                    f"aspects {aspect_names}, confidence={aspect_result.confidence:.2f}"
                )
                
                return Decomposition(
                    nodes=nodes,
                    fan_out_eligible=True,
                    is_comparison=False,  # Note: not marked as "comparison" anymore
                )
        except Exception as e:
            logger.debug(f"Aspect extraction failed: {e}, falling back to comparison detection")
    
    # ── FALLBACK: Original comparison detection (if aspect extraction failed) ──
    # Enhanced with intelligent subquery generation (PHASE 2)
    
    if gate_mode == "SEMANTIC":
        try:
            detector = ComparisonQueryDetector(client=client)
            comparison_result = await detector.detect(task)
        except Exception as e:
            logger.debug(f"Comparison detection failed: {e}")
            comparison_result = None
        
        # Only proceed if actually has comparison keywords (strict mode after fix)
        if comparison_result and comparison_result.is_comparison and len(comparison_result.entities) > 1:
            entity_names = [e.name for e in comparison_result.entities]
            
            logger.info(f"Comparison detected: {entity_names}, generating intelligent subqueries")
            
            # PHASE 2: Generate intelligent subqueries using dimension-based approach
            # Instead of just retrieving entities, retrieve dimensions + progressive queries
            try:
                gen = SubqueryGenerator(client=client)
                subquery_plan = await gen.generate_comparison_plan(task, entity_names)
                
                # Prioritize queries based on importance
                all_queries = gen.prioritize_queries(subquery_plan, max_queries=8)
                
                # Create retriever nodes for each query
                nodes = []
                for i, query_task in enumerate(all_queries):
                    node_id = f"compare_query_{i}_{hash(query_task) % 10000:04d}"
                    
                    nodes.append(TaskNode(
                        node_id=node_id,
                        subagent_type=SubagentType.RETRIEVER,
                        task=query_task,
                        depends_on=[],  # All parallel
                    ))
                
                logger.info(
                    f"Comparison with subqueries (PHASE 2): {len(nodes)} parallel queries "
                    f"across {len(subquery_plan.dimension_queries)} dimensions, "
                    f"confidence={subquery_plan.confidence:.2f}"
                )
                
                return Decomposition(
                    nodes=nodes,
                    fan_out_eligible=True,
                    is_comparison=True,
                    comparison_entities=entity_names,
                )
            except Exception as e:
                logger.debug(f"Subquery generation failed: {e}, using simple comparison fallback")
            
            # Fallback: Simple entity-based comparison if subquery generation fails
            nodes = []
            for i, entity in enumerate(comparison_result.entities):
                node_id = f"compare_entity_{i}_{entity.name.replace(' ', '_')}"
                entity_task = (
                    f"Retrieve detailed information about {entity.name}. "
                    f"In context: {task}"
                )
                
                nodes.append(TaskNode(
                    node_id=node_id,
                    subagent_type=SubagentType.RETRIEVER,
                    task=entity_task,
                    depends_on=[],
                ))
            
            return Decomposition(
                nodes=nodes,
                fan_out_eligible=True,
                is_comparison=True,
                comparison_entities=entity_names,
            )

    if gate_mode == "CODE":
        return Decomposition(nodes=[
            TaskNode("n1", SubagentType.CODE_RETRIEVER, task),
        ])

    # Non-comparison SEMANTIC or default
    if gate_mode == "SEMANTIC":
        return Decomposition(nodes=[
            TaskNode("n1", SubagentType.RETRIEVER, task),
        ])

    # HYBRID or complex — use LLM to decompose
    try:
        messages = [
            {"role": "system", "content": _DECOMPOSE_SYSTEM},
            {"role": "user", "content": f"Task: {task}\nGate mode: {gate_mode}"},
        ]
        raw = await client.chat_fast(messages, temperature=0.0, response_format_json=True)
        clean = re.sub(r'<think>.*?</think>', '', str(raw), flags=re.DOTALL).strip()
        match = re.search(r'```(?:json)?\s*([\s\S]*?)(?:```|$)', clean)
        if match:
            clean = match.group(1).strip()
        start = clean.find('{')
        end = clean.rfind('}')
        if start != -1 and end != -1 and end > start:
            clean = clean[start:end+1]
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            cleaned = re.sub(r',\s*([\]}])', r'\1', clean)
            parsed = json.loads(cleaned)

        nodes = []
        for n in parsed.get("nodes", []):
            st = n.get("subagent_type", "retriever")
            if st not in ("retriever", "code_retriever", "code_gen_executor"):
                st = "retriever"
            nodes.append(TaskNode(
                node_id=n.get("node_id", f"n{len(nodes)}"),
                subagent_type=SubagentType(st),
                task=n.get("task", task),
                depends_on=n.get("depends_on", []),
            ))

        return Decomposition(
            nodes=nodes,
            fan_out_eligible=parsed.get("fan_out_eligible", False),
        )
    except Exception as e:
        logger.warning("Decompose LLM failed: %s, using single retriever", e)
        return Decomposition(nodes=[
            TaskNode("n1", SubagentType.RETRIEVER, task),
        ])


# ── Topological layer execution ──

def _topological_layers(nodes: list[TaskNode]) -> list[list[TaskNode]]:
    """Groups nodes into layers for parallel execution."""
    remaining = {n.node_id: n for n in nodes}
    done: set[str] = set()
    layers: list[list[TaskNode]] = []

    while remaining:
        ready = [n for n in remaining.values() if all(d in done for d in n.depends_on)]
        if not ready:
            raise ValueError(f"Unresolvable dependencies: {list(remaining)}")
        layers.append(ready)
        for n in ready:
            done.add(n.node_id)
            del remaining[n.node_id]

    return layers


# ── Main orchestrator ──

async def run_orchestrator(
    task: str,
    run_subagent: RunSubagentFn,
    gate_mode: str = "SEMANTIC",
    *,
    client: Optional[NIMClient] = None,
    registry: Optional[SubagentRegistry] = None,
    max_subagents: int = DEFAULT_MAX_SUBAGENTS,
    thinking_profile: Optional[ThinkingProfile] = None,
    satisfaction: Optional[SatisfactionTracker] = None,
    intent_analysis=None,
) -> dict[str, SubagentResult]:
    """
    Entry point. Returns node_id → SubagentResult for every node that ran.
    Empty dict = no delegation needed (caller should answer directly).
    
    Args:
        thinking_profile: Adaptive thinking parameters for prompt complexity
        satisfaction: User satisfaction tracker for reward/punishment feedback
    """
    orch_start_time = time.time()
    registry = registry or SubagentRegistry()
    client = client or get_client()
    coordinator = get_state_coordinator()  # Phase 5: Get state coordinator

    # Query knowledge graph context and decompose with KG awareness
    features_enabled = getattr(thinking_profile, 'features_enabled', None) if thinking_profile else None
    related_concepts = []
    try:
        related_concepts = await query_knowledge_graph_for_context(task, features_enabled)
    except Exception as e:
        logger.debug("KG context lookup failed: %s", e)
        related_concepts = []

    try:
        decomposition = await decompose_task(
            task, 
            gate_mode, 
            client=client,
            satisfaction_tracker=satisfaction,
            intent_analysis=intent_analysis,  # Reuse from query.py — skip duplicate classification
            related_concepts=related_concepts,
        )
    except Exception as e:
        logger.error("Decomposition failed: %s", e)
        return {}

    if not decomposition or not decomposition.nodes:
        return {}

    # Adjust max_subagents based on thinking depth if profile provided
    if thinking_profile and thinking_profile.max_depth >= 4:
        max_subagents = min(max_subagents + 2, FAN_OUT_MAX_SUBAGENTS)
    
    effective_max = FAN_OUT_MAX_SUBAGENTS if decomposition.fan_out_eligible else max_subagents
    nodes = decomposition.nodes[:effective_max]
    layers = _topological_layers(nodes)
    results: dict[str, SubagentResult] = {}

    # Phase 5: Register all nodes with state coordinator
    for node in nodes:
        metadata = {
            "subagent_type": node.subagent_type.value,
            "task_preview": node.task[:100],
            "is_comparison": decomposition.is_comparison,
        }
        coordinator.register(node.node_id, f"Retrieve: {node.task[:60]}...", metadata)

    # Phase 1 + Phase 5: Log comparison query detection with entities
    if decomposition.is_comparison:
        logger.info(
            f"🔄 Comparison Query Detected: {decomposition.comparison_entities} | "
            f"Executing {len(nodes)} parallel retrievals (nodes: {[n.node_id for n in nodes[:3]]}...)"
        )
    else:
        logger.info(f"Orchestrator: {len(nodes)} nodes in {len(layers)} layers")

    for layer_idx, layer in enumerate(layers):
        layer_descriptions = [f"{n.node_id}({n.task[:30]}...)" for n in layer]
        logger.info(
            f"▶️  Layer {layer_idx + 1}/{len(layers)}: Executing {len(layer)} parallel operations\n"
            f"    {' | '.join(layer_descriptions)}"
        )
        
        async def _dispatch(node: TaskNode) -> tuple[str, SubagentResult]:
            # Phase 5: Track operation with state coordinator
            await coordinator.start(node.node_id)
            
            try:
                if registry.is_circuit_broken(node.subagent_type):
                    await coordinator.fail(node.node_id, "circuit_broken")
                    return node.node_id, SubagentResult(
                        subagent_type=node.subagent_type,
                        success=False,
                        error_reason="circuit_broken",
                    )

                # Fold upstream results into payload
                upstream = {dep: results[dep] for dep in node.depends_on if dep in results}
                payload = {"upstream": upstream} if upstream else {}
                
                # Add knowledge graph context (Tier 1)
                if related_concepts:
                    payload["related_concepts"] = related_concepts
                
                # Include thinking profile and satisfaction context for subagent decisions
                if thinking_profile:
                    payload["thinking_profile"] = {
                        "max_depth": thinking_profile.max_depth,
                        "budget_s": thinking_profile.budget_s,
                        "prompt_specificity": thinking_profile.prompt_specificity,
                        "use_deep_propositions": thinking_profile.use_deep_propositions,
                        "use_critique": thinking_profile.use_critique,
                    }
                if satisfaction:
                    payload["satisfaction"] = satisfaction
                
                sub_input = SubagentInput(
                    task=node.task,
                    subagent_type=node.subagent_type,
                    payload=payload,
                )

                # Execute with pivot loop for failure recovery
                last_result = [None]

                async def first_action():
                    last_result[0] = await run_subagent(sub_input)
                    return Observation(
                        succeeded=last_result[0].success,
                        detail=last_result[0].error_reason,
                    )

                def gen_hypotheses(_goal, _obs):
                    return [
                        Hypothesis("H_transient", "transient failure — retry may work", prior=0.5),
                        Hypothesis(
                            "H_wrong_subagent", "wrong subagent type for this subtask",
                            prior=0.5, implies_circuit_break=node.subagent_type.value,
                        ),
                    ]

                async def discriminate(_h_a, _h_b):
                    last_result[0] = await run_subagent(sub_input)
                    return Observation(
                        succeeded=last_result[0].success,
                        detail=last_result[0].error_reason,
                    )

                decision, branching_options = await run_pivot_loop(
                    goal=node.task,
                    first_action=first_action,
                    generate_hypotheses=gen_hypotheses,
                    run_discriminating_experiment=discriminate,
                    branching_enabled=thinking_profile.branching_enabled if thinking_profile else False,
                )

                for name in decision.circuit_break:
                    registry.record_failure(name)
                if last_result[0] and branching_options:
                    try:
                        setattr(last_result[0], 'branching_options', branching_options)
                    except Exception:
                        pass
                if last_result[0] and last_result[0].success:
                    registry.record_success(node.subagent_type.value)
                    # Phase 5: Mark as complete
                    await coordinator.complete(node.node_id)
                else:
                    # Phase 5: Mark as failed
                    await coordinator.fail(node.node_id, decision.next_action or "unknown_error")

                return node.node_id, last_result[0] or SubagentResult(
                    subagent_type=node.subagent_type,
                    success=False,
                    error_reason=decision.next_action,
                )
            
            except Exception as e:
                # Phase 5: Track exception
                await coordinator.fail(node.node_id, str(e))
                logger.exception(f"Node {node.node_id} raised exception: {e}")
                return node.node_id, SubagentResult(
                    subagent_type=node.subagent_type,
                    success=False,
                    error_reason=f"exception: {str(e)[:100]}",
                )

        layer_results = await asyncio.gather(*(_dispatch(n) for n in layer), return_exceptions=False)
        for node_id, result in layer_results:
            results[node_id] = result
        
        # Phase 5: Log layer completion with detailed status
        layer_status = {
            "total": len(layer),
            "successful": sum(1 for _, r in layer_results if r.success),
            "failed": sum(1 for _, r in layer_results if not r.success),
        }
        status = coordinator.get_status()
        logger.info(
            f"✅ Layer {layer_idx + 1}/{len(layers)} complete: "
            f"{layer_status['successful']}/{layer_status['total']} succeeded | "
            f"Overall: {status['completed']}/{status['total']} ({status['overall_progress']:.0f}%)"
        )

    # Phase 5: Final status with summary
    final_status = coordinator.get_status()
    successful_results = sum(1 for r in results.values() if r.success)
    failed_results = len(results) - successful_results
    orch_elapsed = time.time() - orch_start_time
    
    logger.info(
        f"\n🏁 ORCHESTRATOR COMPLETE\n"
        f"   Total nodes: {len(results)}\n"
        f"   Successful: {successful_results} ✅\n"
        f"   Failed: {failed_results} ❌\n"
        f"   Success rate: {100*successful_results/len(results) if results else 0:.1f}%\n"
        f"   Comparison query: {'Yes' if any('compare' in r for r in results) else 'No'}\n"
        f"   Orchestration time: {orch_elapsed:.2f}s"
    )

    # ── Retry failed nodes with simplified queries ──
    if failed_results > 0 and results:
        success_rate = successful_results / len(results)
        if success_rate < 0.6:  # More than 40% failed
            failed_nodes = [
                node for layer in layers for node in layer
                if node.node_id in results and not results[node.node_id].success
            ]
            if failed_nodes:
                logger.info(
                    "🔄 Retrying %d failed nodes with simplified queries...",
                    len(failed_nodes),
                )
                retry_coros = []
                for node in failed_nodes[:3]:  # Cap retries at 3
                    # Simplify task: take first 10 words
                    simple_task = " ".join(node.task.split()[:10])
                    retry_inp = SubagentInput(
                        task=simple_task,
                        subagent_type=node.subagent_type,
                    )
                    retry_coros.append(run_subagent(retry_inp))

                try:
                    retry_results = await asyncio.gather(
                        *retry_coros, return_exceptions=True
                    )
                    for node, retry_result in zip(failed_nodes[:3], retry_results):
                        if isinstance(retry_result, Exception):
                            logger.warning("Retry failed for %s: %s", node.node_id, retry_result)
                            continue
                        if retry_result and retry_result.success:
                            results[node.node_id] = retry_result
                            logger.info("✅ Retry succeeded for %s", node.node_id)
                except Exception as e:
                    logger.warning("Retry batch failed: %s", e)
    
    return results
