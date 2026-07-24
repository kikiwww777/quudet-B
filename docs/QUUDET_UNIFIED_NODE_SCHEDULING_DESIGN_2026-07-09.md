# QuuDet 统一节点调度设计（本地节点 + 远程 Linux 节点）

> **状态: ✅ 当前主文档** — 本文件为 quudet 执行层的当前设计基准。方案 C（Phase 1-4）已合并入此模型。Linux 远程节点部分暂缓，本地节点已闭环。

适用对象：后续负责继续改造 `quudet` 执行层的开发者 / AI  
目标：把当前“本地 Celery 执行”和“远程 agent 执行”两套并存的后端，收口为“主控统一调度，所有执行环境都抽象为节点”的单一路径，并同时兼容 Linux 节点部署。

---

## 1. 问题定义

### 1.1 当前现状

`quudet` 目前存在两套执行语义：

1. 本地执行路径
   - 主控 API 创建任务后，直接通过 `Celery worker` 执行 YOLO 命令。
   - 这是近期方案 C（`PostgreSQL + Redis + Celery worker`）强调的新主路径。

2. 远程执行路径
   - 远程节点运行 `python -m app.agent.runner`
   - 通过 `register -> heartbeat -> claim-next -> events` 协议从主控领取任务并回传结果。
   - 当前代码注释已将其标为 legacy / backward compatibility 路径。

### 1.2 当前核心矛盾

用户真实需要不是“全局切换执行后端”，而是：

- 本地环境够用时，本地跑实验
- 本地不够时，把实验发给远程 Linux / GPU 节点
- 后续还可以扩展多个节点，统一利用资源

但当前代码使用的是全局 `EXECUTION_BACKEND` 模型：

- `celery`
- `remote-agent`

这会导致系统只能在“本地执行后端”和“远程执行后端”之间二选一，而不能在“统一调度池”里按任务选择执行环境。

### 1.3 设计结论

对 `quudet` 来说，真正合理的长期模型不是：

- 主控自己执行一部分任务
- 远程节点再执行另一部分任务

而应该是：

- 主控永远只负责调度、记录和汇总
- 本地环境也注册成一个节点
- 远程 Linux 环境也注册成节点
- 所有实验都只走一条统一的节点分发链路

---

## 2. 目标架构

### 2.1 目标原则

1. 主控不是执行节点
2. 本地环境是一个节点
3. 远程 Linux / GPU 环境也是节点
4. 所有实验任务统一进入调度队列
5. 调度器按规则选择节点，而不是按全局后端切换

### 2.2 目标数据流

```text
AI-Researcher / 前端 / API
    -> create job / create experiment group
    -> JobRecord(status=PENDING_ASSIGN)
    -> scheduler policy
    -> node claim-next
    -> execute yolo command on selected node
    -> events(log/progress/metrics/status)
    -> result aggregation / compare / round feedback
```

### 2.3 节点统一模型

所有可执行环境统一抽象成 `ComputeNode`：

- `local-node-windows-01`
- `local-node-linux-01`
- `remote-gpu-01`
- `remote-gpu-02`

主控不再“直接跑实验”，而是在本机上启动一个本地 agent，使本地环境也通过统一协议执行任务。

---

## 3. 改造目标

### 3.1 目标能力

改造后的 `quudet` 必须支持：

1. 每个任务可以选择 `local / remote / auto`
2. 本地节点和远程节点共享同一任务协议
3. 节点可以声明自己的资源能力
4. 调度器可以按任务需求分配节点
5. Linux 节点可以稳定部署并长期驻留
6. 后续可以扩展为多远程节点并行跑实验

### 3.2 非目标

本次设计不追求：

1. 多机 DDP / DeepSpeed 式单任务分布式训练
2. 自动跨节点切分一个 YOLO 训练任务
3. 复杂的 Kubernetes / Slurm 级调度
4. 立刻废除 Celery 的所有用途

Celery 仍可保留给以下非训练职责：

