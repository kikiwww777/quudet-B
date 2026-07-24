# quudet metrics 路径命中修复方案

日期：2026-07-09  
适用对象：后续继续修复 `quudet` 结果层的开发者 / AI  
目标：解决方案 C 联调中 `aggregates.mean`（聚合均值）和 `delta_vs_baseline`（相对基线差值）缺失的问题，根因聚焦在 `resolve_results_csv_for_train()` 对部分 run 的 `results.csv`（结果文件）命中不完整。

关联文档：

- [SCHEME_C_INTEGRATION_STATUS_2026-07-09.md](../AI-Researcher-main/SCHEME_C_INTEGRATION_STATUS_2026-07-09.md)
- [QUUDET_SCHEME_C_ACCEPTANCE_RESULTS_2026-07-08.md](./QUUDET_SCHEME_C_ACCEPTANCE_RESULTS_2026-07-08.md)
- [AI_RESEARCHER_QUUDET_SCHEME_C_INTEGRATION_ACCEPTANCE_PLAN_2026-07-08.md](./AI_RESEARCHER_QUUDET_SCHEME_C_INTEGRATION_ACCEPTANCE_PLAN_2026-07-08.md)

---

## 0. 一句话问题定义

当前方案 C 联调并没有卡在：

- `AI-Researcher` 提交失败
- `quudet` 执行失败
- compare 接口不存在

而是卡在：

```text
实验实际已经跑完，
但 quudet 没有稳定找到每个 run 对应的 results.csv，
导致 metrics 提取不完整，
进而使 aggregates.mean 和 delta_vs_baseline 为空。
```

---

## 1. 当前现象

根据 `SCHEME_C_INTEGRATION_STATUS_2026-07-09.md`，当前 Phase B 的状态是：

### 已通过

1. `primary_metric = AP_small`
2. `primary_metric_resolved = metrics/mAP50(B)`
3. `summary_text` 非空
4. `RoundDecision.reason` 能引用真实数值

### 未通过

1. `aggregates.mean`
2. `delta_vs_baseline`

### 这意味着什么

这说明：

- compare（结果比较）主流程已经跑起来了
- 主指标映射已经生效了
- 但 run 级别的 metrics（指标）并没有对所有 run 都成功命中

换句话说，问题已经缩小到：

**不是“完全拿不到结果”，而是“部分 run 的结果文件没被稳定识别”。**

---

## 2. 根因判断

当前最可能的根因是：

### 2.1 `results.csv` 路径推断过于依赖固定模式

当前逻辑大概率依赖以下信息去猜测 `results.csv`：

1. `payload`（任务载荷）里的 `project / name / data`
2. `work_dir`（工作目录）
3. `job_dir`（任务产物目录）
4. `started_at`（开始时间）
5. `job_type=train`

但在方案 C 新底座下，真实执行路径已经更复杂：

- worker（工作进程）跑在新的执行环境里
- `yolo.exe` 路径被替换过
- `build_command()` 走的是 Python entrypoint
- artifacts（实验产物）路径和训练输出路径不一定完全一致
- `log_path`（日志路径）与真实 `results.csv` 所在目录可能不再严格同源

因此，如果 `resolve_results_csv_for_train()` 仍然按旧的目录规则猜路径，就容易漏掉部分 run。 

### 2.2 日志路径与产物路径不一致

文档已经明确提到：

> agent 日志路径不一致 — `log_path` 指向的 artifacts 目录与实际写入位置不符

这意味着现有代码如果用：

- `log_path`
- `job_dir`
- `results saved to ...` 日志提示

去反推训练结果目录，可能会发生错位。 

### 2.3 历史 artifacts 残留干扰搜索

如果 `artifacts` 目录中残留了大量旧 run，
并且路径搜索逻辑采用“最近修改时间 + 模糊匹配”方式，
就可能出现：

1. 命中旧实验的 `results.csv`
2. 命中不到当前 run 的 `results.csv`
3. 某些 run 成功、某些 run 为空

这与当前“部分 run metrics 为空”的症状高度一致。 

---

## 3. 当前修复目标

这次修复不需要重写 compare 层，也不需要重写 AI-Researcher。 

只需要把目标收缩成下面三点：

### 目标 1

让每个 `train` run 都能稳定定位自己的 `results.csv`。

### 目标 2

让 metrics 提取与日志路径、产物路径解耦，不再过度依赖历史旧约定。

### 目标 3

让 compare 层在所有 run 都有 metrics 时，自动恢复：

