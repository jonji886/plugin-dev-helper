"""Best-effort Langfuse integration.

Observability is deliberately outside the request critical path.  The app
continues with structured local metrics when the SDK is absent or Langfuse is
unavailable.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

logger = logging.getLogger(__name__)


class _NoopSpan:
    def update(self, **_: object) -> None:
        return None

    def end(self, **_: object) -> None:
        return None


class Observability:
    def __init__(self, enabled: bool | None = None):
        configured = os.getenv("LANGFUSE_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.enabled = configured if enabled is None else enabled
        self.client = None
        if self.enabled:
            try:
                from langfuse import Langfuse
                self.client = Langfuse(
                    public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
                    secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
                    host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
                )
            except Exception as error:  # optional dependency/configuration
                logger.warning("Langfuse unavailable; continuing without remote traces: %s", error)
                self.client = None

    @contextmanager
    def trace(self, trace_id: str, metadata: dict | None = None) -> Iterator[object]:
        trace = _NoopSpan()
        if self.client:
            try:
                trace = self.client.trace(id=trace_id, metadata=metadata or {})
            except Exception as error:
                logger.warning("Langfuse trace creation failed: %s", error)
        try:
            yield trace
        finally:
            try:
                if self.client and hasattr(self.client, "flush"):
                    self.client.flush()
            except Exception as error:
                logger.warning("Langfuse flush failed: %s", error)

    @contextmanager
    def span(self, trace: object, name: str, input_data: dict | None = None) -> Iterator[object]:
        span = _NoopSpan()
        started = perf_counter()
        try:
            if hasattr(trace, "span"):
                span = trace.span(name=name, input=input_data or {})
        except Exception as error:
            logger.warning("Langfuse span creation failed (%s): %s", name, error)
        try:
            yield span
        finally:
            try:
                span.end(metadata={"latency_ms": round((perf_counter() - started) * 1000, 2)})
            except Exception as error:
                logger.warning("Langfuse span update failed (%s): %s", name, error)

    @staticmethod
    def update(target: object, **values: object) -> None:
        try:
            if hasattr(target, "update"):
                target.update(**values)
        except Exception as error:
            logger.warning("Langfuse update failed: %s", error)


def get_observability() -> Observability:
    return Observability()