- 定时巡检
- reconciliation
- 清理任务
- 汇总与通知

但不再承担主实验执行。

---

## 4. 执行模型设计

### 4.1 任务级执行目标

给每个实验任务新增调度意图字段，例如：

```json
{
  "execution_target": "local | remote | auto"
}
```

建议语义：

- `local`
  - 只能由本地节点领取
- `remote`
  - 只能由远程节点领取
- `auto`
  - 由调度器自动选择满足条件的节点

### 4.2 节点能力建模

建议为节点增加或规范以下字段：

- `node_kind`
  - `local`
  - `remote`
- `os_type`
  - `windows`
  - `linux`
- `has_gpu`
  - `true/false`
- `gpu_count`
- `vram_gb`
- `cuda_version`
- `max_concurrent_jobs`
- `labels`
  - 例如 `["3090", "cuda12", "linux", "night-only"]`
- `status`
  - `ONLINE / OFFLINE / DRAINING / DISABLED`

### 4.3 节点自动能力探测设计

节点能力不应主要靠手填，而应由 agent 在启动时自动探测，再在注册和心跳时上报。

建议分成两层：

1. 启动探测
   - agent 启动时采集一次较完整的静态能力
   - 例如 OS、Python、CUDA、GPU、CPU、内存、磁盘、YOLO 可执行性

2. 周期心跳
   - 心跳时补充轻量动态状态
   - 例如当前运行任务数、剩余显存估计、磁盘剩余空间、节点负载

建议 agent 自动探测并上报以下字段：

- `os_type`
- `platform`
- `hostname`
- `python_version`
- `torch_version`
- `ultralytics_version`
- `has_gpu`
- `gpu_count`
- `gpu_names`
- `cuda_available`
- `cuda_version`
- `vram_gb`
- `cpu_count`
- `memory_gb`
- `disk_free_gb`
- `yolo_cli_available`
- `node_kind`
- `path_style`

建议注册时上报的 `capabilities` 示例：

```json
{
  "node_kind": "local",
  "os_type": "windows",
  "platform": "win32",
  "hostname": "DESKTOP-01",
  "python_version": "3.11.9",
  "torch_version": "2.7.0",
  "ultralytics_version": "8.x",
  "has_gpu": true,
  "gpu_count": 1,
  "gpu_names": ["NVIDIA GeForce RTX 4090"],
  "cuda_available": true,
  "cuda_version": "12.4",
  "vram_gb": [24],
  "cpu_count": 24,
  "memory_gb": 64,
  "disk_free_gb": 512,
  "yolo_cli_available": true,
  "path_style": "windows"
}
```

Linux 节点同理，只是：

- `node_kind=remote`
- `os_type=linux`
- `path_style=posix`

### 4.4 自动探测实现建议

建议在 `app/agent/runner.py` 增加统一的能力采集函数，例如：

- `collect_node_capabilities()`
- `collect_runtime_status()`

推荐探测来源：

1. Python 标准库
   - `platform`
   - `socket`
   - `shutil`
   - `os`

2. 可选依赖
   - `psutil`
   - `torch`

3. 命令探测
   - `yolo version`
   - `nvidia-smi --query-gpu=...`

建议优先级：

1. 优先用 `torch.cuda`
2. 若失败，再尝试 `nvidia-smi`
3. 若仍失败，则保守标记 `has_gpu=false`

### 4.5 自动探测的容错原则

能力探测不能阻塞节点上线。

也就是说：

- 某项探测失败，不应导致 agent 启动失败
- 应写日志并回退为 `unknown` 或保守值
- 调度器必须允许按缺省保守策略处理未知能力节点

建议：

- `has_gpu` 无法判断时默认为 `false`
- `cuda_version` 无法判断时为 `null`
- `vram_gb` 无法判断时为空数组
- `yolo_cli_available=false` 时节点仍可注册，但应标记为不可接训练单

### 4.6 心跳动态状态建议

除静态能力外，心跳里建议上报轻量动态状态：

