"""OpenBox Instrumentation — OTel setup, hooks, and span processing."""

from openbox.instrumentation.otel_setup import setup_otel_governance
from openbox.instrumentation.span_processor import GovernanceSpanProcessor

__all__ = [
    "setup_otel_governance",
    "GovernanceSpanProcessor",
]
