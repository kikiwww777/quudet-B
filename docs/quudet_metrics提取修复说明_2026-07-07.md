> **状态: 已过期** — 当前执行路径已切换为统一节点调度（PostgreSQL + Redis + agent）。本文档保留仅作历史参考。
>

# quudet metrics 提取修复说明

> 基于 `quudet_metrics_extraction_fix_plan_2026-07-07.md` 的实现记录  
> 日期：2026-07-07

---

## 0. 一句话问题

```text
YOLO 已经跑完并生成了 results.csv，
但 quudet 没找到这个文件，也没把里面的数字读回到 metrics_cache，
所以 compare 看到的是 runs[*].metrics = None, aggregates.mean = null。
```

---

## 1. 根因

### 根因 A：`build_results_csv_candidates` 路径覆盖不全

`train_metrics.py` 的 `build_results_csv_candidates()` 函数只搜索 `runs/train/` 子目录下的 `results.csv`，但 YOLO 实际输出可能因 task 参数不同落到其他子目录（`runs/detect/`、`runs/val/` 等）。

具体来说：

| 场景 | 搜索路径 | 实际路径 | 命中？ |
|------|---------|---------|-------|
| train job, 无显式 project | `runs/train/{project}/{name}/` | `runs/train/{project}/{name}/` | ✅ |
| detect job | `runs/train/...`（硬编码） | `runs/detect/...` | ❌ |
| val job | `runs/train/...`（硬编码） | `runs/val/...` | ❌ |

### 根因 B：executor post-run 只复制文件，不解析 metrics_cache

`executor.py` 的 post-run 处理在复制 `results.csv` 到 `job_dir/` 后，没有调用 `parse_results_csv()` 把数值写到 `job.metrics_cache`。

监控线程（`monitor_progress`）可能因时间窗口未捕获到最后一个 epoch 的数据就直接被停止，导致 `metrics_cache` 在终态仍为 None。

### 根因 C：Agent 侧的 `_metrics_for_job` 也不传 job_type

`resolve_results_csv_for_train` 的 `_JobShim` 不包含 `job_type` 字段，导致 agent 端的 resolver 同样路径搜索受限。

---

## 2. 修复内容

### 修复 A：`train_metrics.py` — 完整重写路径搜索

#### 动态 task 子目录

```python
# 旧：只查 runs/train/
task_subdirs = ["train"]

# 新：先查 job_type，再 fallback 到 train/detect/val
task_subdirs = [job.job_type, "train", "detect", "val"]
# 去重后依次搜索
```

#### 路径标准化（防双重嵌套）

```python
# project = "runs/train/visdrone_ablation"
# bare_project → "visdrone_ablation"  ← 标准化去掉 runs/{task}/ 前缀
cands.append(work_dir / "runs" / task / bare_project / name / "results.csv")
# 同时保留原始 project 路径（可能已是完整路径）
cands.append(work_dir / project / name / "results.csv")
```

防止 `runs/train/runs/train/xxx/` 的双重嵌套问题。

#### 最终 rglob 兜底搜索

当所有已知路径都找不到时，在 `runs/` 目录下做一次受限递归搜索（最多 500 个文件，只查 mtime 在任务开始后的）：

```python
_last_resort = _rglob_fallback(work_dir, job.started_at)
# 只在 runs/ 下搜索，不全局乱搜
```

路径优先级：

1. `job_dir/results.csv`（artifact 目录的已拷贝文件）
2. `resolved_command_path` 快照中记录的路径
3. `runs/{task}/{bare_project}/{name}/`（标准化后的路径）
4. `runs/{task}/{project}/{name}/`（原始 project 路径）
5. `runs/{task}/{name}/`（仅 name 匹配）
6. legacy `runs/detect/runs/train/` 嵌套路径
7. **受限 rglob 兜底**（`runs/*/results.csv`，最多 500 条）

### 修复 B：`executor.py` — post-run 显式回写 metrics_cache

```python
# 旧的：只复制文件
dest.write_bytes(latest.read_bytes())

# 新的：复制文件 + 解析 + 写入 metrics_cache
dest.write_bytes(latest.read_bytes())
parsed = parse_results_csv(dest)
if parsed and parsed.get("series"):
    row.metrics_cache = parsed  # ← 新增
```

同时 fallback 搜索扩展到 `("train", "detect", "val")` 三个子目录：

```python
for task_sub in ("train", "detect", "val"):
    latest = _find_latest_results_file(work_dir / "runs" / task_sub, row.started_at)
    if latest:
        break
```

### 修复 C：`_JobShim` 增加 `job_type` 字段

```python
class _JobShim:
    def __init__(self):
        self.job_type = job_type  # ← 新增
```

Agent 调用 `resolve_results_csv_for_train` 时传入 `job_type` 参数：

```python
csv_path = resolve_results_csv_for_train(
    payload=...,
    job_type=str(job.get("job_type") or ""),  # ← 新增
)
```

---

## 3. 数据流修复前后对比

### 修复前

```
YOLO 跑完
  → results.csv 落在 runs/detect/{project}/{name}/
  → executor post-run 只查 runs/train/
  → 找不到 → 不 copy
  → metrics_cache 保持 None
  → compare 看到 None → aggregates.mean = null
```

### 修复后

```
YOLO 跑完
  → results.csv 落在 runs/{task}/{project}/{name}/
  → executor post-run 查 runs/train/ + runs/detect/ + runs/val/
  → 找到 → copy 到 job_dir/results.csv
  → parse_results_csv() → metrics_cache = {...}
  → compare 读到数值 → aggregates.mean = 0.45
```

---

## 4. 改动文件

| 文件 | 改动 |
|------|------|
| `app/services/train_metrics.py` | `build_results_csv_candidates` 重写为 task-aware 动态路径；`_JobShim` 增加 `job_type` |
| `app/tasks/executor.py` | post-run 显式 `parse_results_csv` + 回写 `metrics_cache`；fallback 搜索扩展到 detect/val |
| `app/agent/runner.py` | `_metrics_for_job` 调用时传入 `job_type` |

---

## 5. 验收测试

### 路径生成

| 输入 | 输出验证 | 结果 |
|------|---------|------|
| train job, project=visdrone_ablation | 含 `runs/train/...` + `runs/detect/...` + `runs/val/...` | ✅ |
| detect job | detect 优先，train/val fallback | ✅ |
| val job, name=val_exp | val 优先，train/detect fallback | ✅ |

### CSV 解析

```python
CSV: 10 epochs, mAP50-95 从 0.12 到 0.45
解析结果: last mAP50-95 = 0.45  ✅
          series 包含 mAP50(B), mAP50-95(B), precision(B), recall(B)  ✅
```
