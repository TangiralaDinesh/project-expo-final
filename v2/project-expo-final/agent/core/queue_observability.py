"""
Queue State Visualization & Observability API (Plan 1, Phase D)

Provides logging, monitoring, and visualization of adaptive task queue execution.
Useful for debugging adaptation behavior and understanding wave progression.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Optional, Any

from .task_queue import TaskQueue, QueueState
from .wave_executor import WaveExecutor

logger = logging.getLogger(__name__)


class QueueObserver:
    """Logs and tracks queue state evolution across waves."""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.execution_log = []
    
    def log_queue_initialized(self, queue: TaskQueue):
        """Log initial queue state."""
        summary = queue.get_queue_summary()
        entry = {
            "event": "queue_initialized",
            "timestamp": datetime.now().isoformat(),
            "queue_summary": summary,
        }
        self.execution_log.append(entry)
        
        logger.info(
            f"📋 Queue initialized with {summary['total_tasks']} tasks: "
            f"{', '.join([t.task_id for t in queue.get_all_tasks()[:3]])}..."
        )
    
    def log_wave_start(self, wave_num: int, tasks: list):
        """Log beginning of wave execution."""
        entry = {
            "event": "wave_start",
            "timestamp": datetime.now().isoformat(),
            "wave_num": wave_num,
            "tasks_count": len(tasks),
            "task_ids": [t.task_id for t in tasks],
        }
        self.execution_log.append(entry)
        
        task_str = ", ".join([t.task_id for t in tasks[:3]])
        if len(tasks) > 3:
            task_str += f", +{len(tasks)-3} more"
        
        logger.info(f"🌊 Wave {wave_num}: Executing {len(tasks)} tasks → {task_str}")
    
    def log_wave_complete(self, wave_num: int, success: int, failures: int, learnings_count: int):
        """Log wave completion."""
        entry = {
            "event": "wave_complete",
            "timestamp": datetime.now().isoformat(),
            "wave_num": wave_num,
            "success": success,
            "failures": failures,
            "learnings_count": learnings_count,
        }
        self.execution_log.append(entry)
        
        status = "✓" if failures == 0 else "⚠️"
        logger.info(
            f"{status} Wave {wave_num} complete: {success} ✓, {failures} ✗, "
            f"{learnings_count} learnings"
        )
    
    def log_reflection_analysis(self, wave_num: int, analysis):
        """Log reflection engine analysis results."""
        entry = {
            "event": "reflection_analysis",
            "timestamp": datetime.now().isoformat(),
            "wave_num": wave_num,
            "new_tasks": len(analysis.new_tasks_detected),
            "reprioritizations": len(analysis.reprioritization_suggestions),
            "confidence": analysis.confidence,
            "gaps": analysis.knowledge_gaps[:3],  # Top 3 gaps
        }
        self.execution_log.append(entry)
        
        logger.info(
            f"🔍 Reflection {wave_num}: "
            f"+{len(analysis.new_tasks_detected)} tasks, "
            f"{len(analysis.reprioritization_suggestions)} repriorities, "
            f"confidence={analysis.confidence:.2f}"
        )
        
        if analysis.knowledge_gaps:
            for gap in analysis.knowledge_gaps[:2]:
                logger.debug(f"   Gap: {gap}")
    
    def log_queue_reprioritization(self, task_id: str, old_priority: int, new_priority: int):
        """Log task priority change."""
        entry = {
            "event": "reprioritization",
            "timestamp": datetime.now().isoformat(),
            "task_id": task_id,
            "old_priority": old_priority,
            "new_priority": new_priority,
        }
        self.execution_log.append(entry)
        
        direction = "↑" if new_priority > old_priority else "↓"
        logger.debug(f"  {direction} {task_id}: {old_priority} → {new_priority}")
    
    def log_execution_complete(self, executor: WaveExecutor, queue: TaskQueue):
        """Log final execution summary."""
        report = executor.get_execution_report()
        queue_summary = queue.get_queue_summary()
        
        entry = {
            "event": "execution_complete",
            "timestamp": datetime.now().isoformat(),
            "execution_report": report,
            "final_queue_summary": queue_summary,
        }
        self.execution_log.append(entry)
        
        logger.info(f"✅ Orchestration complete")
        logger.info(f"   Waves: {report['total_waves']}")
        logger.info(f"   Tasks: {report['total_success']} ✓, {report['total_failures']} ✗")
        logger.info(f"   Learnings: {report['total_learnings']}")
        logger.info(f"   Success rate: {report['overall_success_rate']}")
    
    def get_execution_log(self) -> list[dict]:
        """Get complete execution log."""
        return self.execution_log
    
    def get_timeline(self) -> str:
        """Get formatted execution timeline."""
        lines = ["=== EXECUTION TIMELINE ==="]
        for entry in self.execution_log:
            event = entry.get("event", "unknown")
            timestamp = entry.get("timestamp", "")
            
            if event == "wave_start":
                lines.append(f"{timestamp} → Wave {entry['wave_num']}: Start")
            elif event == "wave_complete":
                lines.append(
                    f"{timestamp} ← Wave {entry['wave_num']}: "
                    f"{entry['success']} ✓, {entry['failures']} ✗"
                )
            elif event == "reflection_analysis":
                lines.append(
                    f"{timestamp}   Reflection: +{entry['new_tasks']} tasks, "
                    f"{entry['reprioritizations']} repriors"
                )
            elif event == "execution_complete":
                lines.append(f"{timestamp} DONE")
        
        return "\n".join(lines)


class QueueVisualizationAPI:
    """REST API for queue state visualization (for future web UI)."""
    
    def __init__(self, executor: WaveExecutor, queue: TaskQueue):
        self.executor = executor
        self.queue = queue
    
    def get_queue_status(self) -> dict:
        """GET /queue-status - Current queue state."""
        return {
            "current_wave": self.executor.waves[-1].wave_number if self.executor.waves else 0,
            "tasks_done": len(self.executor.waves) * 3,  # Approx
            "tasks_pending": len(self.queue.get_pending_tasks()),
            "learnings_so_far": len(self.executor.total_learnings),
            "queue_summary": self.queue.get_queue_summary(),
        }
    
    def get_wave_history(self) -> list[dict]:
        """GET /waves - History of all completed waves."""
        return self.executor.get_wave_history()
    
    def get_execution_report(self) -> dict:
        """GET /report - Comprehensive execution report."""
        return self.executor.get_execution_report()
    
    def get_learnings_sample(self, limit: int = 10) -> list[Any]:
        """GET /learnings - Sample of accumulated learnings."""
        learnings = self.executor.get_total_learnings()
        return learnings[:limit]


# Structured logging for different verbosity levels

def log_queue_debug(queue: TaskQueue, message: str = ""):
    """Detailed queue state for debugging."""
    summary = queue.get_queue_summary()
    
    logger.debug(f"📊 Queue State {message}")
    logger.debug(f"   Total: {summary['total_tasks']} | "
                f"Done: {summary['completed']} | "
                f"Pending: {summary['pending']}")
    
    if summary['by_status']:
        for status, tasks in summary['by_status'].items():
            if tasks:
                logger.debug(f"   {status}: {', '.join(tasks[:3])}")


def log_wave_metrics(wave_num: int, wave_exec):
    """Log wave-level metrics."""
    summary = wave_exec.get_summary()
    logger.info(
        f"📈 Wave {wave_num} Metrics: "
        f"tasks={summary['tasks']}, "
        f"success={summary['success']}, "
        f"failures={summary['failures']}, "
        f"success_rate={summary['success_rate']}, "
        f"duration={summary['duration_ms']}ms, "
        f"learnings={summary['learnings_count']}"
    )


def log_final_report(executor: WaveExecutor):
    """Log final comprehensive report."""
    report = executor.get_execution_report()
    
    logger.info("=" * 60)
    logger.info("FINAL EXECUTION REPORT")
    logger.info("=" * 60)
    logger.info(f"Total Waves:       {report['total_waves']}")
    logger.info(f"Total Tasks:       {report['total_tasks_executed']}")
    logger.info(f"Success:           {report['total_success']}")
    logger.info(f"Failures:          {report['total_failures']}")
    logger.info(f"Success Rate:      {report['overall_success_rate']}")
    logger.info(f"Total Learnings:   {report['total_learnings']}")
    logger.info("=" * 60)
    
    logger.debug("Wave Summaries:")
    for wave_summary in report['wave_summaries']:
        logger.debug(f"  Wave {wave_summary['wave']}: "
                    f"{wave_summary['success']} ✓, {wave_summary['failures']} ✗, "
                    f"{wave_summary['learnings_count']} learnings")
