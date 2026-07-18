"""Observability module for logging, metrics, and tracing."""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import time
import structlog
import logging


# Configure structlog
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)


logger = structlog.get_logger(__name__)


@dataclass
class MetricValue:
    """A single metric value with timestamp."""
    value: float
    timestamp: datetime
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class MetricDefinition:
    """Definition of a metric."""
    name: str
    description: str
    unit: str
    metric_type: str  # counter, gauge, histogram, summary


class MetricsCollector:
    """
    Collects and stores application metrics.
    
    Provides:
    - Counters for discrete events
    - Gauges for current values
    - Histograms for distributions
    - Summary statistics
    """
    
    def __init__(self):
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._labels: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
        self._last_update: Dict[str, datetime] = {}
    
    def increment(
        self,
        name: str,
        value: float = 1.0,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Increment a counter metric."""
        key = self._make_key(name, labels)
        self._counters[key] += value
        self._labels[name][key].update(labels or {})
        self._last_update[name] = datetime.utcnow()
    
    def gauge(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Set a gauge metric."""
        key = self._make_key(name, labels)
        self._gauges[key] = value
        self._labels[name][key].update(labels or {})
        self._last_update[name] = datetime.utcnow()
    
    def observe(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Observe a value for histogram."""
        key = self._make_key(name, labels)
        self._histograms[key].append(value)
        self._labels[name][key].update(labels or {})
        self._last_update[name] = datetime.utcnow()
    
    def _make_key(self, name: str, labels: Optional[Dict[str, str]]) -> str:
        """Create a unique key from name and labels."""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"
    
    def get_counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> float:
        """Get current counter value."""
        key = self._make_key(name, labels)
        return self._counters.get(key, 0.0)
    
    def get_gauge(self, name: str, labels: Optional[Dict[str, str]] = None) -> Optional[float]:
        """Get current gauge value."""
        key = self._make_key(name, labels)
        return self._gauges.get(key)
    
    def get_histogram_stats(
        self,
        name: str,
        labels: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, float]]:
        """Get histogram statistics."""
        key = self._make_key(name, labels)
        values = self._histograms.get(key, [])
        if not values:
            return None
        
        sorted_values = sorted(values)
        n = len(sorted_values)
        
        return {
            "count": n,
            "sum": sum(sorted_values),
            "mean": sum(sorted_values) / n,
            "min": sorted_values[0],
            "max": sorted_values[-1],
            "p50": sorted_values[n // 2],
            "p90": sorted_values[int(n * 0.9)],
            "p95": sorted_values[int(n * 0.95)],
            "p99": sorted_values[int(n * 0.99)] if n >= 100 else sorted_values[-1],
        }
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """Get all metrics in Prometheus-compatible format."""
        output = []
        
        # Counters
        for key, value in self._counters.items():
            labels = self._labels.get(key.split("{")[0], {}).get(key, {})
            output.append({
                "name": key.split("{")[0],
                "type": "counter",
                "value": value,
                "labels": labels,
            })
        
        # Gauges
        for key, value in self._gauges.items():
            labels = self._labels.get(key.split("{")[0], {}).get(key, {})
            output.append({
                "name": key.split("{")[0],
                "type": "gauge",
                "value": value,
                "labels": labels,
            })
        
        # Histograms
        for key, values in self._histograms.items():
            stats = self.get_histogram_stats(key.split("{")[0], {})
            if stats:
                labels = self._labels.get(key.split("{")[0], {}).get(key, {})
                output.append({
                    "name": key.split("{")[0],
                    "type": "histogram",
                    "value": stats,
                    "labels": labels,
                })
        
        return {"metrics": output, "timestamp": datetime.utcnow().isoformat()}
    
    def reset(self) -> None:
        """Reset all metrics."""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()
        self._labels.clear()
        self._last_update.clear()


class TaskMetrics:
    """Metrics specific to task execution."""
    
    def __init__(self, collector: MetricsCollector):
        self.collector = collector
    
    def record_task_start(self, task_id: str, agent_type: str) -> None:
        """Record task start."""
        self.collector.increment(
            "tasks_started_total",
            labels={"agent_type": agent_type}
        )
        self.collector.gauge(
            "tasks_running",
            self.collector.get_counter("tasks_started_total") - 
            self.collector.get_counter("tasks_completed_total") -
            self.collector.get_counter("tasks_failed_total")
        )
    
    def record_task_complete(
        self,
        task_id: str,
        agent_type: str,
        duration_seconds: float,
        success: bool,
    ) -> None:
        """Record task completion."""
        self.collector.increment(
            "tasks_completed_total" if success else "tasks_failed_total",
            labels={"agent_type": agent_type}
        )
        self.collector.observe(
            "task_duration_seconds",
            duration_seconds,
            labels={"agent_type": agent_type, "status": "success" if success else "failed"}
        )
        self.collector.gauge(
            "tasks_running",
            self.collector.get_counter("tasks_started_total") - 
            self.collector.get_counter("tasks_completed_total") -
            self.collector.get_counter("tasks_failed_total")
        )
    
    def record_llm_call(
        self,
        model: str,
        tokens_used: int,
        cost: float,
        latency_ms: float,
        success: bool,
    ) -> None:
        """Record an LLM API call."""
        self.collector.increment(
            "llm_requests_total",
            labels={"model": model, "success": str(success)}
        )
        self.collector.observe(
            "llm_latency_ms",
            latency_ms,
            labels={"model": model}
        )
        self.collector.increment(
            "llm_tokens_total",
            tokens_used,
            labels={"model": model}
        )
        self.collector.increment(
            "llm_cost_total",
            cost,
            labels={"model": model}
        )
    
    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float,
        success: bool,
    ) -> None:
        """Record a tool execution."""
        self.collector.increment(
            "tool_calls_total",
            labels={"tool_name": tool_name, "success": str(success)}
        )
        self.collector.observe(
            "tool_duration_ms",
            duration_ms,
            labels={"tool_name": tool_name}
        )


class TracingContext:
    """Distributed tracing context."""
    
    def __init__(
        self,
        trace_id: str,
        span_id: str,
        parent_span_id: Optional[str] = None,
    ):
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.tags: Dict[str, str] = {}
        self.events: List[Dict[str, Any]] = []
    
    def add_tag(self, key: str, value: str) -> None:
        """Add a tag to the span."""
        self.tags[key] = value
    
    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Add an event to the span."""
        self.events.append({
            "name": name,
            "timestamp": datetime.utcnow().isoformat(),
            "attributes": attributes or {},
        })
    
    def finish(self) -> None:
        """Mark the span as finished."""
        self.end_time = time.time()
    
    @property
    def duration_ms(self) -> Optional[float]:
        """Get span duration in milliseconds."""
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "tags": self.tags,
            "events": self.events,
        }


class TracingCollector:
    """Collects and manages tracing spans."""
    
    def __init__(self, max_spans: int = 10000):
        self.max_spans = max_spans
        self._spans: Dict[str, TracingContext] = {}
        self._lock = None  # Will be asyncio.Lock when needed
    
    def start_span(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        name: str = "span",
    ) -> TracingContext:
        """Start a new span."""
        import uuid
        
        if trace_id is None:
            trace_id = str(uuid.uuid4())
        if span_id is None:
            span_id = str(uuid.uuid4())[:16]
        
        span = TracingContext(trace_id, span_id, parent_span_id)
        span.add_tag("span.name", name)
        
        self._spans[span_id] = span
        
        # Cleanup old spans
        if len(self._spans) > self.max_spans:
            oldest = min(self._spans.items(), key=lambda x: x[1].start_time)
            del self._spans[oldest[0]]
        
        return span
    
    def get_span(self, span_id: str) -> Optional[TracingContext]:
        """Get a span by ID."""
        return self._spans.get(span_id)
    
    def finish_span(self, span_id: str) -> Optional[TracingContext]:
        """Finish a span."""
        span = self._spans.get(span_id)
        if span:
            span.finish()
        return span
    
    def get_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Get all spans for a trace."""
        return [
            span.to_dict()
            for span in self._spans.values()
            if span.trace_id == trace_id
        ]


# Global instances
_metrics_collector: Optional[MetricsCollector] = None
_task_metrics: Optional[TaskMetrics] = None
_tracing_collector: Optional[TracingCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def get_task_metrics() -> TaskMetrics:
    """Get the task metrics helper."""
    global _task_metrics
    if _task_metrics is None:
        _task_metrics = TaskMetrics(get_metrics_collector())
    return _task_metrics


def get_tracing_collector() -> TracingCollector:
    """Get the tracing collector."""
    global _tracing_collector
    if _tracing_collector is None:
        _tracing_collector = TracingCollector()
    return _tracing_collector


class LogContextAdapter:
    """Adapter for adding context to logs."""
    
    def __init__(self, **context):
        self._context = context
    
    def bind(self, **additional_context):
        """Add more context."""
        from structlog.contextvars import bind_contextvars
        bind_contextvars(**self._context, **additional_context)
    
    def unbind(self, *keys):
        """Remove context."""
        from structlog.contextvars import unbind_contextvars
        unbind_contextvars(*keys)


def get_logger(name: str, **context) -> structlog.BoundLogger:
    """Get a logger with optional context."""
    log = structlog.get_logger(name)
    if context:
        log = log.bind(**context)
    return log
