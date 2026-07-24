> **状态: 已过期** — 当前执行路径已切换为统一节点调度（PostgreSQL + Redis + agent）。本文档保留仅作历史参考。
>

# quudet 主指标 key 映射修复设计

日期：2026-07-07  
适用对象：后续接手修复 `quudet` compare 主指标映射问题的 AI / 开发者  
目标：解决 `primary_metric` 与 `results.csv` 解析出来的真实 metrics key 不一致，导致 `aggregates.mean = null`、`delta_vs_baseline = null` 的问题。  

关联文档：

- [quudet_metrics提取修复说明_2026-07-07.md](D:/Developer/quudet/docs/quudet_metrics提取修复说明_2026-07-07.md)
- [quudet_metrics_chain_must_fix_2026-07-07.md](D:/Developer/quudet/docs/quudet_metrics_chain_must_fix_2026-07-07.md)
- `AI-Researcher-main/INTEGRATION_TEST_RESULTS_2026-07-03.md`

---

## 0. 一句话问题

当前很可能不是 `metrics` 完全没回来，而是：

```text
compare 在聚合时使用的 primary_metric 名字，
和 metrics_cache 里真实保存的 key 名字不一致，
导致虽然 metrics dict 已经存在，compare 还是取不到主指标值。
```

于是最后表现为：

- `runs[*].metrics` 里有内容
- 但 `_get_primary_value()` 取值失败
- `aggregates.mean = null`
- `delta_vs_baseline = null`

---

## 1. 根因说明（用人话）

### 1.1 上游给的是“人类可读指标名”

`AI-Researcher` / `ORD` 产出的 `primary_metric` 常常像：

- `mAP@50`
- `mAP@50:95`
- `Precision`
- `Recall`
- `AP_small`

这些名字对人类很友好，也符合科研叙述。

### 1.2 下游 parser 读出来的是“YOLO 原始列名”

`parse_results_csv()` 会把 `results.csv` 的列名原样保留，常见像：

- `metrics/mAP50(B)`
- `metrics/mAP50-95(B)`
- `metrics/precision(B)`
- `metrics/recall(B)`

也就是说，平台内部真实保存的 key 是：

```text
YOLO/Ultralytics 风格
```

而不是上游科研语义风格。

### 1.3 compare 现在怎么取值

当前 `experiment_compare.py` 用的是：

```python
metrics.get(primary_metric)
```

也就是拿上游的 `primary_metric` 原文，直接去 metrics dict 里查。

如果：

- 上游是 `mAP@50`
- 内部真实 key 是 `metrics/mAP50(B)`

那结果就是：

```python
None
```

然后：

- 这个 run 不参与聚合
- `aggregates` 为空
- `delta` 为空

所以 compare 看起来像“没数据”，但其实可能是：

```text
有数据，只是名字对不上。
```

---

## 2. 当前代码中的直接证据

### 2.1 parser 不做标准化

位置：

- `app/services/train_metrics.py`

函数：

- `parse_results_csv()`

行为：

- 读取 `results.csv`
- 用原始列名作为 metrics key
- 不做 alias 映射

这意味着 `metrics_cache` 中的 key 取决于 CSV 列名。

### 2.2 compare 直接按原文取主指标

位置：

- `app/services/experiment_compare.py`

函数：

- `_get_primary_value()`

当前逻辑：

```python
metrics.get(primary_metric)
```

这里没有任何：

- key 归一化
- 同义词映射
- 模糊匹配

### 2.3 上游 primary_metric 来自 experiment group

位置：

- `app/services/experiment_compare.py`

逻辑：

```python
primary_metric = group.primary_metric or "metrics/mAP50-95(B)"
```

这说明只要上游提交的是：

- `mAP@50`
- `AP_small`

而不是内部真实 key，compare 就可能直接取不到值。

---

## 3. 修复目标

这次修复的目标不是改 parser，而是让 compare 层能理解：

```text
科研语义指标名
```

和：

```text
YOLO 原始列名
```

之间的对应关系。

也就是说：

### 修复后应支持

- `mAP@50` → `metrics/mAP50(B)`
- `mAP@50:95` → `metrics/mAP50-95(B)`
- `Precision` → `metrics/precision(B)`
- `Recall` → `metrics/recall(B)`

如果以后有：

- `AP_small`
- `AP_medium`
- `AP_large`

也要支持相应映射（如果 metrics_cache 里确实存在对应字段）。

---

## 4. 推荐修复方案

我建议采用：

```text
主指标标准化 + alias 映射 + 安全 fallback
```

