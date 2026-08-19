"""
Observability module for reasoning improvements.

Tracks metrics and telemetry to understand how the reasoning system performs:
- How often corrections are applied
- Which correction types are most common
- Which domains have highest error rates
- How much corrections improve answer quality
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MetricType(str, Enum):
    """Types of metrics tracked"""
    CORRECTION_APPLIED = "correction_applied"
    BRANCHING_PRESENTED = "branching_presented"
    CONFIDENCE_GAP = "confidence_gap"
    THINKING_DEPTH_ADJUSTED = "thinking_depth_adjusted"
    KNOWLEDGE_GRAPH_QUERY = "knowledge_graph_query"
    SATISFACTION_UPDATE = "satisfaction_update"


@dataclass
class MetricEvent:
    """One tracked event"""
    metric_type: MetricType
    value: float = 0.0
    domain: str = ""
    details: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class ObservabilityTracker:
    """Track metrics for reasoning improvements"""
    
    def __init__(self):
        self.events: list[MetricEvent] = []
        self._event_counters: dict[str, int] = {}
        self._start_time = time.time()
    
    def record_event(
        self,
        metric_type: MetricType,
        value: float = 0.0,
        domain: str = "",
        **details
    ):
        """Record a metric event"""
        event = MetricEvent(
            metric_type=metric_type,
            value=value,
            domain=domain,
            details=details,
        )
        self.events.append(event)
        
        # Keep event count for quick aggregation
        key = f"{metric_type.value}:{domain}" if domain else metric_type.value
        self._event_counters[key] = self._event_counters.get(key, 0) + 1
        
        logger.debug(f"Metric recorded: {metric_type.value} (domain={domain}, value={value})")
    
    def get_stats(self) -> dict:
        """Get aggregated statistics"""
        uptime = time.time() - self._start_time
        
        return {
            "uptime_seconds": uptime,
            "total_events": len(self.events),
            "event_counts": self._event_counters,
            "events_per_second": len(self.events) / max(1, uptime),
        }
    
    def get_domain_stats(self, domain: str) -> dict:
        """Get stats for a specific domain"""
        domain_events = [e for e in self.events if e.domain == domain]
        
        if not domain_events:
            return {"domain": domain, "event_count": 0}
        
        by_type = {}
        total_value = 0.0
        
        for event in domain_events:
            type_key = event.metric_type.value
            by_type[type_key] = by_type.get(type_key, 0) + 1
            total_value += event.value
        
        return {
            "domain": domain,
            "event_count": len(domain_events),
            "by_type": by_type,
            "avg_value": total_value / len(domain_events) if domain_events else 0,
        }
    
    def clear_old_events(self, keep_last_n: int = 1000):
        """Prevent unbounded event list growth"""
        if len(self.events) > keep_last_n:
            self.events = self.events[-keep_last_n:]
            logger.info(f"Trimmed events to {keep_last_n} most recent")


# Global observability tracker
_global_tracker: Optional[ObservabilityTracker] = None


def get_observability_tracker() -> ObservabilityTracker:
    """Get or create global observability tracker"""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = ObservabilityTracker()
    return _global_tracker


def reset_observability():
    """Reset global tracker (useful for tests)"""
    global _global_tracker
    _global_tracker = ObservabilityTracker()
