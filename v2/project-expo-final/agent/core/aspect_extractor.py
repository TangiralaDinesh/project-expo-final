"""
Aspect Extraction for Dynamic Query Decomposition (Phase 1 Fix).

Replaces entity-based comparison detection with aspect-based parallel retrieval.

Key insight: For queries like "Should I buy CDSL or EMVEE?", don't extract
[CDSL, EMVEE] as entities to compare. Instead, extract ASPECTS 
[cost, features, support, adoption, performance] to retrieve in parallel.

This aligns with how industry systems (Claude, ChatGPT, Gemini) work.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .satisfaction import SatisfactionTracker

from ..llm.client import NIMClient, get_client

logger = logging.getLogger(__name__)


class AspectCategory(Enum):
    """Categories of aspects for different query types."""
    # Factual queries ("interesting facts about X")
    ORIGIN = "origin"
    CHARACTERISTICS = "characteristics"
    HISTORICAL_CONTEXT = "historical_context"
    IMPACT_LEGACY = "impact_legacy"
    NOTABLE_FACTS = "notable_facts"
    PERSONAL_TRAITS = "personal_traits"
    
    # Comparison queries ("should I buy X or Y?")
    COST = "cost"
    FEATURES = "features"
    SUPPORT_QUALITY = "support_quality"
    ECOSYSTEM = "ecosystem"
    PERFORMANCE = "performance"
    ADOPTION_USAGE = "adoption_usage"
    LEARNING_CURVE = "learning_curve"
    COMMUNITY = "community"
    
    # Technical queries ("how does X work?")
    MECHANISM = "mechanism"
    COMPONENTS = "components"
    WORKFLOW = "workflow"
    ARCHITECTURE = "architecture"
    ALGORITHMS = "algorithms"
    INTERFACES = "interfaces"
    
    # General aspects
    OVERVIEW = "overview"
    DETAILS = "details"
    RELATIONSHIPS = "relationships"
    TRENDS = "trends"


@dataclass
class Aspect:
    """A dimension/aspect of a query to retrieve in parallel."""
    category: AspectCategory
    name: str
    description: str
    priority: float                # 0-1, higher = retrieve first
    depth_target: int              # target tokens for this aspect
    keywords: list[str] = field(default_factory=list)
    
    def __repr__(self):
        return f"Aspect({self.name}, priority={self.priority:.2f}, depth={self.depth_target})"


@dataclass
class AspectExtractionResult:
    """Result of aspect extraction from query."""
    aspects: list[Aspect]
    primary_intent: str            # "facts", "comparison", "how-to", "general"
    suggested_retrieval_depth: str # "summary", "details", "comprehensive"
    confidence: float              # 0-1, confidence in extraction
    reasoning: str


class AspectExtractor:
    """
    Extract aspects from queries for parallel retrieval.
    
    PHASE 1 FIX: Replaces ComparisonQueryDetector with aspect-based approach.
    
    Process:
    1. Detect query intent (facts, comparison, how-to, general)
    2. Extract aspects relevant to that intent
    3. Prioritize aspects by relevance
    4. Return as list for parallel retrieval
    """
    
    # Heuristic keywords for different query types
    FACT_QUERY_KEYWORDS = {
        "interesting", "facts", "about", "who", "biography", "life", "story",
        "background", "tell me", "information", "history", "notable", "famous"
    }
    
    COMPARISON_KEYWORDS = {
        "vs", "versus", "compare", "comparison", "better", "best", "worse",
        "should i buy", "should i use", "prefer", "choice", "between", "or"
    }
    
    HOW_TO_KEYWORDS = {
        "how", "does", "work", "explain", "mechanism", "process", "steps",
        "algorithm", "architecture", "implementation", "design", "system"
    }
    
    # Aspect templates for different intents
    FACT_ASPECTS = [
        Aspect(AspectCategory.ORIGIN, "origin", "Early background and origins", 0.9, 300),
        Aspect(AspectCategory.CHARACTERISTICS, "characteristics", "Key characteristics and traits", 0.85, 400),
        Aspect(AspectCategory.HISTORICAL_CONTEXT, "historical_context", "Historical context and events", 0.8, 400),
        Aspect(AspectCategory.IMPACT_LEGACY, "impact_legacy", "Impact and lasting legacy", 0.8, 300),
        Aspect(AspectCategory.NOTABLE_FACTS, "notable_facts", "Surprising and notable facts", 0.95, 500),
        Aspect(AspectCategory.PERSONAL_TRAITS, "personal_traits", "Personal traits and habits", 0.7, 250),
    ]
    
    COMPARISON_ASPECTS = [
        Aspect(AspectCategory.COST, "cost", "Pricing and cost comparison", 0.9, 250),
        Aspect(AspectCategory.FEATURES, "features", "Features and capabilities", 0.95, 500),
        Aspect(AspectCategory.SUPPORT_QUALITY, "support_quality", "Support and community", 0.75, 200),
        Aspect(AspectCategory.PERFORMANCE, "performance", "Performance metrics", 0.85, 300),
        Aspect(AspectCategory.ECOSYSTEM, "ecosystem", "Ecosystem and integrations", 0.7, 250),
        Aspect(AspectCategory.ADOPTION_USAGE, "adoption_usage", "Adoption and market share", 0.65, 200),
        Aspect(AspectCategory.LEARNING_CURVE, "learning_curve", "Learning curve and ease of use", 0.7, 200),
    ]
    
    HOW_TO_ASPECTS = [
        Aspect(AspectCategory.OVERVIEW, "overview", "High-level overview", 0.9, 200),
        Aspect(AspectCategory.MECHANISM, "mechanism", "Core mechanism or principle", 0.95, 400),
        Aspect(AspectCategory.COMPONENTS, "components", "Main components or parts", 0.85, 300),
        Aspect(AspectCategory.WORKFLOW, "workflow", "Workflow or process flow", 0.8, 350),
        Aspect(AspectCategory.ARCHITECTURE, "architecture", "Architecture or structure", 0.75, 300),
    ]
    
    GENERAL_ASPECTS = [
        Aspect(AspectCategory.OVERVIEW, "overview", "General overview", 0.9, 200),
        Aspect(AspectCategory.DETAILS, "details", "Key details", 0.8, 300),
        Aspect(AspectCategory.RELATIONSHIPS, "relationships", "Relationships to related concepts", 0.6, 200),
    ]
    
    def __init__(self, client: Optional[NIMClient] = None):
        self.client = client or get_client()
    
    async def extract(
        self,
        query: str,
        use_llm: bool = False,
    ) -> AspectExtractionResult:
        """
        Extract aspects from query for parallel retrieval.
        
        Args:
            query: User query
            use_llm: If True, use LLM for complex queries; else use heuristics
        
        Returns:
            AspectExtractionResult with list of aspects and metadata
        """
        # Fast heuristic pass
        heuristic_result = self._extract_heuristic(query)
        
        # If high confidence heuristic, use it
        if heuristic_result.confidence > 0.75:
            logger.debug(f"Using heuristic aspect extraction: {[a.name for a in heuristic_result.aspects]}")
            return heuristic_result
        
        # For low-confidence queries, optionally use LLM
        if use_llm:
            try:
                llm_result = await self._extract_llm(query)
                logger.debug(f"Using LLM aspect extraction: {[a.name for a in llm_result.aspects]}")
                return llm_result
            except Exception as e:
                logger.warning(f"LLM extraction failed: {e}, falling back to heuristic")
        
        return heuristic_result
    
    def _extract_heuristic(self, query: str) -> AspectExtractionResult:
        """Fast heuristic-based aspect extraction."""
        query_lower = query.lower()
        
        # Detect query intent
        fact_keywords_found = len([k for k in self.FACT_QUERY_KEYWORDS if k in query_lower])
        comparison_keywords_found = len([k for k in self.COMPARISON_KEYWORDS if k in query_lower])
        howto_keywords_found = len([k for k in self.HOW_TO_KEYWORDS if k in query_lower])
        
        # Determine primary intent
        intent_scores = {
            "facts": fact_keywords_found,
            "comparison": comparison_keywords_found,
            "how-to": howto_keywords_found,
            "general": 0,  # fallback
        }
        
        primary_intent = max(intent_scores, key=intent_scores.get)
        max_score = intent_scores[primary_intent]
        confidence = min(max_score / 3.0, 0.95)  # Normalize confidence
        
        # Select aspects based on intent
        if primary_intent == "facts":
            aspects = self.FACT_ASPECTS.copy()
            depth = "comprehensive"
        elif primary_intent == "comparison":
            aspects = self.COMPARISON_ASPECTS.copy()
            depth = "detailed"
        elif primary_intent == "how-to":
            aspects = self.HOW_TO_ASPECTS.copy()
            depth = "comprehensive"
        else:
            aspects = self.GENERAL_ASPECTS.copy()
            depth = "summary"
        
        # Adjust priorities based on specific keywords found
        for aspect in aspects:
            for keyword in aspect.keywords:
                if keyword.lower() in query_lower:
                    aspect.priority = min(aspect.priority + 0.1, 1.0)
        
        # Sort by priority
        aspects.sort(key=lambda a: a.priority, reverse=True)
        
        reasoning = (
            f"Detected {primary_intent} query with {max_score} keyword matches. "
            f"Extracting {len(aspects)} aspects for parallel retrieval."
        )
        
        return AspectExtractionResult(
            aspects=aspects,
            primary_intent=primary_intent,
            suggested_retrieval_depth=depth,
            confidence=confidence,
            reasoning=reasoning,
        )
    
    async def _extract_llm(self, query: str) -> AspectExtractionResult:
        """Use LLM for sophisticated aspect extraction."""
        prompt = f"""Analyze this query and extract retrieval aspects:

