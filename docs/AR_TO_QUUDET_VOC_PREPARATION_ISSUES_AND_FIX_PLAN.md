# AR → QuuDet → Linux：通用数据集链接交付与实验前准备修复方案

**日期：** 2026-07-27  
**目标：** 不是只修复 VOC；而是让 LLM 为任意数据集/权重找到的链接，可靠地从 AR 传递到 QuuDet、Linux agent，并最终传给训练任务。  
**状态：** 当前训练调度链路可用；通用资源交付链路需要按本文方案实现并验收。

---

## 1. 核心要求

系统必须支持任意资源类型，而不是为 `VOC`、`COCO8`、`yolo11n.pt` 写特例：

```text
LLM / AR 找到资源链接
  → 生成标准 ResourceManifest
  → QuuDet 保存、验证并下发同一份 manifest
  → Linux agent 下载、校验、解压/准备
  → agent 返回最终可用路径
  → 训练任务使用该最终路径
```

### 必须保证

1. **链接不丢失、不改写。** LLM/AR 发现的 URL 原样记录在 `manifest.source.url`，由 Linux agent 实际请求。
2. **不依赖数据集名字猜文件。** 不得由 `coco8` 推断 `coco8.yaml`，不得由 `voc` 假设目录结构。
3. **不依赖 Ultralytics 隐式下载。** 训练开始前，QuuDet 必须拿到 agent 已准备好的本地路径。
4. **不同格式走不同准备器。** 原始数据、YOLO-ready zip、COCO JSON、VOC XML、单文件权重、Git/Hugging Face 资产都通过统一 manifest + 不同 `preparer_kind` 处理。
5. **任何失败必须可观测。** 主控显示 URL、字节数、SHA256、解压结果、准备器、最终路径、错误和 Linux PID。

---

## 2. 唯一资源交接协议：ResourceManifest v1

每一个数据集或权重都必须使用此契约；AR、主控和 Linux agent 只通过它交接资源。

```yaml
manifest_version: 1
resource_id: dataset:<provider>:<name>
resource_type: dataset                 # dataset | weight | code | auxiliary
version: <资源版本或日期>

source:
  url: https://example.org/file.zip    # LLM/AR 找到的原始链接，原样保存
  mirrors: []
  headers: {}

integrity:
  archive_sha256: <64位SHA256>
  expected_size_bytes: 123456

transport:
  kind: archive                         # archive | file | git | huggingface
  archive_format: zip                   # zip | tar | tar.gz | none
  allow_resume: true

delivery:
  cache_key: sha256:<manifest-hash>
  target_relative_path: datasets/<name>
  extract_subdir: null

prepare:
  preparer_kind: yolo_ready             # yolo_ready | voc_to_yolo | coco_to_yolo | custom
  options: {}

output_contract:
  kind: yolo_dataset
  data_yaml_path: VOC.yaml              # 明确真实路径，禁止由名称推断
  required_paths:
    - images/train
    - images/val
    - labels/train
    - labels/val

provenance:
  discovered_by: ar_llm
  discovered_at: <UTC ISO8601>
```

### 契约规则

- `source.url` 是传输输入；`output_contract` 是训练输入。
- URL 指向什么格式并不重要；训练只依赖准备器返回的最终本地路径。
- `data_yaml_path` 和 `required_paths` 必须由 archive 检查或准备器输出填写，不能由资源 ID 推断。
- `cache_key` 必须基于完整 manifest 内容计算。

---

## 3. 通用流程

### A. AR / LLM：发现

LLM 只负责找候选链接和来源信息，不能直接把裸 URL 交给训练：

1. AR 记录 URL、资源类型、版本、许可证/来源。
2. AR 生成 ResourceManifest 草案。
3. QuuDet resource intake 预检链接和 archive。
4. 只有预检成功的 manifest 才能进入 `ACTIVE` 并创建 provision plan。

