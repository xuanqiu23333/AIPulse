# 展示、离线验证与 CI 边界

本页对应 2026-10-26 的 Sprint 7 本地检查。只改文档、展示资源、CI 与包元数据；新闻、API 业务语义、研究图、报告契约、ORM、四份 Alembic revision 及冻结上游没有修改。不存在真实模型、数据库或采集验收。

## 1. 可阅读的架构

- [README](../README.md)：12 个节点的分层系统图，同时呈现新闻处理和研究链路；箭头表示调用方向。
- [当前技术架构](06-ARCHITECTURE.md)：模块与源码对应关系，原设计收进历史章节。SSE、可选 Ollama 与恢复设想不代表已实现。
- [研究图](13-DEEP-RESEARCH.md)：业务阶段展示图、真实八节点 StateGraph、条件回路及外层截止边界。
- 五个当前 Mermaid 图采用常规 flowchart / subgraph / classDef；本地 Mermaid 11.17.2 解析通过。GitHub 页面渲染仍待推送后确认，不声称已完成远程验证。

360 秒是外层任务截止，不是 `_route` 的条件。路由检查缺口、未用查询、轮次与补充次数。预算耗尽仍可能进入 partial / failed；图示没有把流程结束画成必然成功。

## 2. 固定离线 Python 检查

入口：[run_offline_tests.py](../.github/scripts/run_offline_tests.py)。只运行以下 8 个文件，不扫描整个测试目录：

| 文件（tests/aipulse/unit/） | 范围 |
| --- | --- |
| test_runtime.py | runtime 配置与注入 |
| test_news_api.py | 新闻 GET 契约与 Mock 仓储 |
| test_daily_brief_api.py | 日报请求、安全默认值与 Mock 生成 |
| test_llm_gateway.py | Mock HTTP、JSON / usage / 错误处理 |
| test_research_graph.py | Fake 检索和模型、有界图运行与终态 |
| test_research_attribution.py | 中英主体归属、问题覆盖及支持片段 |
| test_research_report_contract.py | v6 结构契约、逐项引用及安全字段诊断 |
| test_public_bootstrap.py | 合成备份和 Fake 连接的迁移保护契约 |

守卫在导入测试前阻断 socket connect / connect_ex、create_connection 及 DNS；即使测试捕获了连接异常，仍将该次运行判为失败。唯一特例是标准库 `socketpair()` 在 Windows 创建 asyncio 自唤醒通道时的受限 loopback 操作，不允许一般 loopback 数据库 / HTTP 连接。四项自检均在 I/O 前被拒绝。

守卫不是恶意代码沙箱，不拦截任意子进程或原生扩展。测试白名单已审查为 Fake / Mock；增加测试、插件或联网路径时必须重新审查。禁用 pytest 自动插件加载，只显式加载 pytest-asyncio；六个写入 / 外部检索开关均强制关闭，关闭环境 tracing，不读取真实密钥用于请求。

本机已安装依赖时，在项目根目录运行：

```powershell
python -B .github/scripts/run_offline_tests.py --check-guard
python -B .github/scripts/run_offline_tests.py
```

新环境先按 [部署文档](15-LOCAL-DEPLOYMENT.md) 激活 Conda，并在指定该环境后运行 `uv sync --locked --group dev --inexact`。测试会生成临时合成 SQL / JSON，不连接数据库。若 Windows 限制临时目录权限，可将 `AIPULSE_OFFLINE_TEST_DIR` 设为当前用户可写的项目外专用目录；不要放真实备份或密钥。

## 3. Frontend 与 Actions

在 frontend/ 运行：

```powershell
npm ci
npm run test
npm run typecheck
npm run build
```

[ci.yml](../.github/workflows/ci.yml) 监听 dev / main push、面向 main 的 PR 与手动 dispatch。两个独立 Ubuntu 24.04 job：Python 3.11 + uv 0.11.15，以及满足 package.json engines 的 Node 24。官方 Actions 固定完整提交 SHA；权限只有 contents: read，checkout 不保留凭据，每个 job 最多 10 分钟。

依赖安装会访问包注册表，**业务测试执行阶段离线**。无 MySQL service、模型 Key、实时新闻请求、数据库迁移、部署或自动 PR 操作。前端使用现有 Mock 测试，生产构建不会启用后端功能。

本轮未推送，因此 **远程 Actions 尚未执行**。不能根据 YAML 静态检查或本地结果显示“CI passed” Badge。维护者推送 dev、创建 PR 后应先看两项 job 的实际结果，再决定是否添加 Badge。

## 4. 包元数据与可复现性

distribution 从历史名称 `ollama-deep-researcher` 改为 `aipulse`，版本仍为 0.0.1；业务 import 不变。保留冻结上游 `ollama_deep_researcher` 的包发现及原版权，未修改上游源码或给它重新署名。

验证方式：离线更新 uv.lock，只替换并移动根项目记录；所有第三方版本保持不变。离线构建 wheel、向项目外临时 target 安装 editable 包，并在禁用旧环境 .pth 自动加载的进程中核对来源。`aipulse`、API、Research 和冻结上游导入均来自当前发布仓库；wheel 包含 67 个 Python 文件和两份原版权声明。未向既有 Conda 环境安装新 distribution，也不自动卸载历史包。

这些检查不是全新机器完整安装或跨平台迁移验收。MySQL 入口对目标、身份、版本与备份仍有严格限制，见部署文档；本轮不运行实际迁移。

## 5. 本次实际结果

| 检查 | 本地结果 |
| --- | --- |
| Python 固定离线用例 | 119 passed；未尝试外部 / 数据库连接 |
| 网络守卫自检 | 4 项连接 / DNS 尝试均在 I/O 前阻断 |
| Vue / Vitest | 5 个文件、33 passed |
| TypeScript | vue-tsc --noEmit 通过 |
| Vite | 生产构建通过；产物未纳入 Git |
| 包元数据 | wheel、临时 editable 来源、第三方锁定版本与版权检查通过 |
| Mermaid | 5 个当前图解析通过 |
| 远程 Actions / GitHub 图形渲染 | 未执行 / 未确认 |

Python 有一项既有 Starlette / httpx TestClient 的弃用警告，不影响本轮通过，也不据此升级业务依赖。

## 6. 截图与真实性

四张截图均来自现有 UI V2 的本地实际渲染，详见 [资源说明](assets/README.md)。使用自建 Demo Data、example.org 来源和可见水印；截图服务只接受 GET，未启动真实后端。研究工作台不展示伪造的成功报告。公开仓库只包含最终压缩 JPEG，不包含临时服务、原始截图或本机运行日志。

新闻采集、摘要与日报 v2 具有此前真实验收；研究已执行过真实任务并保存证据，但 **v6 完整成功报告尚未真实模型验收**。本次 119 项检查验证的是确定性代码契约，不能替代研究质量验收，也不支持用户量、QPS、线上部署或准确率宣传。
