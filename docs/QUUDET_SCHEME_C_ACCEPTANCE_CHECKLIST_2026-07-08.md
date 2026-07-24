# quudet 方案 C 验收清单

日期：2026-07-08  
适用对象：后续验收 `quudet` 方案 C（PostgreSQL + Redis + Celery worker）是否真正落地的开发者 / AI  
目标：把“实施报告（implementation report，实施说明）”转成可执行的验收清单，确认 `quudet` 是否已经从原型后端升级为可依赖的实验执行后端。

关联文档：

- [QUUDET_SCHEME_C_INFRA_DESIGN_2026-07-08.md](./QUUDET_SCHEME_C_INFRA_DESIGN_2026-07-08.md)
- [QUUDET_SCHEME_C_INFRA_IMPLEMENTATION_REPORT_2026-07-08.md](./docs/QUUDET_SCHEME_C_INFRA_IMPLEMENTATION_REPORT_2026-07-08.md)

---

## 0. 一句话目标

这份文档不是检查“代码是不是看起来改了”，而是检查：

```text
PostgreSQL（关系型数据库）
+ Redis（消息代理）
+ Celery worker（工作进程）
+ quudet API（接口服务）
```

这套新的基础设施主路径，能不能真的支撑实验创建、任务执行、结果回流和多轮联调。

如果这些验收不过，那么 `quudet` 仍然只是“写了重构代码”，还不能算“方案 C 已验收通过”。

---

## 1. 验收原则

方案 C 验收必须按顺序走，不要跳步。

### 顺序

1. 启动验收
2. 单轮实验验收
3. 多轮联调验收
4. 恢复机制验收

### 原则

1. 先验基础设施，再验业务链路
2. 先验单轮稳定，再验多轮闭环
3. 先验真实运行，再看设计是否漂亮
4. 只要主路径不通，不要急着测边角功能

---

## 2. Phase A：启动验收

目标：确认方案 C 的四件套能真实启动，并且彼此连通。

### A1. 数据库启动

必须确认：

1. `PostgreSQL` 容器或服务可启动
2. `DATABASE_URL` 配置正确
3. `alembic upgrade head` 能成功执行
4. 5 张核心表已创建成功

通过标准：

- `alembic upgrade head` 无报错
- API 启动后不会再依赖临时 `ALTER TABLE` 补字段

### A2. Redis 启动

必须确认：

1. `Redis` 可连接
2. `REDIS_URL` 配置正确
3. `Celery broker`（消息代理）能正常连接 Redis

通过标准：

- API `readyz` 不因 Redis 不可用而静态返回 ready
- worker 启动时不报 broker 连接失败

### A3. Celery worker 启动

必须确认：

1. `Celery worker` 可独立启动
2. 任务注册成功
3. worker 能响应 ping（存活探测）

通过标准：

- `celery -A app.celery_app worker -l info` 可启动
- `readyz` 能探测到至少一个在线 worker

### A4. API 启动

必须确认：

1. API 启动时能连接 PostgreSQL
2. API 启动时能连接 Redis
3. `readyz`（就绪检查）为真实检查而非静态返回
4. 启动时 `reconcile_all()`（状态修复）不会报错

通过标准：

- `/healthz` 返回 ok
- `/readyz` 只有在 DB / Redis / worker 条件满足时才返回 ready

### A 阶段总通过标准

只要有任一组件起不来，或 `readyz` 仍然是假健康检查，整个方案 C 不能算启动验收通过。

---

## 3. Phase B：单轮实验验收

目标：确认新底座下，最小实验闭环仍然成立。

### B1. 创建实验组

必须确认：

1. `POST /api/v1/experiments` 能成功创建 `ExperimentGroup`（实验组）
2. `JobRecord`（任务记录）能被正确展开
3. `status` 初值合理（例如 `PENDING`）
4. API 创建后会走 enqueue（入队）主路径，而不是回退到旧的 sync / claim-next 主路径

通过标准：

- 新建实验后数据库里有 group 和对应 jobs
- enqueue 发生在 `Redis + Celery` 主路径上

### B2. Worker 执行任务

必须确认：

1. worker 能消费新任务
2. 任务状态能从 `PENDING` -> `RUNNING` -> `SUCCESS / FAILED`
3. 日志、metrics、summary 能回写

通过标准：

- 不需要人工重启 API / worker 才能推进状态
- `JobRecord` 状态流转正确

### B3. Compare 结果回流

必须确认：

1. `/compare` 能正常返回
2. `baseline / variant / delta` 聚合正常
3. `summary_text`（摘要文本）存在
4. `comparison_cache` 可写入 group

通过标准：

- 单轮实验完成后，compare 结果可直接供 `AI-Researcher` 使用

