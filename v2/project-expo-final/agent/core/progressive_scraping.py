"""Progressive scraping with multi-phase depth navigation.

Implements Phase 3: Allows users to navigate from overview (Phase 0) 
through focused deep dives (Phase 1-2) on specific aspects.

Each phase returns deeper information and asks which aspects to explore next.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, AsyncIterator
import logging

logger = logging.getLogger(__name__)


class ProgressivePhase(Enum):
    """Depth levels for progressive information revelation."""
    OVERVIEW = 0        # Quick overview, 300 tokens
    FOCUSED = 1         # Focused deep dive on selected aspects, 800 tokens
    COMPREHENSIVE = 2   # Full comprehensive answer, 2000+ tokens


@dataclass
class ProgressiveResult:
    """Result from one progressive phase."""
    phase: ProgressivePhase
    content: str
    token_budget: int
    aspects_found: List[str]  # Key aspects covered in this phase
    suggested_aspects: List[str]  # Aspects user could dive into next
    
    
async def determine_progressive_phase(
    zoom_level: Optional[int] = None,
    has_prior_results: bool = False,
) -> ProgressivePhase:
    """Determine which phase to run based on zoom_level and prior results.
    
    Args:
        zoom_level: 0=overview, 1=focused, 2=comprehensive (or None for auto)
        has_prior_results: Whether we have results from a prior phase
    
    Returns:
        ProgressivePhase to execute
    """
    if zoom_level is not None:
        if zoom_level == 0:
            return ProgressivePhase.OVERVIEW
        elif zoom_level == 1:
            return ProgressivePhase.FOCUSED
        else:
            return ProgressivePhase.COMPREHENSIVE
    
    # Auto-select based on context
    if has_prior_results:
        return ProgressivePhase.FOCUSED  # Natural progression
    else:
        return ProgressivePhase.OVERVIEW  # Start with overview


def get_phase_token_budget(phase: ProgressivePhase) -> int:
    """Get token budget for a phase (Phase 3)."""
    budgets = {
        ProgressivePhase.OVERVIEW: 300,
        ProgressivePhase.FOCUSED: 800,
        ProgressivePhase.COMPREHENSIVE: 2000,
    }
    return budgets.get(phase, 1024)


def get_phase_system_prompt(phase: ProgressivePhase) -> str:
    """Get system prompt tailored for phase depth (Phase 3)."""
    if phase == ProgressivePhase.OVERVIEW:
        return (
            "Provide a HIGH-LEVEL OVERVIEW only. Keep response SHORT and PUNCHY. "
            "Focus on the main points and key takeaways. Aim for roughly 300 tokens. "
            "After this, we'll ask user which aspects to explore deeper."
        )
    elif phase == ProgressivePhase.FOCUSED:
        return (
            "Provide a FOCUSED DEEP DIVE on the selected aspects. "
            "Include specific details, examples, and analysis. "
            "Aim for roughly 800 tokens. Be thorough but focused."
        )
    else:  # COMPREHENSIVE
        return (
            "Provide a COMPREHENSIVE, DETAILED answer covering all aspects. "
            "Include nuances, edge cases, and thorough analysis. "
            "Aim for 2000+ tokens. Be exhaustive and thoughtful."
        )


def extract_aspects_from_content(content: str) -> List[str]:
    """Extract key aspects from content using heuristics (Phase 3).
    
    Looks for:
    - Header patterns ("## Topic", "### Subtopic")
    - Bullet point groupings
    - Section divisions
    """
    aspects = []
    
    lines = content.split('\n')
    for line in lines:
        # Extract headers as aspects
        if line.strip().startswith('##'):
            aspect = line.replace('#', '').strip()
            if aspect and len(aspect) > 3:
                aspects.append(aspect)
        elif line.strip().startswith('###'):
            aspect = line.replace('#', '').strip()
            if aspect and len(aspect) > 3:
                aspects.append(aspect)
    
    # Deduplicate and limit
    return list(set(aspects))[:5]


async def run_progressive_phase(
    query: str,
    phase: ProgressivePhase,
    prior_context: Optional[str] = None,
    synthesis_fn=None,  # Function to synthesize results
    learnings: Optional[list] = None,
    user_selected_aspects: Optional[list] = None,  # Phase 3: User's aspect preferences
) -> ProgressiveResult:
    """Run one phase of progressive scraping (Phase 3).
    
    Phase 0 (OVERVIEW): Broad overview, 300 tokens
    Phase 1 (FOCUSED): Deep dive on selected aspects, 800 tokens  
    Phase 2 (COMPREHENSIVE): Full detailed answer, 2000+ tokens
    
    Args:
        query: Original user query
        phase: Which phase to run
        prior_context: Results from prior phase (if any)
        synthesis_fn: Async function to synthesize results with phase-specific prompt
        learnings: Learnings from retrieval stage
        user_selected_aspects: Which aspects user wants to focus on (Phase 3)
    
    Returns:
        ProgressiveResult with content and suggested next aspects
    """
    if not synthesis_fn or not learnings:
        return ProgressiveResult(
            phase=phase,
            content="",
            token_budget=get_phase_token_budget(phase),
            aspects_found=[],
            suggested_aspects=[],
        )
    
    try:
        # Get phase-specific token budget and system prompt
        token_budget = get_phase_token_budget(phase)
        system_prompt = get_phase_system_prompt(phase)
        
        # Phase 3: If user selected aspects, inject that into the system prompt
        if user_selected_aspects and phase == ProgressivePhase.FOCUSED:
            aspects_str = ", ".join(user_selected_aspects)
            system_prompt += (
                f"\n\nFOCUS ON SELECTED ASPECTS:\n"
                f"User prioritized these aspects: {aspects_str}\n"
                f"Allocate your 800 tokens primarily to these aspects, "
                f"but maintain context for the full picture."
            )
        
        # Generate phase-specific synthesis
        content = await synthesis_fn(
            query=query,
            learnings=learnings,
            system_prompt=system_prompt,
            max_tokens=token_budget,
        )
        
        if not content:
            return ProgressiveResult(
                phase=phase,
                content="",
                token_budget=token_budget,
                aspects_found=[],
                suggested_aspects=[],
            )
        
        # Extract aspects found in this phase's content
        aspects_found = extract_aspects_from_content(content)
        
        # Generate next suggested aspects (Phase 3)
        suggested_aspects = []
        if phase == ProgressivePhase.OVERVIEW:
            # After overview, suggest user-selectable aspects for deep dive
            # Use heuristics to suggest relevant dimensions
            query_lower = query.lower()
            
            # Common aspect dimensions by query type
            common_aspects = {
                # Decision/comparison queries
                "cost": ["cost", "price", "expense", "budget", "affordable"],
                "features": ["feature", "capability", "function", "ability"],
                "performance": ["performance", "speed", "efficiency", "scalability"],
                "community": ["community", "support", "ecosystem", "adoption"],
                "ease_of_use": ["easy", "simple", "learning curve", "usability"],
                "risk": ["risk", "security", "reliability", "stability"],
            }
            
            # Extract relevant aspects based on query
            for aspect_name, keywords in common_aspects.items():
                if any(kw in query_lower for kw in keywords):
                    suggested_aspects.append(aspect_name.replace("_", " ").title())
            
            # Ensure we have suggestions
            if not suggested_aspects:
                suggested_aspects = ["Key Features", "Benefits", "Drawbacks", "Cost Analysis", "Use Cases"]
            
            suggested_aspects = suggested_aspects[:6]  # Max 6 suggestions
        
        return ProgressiveResult(
            phase=phase,
            content=content,
            token_budget=token_budget,
            aspects_found=aspects_found,
            suggested_aspects=suggested_aspects,
        )
    
    except Exception as e:
        logger.exception(f"Progressive phase {phase.value} failed: {e}")
        return ProgressiveResult(
            phase=phase,
            content="",
            token_budget=get_phase_token_budget(phase),
            aspects_found=[],
            suggested_aspects=[],
        )
        system_prompt = get_phase_system_prompt(phase)
        
        # Build context hint for synthesis
        phase_hint = ""
        if phase == ProgressivePhase.OVERVIEW and prior_context:
            phase_hint = f"\n\nPrior context from deeper research: {prior_context[:200]}"
        elif phase == ProgressivePhase.FOCUSED and prior_context:
            phase_hint = f"\n\nBuilding on this overview: {prior_context[:300]}"
        
        # Call synthesis with phase-specific parameters
        content = await synthesis_fn(
            query=query + phase_hint,
            learnings=learnings,
            phase=phase,
            max_tokens=token_budget,
            system_prompt_addition=system_prompt,
        )
        
        # Extract aspects for next phase navigation
        aspects = extract_aspects_from_content(content)
        
        return ProgressiveResult(
            phase=phase,
            content=content,
            token_budget=token_budget,
            aspects_found=aspects,
            suggested_aspects=aspects,
        )
    
    except Exception as e:
        logger.error(f"Progressive phase failed: {e}")
        return ProgressiveResult(
            phase=phase,
            content="",
            token_budget=get_phase_token_budget(phase),
            aspects_found=[],
            suggested_aspects=[],
        )
