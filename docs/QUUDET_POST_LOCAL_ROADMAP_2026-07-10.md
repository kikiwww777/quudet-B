# QuuDet 本地统一调度完成后的后续路线图

适用对象：继续推进 `quudet` 非 Linux 方向开发的开发者 / AI  
日期：2026-07-10

前提假设：

1. 本地统一节点调度第一版已经通过端到端验证
2. `execution_target=local / remote` 筛选已通过
3. `required_gpu=true` 的 GPU 筛选已通过
4. 本机 GPU 执行与 metrics 回流已通过
5. Linux 远程节点部署暂时搁置，不作为当前阶段目标

本文件只规划“本地统一调度跑通之后，继续往前做什么”，不再重复 Linux 节点部署内容。

---

## 1. 当前状态判断

当前 `quudet` 的位置应定义为：

- 本地执行底座已基本成立
- 统一节点调度主路径已可用
- 非 Linux 部分应从“接通链路”切换到“收口协议、接上上游、提升科研可用性”

所以接下来最重要的，不再是“让节点能不能抢到任务”，而是：

1. 把遗留的旧执行语义彻底收口
2. 把新调度协议接回前端和 AI-Researcher
3. 把结果层提升到更接近论文级实验协议

---

## 2. 本阶段不做什么

当前阶段明确不做：

1. Linux 远程节点真实部署
2. systemd / 跨机网络 / 局域网 / 公网连通性工作
3. 多机调度压力测试
4. 更复杂的远程资源管理

这些内容可以等本地协议与上游集成稳定后再恢复。

---

## 3. 后续工作的总目标

在不依赖 Linux 节点的前提下，本阶段应完成：

1. 收口 `quudet` 的执行配置与旧注释
2. 完成前端任务创建与节点选择字段对接
3. 完成 `AI-Researcher -> quudet` 新协议联调
4. 提升实验协议与导出能力，使其更接近论文数据生产底座

---

## 4. 工作流 1：收口旧执行语义

### 目标

让代码层面不再混淆“旧 Celery 主路径”和“新统一节点调度主路径”。

### 背景

虽然本地统一节点调度已通过，但仓库里仍有一些旧语义残留，例如：

- `config.py` 里保留旧的 `EXECUTION_BACKEND=remote-agent/celery` 叙述
- 某些注释还在把当前路径描述成 legacy 或兼容路径
- `execute_remote_job()` 这类命名已不准确，因为本地节点也在使用它

### 建议任务

1. 收口 `app/config.py`
   - 把 `EXECUTION_BACKEND` 从“训练执行主开关”降级为兼容字段或内部保留字段
   - 明确当前训练执行主路径已经是统一节点调度

2. 收口 `app/agent/runner.py`
   - 将 `execute_remote_job()` 更名为更中性的执行函数
   - 收口顶部模块说明

3. 收口 `app/api/routes/dispatch.py`
   - 去掉不再准确的旧时代描述
   - 明确当前就是主调度路径

4. 收口文档
   - 标明哪些文档已过时
   - 标明当前应以统一节点调度文档为准

### 通过标准

1. 代码注释与当前执行事实一致
2. 新接手的人不会再误以为训练主路径仍是 Celery worker

---

## 5. 工作流 2：前端协议对接

### 目标

让前端真正用上统一节点调度字段，而不是后端已经支持、前端还没暴露。

### 背景

当前后端已经开始支持：

- `execution_target`
- `required_gpu`
- `target_node_id`

但这些字段如果没有稳定暴露到前端，用户仍然无法自然使用。

### 建议任务

1. 在训练 / 验证 / 检测表单增加：
   - 执行目标：`local / remote / auto`
   - 是否必须 GPU
   - 指定节点

2. 在节点管理页增加：
   - 节点类型
   - GPU 能力
   - 当前状态
   - 最近心跳

3. 在任务列表页增加：
   - `execution_target`
   - `assigned_node_id`
   - `required_gpu`

4. 在实验组详情页增加：
   - 每个 run 的执行目标
   - 实际领取节点

### 通过标准

1. 用户不看 API 文档也能控制任务发往本地 / 自动
2. 前端可清楚展示任务为何被某节点领取

---

## 6. 工作流 3：AI-Researcher 联调

### 目标

让 `AI-Researcher` 真正理解并利用新的本地统一调度协议。

### 背景

