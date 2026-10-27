"""OpenTelemetry spans for every run, stage, model turn and tool call.

Exports over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set (Arize Phoenix accepts it, e.g.
http://localhost:6006), otherwise writes one JSON line per finished span to
.omnigent-logs/runs/<run_id>/traces.jsonl.

Privacy: only attribute names in ALLOWED reach a span. Tool arguments, resume or job text,
model messages and keys are never recorded, whatever a caller passes.
"""
import json
import os
from pathlib import Path

ALLOWED = {
    "run.id", "task.id", "stage", "agent", "turn", "mode", "tool.name", "attempt", "attempts",
    "outcome", "latency_ms", "prompt_tokens", "completion_tokens", "verdict", "issues",
    "status", "rollback", "revision", "failure.category", "checkpoint",
}

try:
    from opentelemetry import trace as _otel
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
    HAVE_OTEL = True
except ImportError:                                  # tracing becomes a no-op
    HAVE_OTEL = False


def clean(attrs: dict | None) -> dict:
    out = {}
    for key, value in (attrs or {}).items():
        if key in ALLOWED and isinstance(value, (str, bool, int, float)):
            out[key] = value
    return out


if HAVE_OTEL:
    class JsonlExporter(SpanExporter):
        def __init__(self, path: Path):
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)

        def export(self, spans):
            with self.path.open("a", encoding="utf-8") as f:
                for s in spans:
                    f.write(json.dumps({
                        "name": s.name, "trace_id": f"{s.context.trace_id:032x}",
                        "span_id": f"{s.context.span_id:016x}",
                        "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
                        "duration_ms": round((s.end_time - s.start_time) / 1e6, 1),
                        "attributes": dict(s.attributes or {}),
                        "events": [{"name": e.name, **dict(e.attributes or {})} for e in s.events],
                        "status": s.status.status_code.name,
                    }) + "\n")
            return SpanExportResult.SUCCESS

        def shutdown(self):
            return None


class _Handle:
    def __init__(self, cm):
        self._cm, self.span = cm, None

    def __enter__(self):
        self.span = self._cm.__enter__() if self._cm else None
        return self

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc) if self._cm else False

    def set(self, **attrs):
        if self.span is not None:
            for k, v in clean(attrs).items():
                self.span.set_attribute(k, v)


class Tracer:
    """tracer.span(name, attrs) -> context manager with .set(**attrs); tracer.event(handle, ...)."""

    def __init__(self, run_dir: Path | None = None, exporter=None, service: str = "nextnode-harness"):
        self.enabled = HAVE_OTEL
        if not self.enabled:
            return
        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        if exporter is None:
            endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            if endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                exporter = OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces")
                self.target = endpoint
            else:
                path = Path(run_dir or ".") / "traces.jsonl"
                exporter = JsonlExporter(path)
                self.target = str(path)
        else:
            self.target = "custom"
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        self._provider = provider
        self._tracer = provider.get_tracer("nextnode.harness")

    def span(self, name: str, attrs: dict | None = None) -> _Handle:
        if not self.enabled:
            return _Handle(None)
        return _Handle(self._tracer.start_as_current_span(name, attributes=clean(attrs)))

    def event(self, handle, name: str, attrs: dict | None = None):
        if self.enabled and handle is not None and handle.span is not None:
            handle.span.add_event(name, clean(attrs))

    def shutdown(self):
        if self.enabled:
            self._provider.shutdown()
