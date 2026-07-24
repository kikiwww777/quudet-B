> **状态: 已过期** — 当前执行路径已切换为统一节点调度（PostgreSQL + Redis + agent）。本文档保留仅作历史参考。
>

# quudet 实验平台科研化改造 — 开发说明

> 基于 `quudet_实验平台科研化改造实施方案_工程版.md` 的完整落地实现  
> 日期：2026-07-01

---

## 1. 改造目标

把 quudet 从"单次 YOLO 任务执行工具"升级为"目标检测科研实验平台"。

| 改造前 | 改造后 |
|--------|--------|
| 一次只能创建一个训练任务 | 一次创建一个实验组，自动拆分为多个任务 |
| 跑完就完了，无法复现 | 每次执行自动拍快照，所有参数、配置、环境存档 |
| 结果对比靠手动算 | 一键出对比报告：mean/std/delta/best_run |
| 多机执行时配置可能不一致 | 主控打包快照同步到节点，保证一致 |
| 超参数测试要手写几十个任务 | 一个 grid 声明，自动笛卡尔积展开 |

---

## 2. 架构总览

```
上层科研智能体 / 用户
        │
        ▼
   POST /api/v1/experiments
   (ExperimentGroupCreate)
        │
        ▼
┌──────────────────────────────────┐
│  quudet API 层                    │
│  experiments.py  ← 实验组路由      │
│  jobs.py         ← 原有任务路由    │
│  dispatch.py     ← 集群调度路由    │
└──────────┬───────────────────────┘
           │
     ┌─────┴─────┐
     │           │
     ▼           ▼
┌─────────┐ ┌──────────┐
│ 单机模式 │ │ 集群模式  │
│ executor│ │ agent    │
│ (本地   │ │ runner   │
│  执行)  │ │ (远端    │
│         │ │  领取)   │
└────┬────┘ └────┬─────┘
     │           │
     ▼           ▼
  subprocess   subprocess
  .run()       .Popen()
     │           │
     └─────┬─────┘
           ▼
    训练完成 → 回收 metrics
           │
           ▼
    compare → 聚合对比 → 导出报告
```

---

## 3. 新增/修改文件清单

```
quudet-yolo-lab-backend/
  app/
    models/
      experiment_group.py     ← 新建  实验组 ORM
      job_record.py           ← 修改  扩展 11 个字段
      __init__.py             ← 修改
    schemas/
      experiment.py           ← 新建  实验组/Sweep Schema
      job.py                  ← 修改  补充实验字段
      __init__.py             ← 修改
    services/
      job_expand_service.py   ← 新建  任务展开 + Sweep 展开
      snapshot_service.py     ← 新建  快照生成
      experiment_compare.py   ← 新建  结果对比聚合
      yolo_runner.py          ← 修改  extra_args + seed
    api/routes/
      experiments.py          ← 新建  实验组 CRUD + compare + export
      dispatch.py             ← 修改  job-bundle 下载接口
    tasks/
      executor.py             ← 修改  集成 snapshot + 实验组状态回写
    agent/
      runner.py               ← 修改  bundle 下载 + 使用快照
    main.py                   ← 修改  注册 experiments 路由
    database.py               ← 修改  自动迁移逻辑
```

共 **7 个新文件 + 7 个修改文件**。

---

## 4. 数据模型

### 4.1 ExperimentGroup（新表 `experiment_groups`）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| name | str | 实验组名称，建议 `{dataset}_{topic}_{variant}_{date}` |
| description | str? | 描述 |
| hypothesis_id | str? | 关联假设 |
| gap_id | str? | 关联研究缺口 |
| paper_ids | JSON? | 关联论文 |
| dataset_name | str? | 数据集名 |
| primary_metric | str? | 主指标，默认 `metrics/mAP50-95(B)` |
| status | str | PENDING / RUNNING / PARTIAL / SUCCESS / FAILED |
| comparison_cache | JSON? | 缓存最近一次对比结果 |

### 4.2 JobRecord（扩展 `jobs` 表）

原有 19 个字段保持不变，新增 11 个字段（全部可为空，兼容旧数据）：

