# AI-Researcher → quudet 一次通过的双轮科研决策闭环验收方案

**日期**：2026-07-17  
**目标**：在一次受控执行中完成“公平第一轮比较 → 结构化 compare → 自动 RoundDecision → 自动生成并提交第二轮 → 第二轮真实训练”的完整闭环。  
**数据集**：本地 C 盘 VOC。  
**执行约束**：RTX 3050 4GB、Windows、Docker 中 Redis/PostgreSQL 保持运行。

---

## 1. 本轮成功定义

本轮不是再次证明 VOC 能训练，而是证明系统无需人工改 payload，即可根据真实第一轮结果创建并完成第二轮。

```text
Round 1 group（baseline + variant，公平配置）
  → 两个真实 GPU run SUCCESS
  → compare(group_1)
  → RoundDecision（真实结构化指标）
  → generate_next_spec(decision)
  → Preparation API gate
  → POST /api/v1/experiments
  → Round 2 group
  → 真实 GPU run SUCCESS
  → 归档父子 group、decision、spec、metrics 和 manifest
```

只有上述所有节点都成功，才标记“真实双轮科研决策闭环完成”。

---

## 2. 已知风险与固定资源策略

此前失败的组合是 `batch=4 + workers=8 + imgsz=416`：Windows 共享内存与 4GB GPU 同时出现分配失败。以下参数是本轮**不可变的资源安全基线**：

```text
batch=1
workers=0
imgsz=320
device=cuda
cache=false
epochs=1
seed=20260717
```

规则：

- baseline、variant 和第二轮均使用 `batch=1`、`workers=0`；
- 不使用 `batch=4` 或默认 `workers=8`；
- Docker、Redis、PostgreSQL 保持运行；训练前只关闭非必要的浏览器、IDE、游戏/覆盖层；
- C 盘可用空间必须不少于 `15 GB`；数据根目录固定为：

```text
C:\Users\86159\Documents\Obsidian Vault\AI4Science\07_AI代理\实验模块\quudet\quudet-yolo-lab-backend\data\datasets\VOC
```

- 训练输入必须为该目录中的绝对路径 `VOC.yaml`，日志中不得出现新的 VOC 下载。

---

## 3. 运行前一次性闸门

### 3.1 数据与环境

必须全部通过：

1. `validate_dataset("voc", QUUDET_DATA_ROOT).local_ready == true`；
2. 本地 `VOC.yaml` 存在，`path` 指向 C 盘 VOC 根目录；
3. CUDA 可用，GPU 名称为 RTX 3050，C 盘空闲 >= 15GB；
4. Redis、PostgreSQL、quudet API、**恰好一个** `gpu-node-01` agent 均健康；
5. Preparation 不返回 `blocked`；
6. API 对故意无效数据集请求返回 HTTP 412 且不创建任务（一次负向 smoke test）。

任一失败即停止，先修复环境；禁止创建首轮任务后再修复。

### 3.2 自动化能力预检

在提交第一轮前必须验证下列函数/接口可用：

- `compare_experiment_group(group_id)`；
- RoundDecision 构造器能消费 compare 的结构化 JSON；
- `generate_next_spec(decision)` 可生成合法 `ExperimentGroupCreate` payload；
- 第二轮 payload 包含 `parent_group_id`、`decision_id`、`decision_policy_version` 或等价可追溯字段；
- API 路由不允许绕过 Preparation gate。

若任何自动化能力仍需手工改 JSON，本轮不得宣称自动双轮闭环。

---

## 4. Round 1：公平的 baseline / variant

必须在**同一个 experiment group**中创建两条 run，确保 compare 有共同数据、共同指标与共同上下文。

| 字段 | baseline | variant |
|---|---|---|
| role | `baseline` | `variant` |
| model | `yolo11n.pt` | `yolo11s.pt` |
| data | 同一绝对路径 `VOC.yaml` | 同一绝对路径 `VOC.yaml` |
| epochs | `1` | `1` |
| imgsz | `320` | `320` |
| batch | `1` | `1` |
| workers | `0` | `0` |
| device | `cuda` | `cuda` |
| seed | `20260717` | `20260717` |
| metric | `metrics/mAP50-95(B)` | `metrics/mAP50-95(B)` |

要求：

- 只通过 `POST /api/v1/experiments` 创建，不得直接写数据库或投递 Celery；
- 两条 run 都必须经过 Preparation gate；
- `gpu-node-01` 最大并发为 1 时顺序执行是预期行为；
- 每条 run 状态必须为 `SUCCESS`，否则进入 `repair_and_retry`，本轮不进入性能决策；
- `progress` 字段当前只在 epoch 结果写回时更新，实时进度以 `run.log` batch 百分比为准。

### 4.1 第一轮验收产物

```text
round1/
  submitted_payload.json
  preparation_report.json
  group_response.json
  baseline/run.log + results.csv + metrics.json
  variant/run.log + results.csv + metrics.json
  compare.json
```

---

## 5. Compare 与 RoundDecision（无人工参数选择）

### 5.1 compare 最小字段

`compare.json` 必须包含：

- `primary_metric = metrics/mAP50-95(B)`；
- baseline 与 variant 的聚合值；
- `delta_vs_baseline`；
- 每个 run 的状态、耗时、显存/资源证据；
- `summary_text`。

