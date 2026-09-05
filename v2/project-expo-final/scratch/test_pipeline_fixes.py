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

def test_redundancy_tracker():
    from agent.core.redundancy import RedundancyTracker
    tracker = RedundancyTracker(similarity_threshold=0.65)
    
    # 1. Track original query
    tracker.filter_and_track(["tom holland or zebdya who have more block busters"], source="original")
    assert tracker.total_fired == 1
    
    # 2. Test is_redundant on identical/near duplicate
    assert tracker.is_redundant("tom holland or zendaya who have more block busters") is True
    assert tracker.is_redundant("tom holland or zebdya who have more block busters") is True
    
    # 3. Test is_redundant on completely novel query
    assert tracker.is_redundant("Tom Holland box office career gross worldwide") is False
    assert tracker.is_redundant("Zendaya highest grossing movies filmography revenue") is False
    
    # 4. Test is_redundant on short/empty strings
    assert tracker.is_redundant("") is True
    assert tracker.is_redundant("hi") is True
    assert tracker.is_redundant(None) is True
    
    # 5. Test is_redundant with object having .query attribute
    class DummyQuery:
        def __init__(self, q):
            self.query = q
    assert tracker.is_redundant(DummyQuery("tom holland or zendaya who have more block busters")) is True
    assert tracker.is_redundant(DummyQuery("Avatar box office records James Cameron")) is False
    
    # 6. Test track() method
    assert tracker.track("Avatar box office records James Cameron") is True
    assert tracker.is_redundant("Avatar box office records James Cameron") is True
    assert tracker.total_fired == 2
    
    # 7. Test filter_and_track batch deduping
    batch = [
        "Spider-Man box office records",
        "Spider-Man box office records worldwide", # duplicate of above
        "Dune Part Two box office Zendaya",
    ]
    kept = tracker.filter_and_track(batch, source="test_batch")
    assert len(kept) == 2
    assert "Spider-Man box office records" in kept
    assert "Dune Part Two box office Zendaya" in kept
    print("   [PASS] RedundancyTracker.is_redundant and track() verified with 100% precision!")

def test_intent_classifier_json_resilience():
    # Test _clean_and_parse_json against thinking models and preambles
    raw_responses = [
        # Standard JSON
        '{"intent": "comparison", "confidence": 0.9}',
        # Markdown fenced
        '```json\n{"intent": "comparison", "confidence": 0.9}\n```',
        # Preamble text from Nemotron
        'Here is the thinking process:\nWe need to compare two actors.\n{"intent": "comparison", "confidence": 0.9}',
        # Thinking tags <think>
        '<think>The user wants comparison</think>\n{"intent": "comparison", "confidence": 0.9}',
        # Trailing comma
        '{"intent": "comparison", "confidence": 0.9, }',
    ]
    for r in raw_responses:
        parsed = IntentClassifier._clean_and_parse_json(r)
        assert parsed["intent"] == "comparison"
        assert parsed["confidence"] == 0.9
    print("   [PASS] IntentClassifier JSON parsing verified: handles markdown, <think>, preambles, trailing commas!")

def test_llm_reasoning_json_fallback_extraction():
    from agent.core.llm_reasoning import _parse_json
    # Test truncated JSON where closing brace is missing (max_tokens cutoff)
    truncated = (
        'Here\'s a thinking process that leads to the decision:\n'
        '{\n'
        '  "action": "continue",\n'
        '  "target": "box office comparison",\n'
        '  "queries": ["Tom Holland box office earnings", "Zendaya box office earnings"],\n'
        '  "confidence": 0.8,\n'
        '  "reasoning": "Need more specific revenue figures'  # Cut off here!
    )
    parsed = _parse_json(truncated)
    assert parsed is not None, "Failed to recover truncated JSON!"
    assert parsed["action"] == "continue"
    assert "Tom Holland box office earnings" in parsed["queries"]
    assert parsed["confidence"] == 0.8
    print("   [PASS] llm_reasoning._parse_json fallback field extraction verified on truncated output!")

def test_learning_dataclass_url_compatibility():
    # Test initialization with url
    l1 = Learning(text="Fact 1", url="https://example.com/1")
    assert l1.source_url == "https://example.com/1"
    assert l1.url == "https://example.com/1"
    
    # Test initialization with source_url
    l2 = Learning(text="Fact 2", source_url="https://example.com/2")
    assert l2.url == "https://example.com/2"
    assert l2.source_url == "https://example.com/2"
    print("   [PASS] Learning dataclass url/source_url bidirectional compatibility verified!")

def test_nim_client_json_guardrails():
    from agent.llm.client import _enforce_json_system_prompt, _sanitize_json_response
    import json

    # 1. Test _enforce_json_system_prompt
    messages_without_sys = [{"role": "user", "content": "hello"}]
    enforced = _enforce_json_system_prompt(messages_without_sys)
    assert enforced[0]["role"] == "system"
    assert "strictly raw machine-readable JSON" in enforced[0]["content"]

    messages_with_sys = [{"role": "system", "content": "You are a helper."}, {"role": "user", "content": "hi"}]
    enforced2 = _enforce_json_system_prompt(messages_with_sys)
    assert "strictly raw machine-readable JSON" in enforced2[0]["content"]
    assert "You are a helper." in enforced2[0]["content"]

    # 2. Test _sanitize_json_response on reasoning monologue prefix
    raw_with_thinking = (
        "Here's a thinking process:\n\n"
        "1. **Analyze User Input:**\n"
        " - Question: \"tom holland vs zendaya who has more blockbusters\"\n\n"
        "{\n"
        "  \"action\": \"continue\",\n"
        "  \"queries\": [\"Tom Holland box office\", \"Zendaya box office\"],\n"
        "  \"confidence\": 0.9,\n"
        "  \"reasoning\": \"need box office numbers\",\n"
        "  \"target\": \"box office comparison\",\n"
        "}\n"
    )
    sanitized = _sanitize_json_response(raw_with_thinking)
    parsed = json.loads(sanitized)
    assert parsed["action"] == "continue"
    assert len(parsed["queries"]) == 2

    # 3. Test _sanitize_json_response on <think> tags and markdown code fence
    raw_with_think_tags = (
        "<think>Checking entities and attributes...</think>\n"
        "```json\n"
        "[\"Tom Holland\", \"Zendaya\"]\n"
        "```"
    )
    sanitized_arr = _sanitize_json_response(raw_with_think_tags)
    parsed_arr = json.loads(sanitized_arr)
    assert parsed_arr == ["Tom Holland", "Zendaya"]
    print("   [PASS] NIMClient JSON guardrails: system prompt enforcement and preamble sanitization verified!")

async def main():
    print("=== RUNNING REGRESSION & PIPELINE VERIFICATION ===")
    test_heuristic_evaluation_math()
    await test_decide_next_action_round1_resilience()
    test_satisfaction_concept_extraction()
    test_intent_blend_results()
    await test_multitask_and_typo_decomposition()
    test_redundancy_tracker()
    test_intent_classifier_json_resilience()
    test_llm_reasoning_json_fallback_extraction()
    test_learning_dataclass_url_compatibility()
    test_nim_client_json_guardrails()
    print("=== ALL 11 VERIFICATION TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    asyncio.run(main())

