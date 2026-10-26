# 版权、第三方归属与发布边界

## AIPulse 自主新增实现

Copyright (c) 2026 xuanqiu233。维护者已确认：AIPulse 自主开发的新增代码采用 [MIT License](LICENSE)，公开署名统一使用 **xuanqiu233**。

新增范围包括 `src/aipulse/` 的 FastAPI、MySQL 仓储、采集/归并/规则评分、LLM Gateway、摘要/日报缓存、自有 LangGraph 研究图，以及 `frontend/`、Alembic 和相应测试、文档。本声明只覆盖有权授权的新增实现，不给第三方源码换署名，也不代表拥有 LangGraph、DeepSeek、新闻或搜索引擎的版权。

## 冻结上游

- 来源：[langchain-ai/local-deep-researcher](https://github.com/langchain-ai/local-deep-researcher)，固定提交 `03537326ffa0dd90c9702bece2f3aeba5d84162a`。
- `src/ollama_deep_researcher/` 的七个 Python 文件保留冻结。2026-10-23 按 LF/CRLF 规范化后逐文件核验，与固定提交一致。
- 保留 `Copyright (c) 2025 Lance Martin`。原 MIT 许可证完整保存为 [docs/UPSTREAM-LICENSE.txt](docs/UPSTREAM-LICENSE.txt)，SHA-256：`6bb46a07a08d54a6d88b8391a43216311dcc5e2d355c0f9285cc6e4b25ca53ba`。根 LICENSE 在原声明旁增加 AIPulse 署名，没有删除原声明或许可条款。
- [上游 README](docs/UPSTREAM-README.md)、根 `Dockerfile`、`langgraph.json` 有上游历史来源；其中 Ollama / LMStudio / LangGraph Studio 指令不是 AIPulse 当前启动方式。AIPulse 从 `aipulse.api.main` 启动，自有研究图在 `src/aipulse/research/graph.py`。
- Python distribution 名称为 `aipulse`；冻结源码及其 import 命名空间 `ollama_deep_researcher` 保留。包名与维护者元数据不改变上游代码归属。

MIT 要求分发副本或实质部分时保留版权及许可声明，见 [OSI MIT](https://opensource.org/license/mit) 与[固定上游许可证](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/LICENSE)。

## 依赖及内容

- Python 与前端依赖分别由 `uv.lock`、`frontend/package-lock.json` 记录，许可元数据清单见 [18](docs/18-DEPENDENCY-LICENSES.md)。根 MIT 不覆盖第三方依赖许可。
- MIT/BSD/ISC 等依赖仍应保留相应声明；Apache-2.0 的许可与适用 NOTICE 义务须遵守。MPL-2.0 文件级义务不会因本项目 MIT 而消失，分发对应代码或 bundle 前应核对来源和源代码提供方式。参阅 [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)、[Mozilla MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)。这不是法律意见或完整供应链认证。
- 新闻、论文摘要、模型输出、商标与截图的权利独立于代码许可。开发库备份、原始验收 JSON、Prompt、模型原始响应不纳入公开代码仓库。
- `tests/aipulse/fixtures/research_quality_evidence.json` 为 xuanqiu233 自建 MIT 合成样例，不含外部摘录或历史模型报告。真实资料原件及截图仅本地保留，未纳入公开候选。
- `docs/assets/*-demo.jpg` 是当前 Vue UI 的实际浏览器截图；所示内容为 xuanqiu233 自建合成 Demo Data，没有外部新闻正文或真实任务记录，不代表模型生成质量。演示边界见 [展示资源说明](docs/assets/README.md)。

## 发布边界

当前不承诺公网多租户、生产高可用或真实模型报告质量已全面验收。v6 完整研究报告尚未真实验证。最初公开快照范围见 [历史候选说明](docs/24-PUBLIC-CANDIDATE.md)，当前离线检查见 [工程质量说明](docs/25-ENGINEERING-QUALITY.md)。仓库已完成首次发布；后续文档与 CI 更新在本地 dev 提交，推送和 PR 由维护者控制。
