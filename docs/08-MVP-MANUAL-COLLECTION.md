> 当前空库初始化支持 0001–0004；首次安装以 [部署文档](15-LOCAL-DEPLOYMENT.md) 为准。下文保留采集 CLI 的使用说明。

# MVP 第一阶段：手动真实新闻采集

状态：2026-10-20。开发库已升级至 20261016_0002；CLI 已完成一次三源真实采集与评分。后续真实写入均需按次获得授权。

## 首次初始化记录（已完成，不要重复执行）

以下备份和迁移命令仅记录首次空库初始化流程，不适用于已迁移的开发库。不要对当前开发库重跑初始化。`aipulse_test_db` 不能代替开发库。

先核对开发库当前表、`alembic_version` 和迁移账号权限。对 `aipulse_db` 做一次有时间戳的独立备份，记录文件路径并验证备份文件可读取；凭据由本机交互输入，不写入命令参数、`.env` 或报告。数据库为空也保留迁移前快照。随后用专用 `aipulse_migrate` 账号、显式 `dev` 目标执行已审阅的 Alembic 0001 初始化和 0002 升级，再只读核对 revision、表和评分字段。如果迁移中断，先检查实际表结构与 `alembic_version`，不要自动重跑、降级或清表。

`init_dev_database.py` 要求在本机交互式 PowerShell 中运行；迁移密码只由 `getpass` 读取。先使用同一个迁移账号做空库结构导出：

```powershell
Set-Location D:\Projects\AIPulse
$backupDir = Join-Path $env:USERPROFILE 'Documents\ChatGPT\aipulse\backups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$backupFile = Join-Path $backupDir ('aipulse_db_before_0001_{0}.sql' -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
$dump = 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe'
& $dump --host=127.0.0.1 --port=3306 --protocol=tcp --user=aipulse_migrate --password --single-transaction --no-tablespaces --set-gtid-purged=OFF --no-data --databases aipulse_db --result-file=$backupFile
if ($LASTEXITCODE -ne 0) { throw 'mysqldump failed; stop before migration' }
Get-Item -LiteralPath $backupFile | Select-Object FullName,Length
Get-FileHash -LiteralPath $backupFile -Algorithm SHA256
```

