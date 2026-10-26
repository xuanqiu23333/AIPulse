> **历史实施计划（2026-10-14）**：本文中的阶段和待办是当时的规划，不是当前能力清单。已实现模块以 [当前架构](06-ARCHITECTURE.md)、[真实研究图](13-DEEP-RESEARCH.md) 和 [工程验证](25-ENGINEERING-QUALITY.md) 为准；SSE、跨进程恢复等设想未因本计划而实现。

# AIPulse V1 TDD 开发计划

> 状态：待审核的实施计划；编制日期：2026-10-14。本文只规划后续工作，不表示功能、数据表或迁移已经实现。执行依据：[06-ARCHITECTURE.md](06-ARCHITECTURE.md)；产品验收依据：[01-PRD.md](01-PRD.md)；环境与数据库边界依据：[当前部署说明](15-LOCAL-DEPLOYMENT.md)、[首次初始化](15-LOCAL-DEPLOYMENT.md)。本轮只创建本文档。

**目标：** 在现有上游研究图和已验证的本机 MySQL 连接基线之上，按可独立验收的增量交付单用户 AI 资讯、每日简报、深度研究和 Web 页面。

**架构：** 一个 FastAPI 后端内分离资讯、简报、研究、存储和运行时模块；Vue 3 页面通过 REST 与 SSE 访问后端。MySQL 持久化七类数据，研究图基于上游 LangGraph 思路重设状态、证据和停止条件。各外部 API、模型、时钟与数据库边界均可替换，以便默认测试完全离线。

**技术栈：** Windows、现有 Conda `Aipulse` / Python 3.11、FastAPI、Pydantic 2、SQLAlchemy 2、asyncmy、Alembic、HTTPX、APScheduler 3.x、LangGraph/LangChain、Vue 3、TypeScript、Vite、pytest、pytest-asyncio、本机 MySQL 8.0。

## 1. 全局约束与执行方式

- 只使用 `C:\Users\<WindowsUser>\anaconda3\envs\Aipulse\python.exe` 或已核对解释器的 `conda run -n Aipulse`；不创建 `.venv`，不使用默认项目模式的 `uv sync` 或 `uv run`。依赖声明与 `uv.lock` 同步；新增包仅限当前任务所需。
- 生产与测试分别使用 `aipulse_db`、`aipulse_test_db` 和各自运行账号；建表、迁移、备份或使用迁移账号是**后续独立审核的操作**。本文不授权执行 DDL。正式测试入口在创建 Engine 之前拒绝库名相同、测试账号不符或目标不是独立测试库。
- 应用默认只监听 `127.0.0.1`；开发热重载时关闭自动调度；V1 仅运行一个调度器和一个研究执行器。时间以 UTC 入库，页面与简报默认 `Asia/Shanghai`；研究最多 3 次搜索，初次搜索计入上限。
- 所有新增功能采用固定样例、Mock 模型和 Mock HTTP 的离线测试。每个任务按 **红：先写断言并确认因缺少该行为而失败 → 绿：最小实现 → 整理：复跑相关回归 → 审查：检查安全与数据边界** 的顺序执行。测试中的意外导入失败、错误连接或密钥缺失不算预期红灯。
- 每个任务完成后只提交该任务相关文件；提交前检查 `git status` 与暂存区，不覆盖当前 `dev` 分支已有未提交变更。上游 `src/ollama_deep_researcher` 与现有 `tests/test_upstream_baseline.py` 保留为回归基线；对上游适配优先放在 AIPulse 新模块。
- 默认验收不访问真实收费模型或公开 API；真实集成验证须另外配置凭据和费用边界，并明确记录 PASS、FAIL、SKIP。离线测试和真实测试不能互相冒充。

## 2. 拟定文件与接口边界

以下路径是**未来实施位置**，目前并不存在。新增 `src/aipulse` 包时须同步调整 `pyproject.toml` 的显式包发现，确保 editable install 能从项目外导入；不要改动上游 MIT LICENSE 或既有图入口。

