# AI-Researcher × quudet（方案 C）联调验收方案

日期：2026-07-08  
适用对象：后续继续验证 `AI-Researcher` 与 `quudet` 新基础设施主路径联调的开发者 / AI  
目标：在 `quudet` 方案 C 基础设施验收通过的前提下，确认 `AI-Researcher -> quudet -> RoundResult -> RoundDecision` 这条链在新底座上仍然成立。

关联文档：

- [QUUDET_SCHEME_C_ACCEPTANCE_RESULTS_2026-07-08.md](./QUUDET_SCHEME_C_ACCEPTANCE_RESULTS_2026-07-08.md)
- [QUUDET_SCHEME_C_ACCEPTANCE_CHECKLIST_2026-07-08.md](./QUUDET_SCHEME_C_ACCEPTANCE_CHECKLIST_2026-07-08.md)
- [SECOND_ROUND_EXPERIMENT_LOOP_VALIDATION_PLAN_2026-07-08.md](../../AI-Researcher-main/SECOND_ROUND_EXPERIMENT_LOOP_VALIDATION_PLAN_2026-07-08.md)
- [SECOND_ROUND_LOOP_RESULTS_2026-07-08.md](../../AI-Researcher-main/SECOND_ROUND_LOOP_RESULTS_2026-07-08.md)
- [AI_RESEARCHER_NEXT_STAGE_ROADMAP_2026-07-08.md](../../AI-Researcher-main/AI_RESEARCHER_NEXT_STAGE_ROADMAP_2026-07-08.md)

---

## 0. 一句话目标

这份文档不是再验 `quudet` 自己能不能起，
而是验：

```text
AI-Researcher（实验循环大脑）
  -> 提交 ExperimentSpec（实验规格）
  -> quudet（方案 C 新底座）执行
  -> compare（结果比较）回流
  -> 生成 RoundDecision（轮次决策）
  -> 驱动下一轮实验
```

这条链在 `PostgreSQL（关系型数据库） + Redis（消息代理） + Celery worker（工作进程）` 的新主路径下，是否仍然成立。

---

## 1. 当前前置结论

本联调方案建立在以下事实已成立的前提上：

1. `quudet` 方案 C 启动验收已通过
2. `quudet` 单轮实验验收已通过
3. `quudet` 恢复机制验收已通过
4. `AI-Researcher` 旧底座下的首轮闭环已通过
5. `AI-Researcher` 旧底座下至少一条第二轮分支（`stronger_baseline`）已通过

所以当前阶段的核心问题已经不是：

- `quudet` 能不能跑
- `AI-Researcher` 会不会做实验判断

而是：

**两者在新基础设施主路径下重新接起来后，闭环是否仍然成立。**

---

## 2. 联调总原则

### 原则 1

先验单轮，再验多轮。

### 原则 2

先验数据和状态有没有正确穿透，再看实验结果本身好不好。

### 原则 3

只要 compare 回流字段不完整，就不要急着讨论 decision 对不对。

### 原则 4

如果联调失败，优先区分是：

1. `AI-Researcher` 问题
2. `quudet` API / worker 问题
3. 基础设施连接问题

---

## 3. Phase A：接口与配置联调

目标：确认 `AI-Researcher` 指向的新 `quudet` 方案 C 环境是对的。

### A1. 基础 URL 与端口

必须确认：

1. `AI-Researcher` 当前配置的 `quudet_base_url` 指向新环境
2. 没有继续指向旧的 SQLite / 旧 API / 旧目录

通过标准：

- `AI-Researcher` 发出的请求命中当前方案 C API

### A2. 认证与权限

必须确认：

1. `AI-Researcher` 提交实验时不被 401 / 403 拒绝
2. 如启用 guest 模式，调用链路与当前环境一致

通过标准：

- `POST /api/v1/experiments` 从 `AI-Researcher` 调用时能正常通过

### A3. Payload 兼容性

必须确认：

1. `AI-Researcher` 提交的 `ExperimentSpec` 仍与新 `quudet` API 兼容
2. `run_role / seed / run_index / payload` 字段无破坏性变化

通过标准：

- API 不因为 schema 变化拒绝请求

---

## 4. Phase B：单轮联调验收

目标：确认 `AI-Researcher` 在方案 C 新底座下，仍然可以完成一轮闭环。

### B1. Round 1 spec 提交

必须确认：

1. `SpecAgent.generate_first_spec()` 正常生成首轮 `ExperimentSpec`
2. `QuudetAdapter.submit_experiment_group()` 正常提交到新 API
3. 返回 `group_id` 成功写入上下文

通过标准：

- 首轮实验组从 `AI-Researcher` 发出后，能进入 `quudet` 新主路径

### B2. Worker 执行与状态观察

必须确认：

1. `Celery worker` 能消费该实验组的 jobs
2. `AI-Researcher` 轮询 group 时能看到状态推进
3. 不需要人工重启 API / worker 才能进入终态

通过标准：

- `poll_group_result()` 最终能拿到终态

