# quudet 结果文件定位链修复方案

日期：2026-07-09  
适用对象：后续继续修复 `quudet` 方案 C 联调阻塞点的开发者 / AI  
目标：解决 `AI-Researcher × quudet（方案 C）` 联调中 `Phase B` 还卡在 `11/13` 的问题，重点不再只是 compare（比较层）本身，而是统一修复：

1. `results.csv`（训练结果文件）命中不完整
2. `log_path`（日志路径）与实际 artifacts（实验产物）目录不一致
3. 旧缓存导致 `_JobShim.resolved_command_path`（命令路径缓存字段）加载旧代码

关联文档：

- [SCHEME_C_INTEGRATION_STATUS_2026-07-09.md](../AI-Researcher-main/SCHEME_C_INTEGRATION_STATUS_2026-07-09.md)
- [QUUDET_SCHEME_C_ACCEPTANCE_RESULTS_2026-07-08.md](./QUUDET_SCHEME_C_ACCEPTANCE_RESULTS_2026-07-08.md)
- [AI_RESEARCHER_QUUDET_SCHEME_C_INTEGRATION_ACCEPTANCE_PLAN_2026-07-08.md](./AI_RESEARCHER_QUUDET_SCHEME_C_INTEGRATION_ACCEPTANCE_PLAN_2026-07-08.md)

---

## 0. 一句话问题定义

当前方案 C 联调并没有卡在：

- `AI-Researcher` 提交失败
- `quudet` API 起不来
- PostgreSQL / Redis 连不上
- compare 接口完全不可用

而是卡在：

```text
quudet 对部分 train run 的结果文件定位链不稳定，
导致 metrics 提取不完整，
进一步使 aggregates.mean 和 delta_vs_baseline 无法完整生成。
```

这个问题不是单点函数问题，而是下面三件事绑在一起：

1. `results.csv` 路径命中不完整
2. `log_path` 与实际 artifacts 写入目录不一致
3. 旧缓存让 agent / shim 继续加载旧执行路径逻辑

---

## 1. 当前真实状态

根据 `SCHEME_C_INTEGRATION_STATUS_2026-07-09.md`，当前状态应这样理解：

### 已经通过的

1. `Phase A`（接口与配置联调）已通过
2. `AI-Researcher` 首轮 `ExperimentSpec`（实验规格）能正常生成
3. `quudet` 新主路径能创建实验组并执行
4. `group_id` 正常返回
5. 实验能进入终态
6. `primary_metric`（主指标）和 `primary_metric_resolved`（解析后的主指标）已回流
7. `summary_text`（摘要文本）非空
8. `RoundDecision.reason`（轮次决策理由）能引用真实数值

### 还没通过的

1. `aggregates.mean`（聚合均值）
2. `delta_vs_baseline`（相对基线差值）

### 这说明什么

这说明：

- 主链已经不是“完全断的”
- compare（比较层）也不是完全坏的
- 当前问题集中在：**不是所有 run 的 metrics（指标）都被稳定提取到了**

---

## 2. 当前最可能的根因

### 2.1 `results.csv` 路径推断仍然依赖旧假设

当前 `resolve_results_csv_for_train()` 很可能仍然在依赖旧环境中的路径假设，例如：

1. 从 `payload.project / payload.name` 反推 `runs/train/...`
2. 从 `job_dir`（任务产物目录）去猜结果位置
3. 从 `started_at`（开始时间）做模糊时间筛选
4. 从旧的 CLI 约定中推断 YOLO 输出目录

但方案 C 环境已经变了：

- CLI entry point（命令行入口）被替换过
- `build_command()` 改成了 venv Python entrypoint
- worker（工作进程）执行环境与旧路径假设不完全一致
- artifacts 与真实训练输出目录不一定仍然严格同构

因此，单纯依赖“猜路径”的逻辑，已经不足以稳定命中所有 run 的 `results.csv`。

### 2.2 `log_path` 与实际产物目录不一致

当前文档明确列出：

> `agent 日志路径不一致 — log_path 指向的 artifacts 目录与实际写入位置不符`

这意味着：

- 如果结果文件定位逻辑依赖 `log_path`
- 或者依赖从日志位置去反推训练输出目录

那这条链就可能天然错位。

### 2.3 旧缓存仍在污染执行路径

文档里还明确列出：

> `_JobShim.resolved_command_path` — 之前修过但 agent 缓存导致旧代码加载

这说明不是所有进程都一定在执行你以为的新逻辑。

即使你修了：

- CLI wrapper
- build_command
- Python entrypoint

只要某个 agent / worker 还在吃旧 `__pycache__`（Python 缓存），它实际跑出来的路径和日志就仍然可能是旧的。

所以当前问题不能只当成“路径匹配小 bug”，而要当成：

**执行路径逻辑、日志路径记录和结果文件定位三层之间没有完全重新对齐。**

---

## 3. 这次修复的核心目标

这次不要扩大目标，不要顺手去重构 compare 全层，也不要去改 AI-Researcher。 

只聚焦三件事：

### 目标 1

让每个 `train` run 都能稳定定位到自己的真实 `results.csv`。

### 目标 2

让 `log_path` 与实际 artifacts 写入目录重新一一对应。

### 目标 3

