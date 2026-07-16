"""OpenTelemetry wiring for distributed tracing.

Provides a unified tracing setup that all Sealfleet components use.
MVP: Console exporter + in-memory collector.
Later: OTLP exporter to Jaeger/Zipkin/Tempo.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

logger = logging.getLogger("mcpfinder.observability.tracing")


@dataclass
class SpanData:
    """Structured span data for export/storage."""
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    service: str
    operation: str
    start_time: float
    end_time: float
    duration_ms: float
    status: str
    attributes: dict[str, Any]
    events: list[dict]


class TracingProvider:
    """Centralized tracing provider for all Sealfleet services.

    Each service creates a TracingProvider with its service name.
    Spans are collected centrally for export.
    """

    def __init__(self, service_name: str, exporter: Optional[SpanExporter] = None):
        self.service_name = service_name
        self._exporter = exporter or ConsoleExporter()
        self._active_spans: dict[str, dict] = {}

    def new_trace_id(self) -> str:
        return uuid.uuid4().hex

    def new_span_id(self) -> str:
        return uuid.uuid4().hex[:16]

    @contextmanager
    def span(
        self,
        operation: str,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        **attributes: Any,
    ) -> Generator[dict, None, None]:
        """Create and manage a trace span.

        Usage:
            with tracing.span("process_request", user_id="123") as s:
                s["events"].append({"name": "step_1", "time": time.time()})
        """
        span = {
            "trace_id": trace_id or self.new_trace_id(),
            "span_id": self.new_span_id(),
            "parent_span_id": parent_span_id,
            "service": self.service_name,
            "operation": operation,
            "start_time": time.time(),
            "end_time": 0.0,
            "status": "ok",
            "attributes": attributes,
            "events": [],
        }
        self._active_spans[span["span_id"]] = span

        try:
            yield span
        except Exception as e:
            span["status"] = "error"
            span["attributes"]["error"] = str(e)
            raise
        finally:
            span["end_time"] = time.time()
            span["duration_ms"] = (span["end_time"] - span["start_time"]) * 1000
            del self._active_spans[span["span_id"]]

            span_data = SpanData(**span)
            self._exporter.export(span_data)


class SpanExporter:
    """Base class for span exporters."""

    def export(self, span: SpanData) -> None:
        raise NotImplementedError


class ConsoleExporter(SpanExporter):
    """Exports spans to the console (structured logging)."""

    def export(self, span: SpanData) -> None:
        logger.info(
            "trace service=%s op=%s duration=%.1fms status=%s trace_id=%s",
            span.service,
            span.operation,
            span.duration_ms,
            span.status,
            span.trace_id,
        )


class InMemoryExporter(SpanExporter):
    """Collects spans in memory for testing and debugging."""

    def __init__(self, max_spans: int = 10000):
        self._spans: list[SpanData] = []
        self._max = max_spans

    def export(self, span: SpanData) -> None:
        self._spans.append(span)
        if len(self._spans) > self._max:
            self._spans = self._spans[-self._max:]

    def get_spans(self, limit: int = 100) -> list[SpanData]:
        return self._spans[-limit:]

    def find_by_trace(self, trace_id: str) -> list[SpanData]:
        return [s for s in self._spans if s.trace_id == trace_id]

    def clear(self) -> None:
        self._spans.clear()


# Convenience factory
def create_tracer(
    service_name: str,
    exporter: Optional[SpanExporter] = None,
) -> TracingProvider:
    """Create a tracer for a service."""
    return TracingProvider(service_name, exporter=exporter)