### B. QuuDet 主控：预检与下发

主控必须：

1. 检查 URL 可访问、重定向、content-type、大小和 SHA256。
2. 对 archive 做临时解压巡检，记录真实文件布局。
3. 写入准确的 `output_contract`。
4. 将完整 manifest JSON 原样下发给 Linux agent。

主控不得替换 LLM URL、不得猜 YAML 名称、不得把 `VOC.yaml` 这类配置文件 URL 当作完整数据集 archive。

### C. Linux agent：下载、准备、回执

所有资源统一执行：

```text
下载 → 断点续传 → SHA256 → 解压 → preparer → 输出校验 → receipt
```

准备器完成后返回：

```json
{
  "resource_id": "dataset:...",
  "cache_key": "sha256:...",
  "local_uri": "cache://datasets/example",
  "absolute_path": "/srv/quudet/cache/content/<key>",
  "data_yaml_path": "/srv/quudet/cache/content/<key>/dataset.yaml",
  "validator_result": {"ok": true}
}
```

训练 job 只能使用 `data_yaml_path`；禁止再传会触发第三方默认下载的裸逻辑名，例如 `VOC.yaml` 或 `coco8.yaml`。

---

## 4. 准备器插件：支持任意数据集格式

| `preparer_kind` | 输入 | 输出 |
|---|---|---|
| `yolo_ready` | 已含 YOLO labels/YAML 的 archive | 校验后的 `data_yaml_path` |
| `voc_to_yolo` | Pascal VOC XML + 图片 | YOLO TXT labels + `VOC.yaml` |
| `coco_to_yolo` | COCO JSON + 图片 | YOLO TXT labels + YAML |
| `classification_ready` | class-folder 数据集 | 分类 contract |
| `file` | 单个权重/模型文件 | 本地绝对文件路径 |
| `custom` | 特殊数据集 | 由批准脚本输出标准 contract |

统一插件接口：

```python
def prepare(extracted_root: Path, manifest: dict, output_dir: Path) -> dict:
    """返回 output_contract 对应的最终路径和校验结果。"""
```

新增数据集只需要新增 manifest 或 preparer，不改变 AR、调度和 Linux 下载协议。

---

## 5. 实施计划

### 阶段 1：Manifest 通用化

- [ ] 实现 ResourceManifest v1 schema。
- [ ] 增加 `source.url`、`transport`、`prepare`、`output_contract`。
- [ ] 删除从资源 ID/文件名猜 YAML 的逻辑。
- [ ] 将 VOC、COCO8、YOLO 权重迁移到新 schema。

### 阶段 2：Resource Intake 预检

- [ ] 预检 URL、重定向、大小、SHA256。
- [ ] 临时解压并生成真实文件树摘要。
- [ ] 无法确定输出 contract 的资源禁止激活。
- [ ] 保留 LLM 原始 URL、发现记录和批准记录。

### 阶段 3：Agent 通用下载器与准备器

- [ ] Linux agent 原样消费 manifest。
- [ ] 支持 `archive/file/git/huggingface` transport。
- [ ] 实现 preparer registry。
- [ ] 首先实现 `yolo_ready`、`voc_to_yolo`、`coco_to_yolo`。
- [ ] 写入 receipt，并返回绝对训练路径。

### 阶段 4：恢复训练 gate

- [ ] 关闭 `EXPERIMENT_PREPARATION_SKIP=true`。
- [ ] 实验创建时等待资源 provision 为 `READY`。
- [ ] 将 receipt 的 `data_yaml_path` 注入 job payload。
- [ ] 远程训练 job 没有 provision receipt 时拒绝使用裸逻辑数据名。

### 阶段 5：验收

每种资源必须完成“首次下载”和“缓存命中”两次测试：