- `running_jobs`
- `load_avg`
- `disk_free_gb`
- `memory_available_gb`
- `gpu_utilization`
- `gpu_memory_free_gb`

第一版如果不想引入 GPU 利用率探测，也至少应有：

- `running_jobs`
- `disk_free_gb`
- `memory_available_gb`

### 4.7 调度器如何使用自动探测结果

调度器应把自动探测到的能力用于硬约束筛选，而不是仅做展示。

建议至少支持以下判断：

1. `required_gpu=true`
   - 只选 `has_gpu=true` 的节点

2. `required_os=linux`
   - 只选 `os_type=linux`

3. `execution_target=local`
   - 只选 `node_kind=local`

4. `execution_target=remote`
   - 只选 `node_kind=remote`

5. `yolo_cli_available=false`
   - 不可领取训练/验证/检测任务

6. `path_style`
   - 用于调试路径兼容问题，不建议直接作为调度规则

### 4.3 任务需求建模

建议为任务补充调度相关字段：

- `required_gpu`
- `preferred_node_id`
- `required_os`
- `required_labels`
- `estimated_cost`
- `estimated_duration_minutes`
- `allow_fallback_to_remote`
- `allow_fallback_to_local`

其中最小实现可以先只做：

- `execution_target`
- `preferred_node_id`
- `required_gpu`

---

## 5. 调度策略设计

### 5.1 第一版调度规则

先不要做复杂智能调度，按显式规则实现：

1. `execution_target=local`
   - 只允许 `node_kind=local` 的节点领取

2. `execution_target=remote`
   - 只允许 `node_kind=remote` 的节点领取

3. `execution_target=auto`
   - 先筛选满足资源约束的在线节点
   - 优先本地节点
   - 若本地不满足则回退到远程节点

### 5.2 auto 模式推荐规则

建议第一版 `auto` 使用保守规则：

1. 若任务显式要求 GPU，而本地节点无 GPU，则发远程
2. 若任务为 smoke test / 小样本 / 2 epoch / debug，优先本地
3. 若任务数据量、epochs、imgsz 超过阈值，优先远程
4. 若本地节点已满载，则发远程
5. 若远程节点不可用，则可按策略失败或退回本地

### 5.3 节点选择优先级

建议排序键：

1. 满足显式约束
2. `preferred_node_id`
3. `ONLINE` 且未 `DRAINING`
4. 当前运行任务数更少
5. 节点权重更高
6. 本地优先或远程优先策略

---

## 6. 后端结构改造建议

### 6.1 总体原则

训练、验证、检测任务不再直接由 API 进程决定“本地 Celery 还是远程 agent”，而是统一创建为 `PENDING_ASSIGN`。

### 6.2 建议改动点

#### A. `app/config.py`

当前问题：

- `EXECUTION_BACKEND` 是全局二选一模型

建议：

- 将 `EXECUTION_BACKEND` 从“训练执行选择器”降级为“辅助基础设施开关”
- 引入更明确的任务级调度字段
- 若保留配置项，建议只用于兼容迁移期

#### B. `app/api/routes/jobs.py`

当前问题：

- 创建任务后，若 `EXECUTION_BACKEND == "celery"` 会直接 `delay()`

建议：

- 训练 / 验证 / 检测类任务统一写成：
  - `status=PENDING_ASSIGN`
  - `dispatch_status=PENDING_ASSIGN`
- 不再在这里直接调用 `run_yolo_job_task.delay()`

#### C. `app/api/routes/experiments.py`

当前问题：

- 展开 experiment group 后，同样会在 `celery` 路径上直接执行

建议：

- 所有展开后的 run 统一进入节点调度队列
- group 只负责创建任务和观察状态推进

#### D. `app/api/routes/dispatch.py`

当前问题：

- 当前被标注为 legacy

建议：

- 将其升级为主任务分发路径
- `claim-next` 增加基于任务约束和节点能力的筛选
- 不再只按“assigned / unassigned”简单抢占

