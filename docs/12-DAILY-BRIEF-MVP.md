# 每日 AI 情报简报 MVP

本模块只提供单日手动生成。用户已完成 S3-3A 的一次真实生成与缓存验收；S3-3B 仅修改离线代码，未再次调用 DeepSeek 或改写现有日报。

## 数据与日期口径

- 复用现有 `daily_brief` 表：`brief_date + timezone` 唯一，结构化日报、事件与来源快照以版本化 JSON 保存在 `content`，状态为 `complete`，生成时间存入 `generated_at`。无需数据库迁移。现有表没有独立的 revision 或覆盖状态列；这些属于后续增强。
- 固定时区 `Asia/Shanghai`。指定日期的当地 `[00:00, 次日 00:00)` 转为 UTC 后筛选。优先按 `news_item.published_at` 判断；缺少发布时间时按稳定的 `news_event.created_at` 判断，重复抓取不会把旧事件移入新日报。
- 查询 `active` 或 `needs_review` 且 `importance_score >= 30` 的事件，按分数、入库时间和 ID 降序扫描最多 30 个候选，再独立判断 AI 主题相关性，最终最多取 5 个事件。重要性分数不因相关性过滤而改变。arXiv 的 `cat:cs.AI`、GitHub 的 `langchain-ai/langgraph` 是已配置的 AI 专项来源；其他来源需在标题或摘录里有明确 AI 主题证据。OpenBSD、MicroG 等仅凭高分或宽泛类别不会入选。每个事件只出现一次。无合格事件返回 `status=empty`，不调用模型，也不写空简报。
- 先使用输入指纹与 Prompt 版本匹配的新闻摘要缓存；否则只用数据库标题与摘录。原始 URL 经过现有摘要模块校验后，由后端加入响应和已保存的快照，模型不生成 URL。
- 已入选的 arXiv 论文归入 `research`；AI 工具、框架及已配置的 LangGraph 来源归入 `tools_and_frameworks`；模型和产品归入 `model_and_products`。栏目由后端按来源及类别映射，模型不自行决定栏目。参与日报的原始摘录累计至少 80 字符时才把对应重点新闻写入栏目；即使已有缓存摘要，短摘录也不会被当作更充分的原始证据。仅有标题或短摘录时栏目可为空，重点新闻会明确标记具体内容尚未核实。如果有任何事件缺少足够摘录，动态概览改为明确的覆盖与信息不足说明，趋势栏目留空。

## API 与费用边界

- `POST /api/v1/briefs/daily`，请求示例：`{"brief_date":"2026-10-21"}`。只接受日期，不接受 Prompt、模型、URL 或数据库配置。默认关闭，设置 `AIPULSE_ENABLE_DAILY_BRIEF=1` 后仅允许本机 loopback 调用。
- `GET /api/v1/briefs/daily/{brief_date}` 只读取已保存的有效快照，返回 `cached=true`，不加载模型配置、不产生模型费用；不存在时返回 404。旧规则生成的快照仍可读取，但返回 `rules_current=false`。
- S3-3B 将 Prompt/筛选版本升为 `daily-brief-zh-v2`。POST 仅在日期、输入指纹、模型及当前版本均匹配时返回缓存；旧版日报不会被误判为新规则下的缓存。内容变化后可重新生成。一次 POST 最多调用一次 DeepSeek JSON 输出，`max_tokens=3000`、`max_retries=0`，并使用现有超时配置。模型调用前关闭读取 Session；调用后在新短事务里重新核对新闻输入，成功后才替换日报。失败不会覆盖已有快照。
- 同一 FastAPI 实例用进程内锁拒绝重叠生成（HTTP 409）。多进程部署不保证完全避免重复付费，本地 MVP 应运行单个 Uvicorn worker。

## 等待单独授权后的本机人工验收

以下操作会让用户手动触发一次真实付费模型调用并写入 `aipulse_db`。S3-3B 未执行这些操作；再次验收新规则须由用户单独决定。

```powershell
Set-Location D:\Projects\AIPulse
$env:PYTHONPATH = 'src'
$env:AIPULSE_ENABLE_DAILY_BRIEF = '1'
$env:AIPULSE_ENABLE_SCHEDULED_COLLECTION = '0'
python -B -m uvicorn aipulse.api.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/docs`，先用 `GET /api/v1/news/events` 确定要生成日期有新闻，再在 Swagger 调用 POST，最后调用 GET 核对已保存内容。对同一日期再次 POST，应在输入未变时返回 `cached=true` 且不再调用 DeepSeek。若当天无符合筛选条件的事件，POST 返回 `empty`；可换一个确有新闻的日期。完成验收后关闭 Uvicorn。