| 类型 | 示例 | 首次验收 | 二次验收 |
|---|---|---|---|
| YOLO-ready archive | COCO8 | URL→下载→校验→YAML | 命中 cache，不下载 |
| 原始检测数据 | VOC | URL→转换→YAML/labels | 命中 cache，不转换 |
| 权重文件 | yolo11n.pt | URL→SHA256→本地权重 | 命中 cache，不下载 |
| 新 LLM 发现数据集 | 任意公开 archive | manifest 可预检并 provision | 可复用 receipt |

通用验收通过的定义：

- [ ] LLM URL 原样存在于 manifest，且 Linux 实际请求该 URL。
- [ ] 任意数据集不依赖名字猜 YAML、目录或标签格式。
- [ ] 训练 payload 使用 agent receipt 返回的绝对本地路径。
- [ ] 首次实验不依赖 Ultralytics 隐式下载。
- [ ] 第二次实验命中 cache，不访问外网。
- [ ] URL、SHA256、archive 布局或 preparer 失败都有明确可见错误。
- [ ] 不同节点可并行 provision；同一节点按容量串行。

---

## 6. 当前具体问题（VOC/COCO8 作为证据）

以下内容是当前通用机制缺失的具体表现，而不是最终方案只支持 VOC。


---

# 附录：当前 VOC / COCO8 证据与临时绕过

# AR → QuuDet → Linux VOC 实验前准备：问题与修复方案

**日期：** 2026-07-27  
**状态：** VOC 端到端训练已能启动；实验前准备（资源 manifest / provision cache）尚未完成正式修复。  
**范围：** AI-Researcher（AR）→ QuuDet 控制面 → Linux GPU agent → YOLO VOC 训练。

---

## 1. 结论

当前 AR → QuuDet → Linux agent → YOLO 的任务提交、调度和训练进程启动已经验证可用。

但 **实验前准备模块（Experiment Preparation）仍存在资源 manifest、Python 兼容性和 VOC 数据交付格式三类问题**。因此本轮 VOC 烟雾测试临时设置了：

```text
EXPERIMENT_PREPARATION_SKIP=true
```

在该开关开启时，`data=VOC.yaml` 会交由 Ultralytics 自动准备数据集；当前 Linux 节点从 Ultralytics 内置地址下载 VOC，而不是使用 LLM/manifest 生成的 provision cache。

---

## 2. 本轮已验证链路

```text
AR 生成实验规格
  → POST /api/v1/experiments
  → QuuDet 创建 ExperimentGroup 和 JobRecord
  → Linux node-linux-01 claim-next
  → Linux agent 下载 job bundle
  → yolo train model=yolo11n.pt data=VOC.yaml ...
```

已验证的运行事实：

- Linux 节点：`node-linux-01` / `BJDeskPC3090` / RTX 3080 / Python 3.11.15。
- 节点容量控制为每节点独立：`max_concurrent_jobs=1` 时，基线运行、变体保持 `PENDING_ASSIGN`，不会同时抢占同一节点。
- agent 已回传实时运行态：`active_job_id`、YOLO PID、命令、最后输出时间、退出码。
- 当前 VOC 训练的首次数据下载由 Ultralytics 自动触发，目标目录为：

```text
/home/liliya/datasets/VOC/
```

---

## 3. 已发现问题

### P0：Python 3.11 与 `walk_up=True` 不兼容

**现象**

```text
TypeError: PurePath.relative_to() got an unexpected keyword argument 'walk_up'
```

**位置**

```text
quudet-yolo-lab-backend/app/agent/resource_provisioner.py
```

旧实现使用：

```python
content_target.relative_to(alias.parent, walk_up=True)
```

Linux agent 的 Python 为 `3.11.15`，不支持该参数。

**影响**

资源下载、校验和解压成功后，在创建人类可读的缓存别名软链接时仍会失败；最终 provision plan 被标为 `FAILED`。

**修复状态**

已修复并推送：使用 `os.path.relpath(content_target, start=alias.parent)` 生成相对软链接目标。

---