缺失任一字段时，只允许生成 `repair_and_retry`，不得生成“模型更优”的结论。

### 5.2 预注册 Decision policy v1

```text
if baseline.status != SUCCESS or variant.status != SUCCESS:
    next_action = repair_and_retry
elif variant.mAP50_95 - baseline.mAP50_95 >= 0.005:
    next_action = promote_variant
else:
    next_action = retain_baseline
```

RoundDecision 必须写入：

```json
{
  "parent_group_id": "<round1_group_id>",
  "primary_metric": "metrics/mAP50-95(B)",
  "baseline_value": 0.0,
  "variant_value": 0.0,
  "delta": 0.0,
  "next_action": "promote_variant | retain_baseline | repair_and_retry",
  "decision_policy_version": "v1",
  "rationale": "由 compare JSON 自动生成"
}
```

禁止人工直接编辑第二轮 payload；允许人工只在系统故障时停止并记录失败。

---


### 5.3 LLM 生成职责与确定性约束

`generate_next_spec()` 由 AI-Researcher 的 LLM 驱动，而不是用固定 if/else 直接拼装 payload。LLM 的输入必须包括：

- Round 1 的完整 ExperimentSpec；
- 结构化 `compare.json`；
- `RoundDecision`（含指标、delta、`next_action` 和 policy version）；
- 当前可用资源约束：VOC、`batch=1`、`workers=0`、`imgsz=320`、GPU 与磁盘状态；
- ExperimentSpec / quudet API 的 JSON schema；
- 不允许改变的字段和允许探索的字段。

LLM 输出候选 Round 2 spec；随后必须依次经过：

1. schema 校验；
2. Decision 一致性校验（候选模型/目标不能与 `next_action` 矛盾）；
3. 资源安全校验（不得恢复 `batch=4` 或 `workers=8`）；
4. Experiment Preparation API gate。

因此：**LLM 决定具体的科研探索配置和理由；确定性 policy、schema 与 Preparation gate 负责阻止不安全、不可复现或违背决策证据的输出。**
## 6. Round 2：由 LLM 根据 Decision 生成并提交

`generate_next_spec(decision)` 由 LLM 生成候选 spec。下表是 policy v1 的**约束边界和默认建议**，不是直接硬编码 payload：

| `next_action` | 第二轮 model | 唯一允许变化 | 固定资源参数 |
|---|---|---|---|
| `promote_variant` | `yolo11s.pt` | `epochs=2` | `imgsz=320`, `batch=1`, `workers=0` |
| `retain_baseline` | `yolo11n.pt` | `epochs=2` | `imgsz=320`, `batch=1`, `workers=0` |
| `repair_and_retry` | 失败的模型 | `epochs=1` | `imgsz=320`, `batch=1`, `workers=0` |

第二轮 payload 必须：

1. 自动写入 `parent_group_id = round1_group_id`；
2. 写入 Decision ID、policy version、使用的 metric/delta；
3. 保留 VOC 的绝对 `VOC.yaml` 路径与 `required_gpu=true`；
4. 再次通过 Preparation API gate；
5. 使用 API 创建新的 group ID；
6. 不得复用第一轮 job ID 或目录。

第二轮成功后只需单 run `SUCCESS`，无需再递归进入第三轮。

---

## 7. 一次执行顺序

1. 执行第 3 节全部预检并归档结果；
2. 通过 API 提交 Round 1 的两个 run；
3. 等待两个 run `SUCCESS`；
4. 下载/保存 Round 1 的 `compare.json`；
5. 自动生成并保存 `RoundDecision.json`；
6. 自动调用 `generate_next_spec()` 并保存 `round2_spec.json`；
7. 通过 API 提交 Round 2；
8. 等待 Round 2 `SUCCESS`；
9. 收集 manifest、PreparationReport、payload、日志、metrics、compare、Decision；
10. 生成最终验收报告。

中途只要出现资源失败：记录失败证据并停止；不得临时更改 batch、workers、imgsz 后继续把结果称为同一次闭环。

---

## 8. 最终通过标准

- [ ] VOC 由 Preparation 判定 ready，且训练日志没有隐式下载；
- [ ] Round 1 baseline 与 variant 同组、同数据、同资源参数并都 `SUCCESS`；
- [ ] compare 使用真实结构化指标并生成完整 JSON；
- [ ] RoundDecision 按 policy v1 自动生成，含真实数值证据；
- [ ] Round 2 spec 由 Decision 自动生成，非人工编辑；
- [ ] Round 2 再次通过 Preparation API gate；
- [ ] Round 2 真实 GPU run `SUCCESS`；
- [ ] 两轮 parent/child group、Decision、spec、metrics 与日志均可追溯；
- [ ] 所有证据归档到 `quudet/docs/evidence/two_round_2026-07-17/`。

---

## 9. 预计耗时

在 RTX 3050 4GB 和上述低资源参数下：

- Round 1 `yolo11n`：约 45–70 分钟；
- Round 1 `yolo11s`：约 60–90 分钟；
- Round 2（2 epochs）：约 90–150 分钟；
- 总时长：约 3.5–5 小时。

因此应在一次连续可用的时间窗口启动，并避免中途退出 agent、关闭 Docker 或清理 C 盘数据。

