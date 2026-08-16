"""
Feature flags for Tier-based rollout of reasoning improvements.

Controls which features are enabled:
- Tier 1: Connectivity (feedback loops, active pivot, knowledge graph)
- Tier 2: Progressive Depth Revelation (conceptual zoom levels)
- Tier 3: Bayesian Branching (present competing hypotheses)
- Tier 4: Code Execution (dynamic code generation + execution)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeatureFlags:
    """Control new features during rollout"""
    
    # TIER 1: Connectivity
    connectivity_enabled: bool = False          # Feed satisfaction into thinking_profile?
    active_pivot_enabled: bool = False          # Use pivot loop actively in orchestrator?
    knowledge_graph_queries_enabled: bool = False  # Query graph for related concepts?
    
    # TIER 2: Progressive Depth Revelation
    progressive_zoom_enabled: bool = False      # Support conceptual depth levels?
    
    # TIER 3: Bayesian Branching
    bayesian_branching_enabled: bool = False    # Present competing hypotheses to user?
    
    # TIER 4: Code Execution
    code_execution_enabled: bool = True        # Enable dynamic code generation + execution? (now enabled by default)
    
    @staticmethod
    def all_off() -> FeatureFlags:
        """Run with old behavior (backward compatible)"""
        return FeatureFlags()
    
    @staticmethod
    def tier_1_only() -> FeatureFlags:
        """Enable connectivity foundation + code execution"""
        return FeatureFlags(
            connectivity_enabled=True,
            active_pivot_enabled=True,
            knowledge_graph_queries_enabled=True,
            code_execution_enabled=True,
        )
    
    @staticmethod
    def tiers_1_2() -> FeatureFlags:
        """Enable connectivity + progressive revelation"""
        return FeatureFlags(
            connectivity_enabled=True,
            active_pivot_enabled=True,
            knowledge_graph_queries_enabled=True,
            progressive_zoom_enabled=True,
        )
    
    @staticmethod
    def tiers_1_3() -> FeatureFlags:
        """Enable connectivity + branching"""
        return FeatureFlags(
            connectivity_enabled=True,
            active_pivot_enabled=True,
            knowledge_graph_queries_enabled=True,
            bayesian_branching_enabled=True,
        )
    
    @staticmethod
    def all_on() -> FeatureFlags:
        """Full new system (all tiers)"""
        return FeatureFlags(
            connectivity_enabled=True,
            active_pivot_enabled=True,
            knowledge_graph_queries_enabled=True,
            progressive_zoom_enabled=True,
            bayesian_branching_enabled=True,
            code_execution_enabled=True,
        )
