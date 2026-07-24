> **状态: 已过期** — 当前执行路径已切换为统一节点调度（PostgreSQL + Redis + agent）。本文档保留仅作历史参考。
>

# quudet 主指标 key 映射修复说明

> 基于 `quudet_主指标key映射修复设计_2026-07-07.md` 的实现记录  
> 日期：2026-07-07

---

## 0. 一句话问题

```text
不是 metrics 没回来，是 key 名字对不上。
上游说 "mAP@50"，但 metrics_cache 里存的是 "metrics/mAP50(B)"。
```

---

## 1. 根因

| 角色 | 用的名字 | 例子 |
|------|---------|------|
| 上游（AR/ORD/用户） | 科研语义名 | `mAP@50`, `mAP@50:95`, `Precision` |
| YOLO parser（`parse_results_csv`） | YOLO 原始列名 | `metrics/mAP50(B)`, `metrics/mAP50-95(B)` |
| compare 层（`_get_primary_value`） | 直接用上游原文去 dict 里查 | `metrics.get("mAP@50")` → `None` |

所以 `runs[*].metrics` 有内容，`primary_metric` 也传了，但取值失败：
`aggregates.mean = null`，`delta_vs_baseline = null`。

---

## 2. 修复内容

`app/services/experiment_compare.py`：

### 2.1 `_normalise_metric_key()` — 标准化函数

```python
"mAP@50"      → "map50"       # @ 被移除
"mAP@50:95"   → "map50_95"    # : 变 _
"Precision"   → "precision"   # 小写化
"metrics/mAP50(B)" → "metrics/map50_b"
```

### 2.2 `METRIC_ALIASES` — 别名映射表

标准化名 → 可能的真实 YOLO key 列表：

| 标准化名 | 映射到 |
|---------|--------|
| `map50` | `metrics/mAP50(B)`, `mAP50(B)`, `mAP50` |
| `map50_95` | `metrics/mAP50-95(B)`, `mAP50-95(B)`, `mAP50-95` |
| `precision` | `metrics/precision(B)`, `precision(B)`, `precision` |
| `recall` | `metrics/recall(B)`, `recall(B)`, `recall` |
| `f1_score` | `metrics/f1(B)`, `f1(B)`, `f1` |
| `map50_95_small` | `metrics/mAP50-95(S)`, `mAP50-95(S)` |
| `map50_95_medium` | `metrics/mAP50-95(M)`, `mAP50-95(M)` |
| `map50_95_large` | `metrics/mAP50-95(L)`, `mAP50-95(L)` |

### 2.3 `_resolve_metric_key()` — 三阶段解析

```python
# 解析顺序：
# 1. 直接命中 → primary_metric 已经在 available_keys 中
# 2. 别名表 → 标准化后查 METRIC_ALIASES，返回第一个存在的 key
# 3. 模糊匹配 → 遍历所有可用 key，标准化后比较
```

### 2.4 `_get_primary_value()` 重写

```python
# 旧的
metrics.get(primary_metric)

# 新的
resolved = _resolve_metric_key(primary_metric, set(metrics.keys()))
return metrics.get(resolved)
```

### 2.5 返回值新增 `primary_metric_resolved`

```json
{
  "primary_metric": "mAP@50",
  "primary_metric_resolved": "metrics/mAP50(B)",  ← 新增
  ...
}
```

### 2.6 Schema 调整

`ExperimentComparisonRead` 新增 `primary_metric_resolved: str | None = None`。

---

## 3. 改动文件

| 文件 | 改动 |
|------|------|
| `app/services/experiment_compare.py` | 新增 `_normalise_metric_key()`、`_METRIC_ALIASES`、`_resolve_metric_key()`；重写 `_get_primary_value()`；返回值增加 `primary_metric_resolved` |
| `app/schemas/experiment.py` | `ExperimentComparisonRead` 新增 `primary_metric_resolved` 字段 |

---

## 4. 验收测试

```text
normalise("mAP@50")      → "map50"         ✅
normalise("mAP@50:95")   → "map50_95"      ✅
normalise("Precision")   → "precision"     ✅
normalise("Recall")      → "recall"        ✅

resolve("mAP@50", yolo_keys)        → "metrics/mAP50(B)"   ✅
resolve("mAP@50:95", yolo_keys)     → "metrics/mAP50-95(B)" ✅
resolve("Precision", yolo_keys)     → "metrics/precision(B)" ✅
resolve("Recall", yolo_keys)        → "metrics/recall(B)"   ✅
resolve("metrics/mAP50(B)", yolo)   → direct hit            ✅
resolve("AP_small", yolo)           → None (正确拒绝不存在的指标) ✅
```
