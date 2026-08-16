"""
Branching Session Manager — Track branching across multiple turns

When a user is presented with branching options, we need to remember:
- What the options were
- What the user selected
- How to resume execution on their chosen path

This enables multi-turn interactions where the agent presents options,
user responds, and the agent continues with the selected branch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict
import logging
import time

logger = logging.getLogger(__name__)


@dataclass
class BranchingSession:
    """Active branching session (spans multiple query turns)"""
    session_id: str
    original_query: str
    branching_options: list = field(default_factory=list)  # BranchingOption objects
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    user_selection: Optional[int] = None
    user_clarification: str = ""
    selected_option_text: str = ""


class BranchingSessionManager:
    """Manages active branching sessions"""
    
    def __init__(self):
        self.sessions: Dict[str, BranchingSession] = {}
        self._active_session_id: Optional[str] = None
    
    def create_session(
        self,
        session_id: str,
        query: str,
        options: list,  # BranchingOption objects
    ) -> BranchingSession:
        """Create new branching session"""
        session = BranchingSession(
            session_id=session_id,
            original_query=query,
            branching_options=options,
        )
        self.sessions[session_id] = session
        self._active_session_id = session_id
        
        logger.info(f"Created branching session {session_id} with {len(options)} options")
        return session
    
    def get_active_session(self) -> Optional[BranchingSession]:
        """Get currently active branching session"""
        if self._active_session_id and self._active_session_id in self.sessions:
            session = self.sessions[self._active_session_id]
            if not session.resolved:
                return session
        return None
    
    def resolve_session(
        self,
        session_id: str,
        selection: Optional[int] = None,
        clarification: str = "",
    ) -> BranchingSession:
        """Resolve branching session with user's choice"""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        session.resolved = True
        session.user_selection = selection
        session.user_clarification = clarification
        
        if selection is not None and 0 <= selection < len(session.branching_options):
            option = session.branching_options[selection]
            session.selected_option_text = option.label
            logger.info(f"Session {session_id} resolved: user selected '{option.label}'")
        else:
            logger.info(f"Session {session_id} resolved: user provided clarification")
        
        return session
    
    def clear_session(self, session_id: str):
        """Clear completed session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            if self._active_session_id == session_id:
                self._active_session_id = None
            logger.debug(f"Cleared session {session_id}")


# Global branching session manager
_global_branching_manager: Optional[BranchingSessionManager] = None


def get_branching_session_manager() -> BranchingSessionManager:
    """Get or create global branching session manager"""
    global _global_branching_manager
    if _global_branching_manager is None:
        _global_branching_manager = BranchingSessionManager()
    return _global_branching_manager


def reset_branching_sessions():
    """Reset all sessions (useful for testing)"""
    global _global_branching_manager
    _global_branching_manager = BranchingSessionManager()
