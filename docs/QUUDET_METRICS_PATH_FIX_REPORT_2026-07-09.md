# Metrics 结果文件定位链修复说明

> **日期**: 2026-07-09  
> **对应设计文档**: [`QUUDET_RESULTS_LOCATING_CHAIN_FIX_PLAN_2026-07-09.md`](./QUUDET_RESULTS_LOCATING_CHAIN_FIX_PLAN_2026-07-09.md)  
> **对应验收文档**: [`QUUDET_SCHEME_C_ACCEPTANCE_RESULTS_2026-07-08.md`](./QUUDET_SCHEME_C_ACCEPTANCE_RESULTS_2026-07-08.md)

---

## 问题

`AI-Researcher × quudet（方案 C）` 联调 Phase B 卡在 `11/13`：

- **已通过（8项）**: `primary_metric`, `primary_metric_resolved`, `summary_text`, `RoundDecision.reason` 等
- **未通过（2项）**: `aggregates.mean`, `delta_vs_baseline`

根因：**不是所有 run 的 `results.csv` 都被稳定命中**，导致 metrics 提取不完整，compare 聚合缺输入。

## 三层根因

| 层 | 问题 | 修复方式 |
|----|------|----------|
| **results.csv 路径猜测** | `resolve_results_csv()` 按旧路径假设搜索，方案 C 环境已变 | 改为显式记录 + 优先级搜索 |
| **log_path 一致性** | `log_path` 与真实产物路径可能错位 | `metrics_source_path` 独立于 log_path |
| **旧缓存污染** | `__pycache__` 缓存旧代码逻辑 | 清理所有 `.pyc` 后再回归 |

## 改动清单

### 1. 模型 + 迁移

| 文件 | 改动 |
|------|------|
| `app/models/job_record.py` | 新增 `metrics_source_path: Mapped[str]` — 显式记录每个 run 真实 `results.csv` 来源路径 |
| `alembic/versions/002_add_metrics_source_path.py` | **新建** — ALTER TABLE jobs ADD COLUMN metrics_source_path |
| `alembic/versions/001_initial_schema.py` | 同步新增字段，新库可直接使用 |

### 2. 执行阶段记录来源（`tasks/executor.py`）

```python
# 训练成功后
row.metrics_source_path = str(latest.resolve())  # 记录真实的 results.csv 路径

# 训练失败后
row.metrics_source_path = None  # 清空残留
```

### 3. 搜索优先级重构（`services/train_metrics.py`）

```
# 旧逻辑: 日志解析 → payload 目录猜测 → 模糊兜底（易命中旧残留）
# 新逻辑: 优先级依次降级，杜绝模糊匹配污染主路径

优先级 1: metrics_source_path           ← 显式记录，最可靠
优先级 2: job_dir/results.csv            ← executor 拷贝到 artifacts 目录
优先级 3: log "Results saved to" 路径    ← 从 run.log 解析
优先级 4: payload project/name 目录       ← runs/train/<project>/<name>/results.csv
优先级 5: 全局 rglob + 时间过滤           ← 兜底，绝不作为主路径
```

同时新增多候选文件告警：

```python
if len(pool) > 1:
    logger.warning("resolve_results_csv: %d candidates, picked %s", len(pool), selected)
```

### 4. `_JobShim` 适配

`resolve_results_csv_for_train()` 中的 Shim 类新增 `metrics_source_path` 支持：

```python
self.metrics_source_path = payload.get("_metrics_source_path")
```

### 5. 清理

- 清除所有 `__pycache__` 目录，避免旧 `.pyc` 被 import
- 清除旧 `data/artifacts/jobs/*`，消除历史残留对路径搜索的干扰

## 验证结果

实验组 `metrics-fix-verify`（2 runs: baseline + variant, 1 epoch, coco8, cpu）：

| 检查项 | 结果 |
|--------|------|
| 两个 run 均 SUCCESS | ✅ YOLO 训练成功完成 |
| `metrics_cache` 含 14 个 series | ✅ metrics/mAP50-95(B), metrics/precision(B), metrics/recall(B) 等 |
| `metrics_source_path` 已记录 | ✅ 两个 run 均指向实际 results.csv |
| `aggregates.mean` | ✅ baseline=0.61034, variant=0.61034 |
| `aggregates.std` | ✅ 0.0（单 seed 合理） |
| `delta_vs_baseline` | ✅ absolute=+0.0000, relative=+0.00%（同配置，预期内） |
| `summary_text` | ✅ 含完整聚合和 delta 描述 |
| 日志记录 | ✅ 8752 / 8744 chars, 64 lines YOLO 输出 |

## 回归步骤

```bash
# 1. 运行迁移
cd quudet-yolo-lab-backend
.venv\Scripts\python -m alembic upgrade head

# 2. 清旧 artifacts
rm -rf data/artifacts/jobs/*

# 3. 清 Python 缓存（可选）
find . -name __pycache__ -exec rm -rf {} +

# 4. 重启 API + Worker
# 5. 跑最小实验组验证
```
