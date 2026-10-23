# AIPulse 基线源码审计

> 审计日期：2026-10-13。审计对象为上游 [`langchain-ai/local-deep-researcher`](https://github.com/langchain-ai/local-deep-researcher/tree/03537326ffa0dd90c9702bece2f3aeba5d84162a)，固定提交 `03537326ffa0dd90c9702bece2f3aeba5d84162a`。用户指定的本地根目录是 `D:\Projects\AIPulse`；审计时该目录仅有 `docs/`，`src/ollama_deep_researcher` **尚不存在**。因此以下“现有”均指固定提交的上游源码，不能宣称本地项目已能运行。未克隆源码、未安装依赖、未运行程序。建议待源码落地后核对本地提交及差异，再将此审计视为本地基线。

## 1. 审计范围、目录与启动方式

已逐一阅读固定提交的 `README.md`、`pyproject.toml`、`src/ollama_deep_researcher/{graph,state,configuration,utils,prompts}.py`，并核对 `lmstudio.py`、`.env.example`、`langgraph.json`、`Dockerfile` 与仓库树。仓库树还包含 `LICENSE`、`uv.lock` 和少量 GitHub 工作流；固定提交中没有自建 FastAPI 路由、前端目录、采集器目录或测试目录。目录依据为[提交树](https://github.com/langchain-ai/local-deep-researcher/tree/03537326ffa0dd90c9702bece2f3aeba5d84162a)。

```text
local-deep-researcher/                 # 上游仓库根目录，尚未落地到 D:\Projects\AIPulse
├── README.md
├── pyproject.toml
├── langgraph.json
├── .env.example
├── Dockerfile
├── uv.lock
└── src/ollama_deep_researcher/
    ├── __init__.py
    ├── graph.py
    ├── state.py
    ├── configuration.py
    ├── utils.py
    ├── prompts.py
    └── lmstudio.py
```

**已有启动方式**：README 先要求复制 `.env.example` 为 `.env`；Windows 示例为 Python 3.11 虚拟环境、`pip install -e .`、安装 `langgraph-cli[inmem]`、运行 `langgraph dev`；Mac 示例使用 `uvx ... langgraph dev`。`langgraph.json` 将图入口指向 `./src/ollama_deep_researcher/graph.py:graph`，声明 Python 3.11、`.env` 和当前项目依赖；Dockerfile 同样启动 `langgraph dev --host 0.0.0.0`，Ollama 需要另行运行。项目自身未定义 `uvicorn`/FastAPI 应用或自有 Web 前端；README 展示的是 LangGraph Studio 界面。依据：[README L22–33](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/README.md#L22-L33)、[README L83–147](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/README.md#L83-L147)、[langgraph.json L1–10](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/langgraph.json#L1-L10)、[Dockerfile L20–39](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/Dockerfile#L20-L39)。

`pyproject.toml` 声明 Python `>=3.10`，而 `langgraph.json` 与 README 的开发启动选择 3.11；依赖包括 LangGraph、LangChain Community/Ollama/OpenAI、Tavily、DuckDuckGo、OpenAI SDK、HTTPX、markdownify 和 dotenv，**没有显式 FastAPI 依赖**。`uv.lock` 存在但本轮未核验其可安装性。依据：[pyproject.toml L1–22](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/pyproject.toml#L1-L22)、[langgraph.json L1–10](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/langgraph.json#L1-L10)。

## 2. 现有 LangGraph：节点、边、State 与循环

图由 `StateGraph(SummaryState, input=SummaryStateInput, output=SummaryStateOutput, config_schema=Configuration)` 构建并编译。实际拓扑如下，节点名与连接来自[graph.py L444–465](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L444-L465)：

```text
START → generate_query → web_research → summarize_sources
                              ↑                 ↓
                              └─ reflect_on_summary ──(继续)
                                       │
                                       └─ finalize_summary → END
```

| 节点 | 当前输入/输出与作用 | 依据 |
| --- | --- | --- |
| `generate_query` | 用主题和当前日期提示模型，生成初始 `search_query`；结构化输出失败时退回 `Tell me more about <topic>`。 | [graph.py L138–189](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L138-L189) |
| `web_research` | 依配置调用一种 Web 搜索，格式化结果，追加来源字符串和研究结果字符串，并将轮数加一。Tavily 每轮最多 1 条，DuckDuckGo/SearXNG 最多 3 条。 | [graph.py L192–262](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L192-L262) |
| `summarize_sources` | 用最新一轮结果更新 `running_summary`，可去除 `<think>` 内容。 | [graph.py L265–328](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L265-L328) |
| `reflect_on_summary` | 从摘要提出知识缺口及后续查询；仍只把查询写回 `search_query`。 | [graph.py L331–384](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L331-L384) |
| `finalize_summary` | 对 `sources_gathered` 中的文本行做去重，把 `## Summary` 与 `### Sources` 拼进 `running_summary`。 | [graph.py L387–418](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L387-L418) |

`SummaryState` 包含 `research_topic`、`search_query`、`web_research_results`、`sources_gathered`、`research_loop_count`、`running_summary`。两个列表采用 `Annotated[list, operator.add]`，用于跨节点追加；输入仅为主题，输出仅为最终摘要。没有资讯 ID、结构化证据、任务状态、错误信息、时间戳或简报字段。依据：[state.py L1–23](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/state.py#L1-L23)。

循环在 `reflect_on_summary` 后分支：`research_loop_count <= max_web_research_loops` 时再次搜索，否则结束。计数由 `web_research` 在搜索后加一。因此默认 `max_web_research_loops=3` 时，初次搜索后计数为 1，随后仍搜索至计数 4，**实际执行 4 次搜索**；每次搜索后都先摘要、反思，最后一轮反思结果不再使用。即使上限为 0，入口仍至少搜索一次。依据：[graph.py L258–262](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L258-L262)、[graph.py L421–465](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L421-L465)、[configuration.py L19–23](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/configuration.py#L19-L23)。这是代码推导，尚未做运行测试。

## 3. 模型、搜索工具与配置接入

模型由 `Configuration` 选择 `ollama` 或 `lmstudio`，默认 Ollama `llama3.2`；`get_llm` 为查询/反思提供 JSON 模式或工具调用模式，摘要节点则独立创建普通聊天模型。Ollama 使用 `ChatOllama`，LMStudio 通过继承 `ChatOpenAI` 的 `ChatLMStudio` 连接其兼容 API。JSON 模式用 `json.loads` 抽取查询；工具调用模式读取首个工具调用参数，缺失时可回退。依据：[configuration.py L24–61](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/configuration.py#L24-L61)、[graph.py L44–135](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L44-L135)、[graph.py L299–328](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L299-L328)、[lmstudio.py L19–98](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/lmstudio.py#L19-L98)。

配置读取优先级由代码实现为环境变量、RunnableConfig `configurable`、Pydantic 默认值。搜索后端是 `duckduckgo`、`tavily`、`perplexity`、`searxng` 四选一；DuckDuckGo 不需密钥，Tavily 使用 `TavilyClient()`，Perplexity 以 `PERPLEXITY_API_KEY` 调用 `/chat/completions` 的 `sonar-pro`，SearXNG 读取 `SEARXNG_URL`。每轮只用一个后端，并非并行聚合。依据：[configuration.py L34–81](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/configuration.py#L34-L81)、[graph.py L206–256](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L206-L256)、[utils.py L165–387](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L165-L387)。

`.env.example` 给出了搜索与模型变量，但示例设置 `LLM_PROVIDER=lmstudio`、`FETCH_FULL_PAGE=True`；README 文字称默认全页抓取为 false，而 `Configuration` 实际默认是 `True`。后续应以运行配置的真实值为准，并统一文档。依据：[.env.example L1–17](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/.env.example#L1-L17)、[configuration.py L37–40](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/configuration.py#L37-L40)、[README L72–81](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/README.md#L72-L81)。

## 4. 可直接复用的部分与边界

| 可复用部分 | 代码位置 | AIPulse 使用边界 |
| --- | --- | --- |
| `StateGraph` 构建、输入输出模式和条件边 | [graph.py L421–465](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L421-L465) | 可作为“选中资讯后研究”的起点；节点和停止条件需调整，不适合直接承担资讯采集。 |
| Ollama/LMStudio 模型选择与结构化查询思路 | [graph.py L44–135](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L44-L135)、[lmstudio.py L19–98](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/lmstudio.py#L19-L98) | 可复用连接方式；需对模型能力、超时和结构化结果作校验。 |
| 四种 Web 搜索包装器与统一 `results` 形状 | [utils.py L165–387](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L165-L387) | 可供研究阶段使用；它们不是 GitHub Releases、arXiv、HN 的正式资讯采集器。 |
| 搜索结果按 URL 去重并限制原文长度 | [utils.py L55–120](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L55-L120) | 可借鉴格式化；只在一次函数调用内对 URL 去重，不能替代跨日、跨源资讯去重。 |
| 查询、摘要、反思提示模板 | [prompts.py L9–112](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/prompts.py#L9-L112) | 研究流程可改造；现有模板是英文通用搜索与摘要，不含中文简报、分类或证据分级要求。 |
| Studio 启动配置和可视化开发体验 | [langgraph.json L1–10](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/langgraph.json#L1-L10)、[README L124–147](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/README.md#L124-L147) | 可用于开发和调试；不能视作 AIPulse 面向个人用户的正式 Web 界面。 |

## 5. AIPulse 需要新增的模块

以下均为**建议设计**，不是上游已存在的功能。固定提交的文件树只有单个研究图包，`graph.py` 的搜索入口也只处理用户主题，因此没有目标三源采集、日常定时、分类、评分、简报或持久化逻辑。依据：[提交树](https://github.com/langchain-ai/local-deep-researcher/tree/03537326ffa0dd90c9702bece2f3aeba5d84162a)、[graph.py L137–262](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L137-L262)、[state.py L6–23](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/state.py#L6-L23)。

1. **资讯采集**：三个独立适配器，分别处理 GitHub Release、arXiv 元数据及 HN story；统一映射为含稳定外部 ID、URL、原题、发布时间、抓取时间和原始摘要的记录。支持配置范围、分页/窗口、失败状态和有限重试。
2. **标准化与去重**：来源内稳定 ID 幂等；跨来源规范化 URL、arXiv ID、Release 标识以及保守标题相似规则。保留原始记录到归并事件的映射，避免将一次 Web 搜索格式化函数误当作持久去重。
3. **AI 相关性、分类与重要性评分**：先用可解释信号过滤，再按少量固定类别标记；分数附理由与置信度。为低质量或缺字段数据提供“待查看”状态。
4. **中文摘要与每日简报**：在去重后生成摘要；简报按明确时区与日期窗口汇总，记录来源覆盖、失败缺口和生成版本。
5. **持久化和个人 Web 服务**：保存资讯、简报与研究记录，并提供页面所需的读取/触发接口。建议先采用单机简洁方案；FastAPI 是新工作而非现有项目能力。

## 6. 研究工作流与异常处理需重构处

| 问题 | 源码证据 | 后续方向 |
| --- | --- | --- |
| 研究只接受自由文本主题，不能携带选中资讯、原始来源和证据快照 | [state.py L6–23](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/state.py#L6-L23) | 定义研究输入与结构化证据状态，保存来源 ID、URL、标题、抓取时间及引用关系。 |
| 默认轮数实际多做一次搜索，最后反思被丢弃 | [graph.py L258–262](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L258-L262)、[graph.py L421–465](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L421-L465) | 明确“最多 N 次搜索”的语义，先判断是否继续再做多余反思；测试 0/1/N 边界。 |
| 来源只是文本，最终按整行去重；正文引用与来源清单无机器可核对关系 | [graph.py L258–262](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L258-L262)、[graph.py L401–418](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L401-L418) | 引入证据 ID 与引用校验，报告区分事实、推断和证据不足。 |
| JSON/工具调用的回退有限：工具参数存在但为空可返回空查询；JSON 解析只捕获部分异常 | [graph.py L65–95](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L65-L95) | 验证非空查询及类型；限制结构化重试次数，失败状态可诊断。 |
| DuckDuckGo 捕获全部异常后返回空结果；SearXNG/Tavily/Perplexity 异常策略不一致，Perplexity 请求无显式 timeout | [utils.py L188–219](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L188-L219)、[utils.py L248–305](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L248-L305)、[utils.py L354–387](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L354-L387) | 统一超时、有限重试、错误分类、空结果与失败的区别；支持部分成功。 |
| `summarize_sources` 假定至少有一条研究结果，直接读取 `[-1]` | [graph.py L280–285](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L280-L285) | 对空结果、无效来源和模型失败设明确路径，不生成貌似完整的报告。 |
| README 声称输出 Markdown 文件；代码仅返回 `running_summary`，没有文件写入 | [README L174–182](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/README.md#L174-L182)、[graph.py L415–418](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L415-L418)、[state.py L21–23](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/state.py#L21-L23) | 决定 AIPulse 报告是数据库记录、Markdown 导出还是两者，并使文档与行为一致。 |

## 7. 技术风险、第三方 API 约束与安全

### 上游代码可见的风险

- `fetch_raw_content(url)` 对搜索结果 URL 直接发起 HTTPX GET，只有 10 秒超时与异常吞并，没有可见的目标地址、协议、响应大小或内容类型检查。个人部署也可能遭遇对内网/本机地址的服务端请求伪造，后续必须限制跳转与目标地址并隔离不可信网页内容。依据：[utils.py L141–162](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L141-L162)。
- 外部网页内容和搜索摘要直接进入模型提示，现有提示模板没有来源信任边界或引用校验；存在提示注入与无依据结论风险。依据：[utils.py L94–120](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L94-L120)、[graph.py L283–320](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L283-L320)、[prompts.py L44–72](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/prompts.py#L44-L72)。
- `graph.py` 打印原始模型响应，`lmstudio.py` 记录原始/清洗后的 JSON；若模型响应包含敏感信息可能进日志。Perplexity 密钥从环境变量读取；生产日志需脱敏，密钥不能暴露给 Web 客户端。依据：[graph.py L79–95](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/graph.py#L79-L95)、[lmstudio.py L72–90](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/lmstudio.py#L72-L90)、[utils.py L337–356](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L337-L356)。
- `strip_thinking_tokens` 使用首个 `<think>` 与首个 `</think>` 的字符串位置循环截取，未解析不成对或格式变化的标签；仅能视为简单清洗。依据：[utils.py L36–52](https://github.com/langchain-ai/local-deep-researcher/blob/03537326ffa0dd90c9702bece2f3aeba5d84162a/src/ollama_deep_researcher/utils.py#L36-L52)。

### 新数据源的官方接口约束（实施前再次核对）

| 来源 | 官方接口与设计约束 |
| --- | --- |
| GitHub Releases | 使用[GitHub Releases REST 接口](https://docs.github.com/en/rest/releases/releases)读取指定仓库发布记录。GitHub [REST 速率限制](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)按认证方式和端点变化；当前文档列出公共数据未认证请求 60 次/小时、常见认证请求 5,000 次/小时，并另有次级限制。V1 应维护仓库白名单、分页游标/时间窗口，处理 403/429、条件请求及令牌保密。 |
| arXiv | [arXiv API 用户手册](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/api/user-manual.md)说明查询、分页和排序，并建议连续调用之间留 3 秒；单次切片最多 2,000 条，大量元数据收集更适合 OAI-PMH。V1 应限制类别、时间窗口与分页，不进行全量抓取。 |
| Hacker News | [官方 HN API](https://github.com/HackerNews/API)以 `topstories`/`newstories` 等 ID 列表和逐条 `item/<id>.json` 提供数据；文档称目前没有速率限制，但列表与字段会变化，`deleted`/`dead`/空 `url` 必须容错。AI 相关性需要应用自己筛选，不能把 HN 列表直接当作 AI 专题。 |

这些接口约束是外部官方文档的当前说明，不是上游项目已有的采集实现；具体额度、服务条款和响应结构在编码时再核对并写契约测试。

## 8. 后续按 TDD 分步实施建议

本阶段不执行以下步骤。每步先写针对数据契约或失败场景的测试，再做最小实现，并保留固定样例响应；不用测试机械复述实现。

1. **落地并锁定基线**：用户把上游仓库放到 `D:\Projects\AIPulse` 后记录本地提交、工作树状态和与本审计提交的差异；先验证原图最小输入/输出，再决定是否更新审计。
2. **数据模型和存储**：从三种来源的真实/固定样例设计 `raw_item`、归并事件、来源链接与采集运行状态；先测试 ID 稳定、时间统一、重跑幂等和异常数据。
3. **逐个采集器**：按 GitHub Releases → arXiv → HN 的顺序实现。每个采集器先覆盖正常、分页、空响应、限流、超时、字段缺失；再做统一适配接口。
4. **去重、分类和评分**：先测试同一事件跨源合并与误合并边界；随后测试分类、分数区间、理由和低置信度；模型调用放在规则与去重之后。
5. **摘要和简报**：用固定资讯集测试中文摘要的来源绑定、失败回退、日期窗口与重跑结果；再实现简报存储与查询。
6. **研究图重构**：先用模拟搜索/模型测试 0、1、N 轮停止条件、空结果、模型格式错误、部分失败和证据引用；再调整 State、节点和报告结构。
7. **Web 与端到端验收**：在已有数据和研究契约稳定后增加个人 Web 服务与页面；覆盖简报浏览、选中研究、过程展示、报告来源、重试与重启恢复。

## 9. 尚未核实与待决事项

- 本地 `D:\Projects\AIPulse\src\ollama_deep_researcher` 不存在，因此**未核实本地实际代码、依赖安装、启动、模型可用性与任何运行表现**。所有循环和异常行为结论来自固定提交的静态阅读。
- 未指定部署形态、模型硬件资源、默认 GitHub 仓库/arXiv 查询/HN 列表、简报时区与评分权重；见 PRD 的待确认决策。
- `uv.lock`、仓库许可证全文和 GitHub 工作流没有逐行审计；若进入依赖锁定、分发或 CI 阶段，需要单独复核。
