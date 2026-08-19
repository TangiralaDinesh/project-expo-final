"""
Intelligent subquery generation for comparison and multi-aspect queries.

INDUSTRY PRACTICE (Claude, ChatGPT approach):
When user asks "Should I buy X or Y?", don't just retrieve X and Y separately.
Instead:
1. Extract decision dimensions (price, features, support, reviews, adoption)
2. For EACH dimension, generate targeted subqueries
3. Retrieve progressively - initial queries, then drill deeper on gaps
4. Present results organized by dimension

Example: "Should I buy CDSL or EMVEE?"
Initial retrieval queries:
  - "CDSL current stock price and valuation"
  - "EMVEE current stock price and valuation"
  - "CDSL vs EMVEE comparison"
  
Subqueries (generated progressively if needed):
  - "CDSL pros and cons for investment"
  - "EMVEE user reviews and satisfaction"
  - "CDSL recent news and developments"
  - "EMVEE competitive advantages in market"
  - "Which is better CDSL or EMVEE for investors"

This avoids the "only retrieves first entity once" problem by being dimension-aware,
not entity-aware.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..llm.client import NIMClient, get_client
from .types import Learning

logger = logging.getLogger(__name__)


@dataclass
class SubqueryPlan:
    """A plan for progressive subquery generation."""
    initial_queries: list[str] = field(default_factory=list)
    dimension_queries: dict[str, list[str]] = field(default_factory=dict)
    # dimension_queries = {
    #   "price": ["query1", "query2"],
    #   "features": ["query1", "query2"],
    # }
    reasoning: str = ""
    confidence: float = 0.0


class SubqueryGenerator:
    """Generate intelligent subqueries for comparison and multi-aspect queries."""
    
    DECISION_DIMENSIONS = {
        "price_value": ["cost", "pricing", "value for money", "ROI"],
        "features_functionality": ["capabilities", "features", "functions", "what it can do"],
        "quality_reliability": ["quality", "reliability", "durability", "build"],
        "support_service": ["support", "customer service", "help", "warranty"],
        "adoption_market": ["adoption", "market share", "popularity", "usage"],
        "reviews_satisfaction": ["reviews", "ratings", "user feedback", "satisfaction"],
        "competitors_alternatives": ["competitors", "alternatives", "similar products"],
        "pros_cons": ["advantages", "disadvantages", "pros", "cons"],
        "use_case_fit": ["best for", "suitable for", "ideal scenario", "use case"],
        "recent_news": ["latest news", "recent developments", "updates", "announcements"],
    }
    
    def __init__(self, client: Optional[NIMClient] = None):
        self.client = client or get_client()
    
    async def generate_comparison_plan(
        self,
        query: str,
        entities: list[str],
    ) -> SubqueryPlan:
        """
        Generate a subquery plan for comparison queries.
        
        Args:
            query: Original query (e.g., "Should I buy CDSL or EMVEE?")
            entities: List of entities being compared (e.g., ["CDSL", "EMVEE"])
        
        Returns:
            SubqueryPlan with initial and progressive queries organized by dimension
        """
        if len(entities) < 2:
            # Not a comparison, return minimal plan
            return SubqueryPlan(
                initial_queries=[query],
                reasoning="Single entity query, no comparison needed"
            )
        
        logger.info(f"Generating comparison plan for: {entities} (query: {query})")
        
        plan = SubqueryPlan()
        
        # Detect decision-making intent
        is_decision_query = any(
            keyword in query.lower() 
            for keyword in ["should", "buy", "choose", "better", "best", "prefer", "recommend"]
        )
        
        if is_decision_query:
            # For decision queries, focus on decision dimensions
            dimensions = list(self.DECISION_DIMENSIONS.keys())[:7]  # Top 7 dimensions
        else:
            # For general comparison, use fewer dimensions
            dimensions = list(self.DECISION_DIMENSIONS.keys())[:5]  # Top 5
        
        # Generate initial broad queries
        plan.initial_queries = [
            query,  # Original query first
            f"Compare {' vs '.join(entities)}: key differences and similarities",
            f"Which is better {' or '.join(entities)}?",
        ]
        
        # Generate dimension-specific subqueries
        for dimension in dimensions:
            dimension_keywords = self.DECISION_DIMENSIONS[dimension]
            
            # Initial subquery for dimension
            dim_subqueries = [
                f"{dimension.replace('_', ' ')} of {' vs '.join(entities)}"
            ]
            
            # Add entity-specific queries
            for entity in entities[:3]:  # Limit to first 3 entities
                for keyword in dimension_keywords[:1]:  # Use first keyword variant
                    dim_subqueries.append(
                        f"{entity} {keyword}"
                    )
            
            plan.dimension_queries[dimension] = dim_subqueries
        
        plan.confidence = 0.8 if is_decision_query else 0.7
        plan.reasoning = f"Generated plan for {len(entities)} entities across {len(dimensions)} decision dimensions"
        
        return plan
    
    async def generate_gap_filling_queries(
        self,
        query: str,
        entities: list[str],
        current_learnings: list[Learning],
        dimension: str,
    ) -> list[str]:
        """
        Generate follow-up queries to fill gaps in current learnings.
        
        This implements progressive retrieval - if initial queries don't
        cover a dimension well, generate targeted follow-ups.
        
        Args:
            query: Original query
            entities: Entities being compared
            current_learnings: Already retrieved learnings
            dimension: Which dimension has gaps
        
        Returns:
            List of follow-up queries to drill deeper into this dimension
        """
        # Analyze coverage of this dimension
        dimension_keywords = self.DECISION_DIMENSIONS.get(dimension, [])
        
        # Count how many learnings cover this dimension
        dimension_coverage = sum(
            1 for learning in current_learnings
            if any(
                kw.lower() in learning.text.lower()
                for kw in dimension_keywords
            )
        )
        
        coverage_ratio = dimension_coverage / max(len(current_learnings), 1)
        
        # If coverage is low, generate deeper queries
        if coverage_ratio < 0.3:  # Less than 30% coverage
            logger.info(f"Low coverage on {dimension} ({coverage_ratio:.1%}), generating deep queries")
            
            deep_queries = []
            
            # Generate specific drilling queries
            for entity in entities[:2]:  # Focus on first 2 entities
                for keyword in self.DECISION_DIMENSIONS[dimension][:2]:
                    deep_queries.append(
                        f"Detailed {keyword} of {entity}: specific numbers, pros, cons"
                    )
                    deep_queries.append(
                        f"Why do people choose {entity} for {dimension.replace('_', ' ')}"
                    )
            
            return deep_queries[:4]  # Return top 4
        
        return []  # Coverage is sufficient
    
    def prioritize_queries(
        self,
        plan: SubqueryPlan,
        max_queries: int = 10,
    ) -> list[str]:
        """
        Flatten and prioritize subqueries from the plan.
        
        Returns the most important queries first, up to max_queries.
        """
        all_queries = plan.initial_queries.copy()
        
        # Add dimension queries in priority order (top dimensions first)
        for dimension, subqueries in list(plan.dimension_queries.items())[:4]:
            all_queries.extend(subqueries[:2])  # Top 2 per dimension
        
        # Deduplicate
        seen = set()
        prioritized = []
        for q in all_queries:
            q_lower = q.lower().strip()
            if q_lower not in seen:
                seen.add(q_lower)
                prioritized.append(q)
        
        return prioritized[:max_queries]
