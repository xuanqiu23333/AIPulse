# Windows / Conda 首次部署与安全运行

维护者：xuanqiu233。在克隆的项目根目录打开 PowerShell；路径可以任意，不需要原作者用户名、Conda 安装位置、dev 分支或 `.git`。冻结上游的 Dockerfile / LangGraph Studio 配置不是 AIPulse 启动入口。

## 1. 环境和锁定安装

Python 声明为 **>=3.11**；本地实际验证为 3.11.17。MySQL 受保护入口当前严格核验 **8.0.44**、127.0.0.1:3306、utf8mb4 / utf8mb4_0900_ai_ci；其他版本需要独立兼容验证，不要删除安全检查。前端 Node 要求 `^22.12.0 || >=24.0.0`，验证版本为 24.19.0。

已有开发环境无需重装。新机器人工准备：

```powershell
conda create -n Aipulse python=3.11
conda activate Aipulse
$apProject = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $apProject 'pyproject.toml'))) { throw 'Open the project root first' }
python --version
# 先安装/准备 uv；不要创建项目 .venv 或改变其他环境。
$apPreviousUvEnvironment = $env:UV_PROJECT_ENVIRONMENT
try {
    if (-not $env:CONDA_PREFIX) { throw 'Activate Conda first' }
    $env:UV_PROJECT_ENVIRONMENT = $env:CONDA_PREFIX
    uv sync --locked --no-dev --inexact
    if ($LASTEXITCODE -ne 0) { throw 'Locked installation failed; stop' }
} finally {
    if ($null -eq $apPreviousUvEnvironment) {
        Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
    } else { $env:UV_PROJECT_ENVIRONMENT = $apPreviousUvEnvironment }
}
python -m pip check
```

`--locked` 校验元数据与 uv.lock；`--frozen` 会跳过一致性检查。`--inexact` 保留激活环境中的其他包。`pip install -e .` 不使用 uv 锁，不能称为锁定安装。当前 distribution 名 `ollama-deep-researcher` 是历史包名，实际业务模块是 `aipulse`。本轮没有在全新 Conda 环境安装全部 Python 包，因此不宣称干净机器安装已真实验证。

用户在 `.env` 不存在时复制 `.env.example` 并私下填写 DB_PASSWORD 与可选 DEEPSEEK_API_KEY；不覆盖已有文件。数据库应用配置加载本地 `.env`，功能开关只读取进程环境。API Key 不进入前端。没有 Key 也可准备数据库及只读查询。

## 2. 首次空库准备

数据库管理员人工创建 **aipulse_db** 和用途分离的本机服务账号：

- 运行账号 `aipulse_app@localhost`：开发库 SELECT / INSERT / UPDATE / DELETE。
- 迁移账号 `aipulse_migrate@localhost`：开发库 SELECT / INSERT / UPDATE / DELETE / CREATE / REFERENCES / INDEX / ALTER。
- 两个账号使用各自独立密码；这些是非个人的固定服务身份，迁移不读取 `.env` 中运行密码，不用运行账号或 root 执行 DDL。不要授予全局管理权限或测试库权限。
- 字符集/排序规则为 utf8mb4 / utf8mb4_0900_ai_ci。已有库不重建、不清空，不执行 DROP、TRUNCATE、stamp 或 downgrade。测试库不属于首次开发部署步骤。

## 3. 空库备份与 0001–0004 初始化（仅人工执行）

先关闭会写入此库的服务。在已激活环境、项目根目录执行；mysqldump 如不在 PATH，用户自行选择本机 MySQL 8 安装目录。

```powershell
$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$apPython = (Get-Command python -ErrorAction Stop).Source
$apDump = (Get-Command mysqldump -ErrorAction Stop).Source
$apBackupDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'AIPulse-backups'
New-Item -ItemType Directory -Path $apBackupDir -Force | Out-Null
$apBackup = Join-Path $apBackupDir ('empty_before_0001_{0}.sql' -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
if (Test-Path -LiteralPath $apBackup) { throw 'Backup already exists' }
& $apDump --protocol=TCP --host=127.0.0.1 --port=3306 --user=aipulse_migrate --password `
  --single-transaction --quick --skip-lock-tables --no-tablespaces --set-gtid-purged=OFF `
  --column-statistics=0 --skip-extended-insert --default-character-set=utf8mb4 `
  "--result-file=$apBackup" --databases aipulse_db
