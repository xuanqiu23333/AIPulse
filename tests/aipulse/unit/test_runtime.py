"""Runtime identifiers and log records remain safe for later Agent use."""

import json
import logging
from io import StringIO
from uuid import UUID

from aipulse.runtime.context import bind_context, current_context, new_context
from aipulse.runtime.logging import format_event, log_event


def test_new_context_has_unique_trace_and_utc_creation_time():
    first = new_context(request_id="request-1", task_id="task-1")
    second = new_context()

    assert UUID(first.trace_id)
    assert first.trace_id != second.trace_id
    assert first.request_id == "request-1"
    assert first.task_id == "task-1"
    assert first.created_at.utcoffset().total_seconds() == 0


def test_context_binding_restores_previous_context():
    outer = new_context(request_id="outer")
    inner = new_context(request_id="inner")

    assert current_context() is None
    with bind_context(outer):
        assert current_context() is outer
        with bind_context(inner):
            assert current_context() is inner
        assert current_context() is outer
    assert current_context() is None


def test_structured_event_exposes_only_safe_fields():
    context = new_context(request_id="request-1", task_id="task-1")
    record = json.loads(
        format_event(
            "agent.node_started",
            context=context,
            node_name="research",
            status="running",
            round_no=2,
            password="database-secret",
            token="api-secret",
            database_url="mysql+asyncmy://user:dsn-secret@127.0.0.1/db",
            raw_message="Bearer bearer-secret",
        )
    )

    assert record["event"] == "agent.node_started"
    assert record["trace_id"] == context.trace_id
    assert record["request_id"] == "request-1"
    assert record["task_id"] == "task-1"
    assert record["node_name"] == "research"
    assert record["round_no"] == 2
    assert not any(key in record for key in ("password", "token", "database_url", "raw_message"))
    serialized = json.dumps(record)
    assert "secret" not in serialized


def test_logger_emits_one_structured_record_without_credentials():
    stream = StringIO()
    logger = logging.getLogger("aipulse.test.runtime")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with bind_context(new_context(task_id="task-1")):
            log_event(logger, "agent.started", status="running", password="hidden-secret")
    finally:
        logger.removeHandler(handler)
    record = json.loads(stream.getvalue())
    assert record["event"] == "agent.started"
    assert record["task_id"] == "task-1"
    assert "hidden-secret" not in stream.getvalue()
