"""Run the reviewed offline CI allowlist and reject attempted network I/O."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import threading


ROOT = Path(__file__).resolve().parents[2]
TESTS = (
    "test_runtime.py", "test_news_api.py", "test_daily_brief_api.py",
    "test_llm_gateway.py", "test_research_graph.py", "test_research_attribution.py",
    "test_research_report_contract.py", "test_public_bootstrap.py",
)


class NetworkBlocked(RuntimeError):
    """Do not print addresses, credentials or request content."""


def main() -> int:
    attempts: list[str] = []
    context = threading.local()
    originals = {name: getattr(socket, name) for name in ("create_connection", "getaddrinfo", "socketpair")}
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def deny(kind):
        attempts.append(kind)
        raise NetworkBlocked("Offline CI prohibits network and database connections")

    def connect(sock, address):
        # Windows implements asyncio's stdlib self-pipe with a loopback pair.
        # Permit only that scoped stdlib operation, never general loopback I/O.
        if getattr(context, "socketpair", False) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        return deny("connect")

    def socketpair(*args, **kwargs):
        context.socketpair = True
        try:
            return originals["socketpair"](*args, **kwargs)
        finally:
            context.socketpair = False

    socket.socket.connect = connect
    socket.socket.connect_ex = lambda *_a, **_k: deny("connect_ex")
    socket.create_connection = lambda *_a, **_k: deny("create_connection")
    socket.getaddrinfo = lambda *_a, **_k: deny("dns")
    socket.socketpair = socketpair
    try:
        if sys.argv[1:] == ["--check-guard"]:
            for probe in (
                lambda: socket.socket.connect(None, ("127.0.0.1", 1)),
                lambda: socket.socket.connect_ex(None, ("127.0.0.1", 1)),
                lambda: socket.create_connection(("127.0.0.1", 1)),
                lambda: socket.getaddrinfo("example.org", 443),
            ):
                try:
                    probe()
                except NetworkBlocked:
                    pass
                else:
                    raise AssertionError("Offline guard did not reject the operation")
            print("Offline guard self-check: 4 operations blocked before I/O")
            return 0
        if sys.argv[1:]:
            raise SystemExit("Only the fixed offline allowlist or --check-guard is supported")

        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        for name in ("MANUAL_COLLECTION", "SCHEDULED_COLLECTION", "AI_SUMMARY", "DAILY_BRIEF", "RESEARCH", "RESEARCH_EXTERNAL"):
            os.environ["AIPULSE_ENABLE_" + name] = "0"
        sys.dont_write_bytecode = True
        sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
        os.chdir(ROOT)
        import pytest

        result = pytest.main([
            "-q", "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin",
            *(str(ROOT / "tests/aipulse/unit" / name) for name in TESTS),
        ])
        if attempts:
            print(f"FAILED: {len(attempts)} attempted network operations, including caught exceptions")
            return 1
        print("Offline network guard: no attempted external/database connections")
        return int(result)
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        for name, function in originals.items():
            setattr(socket, name, function)


if __name__ == "__main__":
    raise SystemExit(main())
