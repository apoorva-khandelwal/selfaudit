"""
Sets up OpenTelemetry to send traces to a local Phoenix instance.
Call setup() once at startup before running any agents.
"""

import os
import phoenix as px
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

PHOENIX_PORT = 6006
_tracer = None


def setup():
    global _tracer

    # Disable gRPC (port 4317 may be blocked); use HTTP-only OTLP
    os.environ["PHOENIX_GRPC_PORT"] = "0"
    os.environ["PHOENIX_PORT"] = str(PHOENIX_PORT)

    # Start Phoenix in-process
    session = px.launch_app()

    # Point OTel at Phoenix's HTTP OTLP endpoint
    exporter = OTLPSpanExporter(endpoint=f"http://localhost:{PHOENIX_PORT}/v1/traces")
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _tracer = trace.get_tracer("selfaudit")
    print(f"  Phoenix  → http://localhost:{PHOENIX_PORT}\n")
    return _tracer


def get_tracer():
    return _tracer