Query: "{query}"

Determine:
1. Query intent: "facts" (biography/information), "comparison" (vs decision), "how-to" (mechanism), or "general"
2. Key aspects to retrieve in parallel (3-6 aspects max)
3. Each aspect's priority (0-1) and target depth in tokens

Respond with ONLY valid JSON:
{{
  "primary_intent": "facts|comparison|how-to|general",
  "depth": "summary|details|comprehensive",
  "aspects": [
    {{"name": "aspect_name", "description": "short description", "priority": 0.95, "depth_tokens": 400}},
    ...
  ],
  "reasoning": "why these aspects were chosen"
}}"""
        
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an automated aspect extraction engine. Output strictly raw JSON. "
                        "CRITICAL: Do NOT output conversational monologue, 'Here\\'s a thinking process', or preamble. "
                        "Do NOT use markdown code blocks. Start your response IMMEDIATELY with '{' and end with '}'."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            response = await self.client.chat_fast(
                messages,
                temperature=0.1,
                response_format_json=True
            )
            
            text = response.strip()
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            match = re.search(r'```(?:json)?\s*([\s\S]*?)(?:```|$)', text)
            if match:
                text = match.group(1).strip()
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                text = text[start:end+1]
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                cleaned = re.sub(r',\s*([\]}])', r'\1', text)
                data = json.loads(cleaned)
            
            # Convert to Aspect objects
            aspects = [
                Aspect(
                    category=AspectCategory.OVERVIEW,  # Fallback - LLM returns custom names
                    name=a["name"],
                    description=a.get("description", ""),
                    priority=a.get("priority", 0.5),
                    depth_target=a.get("depth_tokens", 300),
                    keywords=[a["name"].lower()]
                )
                for a in data.get("aspects", [])
            ]
            
            # Sort by priority
            aspects.sort(key=lambda a: a.priority, reverse=True)
            
            return AspectExtractionResult(
                aspects=aspects,
                primary_intent=data.get("primary_intent", "general"),
                suggested_retrieval_depth=data.get("depth", "details"),
                confidence=0.85,  # LLM analysis is generally higher confidence
                reasoning=data.get("reasoning", "LLM analysis"),
            )
        except Exception as e:
            logger.error(f"LLM aspect extraction failed: {e}")
            raise
