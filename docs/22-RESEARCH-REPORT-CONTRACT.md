# Deep Research 结构化报告稳定性收尾

日期：2026-10-23。只做代码审查、最小修复和离线定向验证；没有连接开发库/测试库、启动服务、调用模型或创建任务。本轮没有恢复或改写任何历史报告。

## 1. 静态审查结论与历史边界

没有发现能据代码直接证明的字段名冲突、不可满足的约束或必然失败的后端转换。原实现存在结构说明不足：Gateway 只发送示例，报告示例的 `analyses=[]` 没有展示 AnalysisDraft 的字段；自然语言提示也没有完整列出必填项、嵌套结构、禁止额外字段及长度下限。

Prompt 的精简目标与 Pydantic 上限不是互相矛盾的契约：概览最多 1 项/Schema 2 项、结论最多 3 项/Schema 5 项、引文 12–300 字符/Schema 12–350 字符均是允许范围内的更紧输出目标。缺口是模型以前没有获得完整 Schema，不能据此断定历史失败由哪个字段引起。

历史任务 `cd8c8a08-1296-4330-92ee-c3e0da236222` 为 failed / report_schema_mismatch，没有截断。其原始模型 JSON、具体 ValidationError.loc/type、候选 ReportDraft 都没有保存。无法恢复缺失字段、违规类型、长度或额外键，也无法重放那次真实响应。以下测试全部是固定合成样例，不是历史原始输出。

## 2. 单一草稿契约与后端报告契约

模型只生成 `ReportDraft`，Gateway 的 JSON 校验与 Graph 的二次校验使用同一个类。最终 `ResearchReport` 是后端增加来源、状态、验证标记和计费统计后的读取契约，不能要求模型直接生成它。

| 字段 | 模型草稿 ReportDraft | 后端最终报告 |
| --- | --- | --- |
| overview / conclusions / trends | ClaimDraft 数组；每项 text_zh + supports | 验证后 VerifiedClaim 数组，增加 citations、support_quotes、verification |
| analyses | `{question_id, claims}` 数组；不包含 question/title | 后端按 question_id 添加真实 question，并保留通过校验的 claims |
| limitations | 字符串数组；非空字符串、每条最多 300 字符、无 URL | 增加真实缺口、来源局限及错误说明 |
| topic / status / error_code | 不允许模型提交 | 后端从请求、流程和校验结果确定 |
| sources / models / metrics | 不允许模型提交 | 来源来自已存证据，模型和用量来自实际调用 |
| validation_issues | 不允许模型提交 | 逐项引用/原文/数字校验拒绝原因 |
| schema_diagnostics | 不允许模型提交 | 脱敏的结构校验阶段与字段路径 |

草稿严格禁止额外字段；`conclusions` 仍必填，无可支持的结论时必须显式为 `[]`。ClaimDraft 的 text_zh/supports、SupportQuote 的 citation/quote、AnalysisDraft 的 question_id/claims 均继续必填。E 编号匹配 `E[0-9]{3}`，Q 编号匹配 Q1–Q3；引用与来源存在性仍由后端检查。

仅 overview、analyses、trends、limitations 四个可空栏目允许省略并默认 `[]`。不接受把 null、字符串或对象变成数组；不补造结论、引用或编号，没有新增别名转换。没有结论不能因此变成 completed。

## 3. 结构化输出改进

- Gateway 在固定 system 指令中同时发送 `schema.model_json_schema()` 和合法示例，包含 required、类型、嵌套 `$defs`、pattern、长度限制和 additionalProperties。Schema 不来自用户或模型，校验仍由同一个 Pydantic 类执行。
- 报告 Prompt 明确顶层数组、每项字段、后端专属字段；示例现在展示 `analyses: [{question_id: "Q1", claims: []}]`，避免空数组隐藏嵌套形状。
- 保留现有 `response_format={"type":"json_object"}` 和客户端 Pydantic 严格校验；没有换用尚未在项目中验证的供应商 strict JSON Schema 参数，也没有宣称 JSON Object 模式保证 Schema 合规。
- 不展平必要的 supports/citation/quote 关系，不增加另一份手写报告 Schema。可选空栏目可省略以减少输出；没有放宽核心必填字段。
- 当前 Prompt 版本为 **research-zh-v6**。规划 1000 / 报告 6144、累计输出 7144、最多两次调用、SDK retries=0 和所有时限不变。完整 Schema 会增加有界输入上下文，未来输入费用可能增加；本轮没有付费调用。
- 此处仅改善生成约束，不保证真实模型一定遵守，不自动重试、修补或拼接不合法 JSON。

## 4. 安全字段级诊断

新增 `llm/validation.py`，所有诊断来自捕获的 ValidationError，最多记录 10 项、每条 loc 最多 8 层，并附 total_errors/truncated。研究任务最多保留 3 组诊断（两次模型调用及一次报告转换）。

**下例是固定测试样例，不是历史失败内容：**

```json
{
  "stage": "provider_output",
  "schema_name": "ReportDraft",
  "issues": [{
    "loc": ["conclusions", 0, "supports", 0, "quote"],
    "type": "string_type",
    "message": "Expected a string."
  }],
  "total_errors": 1,
  "truncated": false
}
```

