"""Detect and classify comparison queries.

Identifies queries asking for "X vs Y", "compare X and Y", "should I buy X or Y" patterns.
Returns ComparisonDecision with list of entities to explore in parallel.
"""

import re
from dataclasses import dataclass
from typing import Optional, List
from agent.llm.client import get_client, NIMClient


@dataclass
class ComparisonEntity:
    """Entity to be compared."""
    name: str
    keywords: List[str]  # Alternative names/keywords
    relevance: float  # 0.0-1.0 confidence this is an entity to compare


@dataclass
class ComparisonDecision:
    """Result of comparison detection."""
    is_comparison: bool
    entities: List[ComparisonEntity]
    comparison_type: str  # "vs", "better", "choose", "alternative", etc.
    confidence: float
    reasoning: str


class ComparisonQueryDetector:
    """Detect and classify comparison queries."""
    
    # Patterns that indicate comparison intent
    COMPARISON_PATTERNS = [
        # Direct "vs" patterns
        r"(\w+)\s+(?:vs|vs\.|\bversus\b)\s+(\w+)",
        # "or" patterns (decision making)
        r"(?:should\s+i|should\s+we|should\s+you)\s+(?:buy|use|choose|pick|go\s+with)\s+(\w+)\s+or\s+(\w+)",
        # "compare/comparison" patterns
        r"(?:compare|comparison)\s+(?:between\s+)?(\w+)\s+(?:and|\+|with)\s+(\w+)",
        # "better/best" patterns
        r"(?:is\s+)?(\w+)\s+(?:better|worse|best|superior)\s+(?:than|to)\s+(\w+)",
        # "difference" patterns
        r"(?:what(?:'s)?|what\s+is)\s+(?:the\s+)?(?:difference|differences)\s+(?:between|from)\s+(\w+)\s+(?:and|\+|vs)\s+(\w+)",
        # "advantages/disadvantages" patterns
        r"(?:pros?\s+and\s+cons|advantages?\s+and\s+disadvantages)\s+(?:of\s+)?(\w+)\s+(?:vs|versus|and|vs\.)\s+(\w+)",
        # Alternative patterns
        r"(\w+)\s+(?:alternative|alternative\s+to|instead\s+of)\s+(\w+)",
        r"(\w+)\s+(?:vs\.?|versus|rather\s+than)\s+(\w+)",
    ]
    
    DECISION_KEYWORDS = {
        "vs": ["vs", "versus", "vs."],
        "better": ["better", "best", "superior", "worse"],
        "choose": ["should", "choose", "pick", "buy", "use", "go with"],
        "compare": ["compare", "comparison", "compare to", "compare with"],
        "difference": ["difference", "differences", "distinguish"],
        "alternative": ["alternative", "instead of", "rather than"],
    }
    
    def __init__(self, client: Optional[NIMClient] = None):
        self.client = client or get_client()
    
    async def detect(self, query: str) -> ComparisonDecision:
        """Detect if query is a comparison and extract entities."""
        
        # Try regex-based detection first (fast path)
        regex_result = self._detect_regex(query)
        if regex_result and regex_result.confidence >= 0.7:
            return regex_result
        
        # Fall back to LLM for edge cases
        llm_result = await self._detect_llm(query)
        return llm_result
    
    def _detect_regex(self, query: str) -> Optional[ComparisonDecision]:
        """Fast regex-based comparison detection.
        
        IMPORTANT: Only flag as comparison if there's EXPLICIT comparison intent.
        NOT just because query has "and" or "or" (which are common in narratives).
        """
        query_lower = query.lower()
        
        # Check for STRONG comparison keywords ONLY (not generic conjunctions)
        has_vs = any(kw in query_lower for kw in self.DECISION_KEYWORDS["vs"])  # vs, versus
        has_choice = any(kw in query_lower for kw in self.DECISION_KEYWORDS["choose"])  # should, choose, buy
        has_compare_keyword = any(kw in query_lower for kw in self.DECISION_KEYWORDS["compare"])  # compare, comparison
        has_better_keyword = any(kw in query_lower for kw in self.DECISION_KEYWORDS["better"])  # better, best
        has_difference_keyword = any(kw in query_lower for kw in self.DECISION_KEYWORDS["difference"])  # difference
        
        # MUST have at least one strong comparison signal
        # (NOT generic "and/or" which appear in narratives)
        strong_signals = has_vs or has_choice or has_compare_keyword or has_better_keyword or has_difference_keyword
        
        if not strong_signals:
            return None
        
        # Try to extract entities
        entities = []
        comparison_type = None
        
        for pattern in self.COMPARISON_PATTERNS:
            matches = re.finditer(pattern, query, re.IGNORECASE)
            for match in matches:
                if len(match.groups()) >= 2:
                    entity1 = match.group(1).strip()
                    entity2 = match.group(2).strip()
                    
                    if entity1 and entity2:
                        # Determine comparison type
                        if any(kw in query_lower for kw in self.DECISION_KEYWORDS["vs"]):
                            comparison_type = "vs"
                        elif any(kw in query_lower for kw in self.DECISION_KEYWORDS["better"]):
                            comparison_type = "better"
                        elif any(kw in query_lower for kw in self.DECISION_KEYWORDS["choose"]):
                            comparison_type = "choose"
                        else:
                            comparison_type = "compare"
                        
                        entities = [
                            ComparisonEntity(
                                name=entity1,
                                keywords=[entity1],
                                relevance=0.95
                            ),
                            ComparisonEntity(
                                name=entity2,
                                keywords=[entity2],
                                relevance=0.95
                            ),
                        ]
                        break
            
            if entities:
                break
        
        if entities and comparison_type:
            return ComparisonDecision(
                is_comparison=True,
                entities=entities,
                comparison_type=comparison_type,
                confidence=0.85,
                reasoning=f"Detected {comparison_type} pattern matching {len(entities)} entities"
            )
        
        return None
    
    async def _detect_llm(self, query: str) -> ComparisonDecision:
        """Use LLM to detect comparison queries with high confidence threshold."""
        
        prompt = f"""Analyze this query and determine if it's asking for a comparison between options:

Query: "{query}"

IMPORTANT: Only return is_comparison=true if this is clearly a comparison question.
Examples of comparisons:
- "Compare X and Y"
- "Should I buy X or Y"
- "What's the difference between X and Y"
- "X vs Y which is better"

Examples of NON-comparisons (even if mentioning 2 things):
- "Tell me about X and its history"
- "X and Y are both important, explain X"
- "How does X relate to Y"

Respond with JSON:
{{
  "is_comparison": true/false,
  "entities": [
    {{"name": "entity1", "keywords": ["alt_name1", "synonym1"], "relevance": 0.9}},
    {{"name": "entity2", "keywords": ["alt_name2", "synonym2"], "relevance": 0.85}}
  ],
  "comparison_type": "vs" | "better" | "choose" | "compare" | "difference" | "alternative",
  "confidence": 0.0-1.0,
  "reasoning": "explanation"
}}

Only include entities that are actually being compared. Be strict about relevance (>0.7)."""
        
        try:
            response = await self.client.chat_fast(
                messages=[{"role": "user", "content": prompt}],
                response_format_json=True,
            )
            
            import json
            data = json.loads(response)
            
            entities = [
                ComparisonEntity(
                    name=e["name"],
                    keywords=e.get("keywords", [e["name"]]),
                    relevance=e.get("relevance", 0.8)
                )
                for e in data.get("entities", [])
                if e.get("relevance", 0) > 0.7
            ]
            
            return ComparisonDecision(
                is_comparison=data.get("is_comparison", False),
                entities=entities,
                comparison_type=data.get("comparison_type", "compare"),
                confidence=data.get("confidence", 0.0),
                reasoning=data.get("reasoning", "LLM analysis")
            )
        except Exception as e:
            # Fall back to negative detection
            return ComparisonDecision(
                is_comparison=False,
                entities=[],
                comparison_type="none",
                confidence=0.0,
                reasoning=f"LLM detection failed: {str(e)}"
            )