### P0：VOC manifest 不具备可自动 provision 的完整信息

**现象**

```text
No source URL in manifest
```

**影响**

agent 无法按 manifest 下载 VOC；实验前准备会失败，训练只能退回到 Ultralytics 内置自动下载。

**根因**

旧 VOC manifest 不是一份完整、可验证、可缓存的资源交付描述：缺少有效 archive URL、校验和、文件大小、解压目标和可训练数据 YAML 的明确描述。

**正式修复要求**

为 VOC 建立新版本 manifest，至少包含：

```yaml
resource_id: dataset:voc:2012-yolo
source:
  url: <可下载的 VOC 原始包或已准备 YOLO 包地址>
integrity:
  archive_sha256: <SHA256>
  expected_size_bytes: <精确字节数>
delivery:
  archive_format: zip
  cache_key: <内容哈希>
  target_relative_path: datasets/VOC
validation:
  kind: yolo_dataset
  required_paths:
    - VOC.yaml
    - images/train
    - images/val
    - labels/train
    - labels/val
```

> 不能再使用只有 `VOC.yaml` URL 的占位描述；必须描述可下载、可解压、可验证的实际资源。

---

### P0：原始 VOC 与 YOLO 可训练数据格式不一致

**现象**

当前 provisioner 假设 archive 解压后可直接通过 `yolo_dataset` 校验，即已经存在 YOLO 数据 YAML、`images/*` 与 `labels/*`。

但原始 Pascal VOC 数据通常包含 XML 标注；还需要转换为 YOLO TXT 标签，并生成可训练的 `VOC.yaml`。

**影响**

即使获得正确的原始 VOC 下载 URL，当前 provisioner 也不能单独把原始 VOC 变成 QuuDet 所期望的 YOLO-ready cache。

**正式修复方案（推荐）**

采用“两阶段资源准备”：

1. **原始资源 manifest**：下载并校验 Pascal VOC 原始压缩包。
2. **准备器（preparer）**：解压后执行 VOC XML → YOLO TXT 转换，生成 `VOC.yaml`，并写入内容地址缓存。
3. **训练资源 manifest / receipt**：记录最终 YOLO-ready 目录和版本；训练任务只引用该缓存路径。

目标缓存布局：

```text
/srv/quudet/cache/
  content/<cache-key>/
    VOC.yaml
    images/train/
    images/val/
    labels/train/
    labels/val/
  datasets/VOC -> ../content/<cache-key>/
  receipts/<cache-key>.json
```

---

### P1：COCO8 manifest 的 YAML 校验目标错误

**现象**

```text
YOLO dataset yaml not found: coco8.yaml
```

**根因**

当前下载的 `coco8.zip` 内容布局与 manifest 声明的 `required_paths` / YAML 文件名不一致。

**修复方案**

1. 在注册 manifest 前解压检查 archive 内容。
2. `validation.required_paths` 使用 archive 中真实路径。
3. 如 archive 内 YAML 名称与资源 ID 不同，显式在 manifest 中声明 `data_yaml_path`，不要由资源 ID 推断。
4. 为 manifest 注册增加预检：URL 可访问、SHA256 一致、解压后 required paths 均存在。

---

### P1：重复 agent 与节点容量状态曾不一致

**现象**

曾出现两个 Linux runner 同时轮询同一 `NODE_ID`，导致任务状态和节点占用混乱。

**已完成修复**

- 主控使用数据库条件更新原子预占用单节点容量。
- 空闲 agent 心跳不能将已占用的 `running_jobs` 覆盖为 `0`。
- 节点离线时释放遗留容量。
- agent 运行期间使用独立心跳线程，持续上报运行态。

**运维要求**

同一物理节点必须只运行一个 agent 进程，且各节点必须使用不同的 `NODE_ID`。

---

### P1：旧任务 reconciliation 未完全收尾

**现象**

