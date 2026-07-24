> **状态: 已过期** — 当前执行路径已切换为统一节点调度（PostgreSQL + Redis + agent）。本文档保留仅作历史参考。
>

# quudet metrics 链路专项修复文档

日期：2026-07-07  
适用对象：后续必须彻底修好 `quudet` metrics 链路的 AI / 开发者  
目标：把 `results.csv -> metrics -> compare` 这条链彻底修通，不接受“结构有了但数值还是空”的状态。  

关联文档：

- [quudet_结果层修复说明_2026-07-07.md](D:/Developer/quudet/docs/quudet_结果层修复说明_2026-07-07.md)
- [quudet_metrics_extraction_fix_plan_2026-07-07.md](D:/Developer/quudet/docs/quudet_metrics_extraction_fix_plan_2026-07-07.md)
- `AI-Researcher-main/INTEGRATION_TEST_RESULTS_2026-07-03.md`

---

## 0. 先说人话：这条 metrics 链路是干什么的

这条链路不是“可有可无的小功能”，而是：

```text
把 YOLO 训练实际跑出来的结果数字，
真正送进科研实验比较层的唯一通道。
```

更白一点说：

### 实验跑完之后会发生什么

YOLO 训练结束后，会在结果目录里产生一个文件：

```text
results.csv
```

这个文件里有真正有用的实验数字，例如：

- mAP@50
- mAP@50:95
- Precision
- Recall
- loss 变化

这些数字不是“附属信息”，而是：

```text
后面所有科研判断的基础
```

比如：

- baseline 到底是多少分
- variant 有没有提升
- 提升了多少
- 是不是值得继续做下一轮实验
- 能不能写进论文结果部分

### 这条链路到底做什么

这条链路就是把：

```text
results.csv 里的真实数字
```

一步步变成：

```text
runs[*].metrics
-> aggregates
-> delta_vs_baseline
-> summary_text
```

最终给：

- `quudet compare`
- `AI-Researcher Loop Brain`
- 后续论文写作

使用。

所以如果这条链没修好，就会出现现在这种情况：

- 实验是真的跑完了
- 模型也是真的有结果了
- 但系统自己“看不到”这些结果

最后表现成：

- `runs[*].metrics = None`
- `aggregates.mean = null`
- `delta_vs_baseline = null`

也就是说：

```text
实验成功了，但平台像“没看懂实验结果”一样。
```

---

## 1. 当前问题的本质

当前不是 compare 逻辑不会算平均值，  
也不是 role 分组不见了，  
更不是实验没执行。

**当前问题的本质只有一句话：**

```text
YOLO 已经把结果写到了 results.csv，
但 quudet 没有成功把这个文件里的数字提取并写回自己的结构化结果对象。
```

换句话说：

- 输入文件有了
- 中间提取没成功
- 所以上层 compare 没数可算

---

## 2. 这次修复必须达到的目标

这次修复不是“尽量改一改”，而是必须达到下面这个结果：

```text
一个真实成功的 YOLO run
  -> quudet 能找到真实 results.csv
  -> 能解析出主指标和关键指标
  -> 写入 run metrics
  -> compare 能算出 baseline / variant 的 aggregates
  -> delta_vs_baseline 能返回真实数值
```

如果最后还是：

- `metrics=None`
- `aggregates.mean=null`
- `delta_vs_baseline=null`

那就视为：

```text
修复失败
```

---

## 3. 这条链路在代码里的结构

当前正确理解这条链路，应该是：

```text
YOLO 训练执行
  -> results.csv 落盘
  -> 路径解析（找到 results.csv）
  -> CSV 解析（读出 mAP/Precision/Recall）
  -> 回写到 job/run 结构
  -> compare 聚合 baseline / variant
  -> 生成 aggregates / delta / summary
```

所以这个链路天然分成 4 段：

### 第 1 段：文件生成

YOLO 训练本身是否真的生成了 `results.csv`

### 第 2 段：文件定位

平台是否真的知道 `results.csv` 在哪里

### 第 3 段：文件解析

平台是否真的把 csv 里的数读出来了

### 第 4 段：结果聚合

平台是否真的把这些数字用于 compare

现在第 1 段没问题，  
当前主要问题在第 2 段和第 3 段，  
第 4 段只是跟着一起失血。

---

## 4. 这次修复的原则

### 原则 1

```text
先确认真实文件路径，再改代码
```

不能凭猜测改。

### 原则 2

```text
先修“找文件”，再修“读数字”，最后才看 compare
```

### 原则 3

```text
不要在 compare 层硬凑假数字
```

如果 metrics 没进来，就回头修链路，不要在上层伪造。

### 原则 4

```text
用真实成功 run 回归验证，不用只跑单元测试自我安慰
```

---

## 5. 第一阶段：确认真实文件路径（必须先做）

### 目标

确认真实成功实验的 `results.csv` 到底在哪里。

### 为什么必须先做

因为如果路径猜错，后面所有修复都可能是错方向。

### 具体要求

找一个最近真实成功的 job，确认：

1. 它的 `project`
2. 它的 `name`
3. 它的 run 目录
4. `results.csv` 绝对路径
5. 同目录下是否有：
   - `args.yaml`
   - `weights/best.pt`
   - `weights/last.pt`

### 必须输出的核查结论

至少记录成这种形式：

```text
job_id = xxx
project = xxx
name = xxx
results_csv = D:\...\runs\detect\{project}\{name}\results.csv
exists = True
```

