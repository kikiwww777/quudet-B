# 统一节点调度后续开发报告

> **日期**: 2026-07-09  
> **前置**: [`QUUDET_UNIFIED_NODE_SCHEDULING_REPORT_2026-07-09.md`](./QUUDET_UNIFIED_NODE_SCHEDULING_REPORT_2026-07-09.md)  
> **设计文档**: [`QUUDET_UNIFIED_NODE_SCHEDULING_NEXT_DEV_PLAN_2026-07-09.md`](./QUUDET_UNIFIED_NODE_SCHEDULING_NEXT_DEV_PLAN_2026-07-09.md)

---

## 总览

在 Phase 1（本地节点接入）+ Phase 2（训练任务统一 PENDING_ASSIGN）基础上，按 Goals A→D 顺序完成统一节点调度闭环。

## Goal A：补齐本地节点执行闭环

### 问题

之前的实现中，本地空 token 节点虽能 claim-next，但后续 `events` / `job-bundle` / `job-dataset` 接口仍使用远程节点认证逻辑（`_require_node()` 检查 `effective_cluster_enabled`），导致执行链路在 token 校验处断裂。

### 方案

统一 token 认证——所有节点（包括本地）都使用 token。本地 agent 启动时自动生成确定性 token。

### 改动

| 文件 | 改动 |
|------|------|
| **`agent/runner.py`** | 新增 `_generate_node_token()` — 基于主机名+SHA256 生成 32 字符 token |
| | 模块启动注释从 LEGACY 改为统一节点 runner |
| | `collect_node_capabilities()` 的 `node_kind` 从硬编码改为 `NODE_KIND` 环境变量 |
| **`routes/nodes.py`** | 移除 `_is_local_node()` / `_verify_node_auth()`，统一走 `_get_node_or_404()` |
| | `register` / `heartbeat` 不再有空 token 分支 |
| **`routes/dispatch.py`** | `_require_node()` 仅做 token 校验，不再检查 `effective_cluster_enabled` |
| | `claim-next` 移除本地节点特殊注册分支 |
| **`schemas/node.py`** | `token` 恢复 `min_length=4`，所有节点必须带 token |

### 认证模型（统一后）

```
Agent                         API
  │                             │
  ├─ NODE_TOKEN 未设置 ─────────→ 自动生成（主机名+SHA256）
  ├─ POST /nodes/register ─────→ token_hash 存入 DB
  ├─ POST /nodes/{id}/heartbeat → 校验 token_hash
  ├─ POST /dispatch/claim-next  → 校验 token_hash → 领取任务
  ├─ POST /dispatch/events      → 校验 token_hash → 回传结果
  ├─ GET /dispatch/job-bundle/  → 校验 token_hash → 下载 bundle
  └─ GET /dispatch/job-dataset/ → 校验 token_hash → 下载数据集
```

---

## Goal B：规范节点认证与能力模型

### 节点能力自动上报（18 项）

```
node_kind            ← NODE_KIND 环境变量（local/remote）
os_type              ← platform.system()
hostname             ← socket.gethostname()
platform             ← sys.platform
os_release           ← platform.release()
python_version       ← sys.version
torch_version        ← torch.__version__（可选）
ultralytics_version  ← ultralytics.__version__（可选）
has_gpu              ← torch.cuda.is_available()
gpu_count            ← torch.cuda.device_count()
gpu_names            ← torch.cuda.get_device_name()
cuda_available       ← torch.cuda 可用
cuda_version         ← torch.version.cuda
cpu_count            ← os.cpu_count()
memory_gb            ← psutil（可选）
disk_free_gb         ← psutil（可选）
yolo_cli_available   ← ultralytics / shutil.which("yolo")
path_style           ← windows / posix
```

### 调度筛选器

`claim-next` 中新增 `_job_matches_node()` 函数，基于节点能力做硬约束：

```python
execution_target == "local"  → 仅 node_kind == "local" 可领取
execution_target == "remote" → 仅 node_kind == "remote" 可领取
device 含 "cuda"             → 仅 has_gpu == true 可领取
required_gpu == true          → 仅 has_gpu == true 可领取
```

---

## Goal C：远程 Linux 节点接入

