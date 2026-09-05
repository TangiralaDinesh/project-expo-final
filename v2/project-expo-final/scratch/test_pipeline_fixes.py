import asyncio
import sys
import os
from unittest.mock import MagicMock

for mod in ["aiohttp", "pydantic", "numpy"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from agent.core.llm_reasoning import (
    _heuristic_evaluation,
    decide_next_action,
    ReasoningDecision,
)
from agent.core.types import Learning
from agent.core.satisfaction import SatisfactionTracker
from agent.core.intent_classifier import (
    IntentClassifier,
    QueryIntent,
    QueryIntentAnalysis,
    FocusArea,
)

def test_heuristic_evaluation_math():
    query = "tom holland or zebdya who have more block busters"
    # Create 10 mock learnings mentioning tom holland
    learnings = [
        Learning(text=f"Spider-Man starring Tom Holland had massive box office hit #{i}")
        for i in range(10)
    ]
    eval_result = _heuristic_evaluation(query, learnings)
    print(f"1. Heuristic evaluation quality score: {eval_result.quality_score:.2f}")
    assert 0.0 <= eval_result.quality_score <= 1.0, f"Quality score {eval_result.quality_score} out of bounds!"
    assert eval_result.quality_score <= 1.0, f"Quality score exploded: {eval_result.quality_score}"
    print("   [PASS] Math bug fixed: coverage is bounded in [0.0, 1.0]")

async def test_decide_next_action_round1_resilience():
    query = "tom holland or zebdya who have more block busters"
    learnings = [Learning(text="some data")]
    
    # Test fallback behavior when JSON parse fails
    # Calling decide_next_action with dummy client that returns unparseable text
    class BrokenClient:
        async def chat_worker(self, *args, **kwargs):
            return "Broken unparseable output {"

    decision = await decide_next_action(
        query=query,
        learnings=learnings,
        quality_score=0.5,
        round_num=1,
        client=BrokenClient(),
    )
    print(f"2. Decision on Round 1 parse failure: action={decision.action}, reasoning={decision.reasoning}")
    assert decision.action == "continue", f"Expected action='continue' on Round 1, got '{decision.action}'"
    print("   [PASS] Round 1 resilience verified: does not stop on transient parse failure")

def test_satisfaction_concept_extraction():
    tracker = SatisfactionTracker()
    query = "tom holland or zebdya who have more block busters"
    known = ["Tom Holland", "Zendaya"]
    
    concepts = tracker.extract_concepts(query, known_entities=known)
    print(f"3. Satisfaction concepts with known entities: {concepts}")
    assert "Tom Holland" in concepts
    assert "Zendaya" in concepts
    assert "query_topic" not in concepts
    print("   [PASS] Satisfaction concept extraction verified: keeps clean multi-word entities")

def test_intent_blend_results():
    classifier = IntentClassifier()
    heuristic = QueryIntentAnalysis(
        intent=QueryIntent.RESEARCH,
        confidence=0.4,
        focus_areas=[FocusArea(name="query_topic", entity_type="concept", relevance=1.0, relationship_type="primary", retrieval_depth="comprehensive")],
        requires_comparison=False,
        requires_parallel=False,
        suggested_decomposition="single",
        reasoning="heuristic",
    )
    llm = QueryIntentAnalysis(
        intent=QueryIntent.COMPARISON,
        confidence=0.85,
        focus_areas=[
            FocusArea(name="Tom Holland", entity_type="person", relevance=1.0, relationship_type="primary", retrieval_depth="comprehensive"),
            FocusArea(name="Zendaya", entity_type="person", relevance=0.85, relationship_type="comparison", retrieval_depth="detailed"),
        ],
        requires_comparison=True,
        requires_parallel=True,
        suggested_decomposition="parallel_with_synthesis",
        reasoning="llm",
    )
    blended = classifier._blend_results(heuristic, llm, "tom holland or zebdya")
    names = [fa.name for fa in blended.focus_areas]
    print(f"4. Blended focus areas: {names}")
    assert "query_topic" not in names, "Placeholder 'query_topic' was not removed!"
    assert "Tom Holland" in names
    assert "Zendaya" in names
    assert blended.requires_comparison is True
    print("   [PASS] Intent blending verified: clean focus areas and correct comparison flags")

async def test_multitask_and_typo_decomposition():
    from agent.orchestrator.orchestrator import decompose_task
    import json
    
    # 1. Test comparison with lowercase and typo query:
    query_comp = "tom holland or zebdya who have more block busters"
    class MockClientComparison:
        async def chat_worker(self, messages, *args, **kwargs):
            # Model entity extraction returns clean entities with typo fixed
            return '["Tom Holland", "Zendaya"]'
        async def chat_fast(self, messages, *args, **kwargs):
            return json.dumps({
                "intent": "comparison",
                "confidence": 0.9,
                "focus_areas": [
                    {"name": "Tom Holland", "type": "person", "relevance": 1.0},
                    {"name": "Zendaya", "type": "person", "relevance": 0.9}
                ],
                "is_comparison": True,
                "is_parallel": True,
                "decomposition": "parallel",
                "reasoning": "Comparison between two actors"
            })
    
    decomp_comp = await decompose_task(query_comp, gate_mode="SEMANTIC", client=MockClientComparison())
    print(f"5. Comparison decomposition nodes count: {len(decomp_comp.nodes)}")
    assert len(decomp_comp.nodes) >= 2, f"Expected at least 2 nodes, got {len(decomp_comp.nodes)}"
    assert decomp_comp.is_comparison is True
    print("   [PASS] Comparison query decomposed into parallel entity nodes!")

    # 2. Test multi-task query: "research about elephant and create pptx of it"
    query_multi = "research about elephant and create pptx of it"
    class MockClientMultiTask:
        async def chat_worker(self, messages, *args, **kwargs):
            return '["Elephant Research", "Presentation Creation"]'
        async def chat_fast(self, messages, *args, **kwargs):
            return json.dumps({
                "intent": "multi_task",
                "confidence": 0.95,
                "focus_areas": [
                    {"name": "Elephant Research", "type": "concept", "relevance": 1.0},
                    {"name": "Presentation Creation", "type": "task", "relevance": 0.8}
                ],
                "is_comparison": False,
                "is_parallel": True,
                "decomposition": "parallel",
                "reasoning": "Two distinct tasks: researching elephants and generating a presentation"
            })
    
    decomp_multi = await decompose_task(query_multi, gate_mode="SEMANTIC", client=MockClientMultiTask())
    print(f"6. Multi-task decomposition nodes count: {len(decomp_multi.nodes)}")
    assert len(decomp_multi.nodes) >= 2, f"Expected at least 2 nodes for multi-task, got {len(decomp_multi.nodes)}"
    assert decomp_multi.fan_out_eligible is True
    print("   [PASS] Multi-task query dynamically decomposed into parallel tasks!")

async def main():
    print("=== RUNNING REGRESSION & PIPELINE VERIFICATION ===")
    test_heuristic_evaluation_math()
    await test_decide_next_action_round1_resilience()
    test_satisfaction_concept_extraction()
    test_intent_blend_results()
    await test_multitask_and_typo_decomposition()
    print("=== ALL 6 VERIFICATION TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    asyncio.run(main())

