# QuuDet 基础设施重构（方案C）实施报告

> **生成日期**: 2026-07-08  
> **对应设计文档**: [`QUUDET_SCHEME_C_INFRA_DESIGN_2026-07-08.md`](../QUUDET_SCHEME_C_INFRA_DESIGN_2026-07-08.md)  
> **适用目录**: `quudet/quudet-yolo-lab-backend/`

---

## 概述

按照方案 C 设计，分 4 个 Phase 完成了 `quudet` 后端基础设施重构。核心目标：**保留实验语义层，把基础设施主路径切换成 PostgreSQL + Redis + Celery worker，让数据库只负责记账、队列只负责派活、worker 只负责执行**。

---

## Phase 1 — 切数据库与消息代理

### 做了什么

| 改动 | 说明 |
|------|------|
| **默认数据库** | 从 `sqlite:///./data/quudet.db` 改为 `postgresql+psycopg2://quudet:quudet@localhost:5432/quudet` |
| **`app/database.py`** | 移除 SQLite 分支逻辑（`check_same_thread`、相对路径解析）；新增 PostgreSQL 连接池（pool_size=5, max_overflow=10）；新增 `check_db_connection()` |
| **移除 ALTER TABLE 迁移** | 删除了 `_ensure_cluster_columns()` 和 `_ensure_experiment_columns()` 两个启动时打补丁的函数 |
| **Alembic 迁移链** | 新建 `alembic/` 目录、`alembic.ini`、`alembic/env.py`；初始迁移 `001_initial_schema.py` 覆盖全部 5 张表 |
| **Docker 集成** | Dockerfile 启动前自动执行 `alembic upgrade head`；docker-compose 中所有服务显式使用 PostgreSQL |
| **端口暴露** | `docker-compose.yml` 中 `db:5432` 和 `redis:6379` 暴露到宿主机 |

### 涉及文件

- `app/config.py` / `app/database.py`
- `alembic/` (新建目录, 4 个文件)
- `Dockerfile` / `docker-compose.yml`
- `.env` / `.env.example`

---

## Phase 2 — 切调度主路径

### 做了什么

| 改动 | 说明 |
|------|------|
| **移除 sync 执行路径** | `EXECUTION_BACKEND` 只保留 `celery` / `remote-agent`，默认 `celery` |
| **删除 `SYNC_JOBS`** | `config.py` 不再有 `effective_sync_jobs` 属性和 `SYNC_JOBS` 遗留字段 |
| **删除 `enqueue_or_run_job()`** | `tasks/executor.py` 不再有分支逻辑，`execute_job()` 只能被 Celery task 调用 |
| **API 始终 enqueue** | `experiments.py` / `jobs.py` 在 `celery` 模式下直接调用 `run_yolo_job_task.delay()` |
| **claim-next 标记 LEGACY** | `dispatch.py` / `agent/runner.py` 头部加 LEGACY 模块注释 |
| **Redis 健康检查强制** | `/readyz` 始终检查 Redis，不再有 `"skipped"` 状态 |

### 涉及文件

- `app/config.py` / `app/tasks/executor.py`
- `app/api/routes/experiments.py` / `jobs.py` / `dispatch.py` / `nodes.py` / `health.py`
- `app/agent/runner.py`
- `.env` / `.env.example`
- `restart.bat`

---

## Phase 3 — 补恢复机制

### 做了什么

| 改动 | 说明 |
|------|------|
| **新增 CANCELLED / RETRYING 状态** | Job 终态集增加 `CANCELLED`，非终态集增加 `RETRYING`；Group 状态机新增 `CANCELLED` 判定 |
| **Reconciliation 服务** | 新建 `services/reconciliation.py`，包含三个修复步骤：<br>1. 清理无心跳的 RUNNING 任务（>5min）<br>2. 清理残旧 PENDING_ASSIGN（>1h）<br>3. 重算 orphaned groups |
| **启动自动修复** | API 启动时自动运行一次 `reconcile_all()` |
| **手动触发端点** | `POST /api/v1/admin/reconcile`（需 superuser） |
| **Celery Beat 定时调度** | `beat_schedule` 每 60 秒运行一次 `quudet.reconcile`；docker-compose 新增 `beat` 服务 |
| **Celery Worker 探测** | `/readyz` 调用 `celery_app.control.ping()` 列出在线 worker |
| **任务自动重试** | `run_yolo_job_task` 最多重试 3 次，退避 30s→60s→120s |
| **终态防御** | `execute_job` 开头检查，跳过已终态的任务 |

