# QuuDet 统一节点调度后续开发设计

适用对象：继续接手 `quudet` 统一节点调度改造的开发者 / AI  
前置文档：

- [QUUDET_UNIFIED_NODE_SCHEDULING_DESIGN_2026-07-09.md](/C:/Users/86159/Documents/Obsidian%20Vault/AI4Science/07_AI代理/实验模块/quudet/docs/QUUDET_UNIFIED_NODE_SCHEDULING_DESIGN_2026-07-09.md)
- [QUUDET_UNIFIED_NODE_SCHEDULING_REPORT_2026-07-09.md](/C:/Users/86159/Documents/Obsidian%20Vault/AI4Science/07_AI代理/实验模块/quudet/docs/QUUDET_UNIFIED_NODE_SCHEDULING_REPORT_2026-07-09.md)

目标：在已有 Phase 1 / 2 改造基础上，继续把 `quudet` 推到“本地节点可稳定闭环、远程 Linux 节点可接入、调度模型可继续扩展到 local / remote / auto”的状态。

---

## 1. 当前判断

根据现有报告，当前已经开始向统一节点调度迁移，但还不能视为完全完成。

当前最重要的结论是：

1. 训练任务已开始统一走 `PENDING_ASSIGN`
2. 本地 agent 已开始承担执行职责
3. 但“本地空 token 节点”的执行全链路还没有完全收口
4. Linux 节点适配还未真正落地
5. `execution_target=local|remote|auto` 还未进入主协议

因此，接下来的开发不应再做大范围方向变更，而应进入“补齐闭环 + Linux 适配 + 调度字段落地”的阶段。

---

## 2. 下一阶段总目标

下一阶段建议拆成 4 个连续目标：

1. 补齐本地节点执行闭环
2. 规范节点认证与节点能力模型
3. 完成远程 Linux 节点接入
4. 引入任务级 `execution_target`

---

## 3. 目标 A：补齐本地节点执行闭环

### 3.1 问题

当前实现中，本地节点已能：

- register
- heartbeat
- claim-next

但后续链路中的以下接口仍然使用远程节点认证逻辑：

- `/dispatch/events`
- `/dispatch/job-dataset/{job_id}`
- `/dispatch/job-bundle/{job_id}`

这会导致本地空 token 节点虽然能领到任务，但在真实执行阶段可能无法完整回传。

### 3.2 目标

让本地节点在不依赖 Celery 训练执行的前提下，完整完成：

`claim-next -> execute -> events -> terminal status`

### 3.3 建议改动点

文件重点：

- `app/api/routes/dispatch.py`
- `app/api/routes/nodes.py`
- `app/agent/runner.py`

### 3.4 建议实现方式

二选一，但必须统一：

#### 方案 A：本地节点也统一带 token

优点：

- 认证模型单一
- 不需要在 `events / dataset / bundle` 上加特殊分支
- 对后续 Linux 节点更一致

缺点：

- 本地启动要多一层 token 注入

#### 方案 B：继续支持空 token 本地节点

若采用此方案，必须同步补齐：

- `dispatch._require_node()` 对本地节点的兼容
- `events`
- `job-dataset`
- `job-bundle`
- 相关 Query 参数校验

### 3.5 推荐

推荐优先改成“本地节点也带 token”。

原因：

- 认证路径统一
- 降低局域网环境的歧义
- 后续 `local / remote / auto` 调度更干净

### 3.6 验收标准

1. 本地 agent 能成功注册
2. 本地 agent 能成功 claim-next
3. 本地 agent 能下载 job bundle
4. 本地 agent 能下载 dataset bundle
5. 本地 agent 能上报 log / progress / metrics / status
6. 单个训练任务可完整进入 `SUCCESS` 或 `FAILED`

---

## 4. 目标 B：规范节点认证与节点能力模型

### 4.1 问题

当前节点能力模型已开始自动探测，但还不够稳定：

1. `node_kind` 不能写死为 `local`
2. 远程节点与本地节点认证语义不应过度分裂
3. 能力字段需要真正成为调度约束，而不是仅用于展示

### 4.2 目标

建立稳定的节点模型，使其可同时支撑：

- 本地 Windows 节点
- 本地 Linux 节点
- 远程 Linux GPU 节点

### 4.3 建议改动点

文件重点：

- `app/agent/runner.py`
- `app/schemas/node.py`
- `app/models/compute_node.py`
- `app/api/routes/nodes.py`

### 4.4 具体要求

#### 节点认证

建议统一要求：

- 所有节点都带 token
- 本地节点 token 可自动生成或由启动脚本注入
- 不再把“空 token”当作长期正式语义

#### 节点能力

至少保证自动上报：

