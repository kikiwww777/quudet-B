# AI-Researcher -> Experiment Preparation -> quudet 真实端到端验收方案

日期：2026-07-16  
适用对象：准备验证整条科研实验链路是否已具备“真实数据集 + 真实 GPU + 真实实验闭环”能力的开发者 / AI

关联文档：

- [AI_RESEARCHER_QUUDET_SCHEME_C_INTEGRATION_ACCEPTANCE_PLAN_2026-07-08.md](/C:/Users/86159/Documents/Obsidian%20Vault/AI4Science/07_AI代理/实验模块/quudet/docs/AI_RESEARCHER_QUUDET_SCHEME_C_INTEGRATION_ACCEPTANCE_PLAN_2026-07-08.md)
- [ACCEPTANCE_REPORT_2026-07-10.md](/C:/Users/86159/Documents/Obsidian%20Vault/AI4Science/07_AI代理/实验模块/experiment_preparation/ACCEPTANCE_REPORT_2026-07-10.md)
- [QUUDET_POST_LOCAL_WORKFLOWS_REPORT_2026-07-10.md](/C:/Users/86159/Documents/Obsidian%20Vault/AI4Science/07_AI代理/实验模块/quudet/docs/QUUDET_POST_LOCAL_WORKFLOWS_REPORT_2026-07-10.md)

---

## 0. 一句话目标

这次不是再验：

- `quudet` 能不能起
- 实验前准备模块有没有 API
- 本地统一节点调度能不能跑通玩具任务

而是要验：

```text
AI-Researcher
  -> 生成真实 ExperimentSpec
  -> Experiment Preparation 发现/准备真实数据集与权重
  -> quudet 本地 GPU 节点执行真实训练
  -> metrics / compare 回流
  -> 生成 RoundDecision
  -> （可选）驱动下一轮真实实验
```

整条链是否已经具备“真实实验底座”能力。

---

## 1. 本次验收要回答的问题

1. `AI-Researcher` 是否能生成适用于真实数据集的实验规格？
2. `Experiment Preparation` 是否能对真实数据集做资源发现、就绪检查和提交闸门？
3. `quudet` 是否能在本地 GPU 节点上执行真实数据集训练？
4. 真实训练的 `metrics / compare / summary_text` 是否能稳定回流？
5. `RoundDecision` 是否仍建立在真实数值而非 fallback 摘要之上？
6. 至少一条真实实验路径能否进入第二轮？

---

## 2. 本轮验收边界

### 本轮必须包含

1. 真实公开数据集
2. 本地真实 GPU 节点
3. 实验前准备模块闸门
4. `AI-Researcher -> quudet` 真提交
5. `compare` 回流
6. `RoundDecision` 回流

### 本轮不要求

1. Linux 远程节点
2. 多机调度
3. 超大规模 benchmark（如全量 COCO）
4. 最终论文结论

---

## 3. 推荐真实数据集选择

### 主验收数据集：VOC

推荐理由：

1. 是真正公开 benchmark，不是玩具集
2. 数据规模明显大于 `coco8/coco128`
3. 对本地 GPU（如 RTX 3050 4GB）相对可控
4. 适合作为第一轮真实验收主集

### 副验收数据集：VisDrone

推荐理由：

1. 更真实、更复杂的小目标场景
2. 可验证 `Experiment Preparation` 对半自动准备策略的稳定性
3. 可验证 `quudet` 在复杂数据集下的表现

### 不建议本轮作为主集

- 全量 COCO
- Objects365
- xView

原因：

- 下载与资源成本过高
- 对当前本地 GPU 压力过大
- 会把本轮验收的重点从“链路真实性”转移到“资源承载极限”

---

## 4. 推荐模型与资源约束

### 第一优先级模型

- `yolo11n.pt`

原因：

1. 最容易在 4GB 显存上稳定跑
2. 足够验证真实链路
3. 适合做 baseline

### 可选 variant

- `yolo11s.pt`

用于验证：

- baseline vs variant 的差异是否可被 `compare` 捕获

### 建议训练参数

#### VOC

- `imgsz=512` 或 `640`
- `batch=4`（必要时降到 2）
- `epochs=5` 做首轮真实验收
- `device=cuda`
- `seed=42`

#### VisDrone

- `imgsz=512`
- `batch=2`
- `epochs=2~3`
- `device=cuda`
- `seed=42`

---

## 5. 环境前置条件

本轮开始前，必须先确认：

### 5.1 本地 GPU 环境

