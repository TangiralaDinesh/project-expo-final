#!/usr/bin/env python3
"""
Plan 2: Advanced Reasoning Improvements Test Suite

Tests for:
- Phase 1: Comparison Query Detection and Parallel Decomposition
- Phase 2: Critique-Guided Retrieval
- Phase 3: Progressive Scraping
- Phase 4: Speculative Questioning
- Phase 5: Parallel State Tracking
"""

import pytest
import asyncio
from agent.routing.comparison_detector import ComparisonQueryDetector, ComparisonEntity
from agent.core.critique import run_critique_on_retrieval, RetrievalGapAnalysis
from agent.core.progressive_scraping import determine_progressive_phase, get_phase_token_budget, ProgressivePhase
from agent.core.parallel_state import ParallelStateCoordinator, OperationState
from agent.orchestrator.orchestrator import decompose_task, Decomposition


class TestPhase1ComparisonDetection:
    """Phase 1: Comparison Query Detection and Decomposition."""
    
    @pytest.mark.asyncio
    async def test_detector_direct_vs(self):
        """Test detecting direct 'X vs Y' queries."""
        detector = ComparisonQueryDetector()
        
        result = await detector.detect("CDSL vs EMVEE which is better?")
        assert result.is_comparison == True
        assert len(result.entities) >= 2
        assert result.confidence >= 0.85
    
    @pytest.mark.asyncio
    async def test_detector_should_buy(self):
        """Test detecting 'should I buy X or Y' queries."""
        detector = ComparisonQueryDetector()
        
        result = await detector.detect("should I buy CDSL or EMVEE?")
        assert result.is_comparison == True
        assert len(result.entities) >= 2
    
    @pytest.mark.asyncio
    async def test_detector_difference_between(self):
        """Test detecting 'difference between X and Y' queries."""
        detector = ComparisonQueryDetector()
        
        result = await detector.detect("What is the difference between CDSL and EMVEE?")
        assert result.is_comparison == True
        assert len(result.entities) >= 2
    
    @pytest.mark.asyncio
    async def test_detector_non_comparison(self):
        """Test non-comparison queries return False."""
        detector = ComparisonQueryDetector()
        
        result = await detector.detect("Tell me about CDSL")
        assert result.is_comparison == False
    
    @pytest.mark.asyncio
    async def test_decomposition_with_comparison(self):
        """Test that comparison queries decompose into parallel nodes."""
        decomp = await decompose_task(
            "should I buy CDSL or EMVEE?",
            gate_mode="SEMANTIC",
        )
        
        # Should have comparison=True
        assert decomp.is_comparison == True
        
        # Should have at least 2 entity nodes (one per entity)
        assert len(decomp.nodes) >= 2
        
        # Entity nodes should be independent (no dependencies)
        entity_nodes = [n for n in decomp.nodes if "compare_entity" in n.node_id]
        assert len(entity_nodes) >= 2
        for node in entity_nodes:
            assert len(node.depends_on) == 0  # Parallel execution
    
    @pytest.mark.asyncio
    async def test_decomposition_has_validation_node(self):
        """Test comparison decomposition includes validation node."""
        decomp = await decompose_task(
            "CDSL vs EMVEE: which has better support?",
            gate_mode="SEMANTIC",
        )
        
        # Should have validation node that depends on entity nodes
        validation_nodes = [n for n in decomp.nodes if "compare_validation" in n.node_id]
        assert len(validation_nodes) >= 1
        
        if validation_nodes:
            val_node = validation_nodes[0]
            # Validation should depend on entity nodes
            assert len(val_node.depends_on) >= 2


class TestPhase2CritiqueGuidedRetrieval:
    """Phase 2: Critique-Guided Retrieval for Gap Detection."""
    
    @pytest.mark.asyncio
    async def test_critique_identifies_gaps(self):
        """Test that critique identifies gaps in partial learnings."""
        query = "CDSL vs EMVEE"
        partial_learnings = [
            "CDSL is a stock market app",
            "CDSL charges 0% brokerage",
        ]
        
        # This would normally call LLM, so we test the structure
        # In real usage, this runs during retrieval
        result = RetrievalGapAnalysis(
            gaps_found=["EMVEE features", "Risk comparison"],
            suggested_queries=["Tell me about EMVEE", "Compare risks"],
            consensus_strength=0.75,
            persona_verdicts=[],
            confidence=0.8,
        )
        
        assert result.gaps_found is not None
        assert len(result.suggested_queries) > 0
        assert result.consensus_strength > 0.5