- `node_kind`
- `os_type`
- `hostname`
- `python_version`
- `torch_version`
- `ultralytics_version`
- `has_gpu`
- `gpu_count`
- `gpu_names`
- `cuda_version`
- `memory_gb`
- `disk_free_gb`
- `yolo_cli_available`
- `path_style`

#### node_kind 判定

建议规则：

- 显式环境变量优先，例如 `NODE_KIND=local|remote`
- 未设置时再根据 `NODE_TOKEN` / `MASTER_API_BASE` / 启动脚本上下文推断
- 不要在代码里写死 `"node_kind": "local"`

### 4.5 验收标准

1. 本地节点上报 `node_kind=local`
2. Linux 远程节点上报 `node_kind=remote`
3. 调度器可基于 `has_gpu` / `os_type` / `node_kind` 做筛选

---

## 5. 目标 C：完成远程 Linux 节点接入

### 5.1 目标

让 Linux 节点正式作为执行节点接入，不再只是文档层面的“未来支持”。

### 5.2 涉及范围

文件重点：

- `app/agent/runner.py`
- `app/services/yolo_runner.py`
- `app/services/train_metrics.py`
- `app/api/routes/dispatch.py`
- `docs/` 下新增 Linux 部署文档或 systemd 模板

### 5.3 Linux 适配重点

#### 路径兼容

确保以下环节不依赖 Windows 路径习惯：

- job bundle 解压路径
- dataset bundle 解压路径
- `YOLO_WORK_DIR`
- `DATA_DIR`
- `results.csv`
- `run.log`
- snapshot 文件

#### 命令执行

确保 Linux 上：

- `python -m app.agent.runner` 可启动
- `yolo` CLI 可执行
- `cwd=YOLO_WORK_DIR` 时相对路径可解析

#### systemd 常驻

建议补一个正式模板：

- 自动启动
- 崩溃拉起
- 日志可读

### 5.4 建议部署基线

推荐基线：

- Ubuntu 22.04
- Python 3.11+
- CUDA 驱动与 PyTorch 对齐
- 固定目录结构，例如 `/srv/quudet`

### 5.5 Linux 节点验收

1. Linux 节点能 register
2. Linux 节点能 heartbeat
3. Linux 节点能 claim-next
4. Linux 节点能下载 dataset / job bundle
5. Linux 节点能完成一条 YOLO 训练任务闭环
6. metrics 与 log 能正常回流

---

## 6. 目标 D：落地 execution_target

### 6.1 目标

让每个任务都能显式表达：

- `local`
- `remote`
- `auto`

### 6.2 建议改动点

文件重点：

- `app/schemas/job.py`
- `app/schemas/experiment.py`
- `app/models/job_record.py`
- `app/api/routes/jobs.py`
- `app/api/routes/experiments.py`
- `app/api/routes/dispatch.py`
- 前端任务创建表单

### 6.3 推荐最小字段

先做最小集：

- `execution_target`
- `preferred_node_id`
- `required_gpu`

### 6.4 第一版调度规则

1. `local`
   - 只允许 `node_kind=local`

2. `remote`
   - 只允许 `node_kind=remote`

3. `auto`
   - 先选满足约束的本地节点
   - 本地不满足再选远程节点

### 6.5 验收标准

1. `local` 任务不会被远程节点领取
2. `remote` 任务不会被本地节点领取
3. `auto` 任务能按规则自动分配

---

## 7. 推荐开发顺序

严格按以下顺序推进，不建议跳步：

1. 先补本地节点执行闭环
2. 再收紧认证模型和节点能力模型
3. 再接 Linux 节点
4. 最后才把 `execution_target` 接进主协议

原因：

- 若本地闭环未稳，Linux 接入只会放大问题
- 若认证模型未统一，后续多节点调度会变得混乱
- 若节点能力字段还不稳定，`auto` 调度没有可靠依据

---

## 8. 推荐交付物

本轮开发结束后，建议至少交付：

1. 一份本地节点闭环验收记录
2. 一份 Linux 节点部署说明
3. 一份 `execution_target` 协议说明
4. 一个 systemd 模板
5. 一组最小联调截图或日志

---

## 9. 不建议现在做的事

当前阶段不建议：

1. 立刻引入复杂节点打分算法
2. 同时支持“空 token 本地节点”和“强认证远程节点”两套长期正式模型
3. 恢复训练任务直接走 Celery
4. 一开始就尝试混合多机 DDP

---

## 10. 一句话总结

下一阶段继续开发的正确重心不是“再扩展更多花样调度”，而是：

> 先把统一节点调度的基本闭环做实，再把 Linux 节点接进来，最后才引入 local / remote / auto 三态任务路由。