而不是只做字符串硬匹配。

### 4.1 新增统一标准化函数

建议在 `experiment_compare.py` 内部新增：

```python
def _normalize_metric_key(name: str) -> str:
    ...
```

作用：

- 去空格
- 统一大小写
- 统一 `@` / `:` / `-` 风格
- 形成标准化 key

例如：

- `mAP@50` -> `map50`
- `mAP@50:95` -> `map50_95`
- `Precision` -> `precision`
- `Recall` -> `recall`

### 4.2 新增 alias 映射表

建议新增：

```python
METRIC_KEY_ALIASES = {
    "map50": [
        "metrics/mAP50(B)",
        "mAP50",
        "map50",
    ],
    "map50_95": [
        "metrics/mAP50-95(B)",
        "mAP50-95",
        "map50_95",
    ],
    "precision": [
        "metrics/precision(B)",
        "precision",
    ],
    "recall": [
        "metrics/recall(B)",
        "recall",
    ],
}
```

以后上游给什么名字，只要先标准化，就能映射到内部真实 key 集合。

### 4.3 修改 `_get_primary_value()`

不要再直接：

```python
metrics.get(primary_metric)
```

建议改成：

1. 标准化 `primary_metric`
2. 找 alias 列表
3. 按顺序尝试这些 key
4. 命中第一个就返回
5. 如果都没命中，再做一次轻量 fallback：
   - 遍历 metrics keys，找标准化后相同的

### 4.4 可选：把“命中的真实 key”也记录下来

建议 compare 返回时可额外附带：

```json
{
  "primary_metric_requested": "mAP@50",
  "primary_metric_resolved": "metrics/mAP50(B)"
}
```

这样调试会方便很多。

---

## 5. 修复应放在哪一层

### 主修位置

- `app/services/experiment_compare.py`

这是最合理的，因为 compare 层最清楚：

- 上游请求的是哪个 metric
- 下游 run 里真实有哪些 metric key

### 不建议主修位置

#### 不建议把 parser 改成“只输出人类可读 key”

原因：

- parser 读的是原始 CSV
- 保留原始列名更接近事实
- 以后更容易兼容更多 Ultralytics 版本

#### 不建议要求上游永远传内部 key

原因：

- ORD / AR 更适合用科研语义指标名
- 上游不应被 YOLO 内部列名绑死

所以：

```text
映射责任应该主要放在 compare 层
```

---

## 6. 还要顺手确认的一个问题

即使 key 映射修好，仍要确认：

```text
metrics_cache 里确实已经有数据
```

因为如果 `runs[*].metrics` 本身还是空，  
再好的 alias 映射也没用。

所以这次修复前，建议先抽查一个成功 run 的：

- `metrics_cache`
- `series.keys()`

确认确实有类似：

- `metrics/mAP50(B)`
- `metrics/mAP50-95(B)`
- `metrics/precision(B)`
- `metrics/recall(B)`

如果连这些都没有，那主根因仍然在 metrics 提取链路，而不是 key 映射。

---

## 7. 修复步骤

### Step 1

打印一个成功 run 的 `metrics_cache.series.keys()`

### Step 2

新增 `_normalize_metric_key()`

### Step 3

新增 `METRIC_KEY_ALIASES`

### Step 4

重写 `_get_primary_value()`

### Step 5

重新跑 compare，检查：

- `aggregates.mean`
- `delta_vs_baseline`

### Step 6

如果仍为空，再排查：

- run 级 `metrics_cache`
- 某个 role 是否恰好所有 runs 都空

---

## 8. 验收标准

这次修复只有满足下面条件才算通过：

1. 对于已成功且已有 `metrics_cache` 的 baseline / variant runs：
   - `_get_primary_value()` 不再返回 `None`

2. compare 返回：
   - `aggregates.baseline.mean` 非空
   - `aggregates.variant.mean` 非空

3. compare 返回：
   - `delta_vs_baseline.absolute` 非空
   - `delta_vs_baseline.relative_percent` 非空（若 baseline mean ≠ 0）

4. `summary_text` 中开始出现真实数值差异，而不是 `no metrics available`

---

## 9. 一句话给接手 AI

这次你要修的不是：

```text
metrics 有没有被 parser 读出来
```

而是：

```text
上游说的“mAP@50 / Recall”这些科研语义指标名，
如何正确映射到 quudet 内部 metrics_cache 里真实的
"metrics/mAP50(B)" / "metrics/recall(B)" 这些 key。
```

也就是说：

```text
修的是 compare 层的主指标取值语义。
```