| 新字段 | 类型 | 说明 |
|--------|------|------|
| experiment_group_id | UUID? | 所属实验组 |
| run_role | str? | baseline / variant / ablation / repeat / sweep |
| seed | int? | 随机种子 |
| run_index | int? | 组内序号 |
| spec_snapshot_path | str? | 任务规格快照路径 |
| resolved_command_path | str? | 最终命令快照路径 |
| model_snapshot_path | str? | 模型配置快照路径 |
| data_snapshot_path | str? | 数据配置快照路径 |
| code_snapshot_path | str? | 代码快照路径 |
| env_snapshot_path | str? | 环境信息快照路径 |
| artifacts_manifest_path | str? | 产物清单路径 |

---

## 5. 核心流程

### 5.1 创建实验组

```
POST /api/v1/experiments
  → 校验 (runs 和 sweep 二选一)
  → 创建 ExperimentGroup 记录
  → expand_runs() 或 expand_sweep()
  → 批量创建 JobRecord（自动补 run_index, seed, project, name）
  → 本地模式：逐个 enqueue_or_run_job()
  → 返回 group + 关联 jobs
```

### 5.2 任务展开

**显式 runs**：每个 `ExperimentRunCreate` 可指定单个 seed 或 `seeds: [42,43,44]` 自动展开。

```
输入: [baseline(seed=42), variant(seeds=[43,44,45])]
输出: 4 个 jobs (1 baseline + 3 variant)
```

**Sweep 网格**：对 grid 做笛卡尔积 × seeds。

```
输入: grid={lr0:[0.001,0.01], batch:[16,32]}, seeds=[42,43]
输出: 2×2×2 = 8 个 jobs
命名: sweep_lr0-0p001_batch-16_seed42_00
```

### 5.3 任务执行（单机模式）

```
execute_job(job_id):
  1. mark status=RUNNING
  2. build_command()     ← 含 seed + extra_args
  3. create_job_snapshot()  ← 🆕 拍快照
     ├ spec_snapshot.json
     ├ resolved_command.txt
     ├ model_snapshot.yaml
     ├ data_snapshot.yaml
     └ env_snapshot.json
  4. subprocess.run()
     ├ 后台线程监控 progress / metrics
     └ 流式写入 run.log
  5. 完成:
     ├ write_artifacts_manifest()  ← 🆕 产物清单
     └ _update_experiment_group_status()  ← 🆕 回写组状态
```

### 5.4 任务执行（集群模式）

```
agent/runner.py:
  1. register_node() → heartbeat 循环
  2. claim_next_job()
  3. execute_remote_job():
     a. _download_job_bundle()     ← 🆕 下载快照包
        → 用 snapshot yaml 覆盖 payload
     b. _download_dataset_bundle() ← 已有
     c. build_command()
     d. subprocess.Popen() + 流式回传 log/metrics/progress
```

### 5.5 结果对比

```
GET /api/v1/experiments/{group_id}/compare

compare_experiment_group():
  1. 查所有关联 JobRecord
  2. 从 metrics_cache 提取每个 run 的最终指标
  3. 按 run_role 分组
  4. 计算 aggregates: mean / std / min / max / n
  5. 计算 delta_vs_baseline: absolute / relative_percent
  6. 找出 best_run
  7. 结果缓存到 comparison_cache
```

---

## 6. API 参考

### 6.1 实验组

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/experiments` | 创建实验组（runs 或 sweep） |
| GET | `/api/v1/experiments` | 列出当前用户的实验组 |
| GET | `/api/v1/experiments/{id}` | 实验组详情（含关联 jobs） |
| GET | `/api/v1/experiments/{id}/compare` | 结构化对比结果（JSON） |
| GET | `/api/v1/experiments/{id}/export.csv` | 导出 CSV |
| GET | `/api/v1/experiments/{id}/export.md` | 导出 Markdown 报告 |

### 6.2 集群调度（新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/dispatch/job-bundle/{job_id}` | 下载任务快照包（zip） |

### 6.3 原有 jobs API

全部保留，不受影响。

---

## 7. 使用示例

### 7.1 创建对比实验

