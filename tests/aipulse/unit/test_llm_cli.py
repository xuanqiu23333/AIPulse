"""The manual model probe is offline unless explicitly executed."""

from aipulse.llm.config import DeepSeekSettings
from aipulse.llm.gateway import GenerationResult, TokenUsage


def test_check_reports_configuration_without_constructing_client(monkeypatch, capsys):
    from aipulse.llm import cli

    settings = DeepSeekSettings(DEEPSEEK_API_KEY="offline-test-key", _env_file=None)
    monkeypatch.setattr(cli, "load_deepseek_settings", lambda: settings)
    monkeypatch.setattr(cli, "DeepSeekGateway", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("--check must not construct a network client")))
    assert cli.main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "deepseek-v4-pro" in output and "high" in output
    assert "offline-test-key" not in output


def test_execute_makes_one_bounded_call_with_retries_disabled(monkeypatch, capsys):
    from aipulse.llm import cli

    settings = DeepSeekSettings(DEEPSEEK_API_KEY="offline-test-key", _env_file=None)
    calls = []

    class FakeGateway:
        def __init__(self, loaded, *, max_retries):
            assert loaded is settings and max_retries == 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            calls.append("closed")

        async def generate_text(self, prompt):
            calls.append(prompt)
            return GenerationResult(
                text="四。", model="deepseek-v4-pro", elapsed_seconds=0.2,
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

    monkeypatch.setattr(cli, "load_deepseek_settings", lambda: settings)
    monkeypatch.setattr(cli, "DeepSeekGateway", FakeGateway)
    assert cli.main(["--execute"]) == 0
    assert len(calls) == 2 and calls[1] == "closed"
    assert calls[0].max_tokens <= 1024 and calls[0].use_research_model is False
    output = capsys.readouterr().out
    assert "四。" in output and "15" in output
    assert "offline-test-key" not in output
