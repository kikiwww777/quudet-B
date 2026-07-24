# QuuDet 统一节点调度实施设计

适用对象：继续执行 `quudet` 统一节点调度改造的开发者 / AI  
目的：把现有设计文档和 continuation report 收束成一份可直接执行的实施方案。

前置文档：

- [QUUDET_UNIFIED_NODE_SCHEDULING_DESIGN_2026-07-09.md](/C:/Users/86159/Documents/Obsidian%20Vault/AI4Science/07_AI代理/实验模块/quudet/docs/QUUDET_UNIFIED_NODE_SCHEDULING_DESIGN_2026-07-09.md)
- [QUUDET_UNIFIED_NODE_SCHEDULING_NEXT_DEV_PLAN_2026-07-09.md](/C:/Users/86159/Documents/Obsidian%20Vault/AI4Science/07_AI代理/实验模块/quudet/docs/QUUDET_UNIFIED_NODE_SCHEDULING_NEXT_DEV_PLAN_2026-07-09.md)
- [QUUDET_UNIFIED_NODE_SCHEDULING_CONTINUATION_REPORT_2026-07-09.md](/C:/Users/86159/Documents/Obsidian%20Vault/AI4Science/07_AI代理/实验模块/quudet/docs/QUUDET_UNIFIED_NODE_SCHEDULING_CONTINUATION_REPORT_2026-07-09.md)

---

## 1. 实施目标

本轮实施完成后，系统应满足：

1. 本地节点通过 agent 完整执行任务，不再依赖训练 Celery 主路径。
2. 所有节点统一使用 token 认证。
3. 远程 Linux 节点可部署、注册、抢任务、回传结果。
4. 任务支持 `execution_target=local|remote|auto`。
5. 调度器可基于节点能力和任务约束做第一版筛选。

---

## 2. 总体顺序

严格按以下顺序推进：

1. 统一 token 认证
2. 补齐本地节点闭环
3. 完善节点能力探测
4. 接入 Linux 节点部署
5. 落地 `execution_target`
6. 增加调度筛选
7. 做端到端验收

不要跳步。每一步都要先本地验证，再进入下一步。

---

## 3. 工作包 A：统一 token 认证

### 目标

去掉“本地空 token 特判”作为长期正式模型，统一所有节点认证语义。

### 涉及文件

- `app/agent/runner.py`
- `app/api/routes/nodes.py`
- `app/api/routes/dispatch.py`
- `app/schemas/node.py`

### 具体任务

1. 在 `runner.py` 中实现 `_generate_node_token()`。
2. 若未设置 `NODE_TOKEN`，本地 agent 启动时自动生成稳定 token。
3. `nodes.py` 的 `register` / `heartbeat` 统一要求 token。
4. `dispatch.py` 的 `claim-next` / `events` / `job-bundle` / `job-dataset` 全部走同一认证逻辑。
5. 删除或废弃“空 token + DISABLE_AUTH”作为节点认证分支。

### 验收

1. 本地节点能带 token 注册。
2. 本地节点能带 token 心跳。
3. 本地节点能带 token claim-next。
4. 本地节点能带 token 上传 events。

---

## 4. 工作包 B：补齐本地节点执行闭环

### 目标

让本地节点能完整跑通：

`register -> heartbeat -> claim-next -> bundle/dataset download -> execute -> events -> SUCCESS/FAILED`

### 涉及文件

- `app/agent/runner.py`
- `app/api/routes/dispatch.py`
- `app/api/routes/jobs.py`
- `app/api/routes/experiments.py`

### 具体任务

1. 确认 `jobs.py` 和 `experiments.py` 不再对训练任务直接 `delay()`。
2. 确认训练类任务统一写成 `PENDING_ASSIGN`。
3. 确认本地 agent 可以下载 job bundle。
4. 确认本地 agent 可以下载 dataset bundle。
5. 确认本地 agent 可以回传：
   - `log`
   - `progress`
   - `metrics`
   - `status`

### 验收

选一个最小训练 case，例如：

- `coco8.yaml`
- `epochs=1~2`
- `imgsz=320`
- `device=cpu` 或本地可用 GPU

验证：

1. 任务进入 `PENDING_ASSIGN`
2. 被本地节点领取
3. 产生 `run.log`
4. 能看到 metrics 回流
5. 终态为 `SUCCESS` 或可解释的 `FAILED`

---

## 5. 工作包 C：节点能力探测

### 目标

让节点能力自动探测成为真实调度输入，而不是只写进展示字段。

### 涉及文件

- `app/agent/runner.py`
- `app/models/compute_node.py`
- `app/api/routes/nodes.py`

### 最小能力字段

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
- `cpu_count`
- `memory_gb`
- `disk_free_gb`
- `yolo_cli_available`
- `path_style`

### 具体任务

1. `collect_node_capabilities()` 改为由 `NODE_KIND` 驱动，而不是写死。
2. `has_gpu` 优先用 `torch.cuda` 探测。
3. 若 `torch` 不可用，再保守回退。
4. `psutil` 缺失时不要让 agent 启动失败。
5. 心跳时继续更新轻量能力快照。

