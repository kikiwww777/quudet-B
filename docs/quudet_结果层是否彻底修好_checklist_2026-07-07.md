> **状态: 已过期** — 当前执行路径已切换为统一节点调度（PostgreSQL + Redis + agent）。本文档保留仅作历史参考。
>

# quudet 结果层是否彻底修好 Checklist

日期：2026-07-07  
适用对象：后续验证 `quudet` 结果层是否已经真正修好的 AI / 开发者  
目标：给出一套简单、直接、可执行的检查清单，判断当前 quudet 结果层是否已经从“结构可用”升级到“真实数值可用”。  

关联文档：

- [quudet_结果层修复说明_2026-07-07.md](D:/Developer/quudet/docs/quudet_结果层修复说明_2026-07-07.md)
- [quudet_metrics提取修复说明_2026-07-07.md](D:/Developer/quudet/docs/quudet_metrics提取修复说明_2026-07-07.md)
- [quudet_主指标key映射修复说明_2026-07-07.md](D:/Developer/quudet/docs/quudet_主指标key映射修复说明_2026-07-07.md)

---

## 0. 一句话判断标准

只有当下面这条链全部成立时，才算 quudet 结果层真的修好了：

```text
YOLO 跑完
  -> results.csv 被找到
  -> metrics_cache 非空
  -> compare 能识别 primary_metric
  -> aggregates 有真实数值
  -> delta_vs_baseline 有真实数值
  -> summary_text 不再说 no metrics
```

只修好其中一两段，不算彻底修好。

---

## 1. 第一层检查：实验本身真的跑完了吗

### 必须满足

- experiment group 进入终态：
  - `SUCCESS`
  - `PARTIAL`
  - `FAILED`

- 不应继续卡在：
  - `PENDING`
  - `RUNNING`

### 检查项

- [ ] group.status 正确
- [ ] runs 都有终态

### 如果没过

说明结果层还没到 metrics 阶段，先回去修状态流转。

---

## 2. 第二层检查：`results.csv` 真的被命中了吗

### 必须满足

对至少一个成功 run：

- 能找到真实 `results.csv`
- 解析使用的是这份真实文件

### 检查项

- [ ] 成功 run 的真实 `results.csv` 绝对路径已确认
- [ ] resolver 命中这份文件
- [ ] 不是误命中别的旧 run 文件

### 如果没过

说明 metrics 提取链路还没真正通。

---

## 3. 第三层检查：`runs[*].metrics` 是否非空

### 必须满足

对至少 baseline 和 variant 各一个成功 run：

- `runs[*].metrics != None`

### 检查项

- [ ] baseline run metrics 非空
- [ ] variant run metrics 非空
- [ ] metrics 中至少有主指标相关字段

### 最少应看到的典型 key

- `metrics/mAP50(B)`
- `metrics/mAP50-95(B)`
- `metrics/precision(B)`
- `metrics/recall(B)`

### 如果没过

说明：

- `results.csv -> metrics_cache`

这条链还没完全修好。

---

## 4. 第四层检查：主指标映射是否成功

### 必须满足

compare 层必须能把：

- `mAP@50`

映射到：

- `metrics/mAP50(B)`

或等价真实 key。

### 检查项

- [ ] `primary_metric` 非空
- [ ] `primary_metric_resolved` 非空
- [ ] `primary_metric_resolved` 在 run metrics keys 中真实存在

### 理想例子

```json
{
  "primary_metric": "mAP@50",
  "primary_metric_resolved": "metrics/mAP50(B)"
}
```

### 如果没过

说明：

- compare 还没有真正理解上游科研语义指标名

---

## 5. 第五层检查：aggregates 是否有真实数值

### 必须满足

如果 baseline 和 variant 至少各有一个成功且有 metrics 的 run：

- `aggregates.baseline.mean != null`
- `aggregates.variant.mean != null`

### 检查项

- [ ] baseline.mean 非空
- [ ] variant.mean 非空
- [ ] `n >= 1`

### 如果没过

说明：

不是 compare 结构问题，就是：

- 主指标没命中
- 或 run 级 metrics 仍然不完整

---

## 6. 第六层检查：`delta_vs_baseline` 是否有真实数值

### 必须满足

如果 baseline 和 variant 都有可用主指标：

- `absolute` 非空
- baseline mean ≠ 0 时 `relative_percent` 非空

### 检查项

- [ ] `delta_vs_baseline.absolute` 非空
- [ ] `delta_vs_baseline.relative_percent` 非空或明确 N/A

### 如果没过

说明：

- 聚合值还没真正建立起来

---

## 7. 第七层检查：`summary_text` 是否已经摆脱兜底文本

### 必须满足

`summary_text` 不应再只是：

- `No successful runs with valid metrics`
- `no metrics available`

而应该开始包含：

- baseline 分数
- variant 分数
- 提升/下降情况

### 检查项

- [ ] `summary_text` 非空
- [ ] `summary_text` 包含真实数值信息
- [ ] `summary_text` 不只是兜底错误说明

---

## 8. 第八层检查：AR 的 `RoundDecision` 是否开始消费真实数值

这是最终系统角度的验证。

### 必须满足

`RoundDecision.reason` 不应只说：

- “实验成功”
- “可以继续”

而应开始引用：

- baseline vs variant 差异
- 指标提升幅度
- 支持/不支持 hypothesis 的依据

### 检查项

- [ ] `RoundDecision.reason` 提到了指标
- [ ] `RoundDecision.next_action` 与数值结果一致

### 如果没过

说明：

- quudet 结果层可能已经修好
- 但 AR 还没真正用上结果

---

## 9. 最终判断方式

### 结果层“未修好”

只要出现下面任一情况，就不能说彻底修好了：

- [ ] group.status 还是异常
- [ ] `runs[*].metrics` 还是大量 `None`
- [ ] `primary_metric_resolved` 没有
- [ ] `aggregates.mean` 还是 `null`
- [ ] `delta_vs_baseline` 还是 `null`
- [ ] `summary_text` 还是 `no metrics available`

### 结果层“基本修好”

只有当下面全部成立时，才能说：

```text
结果层基本修好了
```

- [ ] group.status 正常
- [ ] baseline/variant 至少各有一个 run 拿到非空 metrics
- [ ] primary metric 映射成功
- [ ] aggregates 非空
- [ ] delta 非空
- [ ] summary_text 有真实比较信息

### 结果层“彻底修好”

在“基本修好”的基础上，再满足：

- [ ] 大部分成功 run 都能提到 metrics
- [ ] 部分失败 run 不影响其余成功 run 的聚合
- [ ] AR 的 RoundDecision 已真正消费 compare 数值

---

## 10. 一句话给接手 AI

不要只看：

- compare 接口是不是 200
- 返回结构是不是存在

真正要看的，是：

```text
compare 里有没有真实数字，
这些真实数字能不能被 AR 用来判断下一轮实验做什么。
```