1. `aggregates.mean`
2. `delta_vs_baseline`
3. 更稳定的 `summary_text`

---

## 4. 建议修复方向

### 4.1 优先方案：显式记录 `results.csv` 路径

最稳的方案不是“继续猜”，而是：

**在任务执行完成时，把实际命中的 `results.csv` 路径显式写回 JobRecord。**

例如新增一个字段或等价持久化信息：

- `metrics_source_path`

这样后续 compare 根本不需要再复杂推断。 

#### 好处

1. 不再依赖目录猜测
2. 不怕 worker 路径变化
3. 不怕 log_path 偏移
4. 最利于后续调试

### 4.2 次优方案：强化 `resolve_results_csv_for_train()` 搜索顺序

如果暂时不想改 schema（数据结构），至少应重构搜索顺序：

#### 推荐搜索优先级

1. **先查 job 专属 artifacts 目录**
   - 例如 `data/artifacts/jobs/<job_id>/...`

2. **再查日志中明确记录的 Results saved to 路径**
   - 只要日志能解析到，就优先使用该路径

3. **再查 payload/project/name 对应路径**
   - 例如 `runs/train/<project>/<name>/results.csv`

4. **最后才做全局模糊搜索 + 时间过滤**
   - 这是兜底，不应成为主路径

### 4.3 对每个 run 做“单任务唯一命中”校验

当前逻辑很可能是“只要找到一个看起来像的 results.csv 就算了”。

更稳的策略应该是：

1. 搜索结果文件
2. 打印候选列表
3. 判断是否唯一命中
4. 如果不唯一，明确记录告警而不是静默选错

这样至少不会在 compare 层出现“悄悄吃错 run”的情况。 

### 4.4 回归前先清理历史 artifacts

在修复代码前后，都建议：

1. 清空旧 `artifacts`
2. 只保留本轮联调新生成的 run
3. 重新跑 Phase B

原因很简单：

如果历史残留不清，路径命中问题会持续被旧数据污染，导致你无法判断修复是否真的生效。 

---

## 5. 推荐修改点

下面是最值得优先看的代码位置：

### 5.1 `resolve_results_csv_for_train()`

这是当前主修点。 

重点检查：

1. 搜索顺序
2. 是否依赖旧路径假设
3. 是否用 `log_path` 反推目录
4. 是否用修改时间做兜底匹配
5. 是否可能误命中旧文件

### 5.2 `execute_job()`

这是最适合记录“真实结果文件位置”的地方。 

重点检查：

1. YOLO 训练结束后是否能拿到真实输出目录
2. 是否已经存在“Results saved to ...”提示
3. 是否能在此时直接把 `results.csv` 的真实路径固化下来

### 5.3 `job_logs()` / ArtifactStore 路径

重点检查：

1. `log_path` 是否始终和当前 job 一一对应
2. 日志实际写入位置与数据库记录是否一致
3. compare 逻辑是否不必要地依赖了日志目录结构

---

## 6. 推荐回归流程

修完后，不要直接跳到多轮联调，先按这个顺序回归：

### Step 1

清空本轮无关的 `artifacts` 和旧测试残留。 

### Step 2

只跑一个最小 `ExperimentGroup`（实验组），例如：

- 2 baseline
- 1 variant

### Step 3

对 3 个 job 分别检查：

1. `results.csv` 是否存在
2. metrics 是否被写回
3. compare 中 run 级结果是否全非空

### Step 4

确认 compare 自动恢复：

1. `aggregates.mean`
2. `delta_vs_baseline`

### Step 5

再回归 `AI-Researcher` 的 Phase B 联调。 

只有这时通过，才能继续推进：

- `stronger_baseline`
- `ablation`
- `repeat`
- while 循环

---

## 7. 验收标准

这次修复算成功，至少要满足：

1. 每个 run 都能稳定提取 metrics
2. `aggregates.mean` 非空
3. `delta_vs_baseline` 非空
4. `RoundDecision.reason` 继续引用真实数值
5. Phase B 从 `11/13` 升级到完整通过

如果只做到“某些 run 偶尔有 metrics”，不算修复完成。 

---

## 8. 一句话给接手开发者

当前阻塞点不是大架构，也不是 AI-Researcher，
而是：

```text
quudet 没有稳定把每个 train run 的 results.csv 命中并转成 metrics，
导致 compare 层缺少完整聚合输入。
```

这次最有价值的修复不是继续改上游，
而是：

```text
让 results.csv 的定位从“猜路径”升级成“显式记录真实来源”或至少“强约束唯一命中”。
```
