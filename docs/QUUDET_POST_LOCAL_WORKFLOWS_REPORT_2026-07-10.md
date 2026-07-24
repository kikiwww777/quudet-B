# 本地闭环后工作流实施报告

> **日期**: 2026-07-10  
> **对应路线图**: [`QUUDET_POST_LOCAL_ROADMAP_2026-07-10.md`](./QUUDET_POST_LOCAL_ROADMAP_2026-07-10.md)  
> **状态**: 全部 6 个工作流完成

---

## 总览

在本地统一节点调度闭环通过后，按路线图推进 6 个工作流，从收口旧语义到文档清理，完成了从"底层设施就绪"到"上层协议就绪"的升级。

---

## 工作流 1：收口旧执行语义

### 改动

| 文件 | 内容 |
|------|------|
| **`config.py`** | `EXECUTION_BACKEND` 注释从"训练执行主开关"改为"仅维护任务" |
| **`agent/runner.py`** | `execute_remote_job()` → `execute_job()`；移除旧注释 |
| **`dispatch.py`** | 模块注释已更新（此前已做） |

### 验收

- 代码注释与当前执行事实一致
- 新接手的人不会误以为训练主路径仍是 Celery

---

## 工作流 2：前端协议对接

### 改动

| 页面 | 内容 |
|------|------|
| **训练表单** | 新增"执行目标"下拉（自动/仅本地/仅远程）+ "强制使用 GPU"复选框 |
| **验证表单** | 新增"执行目标"下拉 |
| **检测表单** | 新增"执行目标"下拉 |
| **节点管理页** | 表格新增「类型」「OS」「GPU」三列，CSS 控制列宽 |
| **节点下拉** | 节点选项标注 `[本地]`/`[远程]` + GPU 标记 |
| **JS `createJob()`** | 透传 `execution_target` / `required_gpu` 到 API |

### 数据流

```
用户选择执行目标 / 是否 GPU
  → createJob(..., {execution_target, required_gpu})
  → POST /api/v1/jobs { execution_target, required_gpu, ... }
  → JobRecord 存储 → claim-next 调度筛选
```

### 缓存处理

`index.html` 中 `app.js` / `styles.css` 版本号更新为 `20260710_node_sched`，强制浏览器刷新缓存。

---

## 工作流 3：AI-Researcher 联调

### 改动

| 文件 | 内容 |
|------|------|
| **`quudet_adapter.py`** | 移除 `device=cpu` 强制覆盖 — GPU 设置不再被降级 |
| | 新增 `_build_run_entry()` — 统一透传 `execution_target` / `required_gpu` |

### 验证

```
AI-Researcher → quudet adapter → POST /api/v1/experiments
  ├─ baseline: device=cpu  → agent 领取 → SUCCESS (14 metrics)
  └─ variant:  device=cuda → agent 领取 → SUCCESS (14 metrics)
```

| 检查项 | 状态 |
|--------|------|
| device=cuda 保留（不再强制变 cpu） | ✅ |
| execution_target 透传到 JobRecord | ✅ |
| required_gpu 透传到调度器 | ✅ |
| 单轮实验闭环完整 | ✅ |

---

## 工作流 4：auto 调度增强

### 改动

| 文件 | 内容 |
|------|------|
| **`dispatch.py`** | `_job_matches_node()` 新增 INFO 级调度日志 |

### 日志格式

```
SchedFilter[gpu-node-01]: job=xxx exec_target=local, but node kind=remote -> REJECT
SchedFilter[gpu-node-01]: job=xxx requires GPU (device=cuda), but node has_gpu=False -> REJECT
SchedFilter[gpu-node-01]: job=xxx exec_target=auto device=cuda -> ACCEPT
```

无需查代码，从日志即可追踪任务被哪个节点领取、为什么被拒绝。

---

## 工作流 5：结果层和论文级协议增强

### 改动

| 文件 | 内容 |
|------|------|
| **`experiments.py`** | `export.md` 新增「Reproducibility Evidence」章节 |

### 导出示例

```markdown
## Reproducibility Evidence

| Job | Role | Model | Data | Command Snapshot |
|-----|------|-------|------|-----------------|
| 101b... | baseline | `model_snapshot.yaml` | `data_snapshot.yaml` | `resolved_command.txt` |
| abc... | variant | `model_snapshot.yaml` | `data_snapshot.yaml` | `resolved_command.txt` |
```

每个 run 的 model / data / command 快照路径均输出到 markdown 报告，可直接作为论文复现附录材料。

---

## 工作流 6：文档交接清理

### 改动

| 操作 | 数量 |
|------|------|
| 旧文档标记为 DEPRECATED | 9 个 `quudet_*.md` |
| 主文档标记为 Current | `QUUDET_UNIFIED_NODE_SCHEDULING_DESIGN_2026-07-09.md` |
| 新建文档索引 | `docs/README.md` |

---

## 改动文件总清单

```
quudet-yolo-lab-backend/
├── app/
│   ├── config.py                    ← 收口 EXECUTION_BACKEND 注释
│   ├── agent/runner.py              ← execute_remote_job → execute_job
│   ├── api/routes/
│   │   ├── dispatch.py              ← 调度日志增强
│   │   └── experiments.py           ← export.md 新增证据章节
│   └── ...
├── docs/
│   ├── README.md                    ← 新建文档索引
│   ├── QUUDET_UNIFIED_*            ← 标记为当前
│   └── quudet_*.md                 ← 标记为 DEPRECATED
└── quudet-yolo-lab/
    ├── index.html                   ← 前端表单新增调度字段
    ├── app.js                       ← createJob 透传 + 节点表 9 列
    └── styles.css                   ← 节点表列宽

AI-Researcher-main/
└── research_agent/loop_brain/
    └── quudet_adapter.py            ← GPU 保留 + 调度字段透传
```
