"""Explicit local DeepSeek configuration check or one paid connection probe."""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from aipulse.llm.config import LLMConfigurationError, load_deepseek_settings
from aipulse.llm.gateway import DeepSeekGateway, GenerationRequest, LLMError


async def _execute(settings) -> int:
    request = GenerationRequest(
        system_prompt="请用简短中文回答，只给最终答案。",
        user_prompt="2 + 2 等于几？请用一句话回答。",
        max_tokens=1024,
    )
    try:
        # One completion request only; no automatic retry in this paid probe.
        async with DeepSeekGateway(settings, max_retries=0) as gateway:
            result = await gateway.generate_text(request)
    except LLMError as error:
        print(f"DeepSeek call failed: {error.code}", file=sys.stderr)
        return 1
    print(f"中文结果：{result.text.strip().replace(chr(10), ' ')[:200]}")
    print(f"模型：{result.model}")
    if result.usage is None:
        print("Token 用量：未返回")
    else:
        usage = result.usage
        print(f"Token 用量：输入 {usage.prompt_tokens}，输出 {usage.completion_tokens}，"
              f"总计 {usage.total_tokens}")
    print(f"耗时：{result.elapsed_seconds:.2f} 秒")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepSeek local model connection probe")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="validate local settings only")
    action.add_argument("--execute", action="store_true", help="make one paid model request")
    args = parser.parse_args(argv)
    try:
        settings = load_deepseek_settings()
    except LLMConfigurationError:
        print("DeepSeek configuration: invalid", file=sys.stderr)
        return 2
    if args.check:
        print("DeepSeek configuration: ready (API key configured)")
        print(f"Model: {settings.model}; reasoning effort: {settings.reasoning_effort}")
        print(f"Research model: {settings.research_model}; "
              f"reasoning effort: {settings.research_reasoning_effort}")
        print(f"Timeout: {settings.timeout_seconds} seconds")
        return 0
    return asyncio.run(_execute(settings))


if __name__ == "__main__":
    raise SystemExit(main())