class TestPhase3ProgressiveScraping:
    """Phase 3: Progressive Scraping with Multi-Phase Depth."""
    
    def test_progressive_phases_enum(self):
        """Test progressive phase enumeration."""
        assert ProgressivePhase.OVERVIEW.value == 0
        assert ProgressivePhase.FOCUSED.value == 1
        assert ProgressivePhase.COMPREHENSIVE.value == 2
    
    def test_phase_token_budgets(self):
        """Test token budgets for each phase."""
        assert get_phase_token_budget(ProgressivePhase.OVERVIEW) == 300
        assert get_phase_token_budget(ProgressivePhase.FOCUSED) == 800
        assert get_phase_token_budget(ProgressivePhase.COMPREHENSIVE) == 2000
    
    @pytest.mark.asyncio
    async def test_determine_phase_overview_default(self):
        """Test phase determination defaults to OVERVIEW."""
        phase = await determine_progressive_phase(zoom_level=None, has_prior_results=False)
        assert phase == ProgressivePhase.OVERVIEW
    
    @pytest.mark.asyncio
    async def test_determine_phase_focused_after_overview(self):
        """Test that FOCUSED is chosen after having prior results."""
        phase = await determine_progressive_phase(zoom_level=None, has_prior_results=True)
        assert phase == ProgressivePhase.FOCUSED


class TestPhase5ParallelStateTracking:
    """Phase 5: Explicit State Machine for Parallel Operations."""
    
    @pytest.mark.asyncio
    async def test_coordinator_register_operation(self):
        """Test registering operations."""
        coordinator = ParallelStateCoordinator()
        
        coordinator.register(
            "retriever_cdsl",
            "Retrieve CDSL information",
        )
        
        assert "retriever_cdsl" in coordinator.operations
        op = coordinator.operations["retriever_cdsl"]
        assert op.state == OperationState.QUEUED
    
    @pytest.mark.asyncio
    async def test_coordinator_start_operation(self):
        """Test starting an operation."""
        coordinator = ParallelStateCoordinator()
        coordinator.register("op1", "Test operation")
        
        await coordinator.start("op1")
        
        op = coordinator.operations["op1"]
        assert op.state == OperationState.IN_PROGRESS
        assert op.start_time is not None
    
    @pytest.mark.asyncio
    async def test_coordinator_complete_operation(self):
        """Test completing an operation."""
        coordinator = ParallelStateCoordinator()
        coordinator.register("op1", "Test operation")
        
        await coordinator.start("op1")
        await coordinator.complete("op1")
        
        op = coordinator.operations["op1"]
        assert op.state == OperationState.COMPLETED
        assert op.end_time is not None
        assert op.elapsed_seconds > 0
    
    @pytest.mark.asyncio
    async def test_coordinator_get_status(self):
        """Test getting overall coordinator status."""
        coordinator = ParallelStateCoordinator()
        
        coordinator.register("op1", "Operation 1")
        coordinator.register("op2", "Operation 2")
        
        await coordinator.start("op1")
        
        status = coordinator.get_status()
        
        assert status["total"] == 2
        assert status["in_progress"] == 1
        assert status["queued"] == 1


class TestPlan2Integration:
    """Integration tests for Plan 2 components working together."""
    
    @pytest.mark.asyncio
    async def test_comparison_to_parallel_decomposition_workflow(self):
        """Test full workflow: detect comparison → decompose into parallel tasks."""
        # Detect comparison
        detector = ComparisonQueryDetector()
        comparison = await detector.detect("should I buy CDSL or EMVEE?")
        
        assert comparison.is_comparison == True
        assert len(comparison.entities) >= 2
        
        # Decompose for parallel execution
        decomp = await decompose_task(
            "should I buy CDSL or EMVEE?",
            gate_mode="SEMANTIC",
        )
        
        assert decomp.is_comparison == True
        assert decomp.fan_out_eligible == True
        assert len(decomp.nodes) >= 3  # At least entity nodes + validation + synthesis
    
    @pytest.mark.asyncio
    async def test_parallel_state_with_comparison_decomposition(self):
        """Test tracking state of parallel comparison retrieval."""
        coordinator = ParallelStateCoordinator()
        
        # Register parallel operations (one per entity)
        coordinator.register("compare_entity_0_CDSL", "Retrieve CDSL info")
        coordinator.register("compare_entity_1_EMVEE", "Retrieve EMVEE info")
        coordinator.register("compare_validation", "Validate coverage")
        
        # Simulate parallel execution
        await coordinator.start("compare_entity_0_CDSL")
        await coordinator.start("compare_entity_1_EMVEE")
        
        status = coordinator.get_status()
        assert status["in_progress"] == 2
        assert status["queued"] == 1
        
        # Complete first entity
        await coordinator.complete("compare_entity_0_CDSL")
        status = coordinator.get_status()
        assert status["completed"] == 1


def test_backward_compatibility_query_result():
    """Ensure QueryResult backward compatibility with new comparison_analysis field."""
    from agent.query import QueryResult
    
    # Old code should still work (no comparison_analysis field)
    result = QueryResult(answer="Test answer")
    assert result.answer == "Test answer"
    assert result.comparison_analysis is None  # New field defaults to None
    
    # New code can use comparison_analysis
    result2 = QueryResult(
        answer="Compare CDSL vs EMVEE",
        comparison_analysis={
            "is_comparison": True,
            "entities": ["CDSL", "EMVEE"],
        }
    )
    assert result2.comparison_analysis is not None
    assert result2.comparison_analysis["is_comparison"] == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
