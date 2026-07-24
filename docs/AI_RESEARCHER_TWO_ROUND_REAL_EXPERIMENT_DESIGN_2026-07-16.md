# AI-Researcher → quudet 真实双轮科研决策闭环设计

**日期**：2026-07-16  
**阶段定位**：真实资源准备与强制提交闸门完成后的下一开发阶段  
**范围**：`AI-Researcher → Experiment Preparation → quudet → compare → RoundDecision → 第二轮 ExperimentSpec`  

---

## 1. 阶段目标

将已完成的“单次真实训练闭环”提升为“基于真实数值自动推进的两轮科研实验闭环”。

```text
第一轮 baseline / variant 真实训练
  → metrics / compare
    → RoundDecision（真实指标）
      → generate_next_spec()
        → Experiment Preparation 强制闸门
          → quudet 提交第二轮
            → 第二轮真实训练与结果回流
```

完成后，系统应能证明：第二轮不是人工拼装 payload，而是由第一轮真实结果、策略和可追溯资产自动生成并提交。

---

## 2. 当前基础

已具备：

- Preparation 的资源发现、真实下载、校验与 `ready/warning/blocked` 判定；
- quudet `POST /api/v1/experiments` 路由强制 Preparation 闸门；
- `blocked → HTTP 412`，且不创建 group/job、不进入 Celery；
- 单机真实 GPU 训练、metrics、compare、RoundDecision 回流；
- VOC 训练前自动准备与 COCO128 的真实下载验收。

本阶段不再验证“能不能训练”；重点验证“能否依据训练结果自动决定并执行下一轮”。

---

## 3. 验收问题

1. 首轮是否至少包含可比较的 baseline 与 variant？
2. `compare` 是否使用真实结果生成明确的差异与证据？
3. `RoundDecision` 是否引用实际 metric、阈值与推荐动作？
4. `generate_next_spec()` 是否由 Decision 生成，而非人工手写 payload？
5. 第二轮 spec 是否保留数据集、GPU、训练约束和资源可复现信息？
6. 第二轮是否必须再次通过 Preparation API 闸门？
7. 两轮的输入、产物、指标与决策是否可复跑和审计？

---

## 4. 边界

### 必须包含

- 本地真实 GPU；
- 已准备的真实数据集（优先 VOC）；
- 至少一个 baseline 与一个 variant；
- 两次真实 quudet 提交；
- 两次 Preparation API 闸门检查；
- 第一轮真实 `compare` 和 `RoundDecision`；
- 从 RoundDecision 自动生成的第二轮 ExperimentSpec；
- 两轮的可复现资产归档。

### 本阶段不要求

- 远程 Linux 或多节点调度；
- 自动发现新的研究课题；
- 超大规模超参数搜索；
- 论文级收敛指标；
- VisDrone 的完全自动下载。

---

## 5. 推荐实验设计

### 5.1 数据集

首选：`VOC`。

原因：Preparation 已能训练前准备，规模适合 4GB 显存的本地真实验收，且能保留真实目标检测 metric。

### 5.2 第一轮：可比较的实验组

建议同一 group 中提交：

| Run | 角色 | 模型 | imgsz | batch | epochs | 目的 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `baseline` | 基线 | `yolo11n.pt` | 416 | 4 | 3–5 | 建立性能与资源基线 |
| `variant` | 变体 | `yolo11s.pt` | 416 | 2–4 | 3–5 | 检验更大模型是否值得投入 |

固定项：

- dataset：`voc`；
- device：`cuda`；
- `required_gpu=true`；
- 训练/验证划分、随机种子、数据根目录和评价 metric；
- 相同的训练轮数与图像尺寸，除非 Decision 明确改变其中一项。

### 5.3 决策策略

第一轮的 `RoundDecision` 至少使用：

- `metrics/mAP50-95(B)` 作为主指标；
- mAP 差异；
- 训练耗时；
- 峰值显存或 OOM；
- 失败状态和结果完整性。

