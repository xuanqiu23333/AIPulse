"""Allowlisted JSON logging; callers cannot pass raw prompts or credentials."""

import json
import logging
import re

from aipulse.runtime.context import RuntimeContext, current_context, new_context


_CODE = re.compile(r"\A[a-zA-Z0-9_.-]{1,128}\Z")
_TEXT_FIELDS = frozenset({"node_name", "status", "error_code"})
_COUNT_FIELDS = frozenset({"round_no", "count"})


def _safe_code(value: str | None) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def format_event(
    event: str, *, context: RuntimeContext | None = None, **fields: object
) -> str:
    """Serialize only known identifiers and short codes; omit arbitrary values."""
    if not _safe_code(event):
        raise ValueError("Invalid event code")
    context = context or current_context() or new_context()
    record: dict[str, str | int] = {
        "event": event,
        "trace_id": context.trace_id,
        "created_at": context.created_at.isoformat(),
    }
    for key in ("request_id", "task_id"):
        value = _safe_code(getattr(context, key))
        if value is not None:
            record[key] = value
    for key in _TEXT_FIELDS:
        value = _safe_code(fields.get(key))
        if value is not None:
            record[key] = value
    for key in _COUNT_FIELDS:
        value = fields.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            record[key] = value
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    context: RuntimeContext | None = None,
    **fields: object,
) -> None:
    """Emit one structured record through the supplied application logger."""
    logger.info("%s", format_event(event, context=context, **fields))
