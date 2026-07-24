# AR → QuuDet Linux 节点资源预置系统 · 实现说明

> 基于设计文档 `AR_QUUDET_LINUX_NODE_RESOURCE_PROVISIONING_DESIGN_2026-07-17.md` 实现。
> 施工日期：2026-07-17 ｜ 最后更新：2026-07-17

---

## 目录

1. [架构总览](#1-架构总览)
2. [文件清单](#2-文件清单)
3. [数据库模型](#3-数据库模型)
4. [API 端点](#4-api-端点)
5. [预置状态机](#5-预置状态机)
6. [核心安全约束](#6-核心安全约束)
7. [Linux 缓存布局](#7-linux-缓存布局)
8. [下载等级](#8-下载等级)
9. [实验准备门控](#9-实验准备门控)
10. [Agent 运行循环](#10-agent-运行循环)
11. [启动与验证](#11-启动与验证)
12. [故障排查](#12-故障排查)

---

## 1. 架构总览

```
┌──────────────────────────────────────────────┐
│  AR 资源发现 (外部系统)                       │
│  发现来源 → 许可证 → 生成不可变清单            │
└──────────────────┬───────────────────────────┘
                   │ POST /api/v1/resources/manifests
                   ▼
┌──────────────────────────────────────────────┐
│  QuuDet Master (Windows)                     │
│                                              │
│  ┌──────────────────┐  ┌──────────────────┐  │
│  │ ResourceManifest  │  │ ProvisionPlan    │  │
│  │ delivery.cache_key│  │ 唯一约束 + 状态机 │  │
│  │ 由服务端强制覆写   │  │ 超时回收          │  │
│  └──────────────────┘  └──────────────────┘  │
│                                              │
│  ┌──────────────────────────────────────┐    │
│  │ Experiment Preparation Gate          │    │
│  │ ① 发现资源需求 → 查 Manifest         │    │
│  │ ② 查节点缓存 → 命中? → 201 CREATED   │    │
│  │ ③ 未命中 → 创建 ProvisionPlan + 412  │    │
│  │ ④ IntegrityError → 返回已有计划      │    │
│  └──────────────────────────────────────┘    │
│                                              │
│  ┌──────────────────────────────────────┐    │
│  │ Reconciliation                       │    │
│  │ 训练超时回收 + 预置计划超时回收        │    │
│  └──────────────────────────────────────┘    │
└──────────────────┬───────────────────────────┘
                   │ claim-next / events / receipt
                   ▼
┌──────────────────────────────────────────────┐
│  Linux Agent (GPU 节点)                      │
│                                              │
│  /srv/quudet/cache/                          │
│    content/<safe_dirname>/ (Windows 兼容)     │
│    receipts/<cache_key>.json (含 resource_id)│
│    datasets/VOC → symlink                    │
└──────────────────────────────────────────────┘
```

### 关键设计决策

| 决策 | 理由 |
|------|------|
| **`delivery.cache_key` 服务端强制覆写** | 拒绝客户端提交值，防止不一致（[P1]） |
| **cache_key 解析顺序** | `manifest_content_hash` → `delivery.cache_key`（[P1]） |
| **ProvisionPlan 去重** | 应用层 + 数据库级 partial unique index（[P1]） |
| **缓存损坏后必须重新解压** | 删除 content 目录后 `needs_extract` 标志确保解压执行（[P0]） |
| **缓存重新验证** | receipt 校验 archive_sha256，不匹配则删除重解压 |
| **206 校验** | Range 续传时必须 206，否则丢弃 .part 重新下载 |
| **路径遍历防护** | `Path.relative_to()` 替代 `str.startswith()` |
| **Windows 兼容** | `sha256:` 中的冒号替换为下划线作目录名 |
| **Gate 阻塞预置** | provisioning 状态返回 412，不创建实验 |

---

## 2. 文件清单

### 数据库模型

| 文件 | 说明 |
|------|------|
| `app/models/resource_manifest.py` | `ResourceManifest` — 不可变资源清单 |
| `app/models/provision_plan.py` | `ProvisionPlan` — 预置计划（状态机 + 唯一约束） |
| `app/models/compute_node.py` | **已修改** — 新增 `cache_root`, `cache_free_bytes`, `resource_cache` |

### Pydantic Schema

| 文件 | 说明 |
|------|------|
| `app/schemas/provisioning.py` | Manifest/Provision/Receipt 全套 Schema（`ManifestDelivery.cache_key` 标记 `exclude=True`）|
| `app/schemas/node.py` | **已修改** — `CacheResourceEntry`, 心跳缓存字段 |

### API 路由

| 文件 | 说明 |
|------|------|
| `app/api/routes/resources.py` | 5 个端点 — 创建时强制覆写 `delivery.cache_key` |
| `app/api/routes/provisioning.py` | 5 个端点 — 服务端派生 `cache_key` + 去重 |
| `app/api/routes/nodes.py` | **已修改** — 心跳处理缓存清单 |
| `app/api/routes/experiments.py` | **已修改** — 412 响应含 `provision_plan_ids` |
| `app/main.py` | **已修改** — 注册新路由 |

### Agent

| 文件 | 说明 |
|------|------|
| `app/agent/resource_provisioner.py` | 下载/校验/解压/缓存引擎（Windows 安全目录名） |
| `app/agent/runner.py` | **已修改** — 预置循环 + 缓存注入 + `resource_id` 绑定 |

### 实验准备门控

| 文件 | 说明 |
|------|------|
| `experiment_preparation/__init__.py` | 包标记 |
| `experiment_preparation/quudet_adapter.py` | 门函数 + `IntegrityError` 恢复 |

### 数据迁移与后台服务

| 文件 | 说明 |
|------|------|
| `alembic/versions/004_add_resource_provisioning.py` | 新建表 + 扩展 `compute_nodes` + partial unique index |
| `alembic/env.py` | **已修改** — 导入新模型 |
| `app/services/reconciliation.py` | **已修改** — 预置计划超时回收 |

---

## 3. 数据库模型

### 3.1 `resource_manifests`

| 字段 | 类型 | 说明 |
|-------|------|---------|
| `id` | UUID (PK) | 主键 |
| `resource_id` | str(256) | 标识符 |
| `resource_type` | str(32) | `dataset` / `weight` / `bundle` |
| `version` | str(128) | 资源版本 |
| `display_name` | str(512) | 人类可读名称 |
| `source` | JSON | `{kind, url, official_page, license}` |
| `integrity` | JSON | `{archive_sha256, expected_size_bytes}` |
| `delivery` | JSON | `{archive_format, extract_subdir, target_relative_path, allow_resume}` — **`cache_key` 由服务端强制覆写** |
| `validation` | JSON | `{kind, yaml_relative_path, required_paths}` |
| `manual_fallback` | JSON | `{allowed, instructions}` |
| `manifest_content_hash` | str(128) **UNIQUE** | `sha256:<json_core_hash>` — **cache_key 权威来源** |

### 3.2 `provision_plans`

| 字段 | 类型 | 说明 |
|-------|------|---------|
| `id` | UUID (PK) | 主键 |
| `node_id` | str (FK) | 目标节点 |
| `manifest_id` | str (FK) | 关联清单 |
| `cache_key` | str(128) | **服务端派生** |
| `state` | str(32) | 状态机 |
| `archive_sha256` | str(128) | 校验通过的归档哈希 |
| `bytes_downloaded` | bigint | 下载字节 |
| `local_uri` | str(1024) | 缓存 URI |

**唯一约束：** `UNIQUE INDEX (node_id, cache_key) WHERE state NOT IN ('READY', 'FAILED')`

- 应用层 + 数据库层双重去重
- 并发请求不会创建重复计划
- `IntegrityError` 时 Gate 自动回滚并返回已有计划

### 3.3 `compute_nodes` 新增字段

| 字段 | 说明 |
|-------|---------|
| `cache_root` | 缓存根目录 |
| `cache_free_bytes` | 可用空间 |
| `resource_cache` | `[{resource_id, cache_key, status, verified_at, local_uri}]` |

### 3.4 Provision Receipt（磁盘文件）

`<cache_root>/receipts/<safe_filename>.json`，文件名中 `:` 替换为 `_`（Windows 兼容）：

```json
{
  "provision_id": "uuid",
  "resource_id": "dataset:voc:2012-yolo",
  "state": "READY",
  "cache_key": "sha256:<hash>",
  "archive_sha256": "verified digest",
  "bytes_downloaded": 5000,
  "local_uri": "cache://datasets/VOC"
}
```

---

## 4. API 端点

### 4.1 资源清单管理

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/v1/resources/manifests` | superuser JWT | 创建立方（幂等） |
| GET | `/api/v1/resources/manifests` | JWT | 列表 |
| GET | `/api/v1/resources/manifests/{id}` | JWT | 详情 |
| GET | `/api/v1/resources/manifests/{id}/for-node` | **节点令牌 (QP)** | Agent 专用 |
| POST | `/api/v1/resources/manifests/{id}/approve` | superuser JWT | 审批 B 级清单 |

**注意：** `POST /manifests` 时，客户端传入的 `delivery.cache_key` 会被服务端强制覆写为 `manifest_content_hash`。客户端传入的值被忽略。

### 4.2 预置计划生命周期

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/v1/provisioning` | JWT | 创建计划（cache_key 服务器派生） |
| GET | `/api/v1/provisioning` | JWT | 列表（`?node_id=&state=`） |
| GET | `/api/v1/provisioning/{id}` | JWT | 轮询状态 |
| POST | `/api/v1/provisioning/claim-next` | 节点令牌 | Agent 认领 |
| POST | `/api/v1/provisioning/{id}/events` | 节点令牌 | 事件上报 |

### 4.3 节点管理（修改）

`POST /api/v1/nodes/{id}/heartbeat` 支持缓存清单：

```json
{
  "token": "...",
  "cache_root": "/srv/quudet/cache",
  "cache_free_bytes": 500000000000,
  "resource_cache": [{
    "resource_id": "dataset:voc:2012-yolo",
    "cache_key": "sha256:<hash>",
    "status": "READY",
    "local_uri": "cache://datasets/VOC"
  }]
}
```

---

## 5. 预置状态机

```
                    ┌──────────┐
                    │  PENDING  │ ← Gate 创建（唯一约束去重）
                    └────┬─────┘
                         │ claim-next
                         ▼
                    ┌──────────────┐
                    │  DOWNLOADING  │ ← HTTP GET（206 必检）
                    └──────┬───────┘
                           │ SHA256 校验通过
                           ▼
                    ┌──────────────┐
                    │  VERIFYING    │ ← 解压 + YOLO 结构验证
                    └──────┬───────┘
                     ┌─────┴──────┐
                     ▼            ▼
              ┌─────────┐   ┌──────────┐
              │  READY   │   │  FAILED   │ ← 超时回收
              └─────────┘   └──────────┘
                     │
              ┌──────┴──────┐
              ▼             ▼
      ┌───────────────┐ ┌────────────────┐
      │ 201 CREATED    │ │ 超时回收       │
      │ (门控放行)      │ │ (reconciliation│
      └───────────────┘ │  2h → FAILED)  │
                        └────────────────┘
```

### 严格状态迁移

| 从 → 到 | PENDING | DOWNLOADING | VERIFYING | READY | FAILED |
|--------|---------|-------------|-----------|-------|--------|
| PENDING | — | claim-next | — | — | 超时 2h |
| DOWNLOADING | ✗ | — | 下载完成 | — | 失败/超时 |
| VERIFYING | ✗ | ✗ | — | 校验通过 | 失败/超时 |
| READY | ✗ | ✗ | ✗ | — | — |
| FAILED | ✗ | ✗ | ✗ | ✗ | — |

---

## 6. 核心安全约束

### 6.1 `delivery.cache_key` 服务端强制覆写

```python
# resources.py — 创建 Manifest 时
manifest.delivery["cache_key"] = cache_key  # 覆写客户端传入值

# resource_provisioner.py — Agent 读取时
cache_key = m.get("manifest_content_hash") or delivery.get("cache_key") or ""
#          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ 优先使用 manifest_content_hash
```

### 6.2 预置计划唯一约束（双重去重）

```python
# 模型层：partial unique index
__table_args__ = (
    Index("ix_provision_plans_active_unique",
          "node_id", "cache_key", unique=True,
          sqlite_where=text("state NOT IN ('READY', 'FAILED')")),
)

# 应用层：IntegrityError 恢复
except IntegrityError:
    db_session.rollback()
    existing = db_session.query(ProvisionPlan).filter(
        ProvisionPlan.node_id == node_id,
        ProvisionPlan.cache_key == cache_key,
        ProvisionPlan.state.in_(["PENDING", "DOWNLOADING", "VERIFYING"]),
    ).first()
    return existing.id  # 返回已有计划
```

### 6.3 缓存损坏恢复（P0 修复）

```
if content_target.exists():
    校验 receipt → 通过 → 返回 (OK)
    不通过 → shutil.rmtree(content_target)
    # 不进入 else 分支，但 needs_extract 仍为 True
if needs_extract:  ← 无论 content 是否存在，都执行解压
    解压 → 验证 → 原子重命名
```

### 6.4 缓存解析绑定 `resource_id`（防跨数据集）

```python
# runner.py — 三级查找
resource_id = _resolve_data_to_resource_id(data_ref)
if resource_id:
    for entry in inv:
        if entry.get("resource_id") != resource_id:
            continue  # 跳过不匹配的资源
        # 找到匹配 → 返回路径
```

### 6.5 Windows 兼容目录名

```python
def _safe_cache_dirname(cache_key: str) -> str:
    """sha256:abc → sha256_abc（Windows 不允许冒号作路径名）"""
    return cache_key.replace(":", "_")
```

### 6.6 其他安全措施

| 措施 | 实现 |
|------|------|
| 路径遍历防护 | `Path.relative_to()` |
| 归档大小限制 | 10 GiB |
| 文件数限制 | 100,000 |
| 符号链接检查 | 解压根目录内检查 |
| 206 校验 | Range 请求必须返回 206 |

---

## 7. Linux 缓存布局

```
/srv/quudet/cache/
├── staging/<provision-id>/download.part
├── archives/<sha256>.tar
├── content/<safe_dirname>/        ← Windows 兼容（sha256_abc 而非 sha256:abc）
├── datasets/VOC → ../content/...
├── weights/yolo11s.pt → ../content/...
└── receipts/<safe_filename>.json  ← 文件名含 resource_id
```

### 原子性规则

1. 下载到 `staging/`（HTTP Range 续传，必须 206）
2. SHA256 校验后复制到 `archives/`
3. 解压到 `.tmp_<id>` → `Path.relative_to()` 防路径穿越
4. 运行 YOLO 校验器
5. **原子重命名** → `content/<safe_dirname>/`
6. 写入 receipt（含 `resource_id`）
7. 创建符号链接别名

---

## 8. 下载等级

| 等级 | `archive_sha256` | `integrity_approved` | Agent 行为 | 结果 |
|-------|-----------------|---------------------|-------------|------|
| **A** | 非空 | — | 自动下载 | `READY` |
| **B** | — | `false` | 不下 | `MANUAL_REQUIRED` → 412 |
| **B** | — | `true` | 审批后下载 | `READY` |
| **C** | — | fallback | 人工放置 | `MANUAL_REQUIRED` |

---

## 9. 实验准备门控

### 9.1 完整流程

```
POST /api/v1/experiments
  │
  ├─ 门控 check_experiment_group(body)
  │   ├─ 无资源需求 → 201
  │   ├─ 查 Manifest
  │   │   ├─ 找不到 → 412 blocked
  │   │   └─ 需审批未审批 → 412 manual_required
  │   │
  │   └─ 查节点缓存
  │       ├─ 命中 → 201 CREATED
  │       ├─ 未中 → 创建 ProvisionPlan
  │       │   ├─ IntegrityError → 返回已有计划
  │       │   └─ 正常 → 新计划
  │       └─ 412 provisioning (+ provision_plan_ids)
  │
  └─ 客户端轮询流程:
      ① GET /api/v1/provisioning/{plan_id}
      ② 全部 READY → 重试 POST /api/v1/experiments
```

### 9.2 412 响应示例

**provisioning：**
```json
{
  "detail": {
    "message": "Resources are being provisioned — poll provision_plan_ids and retry",
    "preparation_report": {
      "status": "provisioning",
      "provision_plan_ids": ["uuid-1"],
      "actions": [...]
    }
  }
}
```

**blocked：**
```json
{
  "detail": {
    "message": "Experiment preparation is blocked",
    "preparation_report": {
      "status": "blocked",
      "actions": [{"type": "blocked", "reason": "no manifest"}]
    }
  }
}
```

### 9.3 内置资源映射

| 名称 | 资源 ID |
|------|---------|
| `voc`, `voc2012` | `dataset:voc:2012-yolo` |
| `coco8` | `dataset:coco:coco8-yolo` |
| `coco128` | `dataset:coco:coco128-yolo` |
| `coco` | `dataset:coco:2017-yolo` |
| `visdrone` | `dataset:visdrone:2019-yolo` |
| `yolo11n.pt` | `weight:ultralytics:yolo11n` |

---

## 10. Agent 运行循环

```
run_forever()
├─ 注册节点
└─ loop:
    ├─ 心跳 (含缓存清单)
    ├─ 预置循环 (≤3 个):
    │   ├─ claim-next provision (节点令牌)
    │   ├─ fetch manifest: GET /for-node?node_id=&token=
    │   ├─ ResourceProvisioner.provision()
    │   └─ report receipt
    └─ 训练任务:
        ├─ claim-next job
        ├─ _resolve_cached_data():
        │   1. cache:// URI → 直接查找
        │   2. resource_id 匹配 → 精确查找
        │   3. 无匹配 → 别名查找
        │   4. 仅一个缓存 → 降级查找
        ├─ 有缓存 → 注入 payload["data"]
        ├─ 无缓存 → _download_dataset_bundle()
        └─ yolo train/val/detect
```

### 环境变量

| 变量 | 默认 | 说明 |
|--------|-------|------|
| `PROVISION_CACHE_ROOT` | `/srv/quudet/cache` (Linux) / `<artifacts>/provision_cache` (Win) | 缓存根 |
| `NODE_KIND` | `local` | `remote` = Linux |
| `NODE_TOKEN` | 自动生成 | 认证令牌 |

---

## 11. 启动与验证

### 11.1 开发

```bash
cd quudet-yolo-lab-backend
uvicorn app.main:app --reload
python -m app.agent.runner  # Windows Agent
```

### 11.2 Linux Agent

```bash
NODE_KIND=remote NODE_ID=lnx-01 NODE_TOKEN=secret \
  python -m app.agent.runner
```

### 11.3 验证

```bash
# 缓存命中 → 201
curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8000/api/v1/experiments \
  -H 'Content-Type: application/json' \
  -d '{"name":"test","dataset_name":"voc","runs":[{"role":"baseline","job_type":"train","payload":{}}]}'
# → 201

# 未审批 → 412
curl -s -X POST http://localhost:8000/api/v1/experiments \
  -H 'Content-Type: application/json' \
  -d '{"name":"blocked","dataset_name":"visdrone","runs":[{"role":"baseline","job_type":"train","payload":{}}]}'
# → 412

# Node GET manifest
curl "http://localhost:8000/api/v1/resources/manifests/<id>/for-node?node_id=lnx-01&token=secret"
# → 200
```

### 11.4 轮询

```bash
curl http://localhost:8000/api/v1/provisioning/<plan_id>
# → {"state": "READY", ...}
# 全部 READY 后重试实验创建
```

---

## 12. 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `/for-node` 401 | 节点令牌不匹配 | 检查 `NODE_TOKEN` 和 `NODE_SHARED_TOKEN` |
| 412 provisioning 不消失 | Agent 离线 | `GET /api/v1/provisioning` 查看状态 |
| 计划一直 PENDING | Agent 未运行 | 确认节点 ONLINE |
| 计划超时 FAILED | 网络慢/中断 | 检查日志 |
| B 级清单 blocked | 未 approve | `POST /manifests/{id}/approve` |
| 训练用错数据集 | resource_id 不匹配 | 检查 receipt 中的 `resource_id` |
| Windows 路径错误 | 冒号未替换 | 使用 `_safe_cache_dirname()` |

---

## 附录：修复记录

| 时间 | 修复 | 影响文件 |
|------|------|----------|
| v1 | P0-1: Agent 认证 | `resources.py`, `runner.py` |
| v1 | P0-2: Gate 阻塞 | `quudet_adapter.py`, `experiments.py` |
| v1 | P0-3: 缓存注入 | `runner.py` |
| v1 | P1: cache_key 伪造 | `provisioning.py`, `schemas/provisioning.py` |
| v1 | P1: receipt 缺 resource_id | `resource_provisioner.py` |
| v1 | P1: 超时回收 | `reconciliation.py` |
| v1 | P1: 206 校验 | `resource_provisioner.py` |
| v1 | P1: 路径穿越 | `resource_provisioner.py` |
| **v2** | **P0: 缓存损坏不再解压** | `resource_provisioner.py` |
| **v2** | **P1: delivery.cache_key 覆写** | `resources.py`, `resource_provisioner.py`, `schemas/provisioning.py` |
| **v2** | **P1: 缓存解析绑定 resource_id** | `runner.py` |
| **v2** | **P1: DB 唯一约束** | `provision_plan.py`, `alembic/004_*.py` |
| **v2** | **P1: IntegrityError 恢复** | `quudet_adapter.py` |
| **v2** | **Other: Windows 路径兼容** | `resource_provisioner.py` |
| **v3** | **P0: Gate 后端不可用时 fail-closed** | `quudet_adapter.py` |
| **v3** | **P1: 数据集与模型权重均纳入资源需求** | `quudet_adapter.py` |
| **v3** | **P1: 多资源求单节点交集并绑定训练 Job** | `quudet_adapter.py`, `experiments.py` |
| **v3** | **P1: 过滤满载与无 GPU 节点** | `quudet_adapter.py` |

### v3 无 Linux 验证

- `python -m unittest experiment_preparation.test_quudet_adapter`：7 项通过，覆盖 fail-closed、权重资源收集、GPU 约束、显式目标冲突、非托管多节点实验和多资源节点交集。
- `python -m unittest experiment_preparation.test_resource_download_flow`：12 项通过。
- Linux 节点上线后仍须执行真实验收：`412 provisioning → 节点 READY → 重试创建 → 指定节点训练并使用缓存`。