### B4. ArtifactStore（产物存储抽象层）

必须确认：

1. run log 能通过 `ArtifactStore` 写入与读取
2. manifest（产物清单）能写入
3. 读取日志和 metrics 的 API 仍然正常工作

通过标准：

- 使用者不需要知道底层是本地文件还是未来对象存储

### B 阶段总通过标准

如果在新底座下，一轮实验仍然可以“创建 -> 执行 -> 回流 compare”，则说明方案 C 没有破坏单轮闭环。

---

## 4. Phase C：多轮联调验收

目标：确认 `quudet` 在新基础设施主路径下，仍然能承接 `AI-Researcher` 的多轮实验循环。

### C1. 接回 AI-Researcher

必须确认：

1. `AI-Researcher` 仍能提交 `ExperimentSpec`（实验规格）
2. `quudet` 新 API 路径与状态语义没有破坏上游调用
3. `RoundResult`（轮次结果）所需字段仍完整存在

通过标准：

- `AI-Researcher -> quudet` 链路不需要为方案 C 大改协议

### C2. 单条第二轮分支验证

建议优先复验：

1. `stronger_baseline`（更强基线）
2. `ablation`（消融实验）
3. `repeat`（重复实验）

必须确认：

- 第二轮 spec 能正常提交
- 第二轮任务能在 worker 主路径中执行
- 第二轮 compare 结果可回流

通过标准：

- 新基础设施下，`AI-Researcher` 仍能完成至少一条第二轮分支闭环

### C3. 多轮 while 循环

必须确认：

1. `AI-Researcher` 的 while 循环不会因为新后端而卡死
2. 多轮实验中不再依赖人工重启服务
3. group / job 状态在多轮下保持一致

通过标准：

- 多轮实验可连续执行到 `stop`（停止）或最大轮数

### C 阶段总通过标准

如果 `AI-Researcher` 能在新底座下继续跑通第二轮实验，方案 C 才能算真正对上游友好。

---

## 5. Phase D：恢复机制验收

目标：确认方案 C 不只是“平时能跑”，而且出问题时能自动恢复或优雅失败。

### D1. Reconciliation（状态修复）手动触发

必须确认：

1. `POST /api/v1/admin/reconcile` 可调用
2. 能正确清理脏状态
3. 不会把正常运行任务误杀

通过标准：

- 手动 reconcile 一次后，group / job 状态能恢复一致

### D2. Beat 定时修复

必须确认：

1. `Celery Beat` 正常运行
2. 定时任务每 60 秒触发一次修复
3. 不会因为修复任务本身造成新的状态混乱

通过标准：

- 无需人工调用，也能周期性修复典型脏状态

### D3. Worker 中断恢复

建议构造异常场景：

1. worker 执行途中退出
2. API 保持运行
3. 观察 job 状态是否进入 `FAILED / RETRYING`
4. 观察 group 状态是否被重算

通过标准：

- 不会长期卡在假 `RUNNING`
- 不会因为单个 worker 异常导致整个系统失明

### D4. API 重启恢复

建议构造异常场景：

1. API 重启
2. PostgreSQL / Redis / worker 保持运行
3. 重启后查询 group / compare

通过标准：

- API 重启不会破坏已有实验状态
- 恢复后仍可查询、比较、继续联调

### D 阶段总通过标准

只要系统在中断后还能自动修复、或至少明确失败而不是卡死，恢复机制就算过关。

---

## 6. 当前不必优先验的内容

以下内容不是方案 C 的第一优先级验收项：

1. `S3 / MinIO`（对象存储）切换
2. `remote-agent`（远程 agent 兼容路径）全面验证
3. Flower / Grafana 等监控工具
4. 多租户安全权限
5. 大规模分布式集群调度

这些都可以等主路径通过后再补。

---

## 7. 最终通过标准

只有同时满足下面 4 条，才能判定：

```text
quudet 方案 C 验收通过
```

1. 基础服务可稳定启动：`PostgreSQL + Redis + Celery worker + API`
2. 单轮实验闭环在新底座下可运行
3. `AI-Researcher` 多轮实验至少一条第二轮分支可运行
4. 恢复机制可处理典型中断场景

如果只做到前两条，最多只能说：

```text
方案 C 已完成基础设施迁移，但未完成上游联调验收
```

---

## 8. 一句话给接手开发者

不要把“实施报告写完”当成“方案 C 已经完成”。

真正的验收顺序必须是：

```text
先验启动，
再验单轮，
再验多轮，
最后验恢复。
```

只有这四步都过了，`quudet` 才能从“重构后的后端”升级成“AI-Researcher 可依赖的实验执行底座”。