### 交付物

| 文件 | 说明 |
|------|------|
| **`deploy/quudet-agent.service`** | systemd 模板 — 开机自启、崩溃自动拉起、journald 日志 |
| **`docs/QUUDET_LINUX_NODE_DEPLOYMENT_2026-07-09.md`** | Linux 部署指南 — 从环境准备到验证的全流程 |

### systemd 模板关键配置

```ini
[Service]
Type=simple
User=quudet
WorkingDirectory=/srv/quudet/quudet-yolo-lab-backend
Environment="MASTER_API_BASE=http://<master>:8000"
Environment="NODE_ID=linux-gpu-01"
Environment="NODE_KIND=remote"
Environment="NODE_TOKEN=<token>"
ExecStart=/srv/quudet/quudet-yolo-lab-backend/.venv/bin/python -m app.agent.runner
Restart=always
RestartSec=10
```

### 路径兼容

确认 agent runner 及相关服务代码无 Windows 硬编码依赖：
- 全部使用 `pathlib` 跨平台路径操作
- `shutil.which("yolo")` 优先，`yolo.exe` 仅作为 Windows 备用

---

## Goal D：引入 `execution_target` 三态调度

### 新增字段

| 位置 | 字段 | 类型 | 说明 |
|------|------|------|------|
| `JobRecord` 模型 | `execution_target` | String(32) | local / remote / auto |
| `ExperimentRunCreate` | `execution_target` | optional | 实验组 run 级别 |
| `ExperimentRunCreate` | `required_gpu` | bool | 是否需要 GPU |
| `JobCreate` | `execution_target` | optional | 单 job 创建 |
| `JobCreate` | `required_gpu` | bool | 是否需要 GPU |
| `JobRead` | `execution_target` | optional | 返回给前端 |

### 三态路由规则

| 值 | 行为 |
|-----|------|
| `local` | 仅被 `node_kind=local` 的节点领取 |
| `remote` | 仅被 `node_kind=remote` 的节点领取 |
| `auto` / `None` | 任何满足资源约束的节点均可领取 |

### 数据流

```
API 创建实验组 / job
  → 指定 execution_target / required_gpu
  → JobRecord 存储
  → claim-next 时 _job_matches_node() 筛选
  → 符合条件的节点 → 领取 → 执行
```

---

## 改动文件总清单

```
app/agent/runner.py               ← 自动 token + NODE_KIND + 能力探测
app/api/routes/nodes.py           ← 统一 token 认证
app/api/routes/dispatch.py        ← 统一 token 认证 + 调度筛选
app/api/routes/experiments.py     ← 透传 execution_target
app/api/routes/jobs.py            ← 透传 execution_target + 移除 Celery delay
app/models/job_record.py          ← 新增 execution_target 字段
app/schemas/node.py               ← token 必填
app/schemas/experiment.py         ← execution_target + required_gpu
app/schemas/job.py                ← execution_target + required_gpu
app/services/job_expand_service.py ← 透传 execution_target
deploy/quudet-agent.service       ← 新建（systemd 模板）
docs/QUUDET_LINUX_NODE_DEPLOYMENT_2026-07-09.md  ← 新建
alembic/versions/003_add_execution_target.py      ← 新建
```

## 当前执行架构（最终状态）

```
                    API
                     │
            create job / experiment
                     │
                     ▼
           status = PENDING_ASSIGN
                     │
                     ▼
            ┌────────┴────────┐
            │  调度队列        │
            │  dispatch/      │
            │  claim-next     │
            └────────┬────────┘
                     │ _job_matches_node()
                     │   ├─ execution_target == local → node_kind=local
                     │   ├─ execution_target == remote → node_kind=remote
                     │   └─ required_gpu → has_gpu=true
                     │
            ┌────────┴────────┐
            │   节点池         │
            ├─────────────────┤
            │ local-windows   │  agent runner (token 自动生成)
            │ linux-gpu-01    │  agent runner (systemd)
            │ linux-gpu-02    │  agent runner (systemd)
            └────────┬────────┘
                     │
                     ▼
              YOLO 训练执行
              events 协议回传
              (log/progress/metrics/status)
                     │
                     ▼
              compare / 结果回流
```