示例规则：

| 条件 | `next_action` | 第二轮建议 |
| --- | --- | --- |
| variant 的 mAP 提升 ≥ 0.01，且无 OOM | `promote_variant` | 对 variant 延长 epochs 或调整 imgsz |
| 提升 < 0.01，或成本明显升高 | `retain_baseline` | 用 baseline 调整 epochs / imgsz |
| 任一 run OOM | `reduce_resource_pressure` | 降低 batch 或 imgsz，保留模型 |
| metrics 缺失或训练失败 | `repair_and_retry` | 只生成诊断/修复 spec，不宣称性能结论 |

阈值必须记录在 Decision 中，不允许在运行后临时解释。

---

## 6. 数据流与接口契约

```text
AI-Researcher
  -> ExperimentSpec(round=1, runs=[baseline, variant])
  -> Experiment Preparation
  -> quudet POST /api/v1/experiments
  -> group_id_1 / job_id_1...n
  -> training results + metrics
  -> compare(group_id_1)
  -> RoundDecision(round=1, evidence=compare)
  -> generate_next_spec(decision, parent_group_id=group_id_1)
  -> ExperimentSpec(round=2, parent_group_id=group_id_1)
  -> Experiment Preparation
  -> quudet POST /api/v1/experiments
  -> group_id_2 / job_id_2...n
  -> training results + metrics + compare
```

### 6.1 第一轮 ExperimentSpec 必填信息

```json
{
  "round": 1,
  "datasets": ["voc"],
  "weights": ["yolo11n.pt", "yolo11s.pt"],
  "required_gpu": true,
  "execution_target": "local",
  "primary_metric": "metrics/mAP50-95(B)",
  "seed": 20260716,
  "runs": [
    {"name": "baseline", "model": "yolo11n.pt", "imgsz": 416, "batch": 4, "epochs": 3},
    {"name": "variant", "model": "yolo11s.pt", "imgsz": 416, "batch": 2, "epochs": 3}
  ]
}
```

实际字段名必须对齐现有 schema；若 schema 没有 `round`、`parent_group_id`、`seed` 或 `runs`，应以向后兼容的可选字段添加，不得改变已有单 run 请求行为。

### 6.2 RoundDecision 最小契约

```json
{
  "round": 1,
  "parent_group_id": "...",
  "primary_metric": "metrics/mAP50-95(B)",
  "baseline_value": 0.0,
  "variant_value": 0.0,
  "delta": 0.0,
  "resource_evidence": {"duration_seconds": 0, "peak_vram_gb": 0.0},
  "next_action": "promote_variant | retain_baseline | reduce_resource_pressure | repair_and_retry",
  "rationale": "...",
  "decision_policy_version": "v1"
}
```

不满足下列条件时不得生成“性能提升”的 Decision：

- 两个 run 都不是 `SUCCESS`；
- 主指标未解析；
- 主指标来自 fallback 文本而非结构化结果；
- baseline 与 variant 使用了不同数据集或不兼容配置；
- 训练产物缺失。

### 6.3 第二轮 spec 生成规则

`generate_next_spec(decision)` 必须：

1. 保存 `parent_group_id`、上一轮 Decision ID 和 policy version；
2. 保留数据集、训练数据根目录、`required_gpu`、`execution_target` 和 seed，除非 Decision 明确调整；
3. 只改变 Decision 允许改变的字段（模型、epochs、batch、imgsz 等）；
4. 写入 `decision_rationale` 和被使用的数值证据；
5. 生成后再次调用 Preparation，而非复用上一轮 `ready` 结果；
6. 由 API 路由创建第二轮，禁止直接调用 Celery。

---

## 7. Preparation 与提交闸门规则

每一轮都必须执行：

```text
ExperimentSpec
  -> prepare_experiment()
  -> blocked: HTTP 412，不创建 group/job，不投递 Celery
  -> warning: 按显式策略处理并记录
  -> ready: 可创建 group/job 并投递 Celery
```

