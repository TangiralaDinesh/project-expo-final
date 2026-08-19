"""
Structured tracing for agent decision paths.

The #1 operational gap: agent can produce confident WRONG answers that look
identical to correct ones in plain logs. This is "semantic failure" — and
ordinary logging can't catch it.

This module captures EVERY decision point as structured spans:
  - Entry gate decisions (mode, confidence, reason)
  - Thinking profile computation
  - Each retrieval round (queries, chunks, rerank scores)
  - Decision LLM calls (sufficient? why? next_queries)
  - Critique persona verdicts
  - Satisfaction loop iterations
  - Fan-out pilot results (EIG scores)
  - GeoHash depth transitions (which branch chosen, why)
  - Resonance scores between rounds
  - Synthesis (token budget, truncation)

JSON format, compatible with future OTEL migration.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class Span:
    """One traced operation in the decision path."""
    span_id: str
    parent_id: Optional[str]
    operation: str
    start_time: float
    end_time: Optional[float] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "in_progress"  # "ok" | "error" | "degraded"
    children: list[str] = field(default_factory=list)
    error_message: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "operation": self.operation,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
            "children": self.children,
            "error": self.error_message,
        }


class QueryTrace:
    """Complete trace of one query execution."""

    def __init__(self, query: str, query_id: Optional[str] = None):
        self.query_id = query_id or str(uuid.uuid4())[:12]
        self.query = query
        self.spans: dict[str, Span] = {}
        self.root_span_id: Optional[str] = None
        self._span_stack: list[str] = []
        self.created_at = time.time()

    def start_span(
        self,
        operation: str,
        parent_id: Optional[str] = None,
        **attributes,
    ) -> str:
        """Start a new span. Returns span_id."""
        span_id = f"{operation}_{str(uuid.uuid4())[:6]}"

        # Auto-parent to current span if not specified
        if parent_id is None and self._span_stack:
            parent_id = self._span_stack[-1]

        span = Span(
            span_id=span_id,
            parent_id=parent_id,
            operation=operation,
            start_time=time.time(),
            attributes=attributes,
        )

        self.spans[span_id] = span

        # Track parent-child
        if parent_id and parent_id in self.spans:
            self.spans[parent_id].children.append(span_id)

        # Set root
        if self.root_span_id is None:
            self.root_span_id = span_id

        self._span_stack.append(span_id)
        return span_id

    def end_span(
        self,
        span_id: str,
        status: str = "ok",
        error: Optional[str] = None,
        **extra_attributes,
    ):
        """End a span with status."""
        if span_id in self.spans:
            span = self.spans[span_id]
            span.end_time = time.time()
            span.status = status
            span.error_message = error
            span.attributes.update(extra_attributes)

        # Pop from stack
        if self._span_stack and self._span_stack[-1] == span_id:
            self._span_stack.pop()

    def add_attribute(self, span_id: str, key: str, value: Any):
        """Add an attribute to an existing span."""
        if span_id in self.spans:
            self.spans[span_id].attributes[key] = value

    @asynccontextmanager
    async def span(self, operation: str, **attributes):
        """Async context manager for automatic span lifecycle.

        Usage:
            async with trace.span("retrieval_round", query="test") as sid:
                # do work
                trace.add_attribute(sid, "chunks_found", 5)
        """
        span_id = self.start_span(operation, **attributes)
        try:
            yield span_id
            self.end_span(span_id, status="ok")
        except Exception as e:
            self.end_span(span_id, status="error", error=str(e))
            raise

    def get_decision_path(self) -> list[str]:
        """Get human-readable decision chain from root to leaf."""
        path = []
        if not self.root_span_id:
            return path

        def _walk(span_id: str, depth: int = 0):
            span = self.spans.get(span_id)
            if not span:
                return

            indent = "  " * depth
            duration = f" ({span.duration_ms:.0f}ms)" if span.end_time else ""
            status_icon = {"ok": "✓", "error": "✗", "degraded": "⚠"}.get(span.status, "•")

            # Build readable line
            attrs = ""
            key_attrs = {k: v for k, v in span.attributes.items()
                        if k in ("mode", "confidence", "sufficient", "resonance",
                                "recommendation", "geohash_depth", "branch_chosen",
                                "chunks_found", "satisfaction", "eig_score")}
            if key_attrs:
                attrs = " | " + ", ".join(f"{k}={v}" for k, v in key_attrs.items())

            path.append(f"{indent}{status_icon} {span.operation}{duration}{attrs}")

            for child_id in span.children:
                _walk(child_id, depth + 1)

        _walk(self.root_span_id)
        return path

    def get_timing_breakdown(self) -> dict[str, float]:
        """Get time spent in each operation type."""
        breakdown: dict[str, float] = {}
        for span in self.spans.values():
            if span.end_time:
                op = span.operation
                breakdown[op] = breakdown.get(op, 0) + span.duration_ms
        return dict(sorted(breakdown.items(), key=lambda x: x[1], reverse=True))

    def to_json(self, indent: int = 2) -> str:
        """Export full trace as JSON."""
        return json.dumps({
            "query_id": self.query_id,
            "query": self.query,
            "created_at": self.created_at,
            "root_span": self.root_span_id,
            "spans": {sid: s.to_dict() for sid, s in self.spans.items()},
            "timing_breakdown": self.get_timing_breakdown(),
            "decision_path": self.get_decision_path(),
        }, indent=indent, default=str)

    def summary(self) -> str:
        """One-line summary for logging."""
        total_ms = 0
        if self.root_span_id and self.root_span_id in self.spans:
            root = self.spans[self.root_span_id]
            if root.end_time:
                total_ms = root.duration_ms

        n_spans = len(self.spans)
        errors = sum(1 for s in self.spans.values() if s.status == "error")
        return (
            f"Trace[{self.query_id}]: {n_spans} spans, "
            f"{total_ms:.0f}ms total, {errors} errors"
        )


# ── Global trace management ──

_current_trace: Optional[QueryTrace] = None


def start_query_trace(query: str) -> QueryTrace:
    """Start a new trace for a query. Replaces any existing trace."""
    global _current_trace
    _current_trace = QueryTrace(query)
    logger.debug("Started trace %s for: %s", _current_trace.query_id, query[:60])
    return _current_trace


def get_current_trace() -> Optional[QueryTrace]:
    """Get the current active trace (or None)."""
    return _current_trace


def end_query_trace() -> Optional[QueryTrace]:
    """End and return the current trace."""
    global _current_trace
    trace = _current_trace
    if trace:
        logger.info(trace.summary())
        # Log decision path at debug level
        for line in trace.get_decision_path():
            logger.debug("  %s", line)
    _current_trace = None
    return trace