如果 `quudet` 只停留在“本地能跑实验”，但没有重新接回 `AI-Researcher`，那它还只是底层工具，不是科研 agent 的实验中枢。

### 建议任务

1. 对齐 `ExperimentSpec -> quudet` 提交协议
   - 确认 experiment group 提交仍然兼容
   - 确认 `run_role / seed / run_index / payload` 透传稳定

2. 把 `execution_target / required_gpu` 纳入 `AI-Researcher` adapter
   - 至少能在 spec 中显式给出本地 / 自动意图

3. 重新做单轮联调
   - `submit -> execute -> compare -> RoundDecision`

4. 再做第二轮联调
   - `stronger_baseline`
   - `ablation`
   - `repeat`

### 通过标准

1. `AI-Researcher` 不需要 fallback 才能读懂 compare 结果
2. 至少一条第二轮分支在本地统一调度新底座上成立

---

## 7. 工作流 4：auto 调度策略收口

### 目标

让 `auto` 不只是“未设置时的默认值”，而是真正可解释的本地调度策略。

### 背景

现在即使 `auto` 存在，若没有明确规则，它仍然只是一个占位语义。

### 当前阶段范围

因为 Linux 暂停，本阶段 `auto` 只需服务本机环境：

- CPU 节点
- 本机 GPU 节点

### 建议规则

1. `required_gpu=true`
   - 只允许 GPU 节点领取

2. `device=cuda*`
   - 只允许 GPU 节点领取

3. 未要求 GPU
   - 可由 CPU 节点或 GPU 节点领取

4. 若同时有 `local-cpu` 和 `local-gpu`
   - 默认优先 CPU 处理轻任务
   - GPU 留给显式要求 GPU 的任务

### 建议任务

1. 为 `auto` 增加更清晰的调度日志
2. 在 `claim-next` 中输出“为何匹配 / 为何被过滤”
3. 增加小规模自动调度验收 case

### 通过标准

1. `auto` 行为可解释
2. CPU 节点不会误领 GPU 任务
3. 用户能从日志或 UI 里理解调度结果

---

## 8. 工作流 5：结果层和论文级协议增强

### 目标

把 `quudet` 从“能跑实验并比较”提升到“更适合产出论文证据”。

### 背景

当前系统已经能产出：

- 单 run metrics
- experiment group compare
- baseline vs variant 的 delta

但距离“论文级实验协议”还差默认机制。

### 建议任务

1. repeat / multi-seed 规范化
   - 默认支持多 seed
   - 默认输出 mean / std / n

2. stronger baseline 规范化
   - 把 stronger baseline 当成正式策略，而不是临时 case

3. ablation 规范化
   - 让 ablation run 的命名、比较和导出更标准化

4. 结果导出增强
   - 导出 markdown / csv 不仅包含 runs，还包含聚合结论
   - 补资源指标字段的导出入口

5. 复现证据增强
   - 明确展示 model / data / env / command snapshot

### 通过标准

1. 一个实验组的导出结果足以作为论文结果表的原始材料
2. compare 输出足以支撑 agent 做下一轮科研决策

---

## 9. 工作流 6：文档和交接清理

### 目标

确保后续任何 AI 或开发者不会被旧文档误导。

### 建议任务

1. 标记旧集群文档的适用范围
2. 标记统一节点调度文档为当前主文档
3. 写一份“当前真实状态”说明
   - 哪些完成
   - 哪些暂停
   - 哪些待做

### 通过标准

1. 新接手者 5 分钟内能判断当前主线
2. 不再出现“以为还在走旧 Celery 主路径”这种误解

---

## 10. 推荐推进顺序

如果 Linux 暂停，本阶段建议按这个顺序推进：

1. 收口旧执行语义
2. 前端协议对接
3. AI-Researcher 联调
4. auto 调度解释性增强
5. 结果层 / 论文级协议增强
6. 文档交接清理

---

## 11. 建议产出物

本阶段结束后，建议至少新增或更新：

1. 一份本地统一调度主线路径说明
2. 一份前端字段对接说明
3. 一份 `AI-Researcher × quudet` 新底座联调结果
4. 一份实验导出增强说明
5. 一份“论文级实验协议待办清单”

---

## 12. 一句话总结

在 Linux 远程节点暂缓的前提下，`quudet` 接下来最值得做的不是继续折腾节点接入，而是：

> 把已经跑通的本地统一调度真正接回前端和 AI-Researcher，并把结果层提升到更接近论文级实验协议。