### B3. RoundResult 字段完整性

必须确认：

1. `primary_metric`
2. `primary_metric_resolved`
3. `aggregates`
4. `delta_vs_baseline`
5. `summary_text`

全部能正常回流到 `RoundResult`。

通过标准：

- `AI-Researcher` 不需要 fallback 才能理解实验结果

### B4. 首轮 RoundDecision

必须确认：

1. `RoundDecision.reason` 继续引用真实数值
2. `next_action` 不是因为缺字段才退回保守路径

通过标准：

- 首轮 decision 能建立在 compare 结果而不是模糊摘要上

### B 阶段总通过标准

如果 `AI-Researcher` 在新底座下仍然能完成“首轮实验提交 -> compare 回流 -> 生成 decision”，则说明新基础设施没有破坏最小闭环。

---

## 5. Phase C：第二轮分支联调验收

目标：确认 `AI-Researcher` 在方案 C 新底座下，仍然可以跑第二轮实验，而不是只会单轮。

### C1. `stronger_baseline` 路径复验

建议作为第一优先级，因为这条线旧环境下已经通过。

必须确认：

1. 首轮结果触发 `next_action=stronger_baseline`
2. `generate_next_spec()` 正常生成第二轮 spec
3. 第二轮实验能被新 `quudet` 执行
4. 第二轮 compare 结果可回流
5. 第二轮 `RoundDecision.reason` 继续引用真实指标

通过标准：

- 新底座下的 `stronger_baseline` 仍然成立

### C2. `ablation` 路径复验

必须确认：

1. 构造 variant 有增益的 case
2. 首轮 decision 进入 `ablation`
3. 第二轮 `ablation_runs` 能正常提交并执行
4. cross-round stability（跨轮稳定性）仍成立

通过标准：

- 第二轮 `ablation` 在新基础设施下不被调度层破坏

### C3. `repeat` 路径复验

必须确认：

1. 构造 delta 接近 0 的 case
2. 首轮 decision 进入 `repeat`
3. 第二轮 spec 结构基本复用上一轮
4. worker 路径仍正常执行

通过标准：

- 新底座下 `repeat` 仍可达且不漂移

### C 阶段总通过标准

至少 `stronger_baseline` 复验通过，且 `ablation / repeat` 中至少一条复验通过，才能判定方案 C 对多轮实验是兼容的。

---

## 6. Phase D：多轮 while 循环验收

目标：确认 `AI-Researcher` 的 while 循环在方案 C 新底座下不会因为任务调度变化而失稳。

### D1. 连续两轮自动推进

必须确认：

1. 第 1 轮结束后自动进入第 2 轮
2. 中间不需要人工干预
3. group_id、result、decision 在上下文中保持一致

### D2. 终止条件

必须确认：

1. `stop` 时系统能干净退出
2. `max_rounds` 时不会卡死
3. 如果 worker 失败，decision 能合理退回 `stronger_baseline / repeat / stop`

### D3. Writer 前置状态

必须确认：

1. `WriterPacket` 所需字段仍完整
2. 新底座不会导致 round_history 丢链条

### D 阶段总通过标准

至少应证明：

- 新基础设施下，多轮 while 循环不会因为调度层变化而中断

---

## 7. 失败分流

联调不过时，必须优先按下面三类归因，不要混着看。

### 7.1 配置接线问题

表现：

1. 请求打错地址
2. 401 / 403
3. API schema 不匹配
4. Worker 根本没消费任务

优先排查：

- base_url
- 环境变量
- 认证模式
- API / worker 启动方式

### 7.2 quudet 新底座问题

表现：

1. group 创建成功但任务不执行
2. compare 回流不完整
3. reconcile 误伤任务
4. 新底座状态语义与旧链路不一致

优先排查：

- API → Redis → Celery → PostgreSQL 路径
- ArtifactStore
- compare 层

### 7.3 AI-Researcher 自身问题

表现：

1. spec 生成错误
2. next_action 错误
3. context 丢字段
4. second-round prompt 漂移

优先排查：

- `spec_agent.py`
- `quudet_adapter.py`
- `_generate_decision()`
- `loop_controller.py`

---

## 8. 最终通过标准

只有同时满足下面 3 条，才能说：

```text
AI-Researcher × quudet（方案 C）联调通过
```

1. 单轮实验闭环在新底座下通过
2. 至少一条第二轮实验分支在新底座下通过
3. while 循环不会因调度主路径变化而中断

如果只做到第一条，最多只能说：

```text
方案 C 已对单轮闭环兼容，尚未完成多轮联调验收
```

---

## 9. 一句话给接手开发者

`quudet` 方案 C 的基础设施验收通过，并不等于整个科研闭环已经恢复。

真正关键的下一步是：

```text
把 AI-Researcher 接回新 quudet，
先验单轮，
再验第二轮，
最后验多轮 while 循环。
```

只有这一轮联调也通过，才能说新 `quudet` 底座已经真正接住了上游实验循环。 
