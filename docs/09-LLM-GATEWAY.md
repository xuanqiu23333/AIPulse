# AIPulse S3-1：DeepSeek 模型接入

本阶段仅提供异步 LLM Gateway、结构化 JSON 校验和手动连接验证入口。不会自动处理已有新闻，也不连接数据库。真实模型调用需要单独授权并产生费用。

## 配置

沿用项目根目录现有 `.env` 加载方式；进程环境变量优先。不要在命令、日志或文档中填写真实密钥。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 必填 | 服务端 API 凭据，使用 `SecretStr` 保存 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 仅接受 DeepSeek 官方 HTTPS 入口 |
| `DEEPSEEK_MODEL` | `deepseek-v4-pro` | 普通文本及 JSON 调用模型 |
| `DEEPSEEK_RESEARCH_MODEL` | `deepseek-v4-pro` | 预留复杂任务的模型选择；本阶段不运行研究 Agent |
| `DEEPSEEK_REASONING_EFFORT` | `high` | 普通任务推理强度 |
| `DEEPSEEK_RESEARCH_REASONING_EFFORT` | `max` | 预留复杂任务推理强度 |
| `DEEPSEEK_TIMEOUT_SECONDS` | `120` | 单次请求超时，允许 1–300 秒 |

配置错误只返回固定错误类别，不输出原始配置。普通和研究强度目前只接受 `high`、`max`。Gateway 使用 DeepSeek Chat Completions 的 `reasoning_effort`，并通过 OpenAI Python SDK 的 `extra_body={"thinking":{"type":"enabled"}}` 启用官方 thinking 模式。[DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 说明了该参数及强度映射。thinking 模式下 `temperature`、`presence_penalty`、`frequency_penalty` 不生效；V4 Pro 的 `top_p` 在 thinking 模式下也被忽略，因此 Gateway 不发送这些参数。

## 在后续服务中使用

```python
from aipulse.llm import DeepSeekGateway, GenerationRequest, load_deepseek_settings

settings = load_deepseek_settings()
async with DeepSeekGateway(settings) as gateway:
    result = await gateway.generate_text(GenerationRequest(
        system_prompt="用中文简短回答。",
        user_prompt="2 + 2 等于几？",
        max_tokens=1024,
    ))
    answer = result.text
    usage = result.usage
```

`GenerationRequest.use_research_model=True` 会选择研究模型与配置中的研究强度（默认 `max`），但不启动研究 Agent。可选的请求字段 `reasoning_effort="high"` 或 `"max"` 仅覆盖本次调用的强度，不修改环境配置；研究规划沿用配置，报告节点显式使用 `high`。结果携带实际模型、耗时、输入/输出/总 Token，以及 API 返回时的 reasoning Token 数；API 不返回 usage 时字段为 `None`，不会伪造零用量。Gateway 只读取最终 `content`，不会输出、保存或返回内部 `reasoning_content`。默认 SDK `max_retries=2`，最多三次尝试；可在构造时设置为 0–2。研究运行固定 `max_retries=0`。需用 `async with` 或 `aclose()` 释放连接。

结构化输出调用 `generate_json(request, Schema, example=Schema(...))`。Gateway 将同一 Pydantic 类生成的完整 JSON Schema、**JSON** 指令和实例示例加入 system prompt，发送 `response_format={"type":"json_object"}`，然后解析 JSON 并由 Pydantic v2 严格验证。JSON Object 模式不保证符合 Schema；未启用或声称已验证供应商 strict JSON Schema。调用方须给出合法示例；模型输出空、非法 JSON、Schema 不符或 `finish_reason=length` 时会抛出分类错误，不返回成功值。请求的 `max_tokens` 必须在 1–8192 之间；JSON 模式应按任务需要给足输出预算。完整 Schema 会增加输入上下文。

```python
from pydantic import BaseModel

class ExampleAnswer(BaseModel):
    answer: str

result = await gateway.generate_json(
    GenerationRequest(system_prompt="请回答问题。", user_prompt="2 + 2 等于几？"),
    ExampleAnswer,
    example=ExampleAnswer(answer="四。"),
)
validated_answer = result.value.answer
```

`LLMError.code` 将 401、402、429、5xx 分别归为 `authentication_failed`、`insufficient_balance`、`rate_limited`、`service_unavailable`，另区分连接错误、超时、无效请求和响应格式问题。SDK 会对限流、服务端错误、部分临时 HTTP 状态及网络/超时错误进行有限重试；身份、余额和参数错误不自动重试。异常内容不包含密钥、Prompt、原始响应或内部推理。

收到模型响应后，Gateway 先提取并验证 `usage`，再判断 `finish_reason`、内容和 JSON。截断、非法 JSON、Pydantic 校验失败等 `LLMError` 携带可用的 `usage` 和 `elapsed_seconds`，不携带输出文本。没有响应或缺失/无效 usage 时保持 `None`。研究任务对每次尝试仅累计一次已知用量；`usage_missing_calls > 0` 代表总用量不完整，不能当作完整账单。`reasoning_tokens` 是输出 Token 的明细，不再加到 `total_tokens` 上。历史失败任务的未知用量不补写。

结构校验失败还携带可选 `LLMError.diagnostic`：阶段、Schema 名、声明字段 loc、标准 type 和固定 message。未知字段名、自定义异常文字及原始值均不记录。研究流程将诊断保存在报告 schema_diagnostics；保留 report_schema_mismatch，与引用拒绝记录分开。详见 [报告契约与安全字段诊断](22-RESEARCH-REPORT-CONTRACT.md)。

## 本地验证

只检查配置，不发出 DeepSeek 请求：

```powershell
Set-Location D:\Projects\AIPulse
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -B -m aipulse.llm.cli --check
```

取得本次付费调用授权后，用户可在本机手动执行一次连接验证：

```powershell
Set-Location D:\Projects\AIPulse
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -B -m aipulse.llm.cli --execute
```

`--execute` 只发送一条简短中文问题，使用普通模型、`high` 强度、`max_tokens=1024` 和配置的超时，关闭 SDK 自动重试以限制为一次 HTTP 请求。输出最终中文内容、实际模型、Token 用量及耗时，不显示密钥或内部推理。该调用可能产生费用；Codex 不代用户执行。