彻底排除旧缓存 / 旧命令路径逻辑，让 worker 只执行最新代码。

只要这三件事成立，compare 层自然就能恢复：

1. `aggregates.mean`
2. `delta_vs_baseline`
3. 更完整的 `summary_text`

---

## 4. 推荐修复方向

### 4.1 不要继续“纯猜” `results.csv`，优先显式记录来源

最稳的方案不是继续加强模糊匹配，
而是在任务执行完成时，直接记录“这次 run 真实使用了哪个 `results.csv`”。

建议：

1. 在 `execute_job()`（任务执行函数）结束后，拿到真实结果文件路径
2. 把它显式写回 JobRecord（任务记录）
   - 可以是新增字段，例如 `metrics_source_path`
   - 或作为 manifest / metadata（元数据）的一部分写入

这样 compare 之后不必再反复猜路径。

### 4.2 在当前版本里，至少重构 `resolve_results_csv_for_train()` 搜索优先级

如果暂时不想改 schema（数据结构），建议把搜索顺序改成：

1. **先查 job 专属 artifacts 目录**
2. **再查日志里明确记录的 Results saved to 路径**
3. **再查 payload.project / payload.name 对应目录**
4. **最后才做全局模糊搜索 + 时间过滤**

当前最大问题之一是：

“模糊搜索”看起来像兜底，但一旦历史残留过多，就会反过来污染主路径。

### 4.3 统一 `log_path` 语义

当前需要明确：

`log_path` 到底表示什么？

建议统一定义为：

- 它必须永远指向当前 job 自己的 artifacts 目录中的 run.log
- 不允许某些执行路径写到别处，再把 log_path 记录成另一个目录

只要 `log_path` 语义统一，后续排查：

- 结果文件位置
- Results saved to
- metrics 来源

才有稳定入口。

### 4.4 每次修复前先清缓存与历史残留

当前这类问题最怕“你以为自己修了，其实进程还在吃旧逻辑”。

每次回归前建议固定做：

1. 清理 `__pycache__`
2. 清空旧 artifacts
3. 重新启动 API / worker / agent
4. 只跑一个最小实验组

否则你会一直在旧状态污染下排查。 

---

## 5. 推荐优先修改点

### 5.1 `resolve_results_csv_for_train()`

这是当前主修点。 

重点看：

1. 它现在按什么顺序找结果文件
2. 是否过度依赖旧路径结构
3. 是否可能命中旧 run 的 `results.csv`
4. 对“多个候选文件”是否做了唯一性检查

### 5.2 `execute_job()`

这是最适合“显式记录真实 metrics 来源”的位置。 

重点看：

1. 训练完成后能否拿到真实输出目录
2. 是否能解析 `Results saved to ...`
3. 是否能在这里直接固化 `results.csv` 的最终来源

### 5.3 `ArtifactStore` 与 `log_path`

重点看：

1. 日志实际写入位置
2. `JobRecord.log_path` 记录位置
3. 两者是否始终一致

### 5.4 `_JobShim.resolved_command_path` / 缓存路径

重点看：

1. 旧 `.pyc` 是否可能继续被 import
2. worker / agent 是否真的重启到了新代码
3. 是否还有任何地方在走旧 shim 路径

---

## 6. 推荐回归流程

### Step 1：清空运行残留

先清：

1. `artifacts`
2. `__pycache__`
3. 若必要，清空本轮测试数据库中的旧 group / job 记录

### Step 2：重启全部相关进程

确保：

1. API 吃到新代码
2. worker 吃到新代码
3. 如果还保留 agent，也必须确认它吃到新代码

### Step 3：只跑一个最小实验组

例如：

- 2 baseline
- 1 variant

目的不是跑出漂亮结果，而是验证每个 run 都能留下完整结果链。

### Step 4：逐个检查 3 个 run

每个 run 必须检查：

1. `run.log` 是否存在且路径正确
2. `results.csv` 是否存在
3. metrics 是否被写回 JobRecord
4. `compare` 里该 run 是否非空

### Step 5：检查 compare 恢复情况

确认自动恢复：

1. `aggregates.mean`
2. `delta_vs_baseline`
3. `summary_text`

### Step 6：回归 Phase B 联调

把 `Phase B` 从 `11/13` 推到完整通过，再继续 Phase C / Phase D。 

---

## 7. 验收标准

这次修复算成功，至少要满足：

1. 每个 `train` run 都能稳定提取 metrics
2. `aggregates.mean` 非空
3. `delta_vs_baseline` 非空
4. `RoundDecision.reason` 继续引用真实数值
5. `Phase B` 从 `11/13` 升级到 `13/13`

如果只是“偶尔能跑出来”，不算修复成功。 

---

## 8. 一句话给接手 AI

当前方案 C 联调剩下的主问题，不是 AI-Researcher，也不是 PostgreSQL / Redis，
而是：

```text
quudet 的结果文件定位链（results.csv + log_path + 缓存执行路径）还没有完全稳定，
导致部分 run 的 metrics 丢失，进一步拖累 compare 聚合输出。
```

这次最值钱的修复，不是继续改上游，
而是：

```text
把结果文件来源显式化、日志路径语义统一、旧缓存彻底清掉，
让每个 run 的 metrics 都能稳定回流。
```