if ($LASTEXITCODE -ne 0) { throw 'Backup failed; stop' }
$apSha = (Get-FileHash -LiteralPath $apBackup -Algorithm SHA256).Hash
Get-Item -LiteralPath $apBackup | Select-Object FullName,Length
# 只检查文件和固定 revision 链，不连接数据库。
& $apPython -B -m aipulse.storage.init_dev_database --check --backup-file $apBackup --backup-sha256 $apSha
if ($LASTEXITCODE -ne 0) { throw 'Offline validation failed; stop' }
# 下一条会写 Schema，须由操作者明确决定执行；口令在本机 getpass 输入。
& $apPython -B -m aipulse.storage.init_dev_database --backup-file $apBackup --backup-sha256 $apSha
if ($LASTEXITCODE -ne 0) { throw 'Initialization failed; preserve state, do not retry' }
```

备份必须位于项目外，是最近一小时的可读空库 mysqldump，SHA-256 匹配且有完成标记。非空备份/库、错误账号、目标库、权限、字符集、版本或修改过的迁移均拒绝。备份在提示后、建立连接前再次验证。检查将在执行 DDL 的同一连接上进行，再依次固定升级 0001、0002、0003、0004，每阶段立即校验版本、完整列定义、键约束及业务表零行；任一步失败停止，绝不重试、清理或恢复覆盖。

MySQL DDL 可能隐式提交，失败后只读检查实际残留并人工处理，不能再次用空库入口接管部分迁移。最终应有七张业务表和 alembic_version，版本为 20261021_0004。本轮仅离线验证，没有连接或初始化真实空库。

| 现有库 | 处理 |
| --- | --- |
| 空库 | 上述入口逐阶段到 0004 |
| 已有 0002 | 新完整备份后，专用 `upgrade_dev_summary` 固定升到 0003；再次备份后再升 0004 |
| 已有 0003 | 新完整备份后，专用 `upgrade_dev_research` 固定升到 0004 |
| 已有 0004 | 不迁移 |

两个增量入口均支持 `--verify-backup --backup-file <外部文件> --backup-sha256 <SHA256>` 离线核验。实际迁移去掉 --verify-backup 并通过 getpass 输入迁移密码；不可用初始化入口替代，不裸跑 upgrade head。`first_test_migration` / `second_test_migration` 是历史本地测试库维护代码，不是开发库部署入口，默认不得执行。

## 4. 默认只读启动

后端：

```powershell
conda activate Aipulse
# 在项目根目录执行
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:AIPULSE_ENABLE_MANUAL_COLLECTION = '0'
$env:AIPULSE_ENABLE_SCHEDULED_COLLECTION = '0'
$env:AIPULSE_ENABLE_AI_SUMMARY = '0'
$env:AIPULSE_ENABLE_DAILY_BRIEF = '0'
$env:AIPULSE_ENABLE_RESEARCH = '0'
$env:AIPULSE_ENABLE_RESEARCH_EXTERNAL = '0'
python -B -m uvicorn aipulse.api.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

另开终端在项目根目录：

```powershell
Set-Location ./frontend
npm ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
npm run dev
```

Web 为 http://127.0.0.1:5173，Swagger 为 http://127.0.0.1:8000/docs。Vite 使用现有本机 /api 代理，保留 Host/Origin/跨站保护，不启用 reload 或多 worker。空库没有新闻时显示空状态，不自动采集或付费。`/health` 会读取数据库；本轮未启动服务。

## 5. 写入、费用与排障

六个开关均默认关闭且不从 `.env` 启用：AIPULSE_ENABLE_MANUAL_COLLECTION、AIPULSE_ENABLE_SCHEDULED_COLLECTION、AIPULSE_ENABLE_AI_SUMMARY、AIPULSE_ENABLE_DAILY_BRIEF、AIPULSE_ENABLE_RESEARCH、AIPULSE_ENABLE_RESEARCH_EXTERNAL。只有操作者明确选择并重启进程后才启用相应功能。AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS 只影响首次调度延迟。

摘要和日报各最多一次模型，研究最多两次，规划/报告上限 1000/6144 Token，总输出 7144、输入另计；模型重试为 0，失败或取消也可能计费。研究整体 360 秒、最多三轮检索/18 条证据。单实例锁不保证多进程避免重复付费。

- Schema/备份不符：停止，人工核验，不删除保护或假造版本。
- 403：核对开关和本机 Origin，不开放公网或绕过保护。
- GET 报告 200：还须看业务 status/error_code；failed/partial 不是成功报告。
- 超时或 POST 结果未知：先查询，不重新提交；未知 usage 不能补零。
- 生产静态托管须配置同源 /api 代理和 SPA 回退；Vite preview 不自带代理。当前无公网认证，不使用隧道公开服务。
- 日报 v2 已有真实验收；Deep Research v6 完整报告尚未真实验证，见 [报告契约](22-RESEARCH-REPORT-CONTRACT.md)。
