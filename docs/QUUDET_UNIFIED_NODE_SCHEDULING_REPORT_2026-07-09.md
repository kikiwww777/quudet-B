# 统一节点调度实施报告

> **日期**: 2026-07-09  
> **对应设计**: [`QUUDET_UNIFIED_NODE_SCHEDULING_DESIGN_2026-07-09.md`](./QUUDET_UNIFIED_NODE_SCHEDULING_DESIGN_2026-07-09.md)  
> **状态**: Phase 1（本地节点接入）+ Phase 2（训练任务统一调度）已完成

---

## 目标

把 `quudet` 从"本地 Celery 执行 + 远程 agent 兼容路径"改造成"统一节点池驱动的实验执行层"。

## 当前架构

```
         API
          │
          ├── create job → status=PENDING_ASSIGN
          │
          ▼
   ┌──────────────┐
   │  调度队列     │  (dispatch/claim-next)
   │ PENDING_ASSIGN│
   └──────┬───────┘
          │
   ┌──────┴───────┐
   │   节点池      │
   ├───────────────┤
   │ local-windows │  ← agent runner (本地进程)
   │ remote-gpu-01 │  ← agent runner (Linux, 未来)
   │ remote-gpu-02 │  ← agent runner (Linux, 未来)
   └───────────────┘
          │
          ▼
     YOLO 执行
```

## Phase 1：本地节点接入

### 做了什么

| 文件 | 改动 |
|------|------|
| **`app/agent/runner.py`** | 模块从 `LEGACY` 升级为统一节点 runner |
| | 新增 `collect_node_capabilities()` — 自动探测能力并上报 |
| | 支持无 token 启动（本地模式），失败自动重试 |
| **`app/api/routes/nodes.py`** | 本地节点（空 token + DISABLE_AUTH）可免集群模式注册 |
| | 保留远程节点 token 认证路径 |
| **`app/schemas/node.py`** | `NodeRegisterRequest.token` / `NodeHeartbeatRequest.token` 改为可选 |

### 能力自动探测字段

```json
{
  "node_kind": "local",
  "os_type": "windows",
  "hostname": "DESKTOP-xxx",
  "platform": "win32",
  "python_version": "3.13.7",
  "torch_version": "2.12.0+cpu",
  "ultralytics_version": "8.4.58",
  "has_gpu": false,
  "gpu_count": 0,
  "cpu_count": 24,
  "memory_gb": 64.0,
  "disk_free_gb": 512.3,
  "yolo_cli_available": true,
  "path_style": "windows"
}
```

## Phase 2：训练任务统一进 PENDING_ASSIGN

### 做了什么

| 文件 | 改动 |
|------|------|
| **`app/api/routes/experiments.py`** | 展开实验组后不再 `run_yolo_job_task.delay()` |
| | 所有 job 统一 `status=PENDING_ASSIGN, dispatch_status=PENDING_ASSIGN` |
| **`app/api/routes/jobs.py`** | 单 job 创建同样走 PENDING_ASSIGN |
| | 移除 `run_yolo_job_task.delay()` 调用 |
| | 移除 `from app.tasks.executor import run_yolo_job_task` |
| **`app/api/routes/dispatch.py`** | `claim-next` 支持本地节点自动注册 + 免认证 |
| | 远程节点保留 token + 集群模式检查 |

### 状态流转变化

| | 之前 | 之后 |
|--|------|------|
| Celery 模式 | `PENDING → delay() → RUNNING` | `PENDING_ASSIGN → claim-next → RUNNING` |
| 集群模式 | `PENDING_ASSIGN → claim-next` | 不变 |
| 训练执行 | Celery worker 直接执行 | agent 通过 events 协议执行 |
| Celery 角色 | 训练主路径 | 仅后台维护（reconciliation） |

## 启动本地节点

```bash
# 简单启动（自动检测能力、注册、心跳）
cd quudet-yolo-lab-backend
.venv\Scripts\python -m app.agent.runner

# 验证节点已注册
curl http://localhost:8000/api/v1/nodes
```

## 后续 Phase

| Phase | 状态 | 内容 |
|-------|------|------|
| **1** 本地节点接入 | ✅ 完成 | agent 注册 + 能力探测 + 心跳 |
| **2** 统一 PENDING_ASSIGN | ✅ 完成 | 移除训练 Celery delay，统一调度 |
| **3** 远程 Linux 节点 | ⏭️ 待做 | Linux systemd 模板，跨平台路径适配 |
| **4** execution_target | ⏭️ 待做 | `local / remote / auto` 三态选择 |
| **5** auto 调度 | ⏭️ 待做 | 按资源约束自动分配节点 |