### 当前已知高概率路径

根据现有联调反馈，很可能是：

```text
runs/detect/{project}/{name}/results.csv
```

但修复时必须用真实 run 再确认。

---

## 6. 第二阶段：修结果文件定位逻辑

### 目标

让平台稳定找到 `results.csv`。

### 当前最可能的问题

当前 `resolve_results_csv_for_train()` 的路径构造假设和真实 YOLO 输出路径不一致。

### 重点文件

- `app/services/train_metrics.py`
- 如有路径组装逻辑外置，再查：
  - `app/tasks/executor.py`
  - `app/services/yolo_runner.py`

### 修复要求

`resolve_results_csv_for_train()` 至少要支持：

1. 直接使用显式 run 目录
2. `runs/detect/{project}/{name}/results.csv`
3. `runs/train/{project}/{name}/results.csv`
4. 最后做一次受限 glob fallback

### 不能做的事

- 不能全盘乱 glob 整个磁盘
- 不能只赌一条路径

### 推荐返回值

不要只返回 `Path | None`，建议返回：

```python
{
    "path": "...",
    "exists": True,
    "source": "runs/detect/project/name"
}
```

这样调试和日志都更容易。

### 验收标准

对至少一个成功 run：

- resolver 找到正确 `results.csv`
- path 是真实存在的

---

## 7. 第三阶段：修 CSV 解析逻辑

### 目标

不只是找到文件，还要把数字读出来。

### 当前最常见风险

1. 列名和 parser 假设不一致
2. 取错行（比如取了空行，不是最后一行）
3. 读出来了，但没写回 job 结构

### 重点文件

- `app/services/train_metrics.py`

### 必须核查的内容

#### A. 真实列名

确认 `results.csv` 里实际列名，例如：

- `metrics/mAP50(B)`
- `metrics/mAP50-95(B)`
- `metrics/precision(B)`
- `metrics/recall(B)`

不要想当然。

#### B. 解析策略

通常应该：

1. 读 CSV
2. 取最后一行有效数据
3. 过滤出数值型关键指标
4. 生成标准 metrics dict

#### C. 指标标准化

建议统一输出：

```python
{
    "metrics/mAP50(B)": ...,
    "metrics/mAP50-95(B)": ...,
    "metrics/precision(B)": ...,
    "metrics/recall(B)": ...
}
```

这样 compare 层才容易消费。

### 验收标准

一个真实成功 run 的 metrics 必须不为空，  
至少能拿到主指标。

---

## 8. 第四阶段：确认回写链路

### 目标

确保解析出来的数字真的进入了平台的结构化对象。

### 重点问题

很多时候不是“没读到”，而是：

```text
读到了，但没写回 job / compare 消费的数据结构
```

### 重点检查

确认 metrics 最终进入了：

- `JobRecord.metrics_cache` 或等效字段
- compare 组装 `runs[*].metrics` 的来源

### 重点文件

- `app/tasks/executor.py`
- `app/services/train_metrics.py`
- `app/services/experiment_compare.py`

### 验收标准

真实 compare 返回中：

- `runs[*].metrics != None`

这一步必须看到，不然前面都白修。

---

## 9. 第五阶段：确认 compare 自然恢复

### 目标

在 metrics 写回成功后，compare 能自然给出真实科研结果。

### 必须检查

#### A. run 级结果

- `run_role`
- `seed`
- `status`
- `metrics`

#### B. aggregates

对于 baseline / variant：

- `mean`
- `std`
- `n`

不能再是 `null`

#### C. delta_vs_baseline

至少要有：

- `absolute`
- `relative_percent`

### 验收标准

如果 baseline 和 variant 都成功：

```text
aggregates.mean 必须有值
delta_vs_baseline 必须有值
```

---

## 10. 必须做的回归测试

这次修复不能只靠“看代码感觉对了”。

### Test 1：单个成功 run

验证：

- 找到真实 `results.csv`
- 解析 metrics
- 写回 `runs[*].metrics`

### Test 2：baseline + variant

验证：

- baseline metrics 有值
- variant metrics 有值
- compare aggregates 有值
- delta 有值

### Test 3：部分失败 group

验证：

- 成功 run 的 metrics 仍能聚合
- compare 不 500

---

## 11. 修复顺序（必须照做）

1. 找真实 `results.csv` 路径
2. 修 `resolve_results_csv_for_train()`
3. 修 `parse_yolo_results_csv()`
4. 确认 metrics 写回 run 结构
5. 重新跑 compare
6. 做回归测试

不要先去 patch compare。

---

## 12. 最终验收标准

这轮修复只有在下面条件都成立时才算完成：

1. 至少一个真实成功 run 的 `results.csv` 被正确命中
2. `runs[*].metrics` 不再是 `None`
3. `aggregates.mean/std` 有真实数值
4. `delta_vs_baseline` 有真实数值
5. `AI-Researcher` 不再只能基于“成功/失败”做决策，而能真正基于指标差异做决策

---

## 13. 一句话给接手 AI

当前不是 quudet “不会比较实验”，而是：

```text
YOLO 已经把结果算出来了，
但 quudet 还没把这些结果数字成功搬进自己的结构化 compare 对象里。
```

这次你要修的，就是这条搬运链：

```text
results.csv
  -> 路径命中
  -> 指标解析
  -> run metrics
  -> aggregates / delta
```