第二轮禁止因为“第一轮已经准备好”而跳过检查。原因包括数据根目录可能变化、资源可能被删除、GPU/磁盘状态可能变化，或第二轮引入新权重。

---

## 8. 可复现实验资产

每轮必须生成独立、不可覆盖的实验 manifest，例如：

```text
<work_root>/<group_id>/
  manifest.json
  preparation_report.json
  submitted_payload.json
  decision_input.json
  run-baseline/
    config.yaml
    environment.json
    stdout.log
    results.csv
    metrics.json
  run-variant/
    config.yaml
    environment.json
    stdout.log
    results.csv
    metrics.json
  compare.json
  round_decision.json
```

`manifest.json` 至少包含：

- group ID、round、parent group ID；
- 代码版本 / 镜像或环境版本；
- 数据集名、数据根目录、数据版本或校验摘要；
- 模型权重来源与摘要；
- 完整训练配置与随机种子；
- 创建与结束时间；
- 关联的 PreparationReport、compare、RoundDecision 文件路径。

路径、数据集和权重发生变化时必须创建新 manifest，不得覆盖首轮产物。

---

## 9. 测试与验收矩阵

| Case | 场景 | 核心断言 |
| --- | --- | --- |
| R1 | 首轮 baseline + variant | 两个 run 均通过 Preparation、提交、训练和 metrics 解析 |
| R2 | compare 真实差异 | 主 metric、baseline/variant 聚合、delta 与 summary 完整 |
| R3 | Decision → spec | 第二轮由 Decision 自动生成，保留 parent/evidence/policy 字段 |
| R4 | 第二轮 API 提交 | 必经 Preparation API 闸门；成功后创建新的 group ID |
| R5 | 第二轮 blocked | 删除数据或制造无效配置后返回 412，零数据库/队列副作用 |
| R6 | 可复现资产 | 两轮 manifest、payload、报告、训练结果、compare、decision 均完整且互相关联 |
| R7 | Decision 防误判 | metrics 缺失、失败、数据集不一致时只生成 repair decision，不生成性能结论 |

### 9.1 真实验收顺序

1. 在干净但已由 Preparation 负责准备的 VOC 数据根目录开始；
2. 提交第一轮 baseline + variant；
3. 等待两个 job `SUCCESS`；
4. 获取 compare，存档原始 JSON；
5. 生成并校验 RoundDecision；
6. 从 Decision 自动生成第二轮 spec，保存输入/输出 JSON；
7. 通过 quudet API 提交第二轮；
8. 等待第二轮完成，核对 group ID 与 parent group ID；
9. 保存全部 manifest；
10. 人工检查训练日志中不存在隐式下载或绕过 Preparation 的痕迹。

---

## 10. 完成标准

以下条件全部满足才可声明“真实双轮科研决策闭环完成”：

- [ ] 第一轮的 baseline 与 variant 都是真实 GPU 训练，且数据集/指标可比较；
- [ ] compare 使用结构化真实 metric 生成差异；
- [ ] RoundDecision 带有数值证据、策略版本和明确 `next_action`；
- [ ] 第二轮 spec 从 Decision 自动生成，有 parent/decision 追溯关系；
- [ ] 第二轮通过 Preparation API 强制闸门后才进入 Celery；
- [ ] 任一 blocked 第二轮请求返回 412 且没有任务副作用；
- [ ] 两轮 manifest、配置、数据/模型摘要、日志、metrics、compare 和 decision 完整归档；
- [ ] 第二轮完成后可重新定位并复跑首轮或第二轮的任一 run。

---

## 11. 后续阶段

完成本设计后，再按价值推进：

1. VisDrone Level B 自动下载与人工下载后的恢复检查；
2. 多数据集与多轮策略选择；
3. 远程节点/多节点 Preparation 闸门同步；
4. 实验队列配额、失败重试、成本和资源预算；
5. 更丰富的研究策略：消融、超参数优化与研究报告自动更新。