1. `nvidia-smi` 可用
2. `torch.cuda.is_available() == True`
3. `ultralytics` 可导入
4. 本地节点已注册为 `node_kind=local`

### 5.2 准备模块

1. `experiment_preparation` 已验收通过
2. `prepare_experiment(spec)` 可返回稳定 `PreparationReport`

### 5.3 quudet

1. 本地统一节点调度已通过
2. 本地 GPU 节点已可领取任务
3. `execution_target=local`
4. `required_gpu=true` 可稳定筛选到 GPU 节点

### 5.4 AI-Researcher

1. `QuudetAdapter` 已接新协议
2. `execution_target / required_gpu` 可透传
3. `SpecAgent` 不再把真实 GPU 实验错误降级成 CPU 玩具实验

---

## 6. 验收分阶段

## Phase A：真实资源准备闸门验收

目标：确认真实数据集不是“手动假装 ready”，而是经过准备模块真实判定。

### A1. VOC 准备

必须确认：

1. `ExperimentSpec` 中请求 `VOC`
2. 准备模块可识别为已知数据集
3. 若本地缺失：
   - 自动下载成功，或
   - 明确给出人工放置路径与动作
4. 准备完成后状态为 `ready`

通过标准：

- `PreparationReport.status == ready`

### A1.5 指定资源发现、下载与落盘正确性（强制）

目标：证明“准备完成”来自本次请求指定资源的正确下载与校验，而不是注册表写死链接、错误 HTML 页面、同名旧文件或人工预置目录造成的假阳性。

#### A1.5.1 每次下载前的可追溯记录

对每个缺失数据集，在调用下载器前记录并保存：

1. `ExperimentSpec.datasets` 中的原始请求名称；
2. 归一化后的数据集名称和注册表命中信息；
3. `DiscoveryResult.success`、`official_url`、`download_url`、`access_mode`；
4. 实际采用的下载 URL 及其来源：`discovery` 或 `registry_fallback`；
5. 预期目标目录、预期 YAML 文件、预期目录结构和校验器名称。

验收要求：发现 URL 存在时，实际使用的 URL 必须是该 `DiscoveryResult.download_url`；只有发现 URL 为空或发现失败时，才允许使用注册表 fallback URL。

#### A1.5.2 正向下载验收：下载到指定资源

主验收先使用 `VOC`，在**干净的数据根目录**运行一次完整准备流程；不能复用已有 `VOC` 目录。

必须确认：

1. 本次请求明确指定 `VOC`，且下载记录中的资源名为 `voc`；
2. 实际请求的 URL 与 A1.5.1 中记录的优先级一致；
3. 新创建的目标目录正好是 `<data_root>/VOC`，不得写入其他数据集目录；
4. 下载产物非零字节，若为压缩包则已成功解压，且没有仅保存 HTML 登录页/错误页后误报成功；
5. `validate_voc`（或当前配置的 VOC 校验器）对新落盘目录校验通过；
6. `VOC.yaml` 指向本次目标目录，并且 train / val（如要求 test）路径都存在且可读取；
7. 删除该临时数据根目录后重新执行，系统会再次完成下载/准备；这证明没有依赖旧目录假阳性。

通过标准：

- `DownloadResult.success == true`；
- `DownloadResult.target_path` 位于本次临时 `data_root/VOC` 之下；
- 下载后 `ValidationResult.name == "voc"`、`local_ready == true`；
- 校验的图像/标签/YAML 结构与 VOC 预期一致；
- 记录能够将 `ExperimentSpec -> DiscoveryResult -> 实际 URL -> target_path -> ValidationResult` 串成一条链。

#### A1.5.3 发现链接优先级验收

使用一个测试用数据集或受控本地 HTTP 服务，使发现结果和注册表 fallback URL 指向不同文件。

必须确认：

1. 发现结果含 URL 时，下载器访问发现 URL，而非注册表 URL；
2. 发现 URL 为空或 `success=false` 时，才访问注册表 URL；
3. 运行后 `KNOWN_DATASETS` 未被改写；
4. 发现 URL 指向的文件内容、文件名或校验值能证明它是预期测试资源。

#### A1.5.4 负向下载验收：不可下载时不得误判 ready

分别覆盖以下情况：

1. 发现 URL 返回 404 / 网络中断；
2. URL 返回 HTML 登录页、授权页或错误页而非数据集压缩包；
3. 压缩包损坏或无法解压；
4. 下载成功但目录结构不符合 `VOC` / `VisDrone` 预期；
5. 指定 `VOC` 却得到 `VisDrone` 或其他数据集内容。