```bash
curl -X POST http://localhost:8000/api/v1/experiments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "visdrone_neck_ablation_20260701",
    "description": "Baseline vs neck variant on VisDrone",
    "hypothesis_id": "h1",
    "gap_id": "gap02",
    "dataset_name": "visdrone",
    "primary_metric": "metrics/mAP50-95(B)",
    "runs": [
      {
        "role": "baseline",
        "seed": 42,
        "payload": {
          "model": "yolo11n.pt",
          "data": "visdrone.yaml",
          "epochs": "100",
          "batch": "16"
        }
      },
      {
        "role": "variant",
        "seeds": [42, 43, 44],
        "payload": {
          "model": "yolo11n-neck-v2.yaml",
          "data": "visdrone.yaml",
          "epochs": "100",
          "batch": "16"
        }
      }
    ]
  }'
```

### 7.2 创建超参数 Sweep

```bash
curl -X POST http://localhost:8000/api/v1/experiments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "lr_batch_sweep_20260701",
    "dataset_name": "visdrone",
    "primary_metric": "metrics/mAP50-95(B)",
    "sweep": {
      "grid": {
        "lr0": [0.001, 0.005, 0.01],
        "batch": [16, 32]
      },
      "seeds": [42, 43],
      "base_payload": {
        "model": "yolo11n.pt",
        "data": "visdrone.yaml",
        "epochs": "100"
      }
    }
  }'
```

### 7.3 获取对比结果

```bash
curl http://localhost:8000/api/v1/experiments/{group_id}/compare
```

返回结构：

```json
{
  "group_id": "...",
  "primary_metric": "metrics/mAP50-95(B)",
  "runs": [
    {
      "job_id": "...",
      "run_role": "baseline",
      "seed": 42,
      "status": "SUCCESS",
      "final_metrics": {"metrics/mAP50-95(B)": 0.45}
    }
  ],
  "aggregates": {
    "baseline": {"mean": 0.445, "std": 0.005, "n": 2},
    "variant":  {"mean": 0.475, "std": 0.005, "n": 3}
  },
  "delta_vs_baseline": {
    "variant": {"absolute": 0.03, "relative_percent": 6.74}
  },
  "best_run_id": "...",
  "summary_text": "Experiment Comparison Summary\n..."
}
```

---

## 8. extra_args 安全规则

通过 `payload.extra_args` 传递 YOLO 支持但未在白名单中的参数：

| 规则 | 示例 |
|------|------|
| ✅ 允许标量 | `{"patience": 50, "deterministic": true}` |
| ❌ 阻止 blocklist 中的 key | `model`, `data`, `epochs` 等已有参数 |
| ❌ 阻止含 shell 元字符的 key | `|`, `;`, `$`, 空格等 |
| ❌ 阻止非标量值 | `[1,2,3]`, `{"nested": true}` |
| ❌ 阻止危险 key 名 | `shell`, `command`, `cmd` |

---

## 9. 快照目录结构

每个任务执行后，`<artifacts>/<job_id>/` 下：

```
<job_id>/
  run.log
  results.csv
  snapshot/
    spec_snapshot.json       # 任务规格：payload + role + seed + 时间戳
    resolved_command.txt     # 展开后的完整 CLI 命令
    model_snapshot.yaml      # 模型配置副本
    data_snapshot.yaml       # 数据集配置副本
    env_snapshot.json        # Python/torch/ultralytics 版本 + GPU
    artifacts_manifest.json  # 所有产物文件的 inventory
```

---

## 10. 数据库迁移

`database.py` 的 `_ensure_experiment_columns()` 在启动时自动检测并补全新表和字段，兼容现有数据库无需手动迁移。

所有新字段 `nullable=True`，旧 job 继续正常工作。

---

## 11. 兼容性保证

- ✅ 旧的 `POST /api/v1/jobs` 完全不受影响
- ✅ 旧的 `GET /api/v1/jobs/*` 返回格式不变
- ✅ 不创建 experiment group 也能继续单任务训练
- ✅ 旧前端不报错
- ✅ 所有新字段允许空值
- ✅ snapshot 创建失败不阻塞任务执行
- ✅ job bundle 下载失败不阻塞远端任务

---

## 12. 命名规范

系统自动生成的命名：

| 类型 | 格式 | 示例 |
|------|------|------|
| Group | `{dataset}_{topic}_{variant}_{date}` | `visdrone_gap02_neck_ablation_20260701` |
| Run | `{role}_seed{seed}_{index}` | `baseline_seed42_00` |
| Sweep | `sweep_{param-val}_seed{seed}_{index}` | `sweep_lr0-0p001_batch-16_seed42_00` |