旧任务因长期无任务心跳被标记为 `FAILED`，但曾遗留：

```text
status = FAILED
dispatch_status = RUNNING_REMOTE
node.running_jobs = 1
```

**影响**

节点实际空闲却不能领取后续任务。

**修复要求**

`reconcile_stuck_jobs()` 将任务标记为失败时，必须同时：

1. 设置 `dispatch_status = FINISHED_REMOTE`；
2. 对 assigned node 执行安全的容量减一；
3. 更新实验组状态；
4. 保证操作幂等。

---

## 4. 临时绕过方案

当前用于验证 AR → QuuDet → Linux → YOLO 执行链路：

```text
EXPERIMENT_PREPARATION_SKIP=true
```

适用范围：

- 节点注册、容量调度、job bundle、Linux agent、YOLO 命令执行和实时遥测验证。

不覆盖：

- LLM 资源发现；
- manifest 生成和审核；
- dataset/weight provision；
- VOC → YOLO 数据准备；
- provision cache 命中。

**注意：** 该开关只应作为调试期临时措施，不能作为正式实验资源交付路径。

---

## 5. 正式修复实施顺序

### 阶段 A：恢复可用的资源交付基础

1. 保留并部署 Python 3.11 软链接兼容修复。
2. 修正 COCO8 manifest 的 archive 内容与 YAML 校验路径。
3. 为 VOC 建立新的、可下载且带 SHA256 的原始数据 manifest。
4. 新增 manifest 注册预检，阻止不完整 manifest 进入 active 状态。

### 阶段 B：实现 VOC 准备器

1. 定义 `preparer_kind: voc_to_yolo`。
2. 解压原始 VOC 后转换 XML 标注为 YOLO labels。
3. 生成稳定的 `VOC.yaml`。
4. 对输出执行 `yolo_dataset` 校验。
5. 写入 receipt，并将最终路径作为 cache URI 返回。

### 阶段 C：恢复实验前准备 gate

1. 关闭：

```text
EXPERIMENT_PREPARATION_SKIP=true
```

2. AR 提交资源需求。
3. QuuDet gate 创建 provision plans。
4. Linux agent 完成 cache provision。
5. 训练命令使用 provisioned cache 中的绝对 `VOC.yaml`，不允许 Ultralytics 再自动下载。

### 阶段 D：验收

对 VOC 执行两次连续实验：

- 第一次：允许下载、转换与缓存建立。
- 第二次：必须命中缓存；日志中不得出现 `Downloading ... VOC`。

---

## 6. 验收标准

正式修复完成必须同时满足：

- [ ] Linux Python 3.11 下 provisioner 不再出现 `walk_up` 异常。
- [ ] VOC manifest 有有效 URL、SHA256、大小、解压规则和真实 required paths。
- [ ] 原始 VOC 能转换为 YOLO-ready 目录。
- [ ] `VOC.yaml`、images 和 labels 均通过数据集校验。
- [ ] 关闭 `EXPERIMENT_PREPARATION_SKIP` 后 AR 能创建实验。
- [ ] Linux agent 自动 provision 成功，plan 为 `READY`。
- [ ] 训练使用 provision cache，不触发 Ultralytics 自动下载。
- [ ] 同一节点容量为 1 时，两个任务严格串行；不同节点仍可并行。
- [ ] agent 监控能显示 active job、PID、命令、最后输出时间与退出码。
- [ ] 失败任务能正确释放 node capacity，并更新 experiment group 状态。

---

## 7. 关联提交

```text
a26cab6  fix: harden remote node capacity handling
adc42dc  feat: report live agent runtime telemetry
```

---

## 8. 当前运行状态

当前 VOC 基线任务已成功进入 Linux YOLO 进程并正在首次下载 VOC 官方数据包；变体任务按节点容量规则排队。

该现象验证了调度、agent、实时遥测与 YOLO 启动链路，但不代表实验前准备模块已通过验收。
