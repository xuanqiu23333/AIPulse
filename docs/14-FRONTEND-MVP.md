# Sprint 5：Vue 3 前端 MVP

> 全站最新视觉、设计变量、视觉说明和只读启动命令见 [UI V2 工作台](19-UI-V2.md)。本文保留 Sprint 5 的契约与初始验收记录；UI V2 继续复用其付费确认及研究生命周期逻辑。

> 本文是 Sprint 5 验收记录。当前启动、安全修复和 Sprint 6 真实/离线边界以 [部署指引](15-LOCAL-DEPLOYMENT.md) 与 [联合验收方案](24-PUBLIC-CANDIDATE.md) 为准。

## 1. 范围与目录

本轮完成可运行的前端、真实数据 GET 集成、离线付费操作测试。没有修改后端源码、Schema、Alembic、.env 或历史研究数据；没有创建研究任务、调用模型、生成摘要/日报或启动新闻采集。

```text
frontend/
  package.json / package-lock.json   依赖与可复现安装
  vite.config.ts / tsconfig.json     代理、构建与严格类型检查
  src/
    api/                            实际响应类型、统一 fetch 客户端
    components/                     卡片、状态、来源、用量、确认框、证据与报告
    composables/                    可取消读取、有界研究状态轮询
    lib/                            日期、来源 URL 校验、本地任务 ID 历史
    pages/                          总览、新闻列表/详情、日报、研究创建/详情、404
    App.vue / router.ts / styles.css 统一响应式外壳、懒加载路由与视觉规范
  tests/                            仅离线 Mock fixtures 与定向测试
  public/favicon.svg
```

采用浅色背景、蓝色强调、系统中文字体、Lucide SVG 图标；无外部字体、图片或统计服务。桌面侧栏、移动抽屉、语义表单、可见焦点、原生 dialog 二次确认、减少动画偏好与安全来源链接均已实现。不需要跨页面业务 Store，因此没有引入 Pinia；只共享本地最近任务 ID。

## 2. 实际安装依赖

Node **24.19.0**；Vue **3.5.44**、Vue Router **4.6.4**、Vite **7.3.7**、TypeScript **5.9.3**、vue-tsc **3.3.13**、Tailwind CSS / @tailwindcss/vite **4.3.3**、@lucide/vue **1.61.0**。定向测试为 Vitest **4.1.12**、@vue/test-utils **2.5.1**、jsdom **27.4.0**；另有 @vitejs/plugin-vue **6.0.10**、@types/node **24.19.2**。具体传递依赖固定在 package-lock.json。Lucide 使用当前维护的 `@lucide/vue`，而非已弃用的 `lucide-vue-next`。

## 3. 页面与真实 API

| 页面 | GET | 主动确认后才发送的 POST |
| --- | --- | --- |
| `/` | `/api/v1/news/events`（最新 6 条、评分前 20 条）、最多 6 条详情、最多 3 日的日报缓存、最近访问的 1 个任务、采集状态 | 无 |
| `/news` | `/api/v1/news/events?page=&page_size=10&category=&min_score=&sort=`、最多 10 条详情用于来源与发布时间 | 无 |
| `/news/:eventId` | `/api/v1/news/events/{id}`、`/{id}/summary` | `/api/v1/news/events/{id}/summary`，无请求 body |
| `/briefs?date=YYYY-MM-DD` | `/api/v1/briefs/daily/{date}` | `/api/v1/briefs/daily`，仅 brief_date |
| `/research` | 本地最近访问的最多 3 个任务状态；不查询不存在的数据库列表接口 | `/api/v1/research/tasks`，仅 topic、可选 focus/event_id |
| `/research/:taskId` | task、evidence、report 三个现有接口 | `/api/v1/research/tasks/{id}/cancel`，无请求 body |

新闻类别严格对应 model/paper/tool/product/industry/discussion/other，排序为 score/latest。首页统计只计算最新已加载页，不伪造运营总量。重点区从实际加载的评分前 20 条中选取模型、论文、工具、产品类别，最多显示 5 条，明确未额外核验主题相关性。新闻详情没有正文接口时只显示已有事件摘录；缺少摘录明确说明，不自动补抓网页。

摘要 GET 404 表示没有有效缓存，不会自动 POST。日报固定 Asia/Shanghai，默认日期按该时区计算，也可直接链接历史日期。兼容已有 v1/v2 日报公共响应，`rules_current=false` 明确标为旧缓存；字段缺失时显示规则状态未提供，不冒充最新规则。

没有后端研究列表 API，前端不新增接口。提供 UUID 查找入口，本地最多保存 10 个已访问/已创建任务 ID，可清除，明确不是全部数据库任务。不把示例或历史 task_id 硬编码为真实列表。

## 4. 研究生命周期、报告与费用