| 边界 | 建议文件 | 对下一层提供的稳定契约 |
| --- | --- | --- |
| 配置与进程 | `src/aipulse/config.py`、`src/aipulse/main.py`、`src/aipulse/runtime.py` | 已校验设置；FastAPI 生命周期；单实例调度/执行器开关。 |
| 存储 | `src/aipulse/storage/db.py`、`models.py`、`repositories.py`；后续 `alembic/` | 开发/测试 Engine 与短会话；七表映射；按事务提交的读写接口。 |
| 资讯 | `src/aipulse/news/types.py`、`adapters/github.py`、`adapters/arxiv.py`、`adapters/hn.py`、`normalize.py`、`merge.py`、`score.py`、`service.py` | 来源适配器产生统一候选记录；服务完成幂等保存和来源级状态。 |
| 简报 | `src/aipulse/briefs/service.py`、`summary.py` | 给定本地日期与时区，返回覆盖情况、可追溯快照和简报版本。 |
| 研究 | `src/aipulse/research/state.py`、`tools.py`、`graph.py`、`evidence.py`、`report.py`、`service.py` | 任务/事件快照入图；有界搜索；结构化证据、带引用报告和持久化节点事件。 |
| HTTP | `src/aipulse/api/news.py`、`briefs.py`、`research.py`、`events.py`、`schemas.py` | `/api/v1` JSON 契约、统一错误、游标分页及 SSE 续传。 |
| 页面 | `frontend/src/pages/`、`frontend/src/api/`、`frontend/src/components/` | 资讯、简报、研究进度及报告页面；开发代理转发 REST/SSE。 |
| 测试 | `tests/aipulse/unit/`、`tests/aipulse/api/`、`tests/aipulse/integration/`、`tests/aipulse/fixtures/` | 默认离线；需要真实测试库的测试单独标记并受预连接保护。 |

跨模块数据契约在首个使用它的任务中固定：`NewsCandidate` 包含 provider、external_id、原始 URL/标题、发布时间、抓取时间和限长摘录；`CollectionResult` 包含来源 ID、成功/部分/失败、计数和脱敏错误码；`EvidenceRef` 包含 task_id、citation_key、URL、摘录、主张及核验状态。它们属于服务端类型，不向页面泄露原始密钥或模型提示。后续任务不得另造同义类型绕开已有契约。

## 3. 每个增量通用的 TDD 门槛

1. 写出本任务所列的具名测试，固定输入与可观察断言；确认测试先因目标行为未实现而 **FAIL**。
2. 仅实现该任务所需的生产文件和必要配置；使具名测试 **PASS**，再运行本 Sprint 已通过的测试及 `tests/test_upstream_baseline.py`。
3. 对涉及数据库的测试区分纯单元测试、API 测试和独立测试库集成测试；默认命令不得隐式连接开发库。访问真实测试库前检查库名、账号、连接身份及迁移状态。
4. 记录执行命令、PASS/FAIL/SKIP 数量、外部接口 Mock 边界与剩余风险；审阅改动和暂存区后才形成单任务提交。若红灯与预期无关或锁文件出现大量无关升级，先查明原因再继续。

建议的默认验证入口为：用上述 Conda 解释器执行 `-m pytest tests/aipulse -q`，再执行 `-B -m unittest discover -s tests -p "test_upstream_baseline.py" -v` 和 `-m pip check`。首次需要 pytest/pytest-asyncio、FastAPI、APScheduler 等包时，先审阅版本及锁文件差异，再安装至同一 Conda 环境；本文不执行安装。前端阶段另运行其 TypeScript、构建与组件/端到端测试，具体工具在 Sprint 5 首任务审定。

## 4. Sprint 1：基础工程

**目标：** 可启动、可测试的空 API 与受保护的数据库访问层；上游研究图仍能离线运行。此 Sprint 的迁移提议须单独审阅，不能把“计划通过”当作执行许可。

### S1-1 配置与测试库预连接保护