`mysqldump` 会在本机提示密码，不要把密码写进命令。确认导出成功、文件非空、路径及 SHA-256 已记录后，在一小时内执行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$python = (Get-Command python).Source
& $python -B -m aipulse.storage.init_dev_database --backup-file $backupFile --backup-sha256 (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash
```

入口会重新检查空库、账号、权限、版本及备份，在备份目录生成不含凭据的预检记录；仅按固定顺序执行 0001 和 0002。任何失败都停止，并只读报告残留表和 revision。**不要重复运行失败的迁移命令**；先审阅残留状态。

## 采集参数与边界

入口为 `python -m aipulse.news.cli`。必须显式选 `--check` 或 `--execute`，并逐个指定 `--provider` 与相应来源：GitHub 使用 `--github-repo OWNER/REPO`，arXiv 使用 `--arxiv-query QUERY`，HN 使用 `--hn-list topstories|newstories`。可重复指定同类来源；相同来源在单次命令中只运行一次。GitHub Token 可选，从当前进程的 `GITHUB_TOKEN` 环境变量读取；公开 Release 不要求 Token。不要把 Token 放入命令参数。

`--limit-per-source 5` 限制每来源处理的候选数。GitHub 和 arXiv 每来源最多请求一页，HN 最多处理 5 个有效候选且读取 story ID 数量有界。各 HTTP 请求默认超时 10 秒，可用 `--http-timeout` 在 1–30 秒之间调整。一次命令只运行一轮，不自动跟随 `next_cursor`，不启动定时任务。某来源失败后继续处理其他来源；运行报告列出来源状态、处理数、事件数和评分状态。重复运行沿用 `(provider, external_id)`、`event_key` 的现有幂等写入规则，结果中的 `processed` 是本次处理量，不代表新插入行数。

## 只读准备检查（现在可执行）

在 PowerShell 中：

```powershell
Set-Location D:\Projects\AIPulse
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$python = (Get-Command python).Source
& $python -B -m aipulse.news.cli --check `
  --provider github --github-repo langchain-ai/langgraph `
  --provider arxiv --arxiv-query 'cat:cs.AI' `
  --provider hn --hn-list topstories `
  --limit-per-source 5 --http-timeout 10
```

这组来源只是可替换示例。`--check` 不调用外部新闻 API，也不写数据库。完整的 0002、0003 或 0004 均显示 `Development news schema (0002/0003/0004): ready` 并返回 0；部分迁移或其他 revision 返回 2。0004 的研究字段升级需单独批准，新闻采集在完整 0003 上继续可用。

## 后续真实 CLI 运行（每次单独授权）

在开发库核对为完整的 0002 或 0003 且获得真实写入授权后，把上条命令的 `--check` 改为 `--execute`。该命令会真实调用三个官方 API，并通过 `NewsScoringService.ingest_and_score` 完成采集、标准化、归并、入库和评分保存。不要用测试库参数代替开发库。

在第二个 PowerShell 窗口启动只读 API：

```powershell
Set-Location D:\Projects\AIPulse
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$python = (Get-Command python).Source
& $python -B -m uvicorn aipulse.api.main:app --host 127.0.0.1 --port 8000
```

采集成功后，在另一个窗口查询：

```powershell
$page = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/news/events?page=1&page_size=20&sort=score'
$page.items | Select-Object event_id,title,category,importance_score,score_reason,providers
$eventId = $page.items[0].event_id
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/news/events/$eventId"
```

列表包含标题、类别、分数、评分理由及 provider；详情另含原始来源记录、原始 URL、时间和摘要字段。只有实际采集并写入后才会有真实新闻结果；如果列表为空，先检查 CLI 每来源状态和数据库 revision，不把空返回解释为成功采集。当前 `summary` 可为空，本阶段尚未接中文摘要生成。

## 手动采集 API（真实执行仍待单独授权）

`POST /api/v1/news/collect` 默认关闭。它只接受 `providers` 子集和 `limit_per_source`（1–5，默认 5）；固定来源为 GitHub `langchain-ai/langgraph`、arXiv `cat:cs.AI`、Hacker News `topstories`，HTTP 超时固定为 10 秒。请求不能指定 URL、数据库或其他地址。每次只采一轮，各来源最多一页；失败来源不会阻止其余来源。响应的 `processed_count` 是本轮处理的原始身份数量，并非新插入行数。

获得**本次 API 写入授权**后，在启动 API 的 PowerShell 中显式设置本地开关，再将服务绑定到 loopback：

```powershell
Set-Location D:\Projects\AIPulse
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:AIPULSE_ENABLE_MANUAL_COLLECTION = '1'
python -B -m uvicorn aipulse.api.main:app --host 127.0.0.1 --port 8000
```

在本机打开 `http://127.0.0.1:8000/docs`，展开 `POST /api/v1/news/collect`，点击 **Try it out**，提交：

```json
{"providers": ["github", "arxiv", "hn"], "limit_per_source": 5}
```

接口只接受 loopback 网络对端及本地主机名；执行前核对开发库为 `aipulse_db`、运行账号为 `aipulse_app@localhost`、revision 为 `20261016_0002`。同一 API 实例只能运行一项采集，第二次并发触发返回 409；完成或异常后释放锁。此锁**不提供跨进程互斥**，本地运行不要启动多个 Uvicorn worker。正常关闭该 PowerShell 后，开关不会写入 `.env`。不设置开关时 POST 返回 403，GET 新闻接口照常可用。

## 定时采集（代码已准备，真实运行待单独授权）

APScheduler 3 的定时采集默认关闭。仅在本机获得真实定时写入授权后，才在启动 API **之前**设置 `AIPULSE_ENABLE_SCHEDULED_COLLECTION=1`，并以 `--host 127.0.0.1`、单 worker、无 `--reload` 的方式启动 Uvicorn。该开关独立于手动 POST 的 `AIPULSE_ENABLE_MANUAL_COLLECTION`。不开启时不构建或启动 Scheduler。

开启后使用内存 JobStore，每 60 分钟执行一轮。未设置 `AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS` 时，首次运行约在启动后 60 分钟；可将该变量设为 1–3600 的十进制整数秒数，只调整首次执行时间，后续仍每 60 分钟执行。非法值会拒绝启动，不会静默回退；不开启定时采集时不读取此变量。不会启动即刻采集，也不会补跑停机期间错过的轮次。来源固定为 GitHub `langchain-ai/langgraph`、arXiv `cat:cs.AI` 和 Hacker News `topstories`，每来源最多处理 5 条，HTTP 超时 10 秒。不保存 `next_cursor`，每轮从最新内容开始，重复项交给现有唯一约束及 UPSERT。

### 本地首次执行快速验收

仅在获得真实定时采集及开发库写入授权后，由用户在本机 PowerShell 手动启动。下面的 120 秒配置只用于本地快速验收；它使首次采集约 2 分钟后执行，第二轮起仍每 60 分钟执行。不要使用 `--reload` 或多个 worker。

```powershell
Set-Location D:\Projects\AIPulse
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:AIPULSE_ENABLE_SCHEDULED_COLLECTION = '1'
$env:AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS = '120'
python -B -m uvicorn aipulse.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

在另一个 PowerShell 窗口查询，等待 `last_run.finished_at` 出现，并查看来源结果、处理数量及失败类别：

```powershell
$status = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/news/collect/status'
$status | ConvertTo-Json -Depth 8
```

首轮完成后立即在 Uvicorn 窗口按 `Ctrl+C` 关闭服务，避免进入下一次自动采集。本说明只准备命令，Codex 不执行真实采集。

手动 POST 和定时任务共用一个 API 实例的非阻塞锁。手动请求遇到忙碌返回 409；定时触发遇到忙碌记录一次 `skipped / overlap`，不排队或自动重试。APScheduler 同时设置 `max_instances=1` 和 `coalesce=True`。此锁不跨进程，因此不能用多个 Uvicorn worker 或多个 API 进程运行该本地 MVP。

本机可用 `GET http://127.0.0.1:8000/api/v1/news/collect/status` 查看内存中的最近一次定时尝试，包括开始和结束时间、每来源结果、本轮处理数及脱敏失败类别。服务重启后状态清空；定时任务失败会记录安全类别，不输出密码或完整 DSN，也不导致 FastAPI 退出。Scheduler 在 FastAPI lifespan 关闭时停止，并等待当前定时任务释放连接后再关闭数据库 Engine。

GitHub Releases 曾出现 `partial / invalid_response`：一次无正文的 `HEAD` 检查证实 GitHub 将分页 `Link` 路径规范化为 `/repositories/676672661/releases`，而旧校验只接受请求所用的 `/repos/langchain-ai/langgraph/releases`。Collector 现允许该数字仓库路径作为页码提示；实际 HTTP 请求仍固定发送到已配置的 `langchain-ai/langgraph` 端点。每来源 5 条的手动和定时入口仍只请求一页，不跟随 `next_cursor`。
