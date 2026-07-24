> **状态: 已过期** — 当前执行路径已切换为统一节点调度（PostgreSQL + Redis + agent）。本文档保留仅作历史参考。
>

# quudet metrics 提取修复计划

日期：2026-07-07  
适用对象：后续接手修复 `quudet` 指标提取问题的 AI / 开发者  
目标：解决当前 `runs[*].metrics = None`、`aggregates.mean = null`、`delta_vs_baseline = null` 的核心问题，让 `quudet` 真正把已经跑出来的 YOLO 结果转成结构化科研结果。  

关联文档：

- [quudet_结果层修复说明_2026-07-07.md](D:/Developer/quudet/docs/quudet_结果层修复说明_2026-07-07.md)
- `AI-Researcher-main/INTEGRATION_TEST_RESULTS_2026-07-03.md`

---

## 0. 一句话问题定义

当前系统不是“实验没跑出来”，而是：

```text
YOLO 实验已经真实完成并生成了 results.csv，
但 quudet 没有把这个文件里的指标正确读回到 JobRecord / compare 结果中。
```

结果就是：

- `runs[*].metrics = None`
- `aggregates.mean = null`
- `delta_vs_baseline = null`

所以当前最核心的问题，不是 compare 结构，不是 role，不是 group.status，  
而是：

```text
指标文件存在，但 quudet 没命中它的位置，或没成功解析它。
```

---

## 1. 当前状态总结

### 已经修好的

根据最新结果层修复说明和联调结果，当前这些已经成立：

1. experiment group 状态会正确变成 `SUCCESS`
2. compare 接口不再 500
3. role 不再丢
4. aggregates 的结构已经返回

也就是说：

```text
结果层的“骨架”已经修好了
```

### 还没修好的

真正没打通的是：

1. `runs[*].metrics`
2. `aggregates.mean/std`
3. `delta_vs_baseline`

所以当前状况可以概括成：

```text
结构通了，数值还没进来。
```

---

## 2. 根本原因判断

从联调结论看，最可能的根因是：

```text
YOLO 真实产生的 results.csv 文件路径，
没有被 quudet 的 metrics 解析逻辑正确命中。
```

更具体地说：

- 训练已经成功
- `results.csv` 确实存在
- 但是 `resolve_results_csv_for_train()` 没找到它

于是：

- `train_metrics.py` 没有读取到真实文件
- `metrics_cache` 没被正确填充
- compare 只能看到 status，拿不到数值

---

## 3. 修复思路总览

这次修复不要从 compare 层硬补逻辑，而应从下往上修：

### 正确顺序

1. **先确认 YOLO 真实输出路径**
2. **再修 `resolve_results_csv_for_train()`**
3. **再修 `train_metrics.py` 的解析与回写**
4. **最后重新验证 compare 是否自然恢复**

不要反过来。

因为 compare 层只是消费者，  
真正的数据源在：

```text
results.csv -> metrics extraction -> job metrics/cache -> compare
```

---

## 4. 第一阶段：确认真实文件路径

### 目标

先不用猜测，直接确认：

```text
当前 YOLO 训练成功后，results.csv 到底落在哪。
```

### 要做什么

对一组最近成功的 run：

1. 找到对应 experiment group
2. 找到成功的 job
3. 看它的实际运行目录
4. 确认 `results.csv` 的真实绝对路径

### 必须确认的内容

至少确认：

- run 目录根路径
- `project`
- `name`
- `results.csv`
- 是否还存在 `args.yaml`
- 是否存在 `best.pt / last.pt`

### 你要让接手 AI 重点核查的路径模式

联调里已经提示当前真实路径更像：

```text
runs/detect/{project}/{name}/results.csv
```

但修复时不要直接假设这一种路径，应该先实际确认。

### 验收标准

至少找到 1 个最近成功 run 的：

- 真实 `results.csv`
- 真实 `run.log`

并记录绝对路径。

---

## 5. 第二阶段：修 `resolve_results_csv_for_train()`

### 目标

让 quudet 在训练成功后，能稳定找到真实 `results.csv`。

### 当前问题

当前 resolver 大概率只按某一套理想路径找，  
而真实运行路径和它的假设不一致。

### 修复要求

`resolve_results_csv_for_train()` 不应该只查单一路径，  
而应该做：

#### 路径优先级查找

建议至少支持：

1. 已知显式 run_dir 下的 `results.csv`
2. `runs/detect/{project}/{name}/results.csv`
3. `runs/train/{project}/{name}/results.csv`
4. artifact/snapshot 中记录的路径
5. 最后可做一次受限 glob 查找

### 推荐实现原则

不要一上来就全局乱搜，优先：

- 明确路径
- 约定路径
- 受限 fallback