#### E. `app/agent/runner.py`

建议：

- 本机也运行该 agent
- 不仅远程 Linux 用，Windows / 本地 Linux 也可复用
- 增加节点能力上报字段
- 增加更明确的执行环境诊断日志

#### F. `app/tasks/executor.py`

建议：

- 不再作为训练执行主路径
- 保留为后台维护任务实现位置

---

## 7. 本地节点设计

### 7.1 设计原则

本地环境不再由主控“内部直接执行”，而是以 agent 身份接入。

### 7.2 本地节点示例

Windows 本地节点：

- `node_id=local-windows-01`
- `node_kind=local`
- `os_type=windows`
- `has_gpu=false/true`

Linux 本地节点：

- `node_id=local-linux-01`
- `node_kind=local`
- `os_type=linux`

### 7.3 本地节点收益

1. 本地与远程完全统一协议
2. 日志、metrics、失败处理逻辑一致
3. 上层 agent 不再需要知道“本地是特殊情况”
4. 可以自然支持“本机也只是一个执行资源”

---

## 8. Linux 节点适配设计

### 8.1 Linux 节点定位

Linux 节点是标准执行节点，而不是特殊分支实现。

它需要具备：

1. 与主控可达的网络连接
2. 与仓库兼容的 Python 环境
3. YOLO 可执行环境
4. 稳定的工作目录结构
5. 常驻 agent 进程

### 8.2 Linux 部署基线

建议至少支持：

- Ubuntu 22.04 LTS
- Python 3.11+
- CUDA 环境可选
- systemd 托管 agent

### 8.3 Linux 节点目录约束

建议 Linux 节点保持固定目录结构，例如：

```bash
/srv/quudet
  /quudet-yolo-lab-backend
  /ultralytics-main
  /data
```

建议统一：

- `YOLO_WORK_DIR=/srv/quudet`
- `DATA_DIR=/srv/quudet/data`

若主控与 Linux 节点路径无法完全一致，必须保证：

- payload 中的相对路径都能相对 `YOLO_WORK_DIR` 解析
- 模型、数据集 YAML、预训练权重路径可在 Linux 侧真实存在

### 8.4 Linux 节点环境变量

建议规范以下环境变量：

```bash
export MASTER_API_BASE="http://master-host:8000"
export NODE_ID="remote-gpu-01"
export NODE_NAME="linux-gpu-01"
export NODE_TOKEN="node-token-remote-gpu-01"
export NODE_MAX_CONCURRENCY="1"
export YOLO_WORK_DIR="/srv/quudet"
export DATA_DIR="/srv/quudet/data"
```

### 8.5 Linux 节点系统服务

建议提供正式的 systemd 模板，保证 agent 常驻：

- 开机自启
- 崩溃自动拉起
- 标准日志收集

### 8.6 Linux 节点额外适配点

需要显式检查：

1. `python -m app.agent.runner` 可在 Linux 环境稳定运行
2. `yolo` CLI 在 Linux 节点可执行
3. `results.csv`、日志、snapshot 文件路径在 Linux 上不依赖 Windows 假设
4. `Path` / URI 处理不存在 Windows-only 写法
5. 数据集下载、解压、yaml 相对路径解析在 Linux 上可复现

---

## 9. 迁移方案

### 9.1 迁移目标

从“本地 Celery 主执行 + 远程 agent 兼容路径”迁移到：

- “统一节点调度主路径”

### 9.2 分阶段迁移

#### 阶段 1：本地节点接入

目标：

- 不改前端交互语义
- 本地环境先以节点身份跑起来

实施：

1. 启动本地 agent
2. 本地节点成功 `register + heartbeat`
3. 节点列表里能看到本地节点

#### 阶段 2：训练任务统一进入 `PENDING_ASSIGN`

目标：

- 取消训练任务创建后直接 `delay()` 执行

实施：

1. jobs / experiments 创建任务后统一等待节点领取
2. 本地节点可以先独占全部任务

