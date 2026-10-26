# S3-2B：单条新闻中文摘要与缓存

`POST /api/v1/news/events/{event_id}/summary` 对一条已入库、可见的事件读取缓存或生成中文摘要。`GET` 同一路径只读缓存，不调用模型。缓存保存在已有 `news_event` 的 0003 新增可空列中；原有 `news_event.summary` 文本列保持原状。不新增业务表，也不读取网页全文。`news_item.content` 是采集时保存的摘录，可能为空。

## 输入与输出

请求只有正整数 `event_id` 路径参数；请求体和查询参数均拒绝。服务端读取该事件和最多 8 条关联 `news_item`，使用原始标题、已存摘录、provider、来源名称和发布时间构造有界模型输入。重复的标题/摘录只交给模型一次；不同的有效原始 URL 仍由后端保留。超过 8 条、摘录很短或来源链接被省略时，响应 `limitations` 会说明边界。

成功响应包含 `event_id`、`title`、`summary_zh`、`key_points`（通常 2–4 点，证据不足时可只有 1 点）、`limitations`、`sources`、`model`、`token_usage`、`latency_ms` 和 `cached`。`cached=false` 表示本次调用模型并保存，`cached=true` 表示直接读取已有结果。缓存响应中的 `latency_ms` 和 `token_usage` 是原始生成调用的记录，不是本次缓存读取耗时或新增用量；上游未返回 usage 时为 `null`。`sources` 每次都由后端从当前数据库记录读取并验证；模型生成字段中的 URL 会被拒绝。任何响应都不含 DeepSeek 密钥或内部 `reasoning_content`。

标题、摘录和来源名称作为不可信 JSON 数据送给 Gateway；固定 system 指令要求忽略其中的任务指令，只按已存事实写中文摘要，无法证实时明确说明局限。此约束不能替代对最终内容的人工核对。模型输出使用 DeepSeek JSON 模式及 Pydantic v2 验证；空内容、非法 JSON、字段不符和截断会返回明确错误码，不伪造摘要。JSON 模式已有单条真实新闻调用验证。

## 缓存与数据库边界

0003 在 `news_event` 增加 `summary_json`（结构化摘要、原次 usage 与耗时）、`summary_model`、`summary_prompt_version`、`summary_input_fingerprint`（SHA-256）和 `summary_generated_at`（UTC `DATETIME(6)`），全部可空。输入指纹由实际发送的有界新闻标题、摘录、来源名称、发布时间，以及用于响应的来源身份、有效 URL 和局限说明确定；不包含 `fetched_at`、原始 Prompt、密钥或 reasoning 内容。Prompt 修改时须更新 `SUMMARY_PROMPT_VERSION`。POST 只有在指纹、当前模型与 Prompt 版本均一致且缓存 JSON 通过 Pydantic 校验时命中；GET 不读取模型配置，只返回指纹及 Prompt 版本仍匹配的有效缓存，过期或不存在时为 404。

模型调用前关闭只读 Session；调用完成后开启新的短事务，锁定并重读事件及相关记录，重新计算指纹。若新闻变化则返回 409 `summary_input_changed` 且不写入旧结果。模型或存储失败不会覆盖原有有效摘要。单实例锁覆盖 POST 的读取、调用和写入；多进程之间仍可能重复付费，因此本地只用一个 worker。

## 费用与运行边界

付费 POST 默认关闭。只有启动 FastAPI 前在**本地进程**设置 `AIPULSE_ENABLE_AI_SUMMARY=1`，loopback 请求才可调用。GET 只允许 loopback，无须启用付费开关。一个 API 实例同一时刻只允许一项 POST；重叠请求返回 409。每次未命中的 POST 固定使用普通模型与 `high` 强度、`max_tokens=2048`、既有 `DEEPSEEK_TIMEOUT_SECONDS` 超时，并将 Gateway 自动重试设为 0，以限制为一次付费 HTTP 请求。

**0003 升级已获授权，但尚未执行。**实际迁移前，先只读确认开发库为 `aipulse_db`、当前 revision 为 `20261016_0002` 并核对现有表和数据量；用 `aipulse_migrate@localhost` 对开发库做项目外备份，记录大小与 SHA-256，确认可读取。使用受保护入口在同一迁移连接核验身份和目标库，固定升级到 `20261021_0003`，逐列核验类型、可空性与 Alembic revision，再用 `aipulse_app` 只读检查旧新闻仍可查询。具体人工命令见 [11-SUMMARY-CACHE-0003-MIGRATION.md](11-SUMMARY-CACHE-0003-MIGRATION.md)。MySQL DDL 可能隐式提交，任一步失败即停止并只读报告，不自动重试或清理。一次性空库初始化入口 `init_dev_database.py` 只允许空库逐阶段到 0004，不适用于已有新闻数据的 0003 升级；不要用它，也不要裸跑 `alembic upgrade head`。

以下为**另行批准并完成 0003 迁移后**，用户手动运行真实付费摘要的步骤。Codex 本轮不执行 POST 或迁移。

在第一个 PowerShell 窗口启动 API，显式关闭定时与手动采集：

```powershell
Set-Location D:\Projects\AIPulse
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:AIPULSE_ENABLE_AI_SUMMARY = '1'
$env:AIPULSE_ENABLE_SCHEDULED_COLLECTION = '0'
$env:AIPULSE_ENABLE_MANUAL_COLLECTION = '0'
python -B -m uvicorn aipulse.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

在另一窗口先通过 `GET http://127.0.0.1:8000/api/v1/news/events` 查看真实事件 ID。打开 `http://127.0.0.1:8000/docs`，找到 `GET /api/v1/news/events/{event_id}/summary` 可先免费查询已有缓存。`POST` 在缓存未命中时会发起一次付费模型请求并回写开发库；只填写真实 `event_id`，不提交请求体。再次 POST 应返回 `cached=true`。未启用付费 POST 或非 loopback 为 403，不存在事件或无有效缓存的 GET 为 404，无效 ID/额外参数为 422，并发 POST 为 409；模型或配置错误返回脱敏类别。完成后在 Uvicorn 窗口按 `Ctrl+C`。