- **文件：** `src/aipulse/config.py`、`src/aipulse/storage/db.py`、`tests/aipulse/unit/test_config.py`、`tests/aipulse/unit/test_db_target_guard.py`；必要时更新 `pyproject.toml` 和 `uv.lock`。
- [ ] **红：** `test_test_engine_rejects_dev_database_before_connect`、`test_test_engine_rejects_dev_user_before_connect`、`test_database_url_escapes_special_password_chars`、`test_missing_password_has_safe_error`。断言危险测试目标在任何连接尝试前被拒绝，报错不含密码；URL 构造保留特殊字符。
- [ ] **绿：** 建立拆分 `DB_*`、`TEST_DB_*` 的设置加载、目标守卫和 Engine 工厂；复用 03 的 `URL.create`、`utf8mb4` 和连接池初值。单元测试只注入假配置，绝不读取真实 `.env` 内容到输出。
- [ ] **验收：** 运行本任务测试、`pip check` 和原有两项 Mock 回归；只有明确选择的独立集成测试才可触达 `aipulse_test_db`。

### S1-2 七表模型与受审阅迁移

- **文件：** `src/aipulse/storage/models.py`、`alembic.ini`、`alembic/env.py`、`alembic/versions/<审核后生成的 revision>.py`、`tests/aipulse/unit/test_model_metadata.py`、`tests/aipulse/integration/test_schema_contract.py`。
- [ ] **红：** `test_metadata_has_exact_seven_v1_tables`、`test_identity_uniques_and_status_columns_exist`、`test_long_urls_are_not_unique_indexed_directly`、`test_test_database_schema_matches_reviewed_revision`。前 3 项仅检查 metadata；最后一项只在独立测试库且凭据齐全时运行。
- [ ] **绿：** 映射 06 第 5 节七表、主外键、状态字段和索引；生成候选迁移后人工审查 InnoDB、utf8mb4、索引长度及 MySQL DDL 的隐式提交。显式核对目标库与迁移账号，先迁测试库，备份开发库后才考虑开发库迁移。
- [ ] **验收：** metadata 测试离线通过；迁移未获单独批准时，集成测试与实际迁移均记 **SKIP**，不得声称表已存在。批准后记录两库实际 revision 与失败后的人工核对步骤，不自动 DROP 或 downgrade。

### S1-3 空状态 API、会话生命周期和错误契约

- **文件：** `src/aipulse/main.py`、`src/aipulse/api/schemas.py`、`src/aipulse/api/news.py`、`src/aipulse/api/briefs.py`、`tests/aipulse/api/test_app_contract.py`。
- [ ] **红：** `test_empty_news_and_briefs_return_consistent_pages`、`test_invalid_cursor_returns_422`、`test_error_response_has_code_message_request_id`、`test_shutdown_disposes_engine`。使用替代仓储，不连真实库。
- [ ] **绿：** 建立 FastAPI 生命周期、依赖注入、JSON 错误格式和空列表读接口；默认绑定与启动方式写入后续运行说明，启动时不自动迁移。
- [ ] **验收：** API 契约测试、上游 Mock 测试通过；服务启动/关闭不泄露连接，未配置模型时空状态可读。

### S1-4 单实例后台运行边界

- **文件：** `src/aipulse/runtime.py`、`tests/aipulse/unit/test_runtime.py`。
- [ ] **红：** `test_reload_mode_does_not_start_scheduler`、`test_scheduler_uses_fixed_id_and_single_instance`、`test_executor_accepts_only_one_running_research_job`、`test_startup_marks_stale_running_tasks_interrupted`。用假时钟与假任务仓储。
- [ ] **绿：** 实现受开关控制的 APScheduler 3.x 生命周期、固定 job ID、`max_instances=1`、`coalesce=true`、有限误触发宽限与单并发执行器；启动恢复规则只处理遗留状态，不执行未知任务。
- [ ] **验收：** 重复启动与热重载模拟不产生第二个定时器；任务状态可诊断。尚无 News/Research 业务时，调度回调只使用可替换的空服务接口。

## 5. Sprint 2：AI News Intelligence Pipeline

**目标：** 三源固定样例能标准化、幂等入库、确定性归并和排序；单源错误可见。真实外部 API 不进入默认 CI/本地回归。