#### 阶段 3：远程 Linux 节点接入

目标：

- Linux 节点能稳定抢任务和回传结果

实施：

1. 部署 Linux agent
2. 验证 register / heartbeat / claim-next / events
3. 验证训练结果完整回流

#### 阶段 4：引入 `execution_target`

目标：

- 支持按任务选本地 / 远程 / 自动

#### 阶段 5：引入 `auto` 调度

目标：

- 合理利用本地和远程资源

---

## 10. UI 与 API 建议

### 10.1 前端建议新增字段

在训练 / 验证 / 检测表单中增加：

- 执行目标
  - `本地`
  - `远程`
  - `自动`
- 指定节点
  - 可选

### 10.2 API 请求建议

任务创建请求里加入：

```json
{
  "execution_target": "auto",
  "target_node_id": null,
  "required_gpu": true
}
```

### 10.3 节点管理页建议

节点页展示：

- 节点类型
- OS
- 是否 GPU
- 当前并发
- 标签
- 最近心跳
- 可否接单

并支持：

- `DRAINING`
- `DISABLED`
- 手动指定权重

---

## 11. 验收标准

### 11.1 本地节点验收

1. 本地节点可注册
2. 本地节点可心跳
3. 本地节点可领取任务
4. 本地节点执行结果可回流

### 11.2 Linux 节点验收

1. Linux 节点可注册
2. Linux 节点可持续心跳
3. Linux 节点可领取训练任务
4. Linux 节点 `results.csv` / log / metrics 可正常上传
5. Linux 节点失败时任务可正确转终态

### 11.3 统一调度验收

1. `local` 任务不会被远程节点领取
2. `remote` 任务不会被本地节点领取
3. `auto` 任务会按规则选择节点
4. 本地节点不可用时，`auto` 可退到远程
5. 远程节点不可用时，系统不会假装成功

---

## 12. 风险与注意事项

### 12.1 不要混跑旧逻辑

在迁移完成前，最危险的状态是：

- API 一边直接 `delay()` 给 Celery
- 节点一边又在 `claim-next`

这会让系统出现两套任务流，必须避免。

### 12.2 目录结构一致性

Linux 节点最容易出问题的不是网络，而是：

- `YOLO_WORK_DIR`
- 模型路径
- 数据集 yaml 路径
- 结果文件路径

这些必须在文档和代码里统一约束。

### 12.3 节点能力不能只靠名字猜

不要通过节点名猜是否 GPU / 是否 Linux。  
必须由节点显式上报结构化能力字段。

### 12.4 auto 调度先保守

第一版 `auto` 不要引入复杂评分模型。  
先用确定性规则，便于调试和复验。

---

## 13. 建议最终状态

当本设计完成后，`quudet` 的执行层应表现为：

1. 主控不再直接跑 YOLO 实验
2. 本地环境只是一个普通节点
3. 远程 Linux 环境也是普通节点
4. 所有训练任务只走统一节点调度主路径
5. `AI-Researcher` 上层只需要关心实验 spec，不需要关心执行细节

一句话总结：

> 把 `quudet` 从“本地后端 + 远程兼容路径”的混合体，改造成“统一节点池驱动的实验执行层”。

---

## 14. 推荐实施优先级

1. 先让本地环境以节点身份接入
2. 再去掉训练任务的直接 Celery 执行
3. 再接回远程 Linux 节点
4. 最后补 `local / remote / auto` 三态调度

---

## 15. 交接说明

如果由别的 AI 继续实施，建议它优先完成以下具体工作：

1. 梳理 `jobs.py`、`experiments.py`、`dispatch.py`、`nodes.py`、`agent/runner.py`
2. 设计任务级 `execution_target` 字段
3. 让本地 Windows / Linux 都能以 node 方式接入
4. 提供 Linux systemd 部署模板
5. 写最小验收用例：本地节点、远程节点、auto 调度

本次文档只给出设计，不包含代码落地。