通过标准：

- 不得把任一负向情况标记为 `ready`；
- 部分下载文件必须清理；
- `DownloadResult.error` 说明失败原因；
- `manual_instructions` 包含优先下载链接、官方页面、目标目录、预期 YAML/目录结构和人工放置后复验动作。

### A2. GPU 环境准备

必须确认：

1. `required_gpu=true`
2. 准备模块识别本地 CUDA 可用
3. 不会误判成 CPU 环境

通过标准：

- `PreparationReport.environment.cuda_available == true`

### A3. VisDrone 准备（可选第二步）

必须确认：

1. 资源发现成功
2. 若自动下载失败，有清晰人工兜底动作
3. 不会误判为 ready

通过标准：

- `blocked` 或 `ready` 的逻辑可解释

---

## Phase B：真实单轮实验提交验收

目标：确认不是“准备完成”，而是能真正提交真实实验给 `quudet`。

### B1. 真实 baseline 提交

建议：

- dataset: `VOC`
- model: `yolo11n.pt`
- device: `cuda`
- execution_target: `local`
- required_gpu: `true`
- epochs: `5`

必须确认：

1. `AI-Researcher` 生成的 spec 保留真实数据集名
2. `Experiment Preparation` 放行
3. `QuudetAdapter.submit_experiment_group()` 成功提交
4. `group_id` 写回上下文

通过标准：

- 实验组进入 `PENDING_ASSIGN`

### B2. 本地 GPU 节点领取

必须确认：

1. CPU 节点不会误领
2. GPU 本地节点成功 claim
3. `assigned_node_id` 正确写回

通过标准：

- 真实训练任务进入 `RUNNING`

---

## Phase C：真实训练执行验收

目标：确认不是“调度通过”，而是真正跑了真实数据。

### C1. 训练过程

必须确认：

1. `run.log` 持续写入
2. `results.csv` 真实生成
3. `metrics_cache` 持续更新
4. 训练不因为真实数据集而在目录/YAML/路径层面崩溃

通过标准：

- `metrics` 至少有一条真实 series 回流

### C2. 资源使用

必须确认：

1. 训练确实跑在 GPU 上
2. 若 OOM：
   - 能识别为真实 GPU 压力问题
   - 不是路径/配置层错误

通过标准：

- 若成功：记录 GPU 执行成功
- 若失败：失败原因必须可解释

---

## Phase D：真实 compare 回流验收

目标：确认真实数据下 `compare` 仍然可用。

### D1. 必查字段

1. `primary_metric`
2. `primary_metric_resolved`
3. `aggregates`
4. `delta_vs_baseline`
5. `summary_text`

### D2. 通过标准

- `RoundResult` 可由真实训练结果构造
- `AI-Researcher` 不需要 fallback 才能理解

---

## Phase E：真实 RoundDecision 验收

目标：确认 decision 仍建立在真实实验数值上。

### E1. 必须确认

1. `RoundDecision.reason` 引用真实指标值
2. 不是因为字段缺失才退回保守路径
3. 对真实实验结果能生成合理 `next_action`

### E2. 通过标准

- 首轮 decision 可解释

---

## Phase F：真实第二轮实验验收（可选但强烈建议）

目标：确认整条链不只会“单轮真实实验”，而是能走入第二轮。

### F1. 推荐路径

优先选：

- `stronger_baseline`

原因：

- 路径最稳定
- 最容易复验

### F2. 必须确认

1. 首轮 decision 进入下一轮
2. `generate_next_spec()` 保留真实数据集和 GPU 意图
3. 第二轮实验能再次进入 `quudet`

### F3. 通过标准

- 至少一条真实实验分支能跑到第二轮提交

---

## 7. 建议测试矩阵
### Case 0：指定资源下载正确性（强制前置）

目标：在进入真实 GPU 训练前，证明准备模块会下载 `ExperimentSpec` 指定的数据集，并正确落盘、解压和校验。

步骤：

1. 新建空的临时 `data_root`，确认其中不存在 `VOC`、`VisDrone` 或同名残留目录；
2. 提交只包含 `VOC` 的 `ExperimentSpec`；
3. 保存发现结果、实际 URL、URL 来源和 `DownloadResult.target_path`；
4. 对下载后的目录执行 `validate_voc` 和 YAML 路径检查；
5. 删除临时 `data_root` 后重复一次，确认第二次不是复用第一次的文件；
6. 使用受控本地 HTTP 服务分别测试发现 URL 优先、registry fallback、404、HTML 错误页、损坏压缩包和错误数据集内容。