- 状态采用后端实际枚举：queued、running、completed、partial、failed、interrupted。取消表现为 interrupted，不虚构 canceled 状态。
- 只在活动任务且页面可见时每 5 秒进行串行 GET 轮询；单次观察最多 80 次、窗口 420 秒，连续 3 次状态读取失败后暂停。进行中的各 GET 另有 15 秒超时。隐藏、路由切换、卸载、终态均停止自动观察；恢复可见不会重置上限。暂停不代表任务已结束，可以显式手动读取。
- 轮询和 GET 错误不会重新创建任务；没有通用自动重试。所有 POST 均有二次确认和提交中禁用。超时、网络或服务端错误可能已受理时给出“先查询、不要立即重复提交”的提示；研究提交结果不明时锁住再次提交，需人工核对后返回编辑。
- 创建请求最多 2 次 DeepSeek、3 轮检索、18 条证据、360 秒；当前规划/报告输出预算为 1000/6144，累计 7144，输入另计。摘要与日报各最多一次模型调用。实际费用、缓存复用与并发保护由已有后端控制，前端不保管密钥。
- 后端没有只读功能开关查询接口，页面说明默认关闭及费用边界，不用试探 POST 检测开关。403 明确提示功能未启用或本机请求限制；本轮后端所有相关开关均设置为 0。
- 即使报告 GET 为 HTTP 200，任务或报告 `status=failed` 时都展示错误并隐藏所有概览、结论、分析和趋势，仅保留局限、来源与用量。`report_output_truncated` 明确说明失败及修复后的真实付费验收尚未完成。
- `partial` 显示证据不足提示；引用只对已加载的证据编号建立定位链接，不存在的编号不生成有效锚点。可展开逐字引文，与证据 E 编号对照。所有外部/模型文字用 Vue 文本转义，不使用 v-html。
- usage_missing_calls 大于 0 时显示“已知 Token”及未知调用数，不能当成完整账单；token_usage=null 显示“用量未知”，不补零。模型名称与耗时均取实际响应。

## 5. 本机安全启动

通用 Windows / Conda 和标准 Node/npm 命令见 [部署说明](15-LOCAL-DEPLOYMENT.md)。在克隆目录执行，不依赖本机工具安装位置。单 worker、loopback、无 reload，六个写入/付费/定时开关保持 0；Vite 使用原同源代理。

## 6. 本轮真实只读验收

通过 **Vite 5173 → FastAPI 8000 → aipulse_db** 实际读取：

| 检查 | 结果 |
| --- | --- |
| 新闻第一页 | HTTP 200，当前查询 total=21 |
| paper + min_score=30 + latest | HTTP 200，total=5 |
| 浏览器翻页与组合筛选 | 第二页可读，应用筛选回到第一页、5 条匹配 |
| 事件 7 详情和摘要 | HTTP 200；摘要 cached=true |
| 2026-10-20 日报 | HTTP 200，5 条重点新闻，cached=true、rules_current=false，页面展示旧版警告 |
| 任务 10dde09d-e19e-44f5-a0ec-0148379426ed | status=failed、report_output_truncated，9 条节点日志、5 条证据 |
| 研究报告 | HTTP 200，但页面明确失败，869 已知 Token + 1 次用量未知，不展示有效结论 |
| 空日报 1900-01-01 | GET 404，页面显示空状态，未生成内容 |
| 后端断开与恢复 | 实际停止本轮只读后端后显示错误页；恢复服务并手动读取后正常恢复，无 POST |
| 响应式 | 1440px 桌面、375px 手机六个主要路由及 812px 横屏检查，无横向溢出 |

验收前数据：revision=20261021_0004；news_event=21、news_item=21、news_source=3、daily_brief=1、research_task=1、research_evidence=5、agent_run_log=9。只读检查和截图证据存于项目外 `C:\Users\<WindowsUser>\Documents\ChatGPT\aipulse\sprint5_acceptance\`。不把离线 fixtures 当作真实业务数据。

验收后再次只读核对，以上全部行数及版本不变；历史研究任务 result_json 的 SHA-256 前后相同，没有修改失败状态或补写未知用量。服务已恢复为只读预览，六个生成/采集/外部检索开关保持 0。

## 7. 定向检查

```powershell
# 在已有 Node/npm 的终端中执行；当前机器也可用上节 $frontendNode $frontendNpm 调用
npm run typecheck
npm test
npm run build
```

测试仅限统一客户端、研究/摘要/日报的主动确认、POST 单次提交与错误、失败报告语义、未知用量、证据锚点、活动/隐藏/终态/卸载/异常/时间上限轮询。测试默认阻断未 Mock 的网络调用，不访问 MySQL，不产生真实模型请求。生产 bundle 不导入 tests/。未运行后端全量测试。

最终检查结果：`typecheck` 退出码 **0**；三个定向测试文件 **25 passed / 0 failed**；`build` 退出码 **0**。产物按路由拆包，主 JS gzip 约 **46.26 kB**，主 CSS gzip 约 **6.44 kB**。构建资产检查没有服务端密钥变量、Authorization、DeepSeek 请求地址或离线 fixtures 内容。测试 JSON 位于项目外 `sprint5_acceptance/vitest.json`。

## 8. 已知边界与 Sprint 6 人工联调

- Sprint 4 截断修复仍缺真实付费验收；本次前端不能证明新参数一定成功。当前真实历史任务仍是失败，不修改其用量或内容。
- 后端无任务列表和独立开关状态 API，采用明确的本地历史与 403 提示，没有扩展后端。
- 真实创建、活动节点变化、成功/部分报告的引用跳转、取消，以及摘要/日报生成与缓存复用，只做了 Mock 交互验证。Sprint 6 需用户按明确费用上限另行批准后，在前端主动二次确认进行一次真实联调。
- 部署、认证、多用户与跨进程互斥未扩展；当前产品仍为个人本机单 worker MVP。
- 不自动进入 Sprint 6，不以页面打开为依据触发任何付费或数据库写入。