| stage | 含义 |
| --- | --- |
| provider_output | 模型返回 JSON 后，在 Gateway 对指定 Pydantic Schema 的严格校验失败 |
| backend_revalidation | Gateway 返回对象类型违反内部契约，或 Graph 对已返回模型的二次校验失败 |
| report_conversion | 合法草稿进入后端报告组装时发生 Pydantic 校验失败 |

安全规则：

- 使用 `errors(include_input=False, include_context=False, include_url=False)`，不序列化异常对象、原始 msg、ctx、input 或完整原始响应。
- loc 只保留 Schema 中声明的字段名及有界数组索引；未知/额外键可能含外部文本，替换为 `[unknown_field]`。
- type 仅保留 pydantic-core 的标准错误类型；未知自定义类型归为 validation_error，避免自定义错误码携带内容。
- message 来自固定描述表，不使用自定义校验器可能拼入原值的异常文字。不记录 Prompt、证据全文、API Key、Authorization 或 reasoning_content。
- 后端二次序列化使用 warnings=False 后再次严格校验，避免损坏的对象通过 Pydantic serializer 警告输出原值；没有跳过校验。
- 日志事件为 `schema_validation_failed`，沿用安全 trace_id/task_id 上下文。通用 Gateway 错误码仍为 schema_mismatch；研究报告错误码仍为 **report_schema_mismatch**。

诊断由 LLMError.diagnostic 传到 ResearchState，再进入最终报告 `schema_diagnostics`，由现有 result_json 保存，GET 报告可读取。没有新增表、ORM 字段或 Alembic revision，也不修改前端。

结构失败与引用失败分开：没有合法草稿时，validation_issues 为空不能解释为全部引用成功；schema_diagnostics 说明具体结构阶段。后端报告转换失败时保留真实来源身份和失败诊断，生成明确 failed 的读取结果，不制造任何成功结论。模型失败用量仍由原单一计数路径累计一次。

旧 failed/partial 报告缺少 schema_diagnostics 时默认 `[]`，不改变原状态、Prompt 版本或历史用量；新代码无法补回旧任务当时没有保存的诊断。未知额外字段名和自定义校验器细节故意不完整展示，是诊断的隐私边界。

另以已保存的 v4 failed 和 v2 partial 报告 JSON 做只读解析，确认状态/版本保持、诊断缺省兼容且文件哈希未变；这只是历史结果读取检查，不是失败模型原始响应的重放。

## 5. 离线验证结果

固定 JSON + MockTransport + Fake Gateway + RecordedSession，无数据库/公网/模型请求。

- 正常 ReportDraft、发送自动 Schema 与嵌套示例。
- 缺少 conclusions、错误类型、Q 编号错误、嵌套 quote 类型错误、未知额外字段、非法 Literal 枚举。
- 可选空栏目缺省、拒绝 null/错误类型；无证据/无结论不会标 completed。
- 模型输出阶段、后端二次校验阶段及后端报告转换阶段区分。
- 正常引用、不合法引用、独立有效结论保留，沿用现有质量回放。
- 嵌套路径、任意额外键、自定义校验器异常含敏感值、诊断条数上限与安全日志。
- 诊断在记录型仓储的失败报告中保存/回读，证据及日志保留，用量不重复累计，运行槽/Session 关闭。
- 历史 failed/partial payload 读取兼容，以及现有摘要/日报 API 的 Mock 回归。

实际结果：8 个相关文件 **126 passed**，仅有既有 Starlette/httpx 弃用提示。没有全量回归，也没有声称重放历史真实模型输出。

```powershell
Set-Location D:\Projects\AIPulse
$env:PYTHONPATH = 'src'
python -B -m pytest -q -p no:cacheprovider `
  tests/aipulse/unit/test_research_report_contract.py tests/aipulse/unit/test_llm_gateway.py `
  tests/aipulse/unit/test_research_graph.py tests/aipulse/unit/test_research_quality.py `
  tests/aipulse/unit/test_research_persistence.py tests/aipulse/unit/test_research_api.py `
  tests/aipulse/unit/test_news_ai_summary.py tests/aipulse/unit/test_daily_brief_api.py
```

## 6. 文件与尚未验证事项

- 新增：`src/aipulse/llm/validation.py`、`tests/aipulse/unit/test_research_report_contract.py`、本文档。
- 修改：`src/aipulse/llm/gateway.py`、`src/aipulse/research/{state,graph,evidence}.py`。
- 测试适配：`tests/aipulse/unit/test_llm_gateway.py`（允许声明字段路径，仍禁止原值）、`tests/aipulse/unit/test_research_persistence.py`（诊断存取）。
- 文档同步：09、13、20；21 及项目外真实验收记录保留其原始失败事实。

未修改 .env、Schema、revision、上游、历史任务或 Git 历史。本轮未启动 FastAPI/Scheduler，没有新任务、真实用量或数据库写入。

**仍未真实验证：**v5/v6 覆盖规则的研究效果、完整 Schema 指令下真实模型的结构稳定性、真实字段诊断落库和成功报告质量。最近一次真实报告仍为 failed，不能标为已修复通过。本轮结束后不安排新的付费研究。
