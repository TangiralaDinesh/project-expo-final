"""
Progressive Revelation (Tier 2) — Depth-Adaptive Synthesis

Implements the geohash zoom model where users see:
  - Level 0: Overview (high-level summary) ~300 tokens
  - Level 1: Focused detail (specific examples) ~800 tokens
  - Level 2: Comprehensive (full treatment) ~2000 tokens

This allows progressive discovery without overwhelming the user initially.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ZoomLevel(str, Enum):
    """Conceptual zoom levels for responses"""
    LEVEL_0 = "overview"           # High-level summary
    LEVEL_1 = "focused"            # Focused detail
    LEVEL_2 = "comprehensive"      # Comprehensive treatment


@dataclass
class ZoomLevelConfig:
    """Configuration for a zoom level"""
    level: ZoomLevel
    token_budget: int              # Max tokens for this level
    time_budget_s: float           # Max time to spend
    depth_instructions: str        # Instructions to LLM for this depth
    
    @staticmethod
    def level_0() -> ZoomLevelConfig:
        """Overview level: high-level summary"""
        return ZoomLevelConfig(
            level=ZoomLevel.LEVEL_0,
            token_budget=300,
            time_budget_s=5.0,
            depth_instructions=(
                "Provide a brief, high-level overview (3-5 sentences). "
                "Focus on the key concept and main use cases. "
                "Avoid implementation details and advanced topics. "
                "Use clear, accessible language."
            )
        )
    
    @staticmethod
    def level_1() -> ZoomLevelConfig:
        """Focused detail: specific examples and explanations"""
        return ZoomLevelConfig(
            level=ZoomLevel.LEVEL_1,
            token_budget=800,
            time_budget_s=12.0,
            depth_instructions=(
                "Provide a detailed explanation with 1-2 concrete examples. "
                "Go deeper than overview but stay focused on the core topic. "
                "Include practical scenarios and typical use cases. "
                "Explain key concepts clearly with examples."
            )
        )
    
    @staticmethod
    def level_2() -> ZoomLevelConfig:
        """Comprehensive: full treatment with all details"""
        return ZoomLevelConfig(
            level=ZoomLevel.LEVEL_2,
            token_budget=2000,
            time_budget_s=30.0,
            depth_instructions=(
                "Provide a comprehensive treatment of the topic. "
                "Include implementation details, edge cases, and best practices. "
                "Cover multiple approaches and when to use each. "
                "Include code examples, warnings, and advanced topics. "
                "Structure with clear sections and headings."
            )
        )


@dataclass
class ZoomOptions:
    """Options available to user for zooming"""
    current_level: ZoomLevel
    can_zoom_in: bool              # Can go deeper?
    can_zoom_out: bool             # Can go shallower?
    next_zoom_in_prompt: str = "Zoom in for more details"
    next_zoom_out_prompt: str = "Show overview only"


def get_zoom_config(level: ZoomLevel) -> ZoomLevelConfig:
    """Get configuration for a zoom level"""
    configs = {
        ZoomLevel.LEVEL_0: ZoomLevelConfig.level_0(),
        ZoomLevel.LEVEL_1: ZoomLevelConfig.level_1(),
        ZoomLevel.LEVEL_2: ZoomLevelConfig.level_2(),
    }
    return configs[level]


def get_zoom_options(current_level: ZoomLevel | int) -> ZoomOptions:
    """
    Get available zoom options for current level.
    
    Args:
        current_level: ZoomLevel enum or integer (0, 1, 2)
    
    Returns:
        ZoomOptions with navigation info
    """
    # Convert integer to ZoomLevel if needed
    if isinstance(current_level, int):
        level_map = {0: ZoomLevel.LEVEL_0, 1: ZoomLevel.LEVEL_1, 2: ZoomLevel.LEVEL_2}
        current_level = level_map.get(current_level, ZoomLevel.LEVEL_0)
    
    can_zoom_in = current_level != ZoomLevel.LEVEL_2
    can_zoom_out = current_level != ZoomLevel.LEVEL_0
    
    return ZoomOptions(
        current_level=current_level,
        can_zoom_in=can_zoom_in,
        can_zoom_out=can_zoom_out,
        next_zoom_in_prompt="Zoom in for more details" if can_zoom_in else "",
        next_zoom_out_prompt="Show overview only" if can_zoom_out else "",
    )


def default_zoom_level() -> ZoomLevel:
    """Default zoom level for first response"""
    return ZoomLevel.LEVEL_0  # Start with overview
