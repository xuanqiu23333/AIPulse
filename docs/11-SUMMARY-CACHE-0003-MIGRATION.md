# 开发库 0002→0003：人工备份与受保护升级

目标仅为本机 `127.0.0.1:3306/aipulse_db`；先停止可能写入新闻的 Uvicorn / Scheduler 进程。本文件是操作命令，**不是迁移或备份成功记录**。Codex 已只读确认升级前为 0002，`news_event=21`、`news_item=21`、`news_source=3`，并未运行下列备份或升级命令。不要使用空库初始化入口、测试库或裸 `alembic upgrade head`。

## 1. 用户在自己的 PowerShell 中生成项目外真实数据备份

```powershell
Set-Location D:\Projects\AIPulse
$backupDir = 'C:\Users\<WindowsUser>\Documents\ChatGPT\aipulse\backups'
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$backupFile = Join-Path $backupDir ("aipulse_db_before_0003_{0}.sql" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
if (Test-Path -LiteralPath $backupFile) { throw 'Backup path already exists' }
$dump = 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe'
& $dump --protocol=TCP --host=127.0.0.1 --port=3306 --user=aipulse_migrate --password `
  --single-transaction --quick --skip-lock-tables --no-tablespaces `
  --set-gtid-purged=OFF --column-statistics=0 --skip-extended-insert `
  --default-character-set=utf8mb4 "--result-file=$backupFile" --databases aipulse_db
if ($LASTEXITCODE -ne 0) { throw 'mysqldump failed; stop without migrating' }
$backupHash = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash
Get-Item -LiteralPath $backupFile | Select-Object FullName, Length
Write-Output "Backup SHA-256: $backupHash"
```

`--password` 会在用户本机终端交互输入，不要将密码放入命令参数、环境文件或聊天。此备份使用 MySQL 8.0.44 的 `mysqldump`、一致性快照及逐行 INSERT。`--result-file` 直接写原始 SQL，避免 PowerShell 管道更改编码。备份保存在项目目录之外，包含新闻业务数据，不是空库结构导出。若命令失败，保留现场并停止。

## 2. 离线验证备份，然后人工启动固定升级入口

在**同一个 PowerShell 窗口**保留 `$backupFile`、`$backupHash`：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$python = (Get-Command python).Source
& $python -B -m aipulse.storage.upgrade_dev_summary --verify-backup `
  --backup-file $backupFile --backup-sha256 $backupHash
if ($LASTEXITCODE -ne 0) { throw 'Backup validation failed; stop without migrating' }
& $python -B -m aipulse.storage.upgrade_dev_summary `
  --backup-file $backupFile --backup-sha256 $backupHash
```

离线验证要求 SHA-256 一致、文件完整且不超过一小时、包含全部 8 张表、Alembic 0002 版本行和 `news_event/news_item/news_source` 真实 INSERT 行。升级入口重新验证备份，并使用 `getpass` 在本机提示 `aipulse_migrate` 密码；它核对账号、数据库、权限、完整 0002 Schema 及备份与实时行数，再把**同一连接**交给 Alembic 固定升级至 `20261021_0003`。升级后核验 5 个可空字段、revision 和所有表行数。失败时只读报告残留，不自动重试、回滚 DDL、清理或恢复。

## 3. 升级成功后只读验收

```powershell
& $python -B -m aipulse.news.cli --check --provider github `
  --github-repo langchain-ai/langgraph --limit-per-source 1
```

CLI 应显示 `Development news schema (0002/0003/0004): ready`。再以正常本地 API 运行账号核对新闻列表和一个真实 event 的详情；摘要 GET/POST 此时具备 0003 Schema，但**本次不调用摘要 POST**，也不启动自动采集。若升级入口失败，不重复执行命令，先只读检查实际 revision 和列状态。Sprint 4 新增的 0004 不由本入口执行，使用另行授权的研究迁移入口。