通过标准：

- 指定 `VOC` 时只在 `<data_root>/VOC` 产生有效资源；
- 下载链条、文件类型、解压结果、目录结构和校验器结果都能证明资源确为 VOC；
- 任何错误资源或下载失败均保持 `blocked`，并返回可执行的人工兜底说明。


### Case 1：VOC + 本地 GPU + baseline

目标：

- 真实主链路最小闭环

配置建议：

- model=`yolo11n.pt`
- data=`VOC`
- epochs=`5`
- imgsz=`512`
- batch=`4`
- execution_target=`local`
- required_gpu=`true`

期望：

- `ready -> submit -> running -> metrics -> compare -> decision`

### Case 2：VOC + 本地 GPU + baseline/variant

目标：

- 验证 compare 与 delta

配置建议：

- baseline=`yolo11n.pt`
- variant=`yolo11s.pt`
- 其余配置保持一致

期望：

- 生成 baseline vs variant 的真实比较结果

### Case 3：VisDrone + 本地 GPU

目标：

- 验证更复杂真实集的准备与执行

配置建议：

- model=`yolo11n.pt`
- epochs=`2~3`
- imgsz=`512`
- batch=`2`

期望：

- 准备阶段可解释
- 执行阶段不因复杂数据集结构直接崩掉

---

## 8. 必须记录的证据
这轮验收必须额外保留以下资源下载证据：

- 原始 `ExperimentSpec.datasets` 与归一化数据集名称；
- `DiscoveryResult` 全量字段，尤其是 `official_url`、`download_url` 和 `access_mode`；
- 实际发起下载的 URL、URL 来源（`discovery` / `registry_fallback`）、HTTP 状态或下载日志；
- `DownloadResult`（`success`、`target_path`、`bytes_downloaded`、`error`、`manual_instructions`）；
- 下载目录树、压缩包/解压文件大小、YAML 内容及 `validate_*` 校验结果；
- 负向用例的失败证据：404/HTML/损坏包/错误资源均未被判为 `ready`。

这轮验收必须保留：

1. `ExperimentSpec`
2. `PreparationReport`
3. `group_id`
4. `job_id`
5. `run.log`
6. `results.csv`
7. `metrics_cache`
8. `compare` 返回 JSON
9. `RoundDecision`

如果进入第二轮，还要再保留一轮对应证据。

---

## 9. 失败分流

如果真实测试失败，按以下类型归因：

### 类型 1：准备层失败

表现：

- 数据集没准备好
- 权重没准备好
- 环境不满足

对应模块：

- `experiment_preparation`

### 类型 2：提交层失败

表现：

- `AI-Researcher` 到 `quudet` 的 payload 断裂

对应模块：

- `QuudetAdapter`

### 类型 3：调度层失败

表现：

- 本地 GPU 节点未领取
- CPU 节点误领 GPU 任务

对应模块：

- `dispatch.py`

### 类型 4：执行层失败

表现：

- 真实训练启动失败
- 路径、yaml、bundle、权重错误

对应模块：

- `quudet agent runner / yolo_runner`

### 类型 5：结果层失败

表现：

- `results.csv` 未回流
- `compare` 字段不完整

对应模块：

- `metrics / compare / result parsing`

### 类型 6：决策层失败

表现：

- `RoundDecision` 退化成模糊判断

对应模块：

- `AI-Researcher loop brain`

---

## 10. 最终通过标准

要判定这条链已经具备“真实实验底座能力”，至少要满足：

1. `VOC` 在本地 GPU 节点上可跑通一轮真实训练
2. `Experiment Preparation` 能对真实数据集做有效闸门
3. `compare` 结果字段完整回流
4. `RoundDecision` 基于真实数值生成
5. 至少一条真实实验路径可进入第二轮
6. 对指定 `VOC` 的全新下载验收通过：发现 URL 优先级正确，资源真实落在 `<data_root>/VOC`，目录/YAML/校验器均证明其为 VOC；
7. 404、HTML 错误页、损坏压缩包、错误数据集内容等失败路径不会被误判为 `ready`。

---

## 11. 一句话总结

如果这轮验收通过，就可以正式说：

> `AI-Researcher + Experiment Preparation + quudet` 已经不只是“本地玩具实验链路”，而是具备了运行真实数据集、真实 GPU 实验并回流科研决策的能力。