### S2-1 GitHub Releases 适配器

- **文件：** `src/aipulse/news/types.py`、`src/aipulse/news/adapters/github.py`、`tests/aipulse/unit/test_github_adapter.py`、`tests/aipulse/fixtures/github_releases.json`。
- [ ] **红：** `test_release_id_is_stable_external_id`、`test_tags_without_release_are_ignored`、`test_github_pagination_and_rate_limit_are_bounded`、`test_bad_payload_returns_source_error`；Mock HTTP 记录请求次数、超时与响应。
- [ ] **绿：** 从明确配置的仓库取发布记录并转换为 `NewsCandidate`；限制页数、总条数和响应体；不调用无关 GitHub API。
- [ ] **验收：** 正常、空页、限流、超时和畸形字段样例均离线通过；不会把 Token 写入结果或日志。

### S2-2 arXiv 适配器

- **文件：** `src/aipulse/news/adapters/arxiv.py`、`tests/aipulse/unit/test_arxiv_adapter.py`、`tests/aipulse/fixtures/arxiv_feed.xml`。
- [ ] **红：** `test_arxiv_version_keeps_base_paper_id`、`test_feed_pagination_and_date_are_normalized`、`test_malformed_entry_isolated`、`test_empty_query_disables_collection`。
- [ ] **绿：** 只对显式配置的查询获取分页结果；基础论文 ID 作来源唯一键，版本留在元数据；解析失败隔离到条目/来源状态。
- [ ] **验收：** 多版本论文重复采集仍是一个原始条目；时间字段为带来源语义的 UTC 值。

### S2-3 Hacker News 适配器

- **文件：** `src/aipulse/news/adapters/hn.py`、`tests/aipulse/unit/test_hn_adapter.py`、`tests/aipulse/fixtures/hn_items.json`。
- [ ] **红：** `test_deleted_and_dead_items_are_skipped`、`test_hn_id_is_stable_external_id`、`test_text_only_story_keeps_hn_url`、`test_item_fetch_failures_leave_other_items_available`。
- [ ] **绿：** 按明确配置的列表获取 story ID 和 item，限制抓取量、并发、超时与重试；无外链时保留 HN 讨论链接。
- [ ] **验收：** 个别 item 失败只造成来源级 partial，不吞掉已取得的条目。

### S2-4 同源幂等与跨源事件归并

- **文件：** `src/aipulse/news/normalize.py`、`merge.py`、`service.py`、`src/aipulse/storage/repositories.py`、`tests/aipulse/unit/test_news_merge.py`、`tests/aipulse/integration/test_news_idempotency.py`。
- [ ] **红：** `test_recollect_same_provider_external_id_updates_one_item`、`test_hn_link_to_arxiv_merges_one_event_with_two_items`、`test_similar_titles_without_strong_key_do_not_merge`、`test_tracking_parameters_do_not_split_event`、`test_concurrent_recollect_respects_unique_constraints`。
- [ ] **绿：** 规范 URL、提取强标识、计算 event_key 并用唯一键处理竞争写入；保留全部原始记录。仅相似标题标记 `needs_review`，不自动合并。
- [ ] **验收：** 固定批次连续运行两次，news_item/news_event 数量不增长；集成测试只用独立测试库，未迁移时明确 SKIP。

### S2-5 分类评分、采集编排和资讯 API

- **文件：** `src/aipulse/news/score.py`、`service.py`、`src/aipulse/api/news.py`、`tests/aipulse/unit/test_scoring.py`、`tests/aipulse/api/test_news_api.py`。
- [ ] **红：** `test_score_is_within_0_100_and_has_reason`、`test_missing_signal_is_not_fabricated`、`test_one_source_failure_does_not_block_others`、`test_manual_collect_duplicate_returns_conflict_or_active_run`、`test_news_cursor_and_detail_preserve_all_sources`。
- [ ] **绿：** 固定类别和可解释的版本化规则评分；单源采集状态独立提交；实现来源状态、手动触发、列表/详情 API。规则阈值可配置，未审核权重不写死成产品事实。
- [ ] **验收：** 与 06 的资讯接口契约一致，页面可获取来源链接和覆盖状态；自动任务接入 S1-4 的单实例调度器后，手动/自动重叠仍幂等。