### 涉及文件

- `app/services/reconciliation.py` **(新建)**
- `app/services/experiment_compare.py`
- `app/api/routes/admin.py` **(新建)**
- `app/api/routes/health.py`
- `app/tasks/executor.py` / `app/celery_app.py`
- `app/main.py` / `docker-compose.yml` / `restart.bat`

---

## Phase 4 — 抽象产物存储

### 做了什么

| 改动 | 说明 |
|------|------|
| **ArtifactStore 抽象层** | 新建 `services/artifact_store.py`，包含：<br>· `ArtifactStore` 抽象基类（10 个抽象方法）<br>· `LocalArtifactStore` 文件系统实现<br>· `get_artifact_store()` 单例工厂 |
| **安全防护** | `_resolve()` 防止路径遍历逃逸 store root |
| **配置项** | `config.py` 新增 `ARTIFACT_STORE_BACKEND = "local"`，预留 `"s3"` |
| **executor.py 重构** | 日志写入、manifest 存储走 ArtifactStore |
| **jobs.py 重构** | `job_logs()` 和 `job_metrics()` 的日志读取走 ArtifactStore |

### 接口一览

```python
store = get_artifact_store()

store.write_text("jobs/<id>/run.log", content)       # 写文本
store.write_bytes("jobs/<id>/model.pt", data)         # 写二进制
store.write_json("jobs/<id>/meta.json", {...})         # 写 JSON
store.read_text("jobs/<id>/run.log")                   # → str | None
store.read_bytes("jobs/<id>/model.pt")                 # → bytes | None
store.read_json("jobs/<id>/meta.json")                 # → dict | None
store.list_files("jobs/<id>", "*.csv")                 # → [str]
store.exists("jobs/<id>/run.log")                      # → bool
store.delete("jobs/<id>/old_file.txt")                 # → bool
store.job_dir("job-xxx")                               # → "jobs/job-xxx"
```

切换到 S3/MinIO 只需新增 `S3ArtifactStore` 实现类，改一行配置：

```env
ARTIFACT_STORE_BACKEND=s3
```

### 涉及文件

- `app/services/artifact_store.py` **(新建)**
- `app/config.py`
- `app/tasks/executor.py`
- `app/api/routes/jobs.py`

---

## 最终架构

```
                    ┌──────────────────┐
                    │  AI-Researcher   │
                    │  (实验循环大脑)   │
                    └────────┬─────────┘
                             │ ExperimentSpec / RoundDecision
                             ▼
                    ┌──────────────────┐
                    │  quudet API      │  FastAPI
                    │  (PostgreSQL)    │  Alembic 迁移
                    │  (ArtifactStore) │  抽象产物存储
                    └────────┬─────────┘
                             │ enqueue job
                             ▼
                    ┌──────────────────┐
                    │  Redis (broker)  │
                    └────────┬─────────┘
                             │ consume
                    ┌────────┴─────────┐
                    │  Celery Worker   │  执行 YOLO
                    │  + Beat          │  每 60s reconcile
                    └────────┬─────────┘
                             │ write / read
                    ┌────────┴─────────┐
                    │  Artifact Store  │  本地文件系统 (可换 S3)
                    └──────────────────┘
```

## 配置文件变更

### 新增环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EXECUTION_BACKEND` | `celery` | `celery` / `remote-agent` |
| `ARTIFACT_STORE_BACKEND` | `local` | `local` / `s3`（预留） |

### 移除的环境变量

| 变量 | 替代 |
|------|------|
| `SYNC_JOBS` | `EXECUTION_BACKEND=celery` |
| `CLUSTER_ENABLED` | `EXECUTION_BACKEND=remote-agent` |

### 本地开发启动

```
restart.bat
# 自动启动: PostgreSQL | Redis | Celery Worker | Celery Beat | API(8000) | 前端(8080)
```

或分步手动：

```bash
# 终端 1: 数据库
docker compose up db redis -d

# 终端 2: Celery Worker
cd quudet-yolo-lab-backend
.venv\Scripts\python -m celery -A app.celery_app worker -l info

# 终端 3: Celery Beat (可选，自动 reconciliation)
cd quudet-yolo-lab-backend
.venv\Scripts\python -m celery -A app.celery_app beat -l info

# 终端 4: API
cd quudet-yolo-lab-backend
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
