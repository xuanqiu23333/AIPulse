"""Per-operation context, ready to propagate through LangGraph later."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    trace_id: str
    request_id: str | None
    task_id: str | None
    created_at: datetime


_CURRENT_CONTEXT: ContextVar[RuntimeContext | None] = ContextVar(
    "aipulse_runtime_context", default=None
)


def new_context(
    *, request_id: str | None = None, task_id: str | None = None
) -> RuntimeContext:
    """Create an operation context with a fresh trace identifier and UTC time."""
    return RuntimeContext(
        trace_id=str(uuid4()),
        request_id=request_id,
        task_id=task_id,
        created_at=datetime.now(timezone.utc),
    )


def current_context() -> RuntimeContext | None:
    """Read the active context without mutating the caller's task."""
    return _CURRENT_CONTEXT.get()


@contextmanager
def bind_context(context: RuntimeContext) -> Iterator[RuntimeContext]:
    """Scope the context and restore an outer context after completion."""
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)