## 6. Sprint 3：中文摘要与 Daily Brief

**目标：** 用已归并的事件生成有来源的中文摘要与按本地日期可回看的简报；模型缺失或单源失败要显式呈现。

### S3-1 受限中文摘要

- **文件：** `src/aipulse/briefs/summary.py`、`tests/aipulse/unit/test_summary.py`。
- [ ] **红：** `test_summary_runs_after_dedup_only_once_per_event`、`test_summary_preserves_original_title_and_url`、`test_invalid_or_unavailable_model_marks_failed_without_fake_text`、`test_source_text_cannot_override_summary_instructions`。
- [ ] **绿：** 通过可替换模型接口生成简短摘要；输入有长度、次数、超时与费用上限；保存模型标识及失败状态，不覆盖原始来源数据。
- [ ] **验收：** 默认离线 Mock 与拒绝/超时样例通过；未配置模型时保留原文元数据并返回明确失败状态。

### S3-2 日期窗口、快照与重生成

- **文件：** `src/aipulse/briefs/service.py`、`tests/aipulse/unit/test_brief_window.py`、`tests/aipulse/integration/test_brief_revision.py`。
- [ ] **红：** `test_shanghai_midnight_window_converts_to_utc`、`test_partial_source_coverage_is_recorded`、`test_rerun_updates_same_date_and_increments_revision`、`test_failed_rerun_preserves_last_readable_brief`、`test_snapshot_keeps_historical_source_links`。
- [ ] **绿：** 从当地 `[00:00, 次日 00:00)` 窗口选择活跃事件，按分数与时间排序；以单事务替换成功快照与 revision；失败只更新最后尝试状态。
- [ ] **验收：** 同日期/时区仅一行；跨日、来源 partial 和失败重跑均可回查；测试库集成测试受 S1-1 保护。

### S3-3 简报 API 与手动/定时生成

- **文件：** `src/aipulse/api/briefs.py`、`src/aipulse/runtime.py`、`tests/aipulse/api/test_briefs_api.py`。
- [ ] **红：** `test_brief_history_and_detail_return_snapshot`、`test_generate_request_returns_accepted_without_waiting_for_llm`、`test_duplicate_generation_is_idempotent`、`test_invalid_date_and_timezone_return_422`。
- [ ] **绿：** 接入历史、详情、手动生成路由和定时触发；运行状态与覆盖缺口可查询，HTTP 请求不持有外部调用。
- [ ] **验收：** 对应 06 的日报接口；人工与定时触发同日不产生重复记录。

## 7. Sprint 4：Deep Research Agent

**目标：** 从事件及来源快照启动可观察、有限轮数的研究，生成可核对引用的报告，并支持断线后追看过程。先以全 Mock 图测试替代真实模型/搜索。

### S4-1 State、图节点和停止条件

- **文件：** `src/aipulse/research/state.py`、`graph.py`、`tests/aipulse/unit/test_research_graph.py`。
- [ ] **红：** `test_initial_search_counts_as_round_one`、`test_zero_or_one_or_three_round_limits_terminate`、`test_repeated_query_stops_loop`、`test_empty_search_ends_with_explicit_reason`、`test_graph_compiles_with_mock_tools`。
- [ ] **绿：** 定义 06 第 4 节 State 和 load/plan/search/extract/synthesize/reflect/report/verify 节点；route 在搜索次数、总耗时或费用达到上限时结束。保留上游查询、摘要、反思思路，不在本任务修改原有 `graph.py`。
- [ ] **验收：** `max=3` 最多 3 次搜索而非上游默认的 4 次；图可编译，异常路径有终止原因；原有上游离线 Mock 仍通过。

### S4-2 工具适配与受控抓取