避免性能和误命中问题。

### 建议函数返回内容

不要只返回 path 或 None，建议返回：

```python
{
    "path": ".../results.csv",
    "source": "runs/detect/project/name",
    "exists": True
}
```

这样后续日志会更清楚。

### 验收标准

给定一个成功 job：

- resolver 必须返回正确 path
- `exists == True`
- 日志里能明确看到命中来源

---

## 6. 第三阶段：修 `train_metrics.py` 指标解析与回写

### 目标

找到文件以后，还必须真的把数字读进去。

### 需要确认的两件事

1. `results.csv` 的列名和 parser 假设是否一致
2. parser 读到结果后，回写到了哪里

### 重点检查

#### A. CSV 列名

不同 YOLO / ultralytics 版本的列名可能有差异，例如：

- `metrics/mAP50(B)`
- `metrics/mAP50-95(B)`
- `metrics/precision(B)`
- `metrics/recall(B)`

必须确认 parser 读取的列名和实际文件一致。

#### B. 读取策略

通常需要：

- 取最后一行
- 转成 dict
- 只保留数值列

#### C. 回写目标

必须确认这些位置至少一个被正确更新：

- `job.metrics_cache`
- `job.status` 相关 summary
- `compare` 消费所需字段

### 推荐修法

增加一个明确的解析函数，例如：

```python
def parse_yolo_results_csv(path: Path) -> dict[str, float]:
    ...
```

要求：

- 只负责 CSV -> metrics dict
- 不掺杂 DB 更新

然后再由上层负责：

- 写回 job 记录
- 刷新 compare 可用数据

### 验收标准

对于一个成功 run：

必须能拿到非空：

- `metrics/mAP50(B)` 或等效字段
- `metrics/mAP50-95(B)` 或等效字段

而不是 `None`。

---

## 7. 第四阶段：确保 compare 自然恢复

### 目标

在 metrics 正常回写后，compare 不需要额外硬补，也能自然得到：

- run-level metrics
- aggregates
- delta_vs_baseline

### 要检查什么

#### A. `runs[*].metrics`

现在必须不是 `None`

#### B. `aggregates`

对于 baseline / variant：

- `mean`
- `std`
- `n`

都应该有真实数值

#### C. `delta_vs_baseline`

如果 baseline 和 variant 都有有效指标：

- `absolute`
- `relative_percent`

都应该出现

### 通过标准

至少一个 baseline + 一个 variant 成功时：

```text
runs[*].metrics != None
aggregates.mean != null
delta_vs_baseline != null
```

---

## 8. 建议具体排查文件

优先检查这些文件：

### 第一优先级

- `app/services/train_metrics.py`
- `app/tasks/executor.py`
- `app/services/yolo_runner.py`

### 第二优先级

- `app/services/experiment_compare.py`
- `app/api/routes/experiments.py`

### 第三优先级

- `app/services/snapshot_service.py`

看是否已经有 run_dir / results 路径被保存，但没被消费。

---

## 9. 建议修复顺序

### Step 1

找一个成功 job，确认真实 `results.csv` 绝对路径

### Step 2

修 `resolve_results_csv_for_train()`

### Step 3

修 CSV parser，确保指标能读出来

### Step 4

确认 job 记录中 `metrics` 非空

### Step 5

重新调用 compare，确认 aggregates / delta 恢复

不要跳步。

---

## 10. 测试要求

### 最少需要 3 类测试

#### Test A：resolver 测试

输入：

- 模拟成功 run 的 project / name / run_dir

断言：

- resolver 命中真实 `results.csv`

#### Test B：CSV parser 测试

输入：

- 一个真实或构造的 `results.csv`

断言：

- 产出 metrics dict
- 主指标非空

#### Test C：compare 集成测试

输入：

- baseline 成功
- variant 成功

断言：

- `runs[*].metrics` 非空
- `aggregates` 非空
- `delta_vs_baseline` 非空

---

## 11. 最终验收标准

这轮修复只有满足下面全部条件才算完成：

1. 一个真实成功 run 的 `results.csv` 被正确命中
2. `runs[*].metrics` 不再是 `None`
3. `aggregates.mean/std` 有真实数值
4. `delta_vs_baseline` 有真实数值
5. `summary_text` 开始反映真实实验差异，而不只是“no metrics”

---

## 12. 一句话给接手 AI

当前 quudet 不是不会比较，而是：

```text
YOLO 的真实结果文件已经生成了，
但 quudet 没有把这个文件里的数字正确读回自己的结果对象。
```

所以你要修的核心不是 compare 表达层，而是：

```text
results.csv -> metrics extraction -> job metrics -> compare aggregation
```

这条链。

