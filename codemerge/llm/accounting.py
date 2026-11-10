"""Context-local provider call accounting for LLM boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class ProviderCallCounter:
    total: int = 0
    by_operation: dict[str, int] = field(default_factory=dict)
    by_provider: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, int] = field(default_factory=dict)

    def record(self, *, provider: str, operation: str, model: str | None = None) -> None:
        provider_key = str(provider or "unknown")
        operation_key = str(operation or "unknown")
        self.total += 1
        self.by_provider[provider_key] = self.by_provider.get(provider_key, 0) + 1
        self.by_operation[operation_key] = self.by_operation.get(operation_key, 0) + 1
        if model:
            model_key = str(model)
            self.by_model[model_key] = self.by_model.get(model_key, 0) + 1

    def snapshot(self) -> dict[str, object]:
        return {
            "observed_provider_calls": int(self.total),
            "observed_by_operation": dict(sorted(self.by_operation.items())),
            "observed_by_provider": dict(sorted(self.by_provider.items())),
            "observed_by_model": dict(sorted(self.by_model.items())),
        }


_ACTIVE_COUNTER: ContextVar[ProviderCallCounter | None] = ContextVar(
    "codemerge_provider_call_counter",
    default=None,
)


@contextmanager
def provider_call_counter() -> Iterator[ProviderCallCounter]:
    counter = ProviderCallCounter()
    token = _ACTIVE_COUNTER.set(counter)
    try:
        yield counter
    finally:
        _ACTIVE_COUNTER.reset(token)


def record_provider_call(*, provider: str, operation: str, model: str | None = None) -> None:
    counter = _ACTIVE_COUNTER.get()
    if counter is None:
        return
    counter.record(provider=provider, operation=operation, model=model)