- **文件：** `src/aipulse/research/tools.py`、`tests/aipulse/unit/test_research_tools.py`。
- [ ] **红：** `test_private_loopback_and_link_local_urls_are_rejected`、`test_redirect_target_is_revalidated`、`test_response_size_and_type_are_bounded`、`test_search_timeout_has_typed_error`、`test_unconfigured_paid_model_is_never_called`。
- [ ] **绿：** 用可注入接口包装既有搜索能力与 Ollama/云模型；抓取前和跳转后校验目标，限制返回内容；对收费模型只允许明确配置且受预算约束的调用。
- [ ] **验收：** 默认测试无真实网络、无真实模型；拒绝策略与脱敏错误可由调用方识别。

### S4-3 证据、报告与引用校验

- **文件：** `src/aipulse/research/evidence.py`、`report.py`、`tests/aipulse/unit/test_evidence_report.py`。
- [ ] **红：** `test_same_url_can_support_distinct_claims`、`test_report_rejects_unknown_citation_key`、`test_conflicting_evidence_is_disclosed`、`test_no_verifiable_evidence_yields_partial_or_failed`、`test_report_links_back_to_original_sources`。
- [ ] **绿：** 保存证据 URL、摘录、内容哈希、抓取时刻和主张状态；报告生成后核对每个 citation_key 属于本任务证据，明确未知与矛盾点。
- [ ] **验收：** 所有可支持的关键结论能追至来源；搜索摘要不能自动提升为已核验原文。

### S4-4 任务、事件日志与重试

- **文件：** `src/aipulse/research/service.py`、`src/aipulse/storage/repositories.py`、`tests/aipulse/unit/test_research_service.py`、`tests/aipulse/integration/test_research_persistence.py`。
- [ ] **红：** `test_task_captures_event_snapshot_before_run`、`test_log_sequence_is_monotonic_per_task`、`test_partial_failure_keeps_existing_evidence`、`test_retry_creates_new_task_linked_to_old`、`test_restart_marks_running_interrupted`。
- [ ] **绿：** 以短事务记录 queued/running/终态、结构化证据及脱敏节点日志；日志入库成功后才通知订阅者。任务重试创建新 ID，不覆盖原报告。
- [ ] **验收：** 重启、失败及重试均可诊断；查询不需要重新访问原始新闻页。集成测试只连独立测试库。

### S4-5 研究 REST 与 SSE 契约

- **文件：** `src/aipulse/api/research.py`、`events.py`、`tests/aipulse/api/test_research_api.py`、`tests/aipulse/api/test_research_events.py`。
- [ ] **红：** `test_create_task_returns_202_and_idempotency_result`、`test_report_before_completion_returns_409`、`test_sse_replays_only_events_after_last_event_id`、`test_sse_ids_are_persisted_log_sequence`、`test_terminal_event_closes_stream`、`test_sse_never_exposes_prompt_or_secret`。
- [ ] **绿：** 接入任务创建、列表、详情、报告、重试和 SSE；事件只含公开进度、节点与安全元数据；支持 Last-Event-ID 和心跳，断线可回退轮询。
- [ ] **验收：** 离线客户端断开再连接不丢已提交事件、不重发已确认事件；状态码与 06 API 契约一致。

## 8. Sprint 5：Vue 前端与整体验收

**目标：** 单用户在本机浏览器完成“看资讯 → 看简报 → 选事件研究 → 看过程和引用报告”。前端工具版本和测试器在本 Sprint 首任务审阅后锁定，避免提前安装不必要的组件。

### S5-1 前端工程、API 客户端与只读页面

- **文件：** `frontend/package.json`、`frontend/vite.config.ts`、`frontend/src/api/client.ts`、`frontend/src/pages/NewsPage.vue`、`BriefPage.vue`、`frontend/src/components/SourceLinks.vue`、对应组件测试。
- [ ] **红：** 测试空列表、来源级 partial、资讯详情多来源链接、简报历史快照以及 `/api/v1` 开发代理契约；用 Mock API，不直连 MySQL/外部 API。
- [ ] **绿：** 建立 Vue 3 + TypeScript + Vite，显示日期/类别/评分、英文原题、中文摘要、来源与覆盖缺口；代理 REST/SSE 到本机后端。
- [ ] **验收：** TypeScript 检查、组件测试和构建通过；构建产物不含服务端凭据。

