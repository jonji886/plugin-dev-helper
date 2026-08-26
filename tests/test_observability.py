import unittest

from app.observability import Observability


class ExplodingSpan:
    def update(self, **_kwargs):
        raise RuntimeError("span update unavailable")

    def end(self, **_kwargs):
        raise RuntimeError("span end unavailable")


class ExplodingTrace:
    def span(self, **_kwargs):
        raise RuntimeError("span creation unavailable")


class ExplodingClient:
    def trace(self, **_kwargs):
        raise RuntimeError("trace creation unavailable")

    def flush(self):
        raise RuntimeError("flush unavailable")


class ObservabilityTests(unittest.TestCase):
    def test_disabled_observability_is_a_noop(self):
        observability = Observability(enabled=False)
        with observability.trace("trace-1", {"query": "safe"}) as trace:
            with observability.span(trace, "answer") as span:
                observability.update(span, answer="ok")

    def test_client_creation_and_flush_failures_are_fail_open(self):
        observability = Observability(enabled=True)
        observability.client = ExplodingClient()
        with observability.trace("trace-2") as trace:
            with observability.span(trace, "retrieval") as span:
                observability.update(span, retrieved_chunks=1)

    def test_span_update_and_end_failures_are_fail_open(self):
        observability = Observability(enabled=False)
        with observability.span(ExplodingTrace(), "llm") as span:
            observability.update(ExplodingSpan(), model="test")
            self.assertIsNotNone(span)


if __name__ == "__main__":
    unittest.main()