### 验收

1. 本地 Windows 节点上报 `node_kind=local`
2. Linux 节点上报 `node_kind=remote`
3. GPU 机 `has_gpu=true`
4. CPU 机 `has_gpu=false`

---

## 6. 工作包 D：Linux 节点接入

### 目标

提供正式可部署的 Linux 节点接入方案。

### 涉及文件

- `app/agent/runner.py`
- `app/services/yolo_runner.py`
- `app/services/train_metrics.py`
- `deploy/quudet-agent.service`
- `docs/QUUDET_LINUX_NODE_DEPLOYMENT_2026-07-09.md`

### 具体任务

1. 新增 `systemd` 模板。
2. 新增 Linux 部署文档。
3. 检查所有路径逻辑是否兼容 `posix`。
4. 确认 Linux 下：
   - `YOLO_WORK_DIR`
   - `DATA_DIR`
   - `results.csv`
   - `run.log`
   - snapshot 文件
   都能正确生成和解析。
5. 确认 `yolo` CLI 在 Linux 节点可执行。

### 建议部署基线

- Ubuntu 22.04
- Python 3.11+
- 固定目录 `/srv/quudet`
- `NODE_KIND=remote`
- 显式 `NODE_TOKEN`

### 验收

1. Linux 节点能注册
2. Linux 节点能心跳
3. Linux 节点能领取任务
4. Linux 节点能回传日志和 metrics
5. Linux 节点可完成一个最小训练 case

---

## 7. 工作包 E：落地 execution_target

### 目标

让任务显式表达执行意图。

### 涉及文件

- `app/models/job_record.py`
- `app/schemas/job.py`
- `app/schemas/experiment.py`
- `app/services/job_expand_service.py`
- `app/api/routes/jobs.py`
- `app/api/routes/experiments.py`
- `alembic/versions/*`

### 最小字段

- `execution_target`
- `required_gpu`
- `preferred_node_id`

### 推荐语义

- `local`
- `remote`
- `auto`
- 未设置时等价 `auto`

### 具体任务

1. 模型新增字段
2. schema 新增字段
3. alembic 增加迁移
4. experiment group 展开时透传字段
5. 单 job 创建时透传字段

### 验收

1. DB 可存储字段
2. API 可读写字段
3. 前端或调用侧可传入字段

---

## 8. 工作包 F：第一版调度筛选

### 目标

在 `claim-next` 中加入最小可用筛选逻辑。

### 涉及文件

- `app/api/routes/dispatch.py`

### 建议新增函数

- `_job_matches_node(job, node) -> bool`

### 最小规则

1. `execution_target=local`
   - 仅 `node_kind=local`

2. `execution_target=remote`
   - 仅 `node_kind=remote`

3. `required_gpu=true`
   - 仅 `has_gpu=true`

4. `auto`
   - 满足约束即可

### 暂不做

1. 复杂打分
2. 队列权重
3. 调度历史学习

### 验收

1. `local` 任务不会被远程节点领取
2. `remote` 任务不会被本地节点领取
3. `required_gpu=true` 的任务不会被 CPU 节点领取

---

## 9. 工作包 G：端到端验收

### Case 1：本地节点

1. 启动 API
2. 启动本地 agent
3. 创建最小训练任务
4. 验证本地节点领取并完成

### Case 2：Linux 远程节点

1. 启动 API
2. 启动 Linux agent
3. 创建 `execution_target=remote` 任务
4. 验证 Linux 节点领取并完成

### Case 3：auto 调度

1. 同时启动本地节点和 Linux 节点
2. 创建 `required_gpu=true, execution_target=auto` 任务
3. 验证只会被 GPU 节点领取

### Case 4：失败链路

1. 人为停止 agent
2. 验证任务超时或失败是否可解释
3. 验证节点状态是否转为 `OFFLINE`

---

## 10. 失败分流

若继续开发中出现问题，按以下类型归因：

1. 认证失败
   - token 不一致
   - register / heartbeat / claim-next 逻辑不一致

2. 路径失败
   - Windows / Linux 路径差异
   - `YOLO_WORK_DIR` 相对路径失效

3. 调度失败
   - `execution_target` 未透传
   - `_job_matches_node()` 规则错误

4. 回流失败
   - `events` 上传失败
   - metrics 解析失败

5. Linux 环境失败
   - `yolo` CLI 不可执行
   - torch / CUDA 不匹配

---

## 11. 最终通过标准

本轮工作可以判定为“统一节点调度第一版完成”，至少要满足：

1. 训练主路径不再依赖训练 Celery worker
2. 本地节点可稳定完整闭环
3. Linux 节点可稳定完整闭环
4. `execution_target=local|remote|auto` 生效
5. 调度器能基于 `node_kind` 和 `has_gpu` 做最小筛选

---

## 12. 一句话指令

如果交给别的 AI 继续做，可以直接按下面这句话执行：

> 先统一 token 和本地闭环，再完成 Linux 节点接入，最后把 execution_target 和 _job_matches_node 调度筛选接进主链路，并用本地 / 远程 / auto 三个 case 验收。