### S5-2 研究交互、SSE 恢复与报告呈现

- **文件：** `frontend/src/pages/ResearchPage.vue`、`frontend/src/api/research.ts`、`frontend/src/components/ResearchTimeline.vue`、`ReportView.vue`、对应组件与端到端测试。
- [ ] **红：** 测试提交后的 queued/running/partial/failed 状态、SSE 重连时 Last-Event-ID、心跳和终态处理、轮询兜底、报告引用回到证据来源、恶意 Markdown/HTML 不执行。
- [ ] **绿：** 加入任务入口、过程时间线和安全 Markdown 报告；页面仅渲染可公开事件，不显示模型内部推理或提示。
- [ ] **验收：** 用 Mock 后端完成浏览器端到端流程；断流后能补看事件且不会重复显示，失败和证据不足状态可见。

### S5-3 V1 跨模块回归与交付核对

- **文件：** `tests/aipulse/integration/test_v1_flow.py`、必要的固定样例；运行说明仅在实施时添加，不在本轮写入。
- [ ] **红：** 固定三源样例 → 重复采集 → HN/arXiv 归并 → partial 简报 → 研究 Mock 循环 → 带引用报告 → SSE 回放的链路测试先失败；另验证无密钥输出和测试库保护。
- [ ] **绿：** 仅修复实际暴露的接口衔接问题，不趁验收阶段加入新功能。
- [ ] **验收：** 对照 PRD A01–A08 逐项给出 PASS/FAIL/SKIP 和证据；完成 Python 离线测试、上游 Mock 回归、`pip check`、前端类型检查/测试/构建。真实 API/LLM 若未配置明确 SKIP，不宣称线上可用。

## 9. 审查重点与风险测试归属

| 易出问题的输入或状态 | 预期行为 | 负责测试 |
| --- | --- | --- |
| 测试配置意外指向开发库或使用开发账号 | 在创建 Engine 之前拒绝，不尝试连接 | S1-1 的两个预连接保护测试；S5-3 复核 |
| 同时手动、定时重抓或重启后重跑 | 唯一键维持新闻/简报幂等，调度器不复制 | S1-4、S2-4、S2-5、S3-2 |
| 外部 API 半页成功后限流、模型失效 | 保留已得到的原始数据，标记 partial/failed，不生成虚构摘要 | S2-1 至 S2-3、S2-5、S3-1、S3-2 |
| 搜索结果为不可信页面且跳转到内网 | 拒绝抓取；证据不足在报告中明示 | S4-2、S4-3 |
| SSE 在节点提交后、送达前断开 | 凭持久化 seq 续传，仅回放尚未确认事件 | S4-4、S4-5、S5-2 |

## 10. Sprint 出口、需求覆盖与待审核参数

| Sprint 出口 | 对应 PRD 验收 |
| --- | --- |
| 1：解释器、测试目标守卫、空 API、七表模型与迁移候选均可检查；实际 DDL 另行批准 | A07、A08 的基础保障 |
| 2：三源候选、去重归并、评分、来源状态和资讯 API 有离线证据 | A01、A02、A03 |
| 3：有来源的中文摘要、日期快照、覆盖缺口和历史简报可查 | A03、A04、A07 |
| 4：有界研究、证据、报告、重试及 SSE 续传可用 Mock 证明 | A05、A06、A07、A08 |
| 5：浏览器完整流程和全链路回归结果齐备 | A01–A08 |

实施前仍需审核 06 第 9 节参数：GitHub 仓库白名单、arXiv 查询、HN 列表、每来源采集上限、简报本地生成时刻、评分权重、默认模型与云端费用上限。未确定时保留明确配置入口，GitHub/arXiv 默认不采集，云模型默认不调用。上述参数变化须同步调整固定样例和验收断言。

**本轮边界：** 只生成 TDD 开发计划。没有修改源码、`pyproject.toml`、`uv.lock` 或已有测试；没有安装依赖、运行迁移、创建数据库表、启动服务、开发前端或提交 Git。
