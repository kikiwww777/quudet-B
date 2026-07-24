# QuuDet YOLO Lab：计算机集群架构与 Linux 机房从零部署指南

本文档基于本仓库 **实际代码** 编写，说明「集群」在本平台中的含义、控制面与计算面如何协作，并给出 **Linux 机房环境从 0 到可用** 的操作步骤。

---

## 第一部分：必须先理解的「集群」含义

### 1.1 本系统实现的集群类型

本仓库中的 **集群 = 中心调度 + 多计算节点执行 YOLO 子进程**，**不是** Ultralytics / PyTorch 意义上的 **单机多卡 DDP** 或 **多机多卡分布式训练**。

| 能力 | 本仓库是否实现 | 说明 |
|------|----------------|------|
| 多台 Linux 机器分担「训练 / 验证 / 检测」任务 | **是**（可选） | 通过 `CLUSTER_ENABLED=true` + 各节点运行 **Agent**（`app.agent.runner`）轮询主控、领取任务、本机执行 `yolo` CLI |
| 主控统一 Web/API、任务与日志入库 | **是** | FastAPI + SQLAlchemy（PostgreSQL 或 SQLite） |
| 数据集从主控下发到计算节点 | **是**（有 `dataset_id` 时） | `GET /api/v1/dispatch/job-dataset/{job_id}` 打 ZIP，`x-data-yaml-rel` 标明 yaml 相对路径 |
| 多机 NCCL 分布式训练同一模型 | **否** | 每个任务在 **一台** 节点上起一个 `yolo train` 进程；多机并行 = 多个独立任务，而非一个任务跨节点 |

**结论**：机房若需要「多 GPU 服务器排队跑各自训练任务」，本方案匹配；若需要「一个训练任务自动跨 8 台机器做 DDP」，需要另行引入 DeepSpeed / TorchElastic 等，**不在当前代码范围内**。

### 1.2 与代码的对应关系（便于你或同事审计）

| 概念 | 主要代码位置 |
|------|----------------|
| 是否开启集群 | `quudet-yolo-lab-backend/app/config.py`：`CLUSTER_ENABLED`、`NODE_SHARED_TOKEN`、`NODE_HEARTBEAT_TIMEOUT_SECONDS` |
| 创建任务时是否走本机 Celery | `quudet-yolo-lab-backend/app/api/routes/jobs.py`：`create_job` 在 `CLUSTER_ENABLED` 为真时 **不** 调用 `enqueue_or_run_job` |
| 节点注册 / 心跳 | `quudet-yolo-lab-backend/app/api/routes/nodes.py`：`POST /nodes/register`、`POST /nodes/{id}/heartbeat` |
| 领取任务、上报日志/进度/指标 | `quudet-yolo-lab-backend/app/api/routes/dispatch.py`：`POST /dispatch/claim-next`、`POST /dispatch/events`、`GET /dispatch/job-dataset/{job_id}` |
| 计算节点 Agent 主循环 | `quudet-yolo-lab-backend/app/agent/runner.py`：`run_forever`、`execute_remote_job` |
| 本机异步执行（非集群） | `quudet-yolo-lab-backend/app/tasks/executor.py`：`enqueue_or_run_job` → Celery 或 `SYNC_JOBS` 同步 |
| 数据模型 | `app/models/compute_node.py`、`app/models/job_record.py`（`assigned_node_id`、`dispatch_status` 等） |

### 1.3 非集群模式（默认）数据流

```
浏览器 → Nginx/静态前端 → FastAPI(API)
                              ↓
                    创建 Job → enqueue_or_run_job
                              ↓
              Celery Worker（或 SYNC_JOBS 时在 API 进程内）
                              ↓
                    subprocess: yolo train/val/predict
```

- `docker-compose.yml` 中 **`api` + `worker` + `redis` + `db`** 即该形态。
- `SYNC_JOBS=true` 时 **不经过 Celery**，仅在 API 进程内执行（**禁止**多 Worker 生产）。

### 1.4 集群模式（`CLUSTER_ENABLED=true`）数据流

```
浏览器 → 前端 → FastAPI：创建 Job（状态 PENDING_ASSIGN，不写 Celery 队列）
                         ↑
各 GPU 节点：python -m app.agent.runner
    → POST /nodes/register、heartbeat
    → POST /dispatch/claim-next 领取任务
    →（若有 dataset_id）GET /dispatch/job-dataset/{job_id} 下载 ZIP 并解压
    → subprocess: yolo ...（cwd = 本机 YOLO_WORK_DIR）
    → POST /dispatch/events 回传 log / progress / metrics / status
```

**关键行为**（代码事实）：

- 集群开启后，主控 **不会** 对新建任务调用 `enqueue_or_run_job`，因此 **Celery Worker 不会执行这些任务**。
- 必须由 **至少一台** 已注册且在线的 Agent 通过 `claim-next` 把任务领走并在该机器上跑完。
- Agent 与主控之间的鉴权：`token_hash = SHA256(NODE_SHARED_TOKEN + ":" + NODE_TOKEN)`（见 `nodes.py` / `dispatch.py` 中 `_hash_node_token`）。

---

## 第二部分：机房 Linux 拓扑建议

### 2.1 角色划分

| 角色 | 建议机器数 | 运行内容 |
|------|------------|----------|
| **主控** | 1（可 HA 再扩展，需自行做负载均衡与 DB，超出本文） | Docker Compose 或裸机：`api`、**PostgreSQL**、**Redis**（若仍用 Celery 处理非集群逻辑；纯集群时 Redis 可仅保留给未来扩展）、Nginx 托管前端 |
| **计算节点** | N | 同一套代码 + Python 环境 + CUDA + `yolo`；运行 **`python -m app.agent.runner`**；**强烈建议** `YOLO_WORK_DIR` 与主控 **目录结构一致**（见下文） |

### 2.2 存储与路径一致性（极其重要）

Agent 执行命令时使用：

```text
cwd = settings.resolved_yolo_work_dir   # 见 app/agent/runner.py
```

训练/检测 payload 里的 `model=`、`data=`（内置 cfg）等往往是 **相对 `YOLO_WORK_DIR` 的路径**（例如 `ultralytics-main/ultralytics/cfg/...`）。

因此 **推荐**：

- 将整个工程（含 `ultralytics-main`）放在 **共享文件系统（NFS / Lustre / 企业 NAS）**，主控与各计算节点 **挂载到相同绝对路径**（例如均为 `/srv/yolo26`），并在 `.env` 中设置相同的 `YOLO_WORK_DIR=/srv/yolo26`。
- 若无法 NFS：需在每台机器 **相同路径** 克隆同版本仓库，并同步 `ultralytics-main`、自定义权重等。

**上传数据集**：带 `dataset_id` 的任务，Agent 会从主控 **下载 ZIP** 到本机 `DATA_DIR/artifacts/node_datasets/`，不依赖 NFS 数据集；但 **模型 yaml / 预训练权重路径** 仍相对于各节点 `YOLO_WORK_DIR`。

### 2.3 网络与端口

| 方向 | 端口 | 说明 |
|------|------|------|
| 用户浏览器 → 主控 | `8080`（Nginx 静态）、`8000`（API，可只开内网） | `CORS_ORIGINS` 需包含实际访问前端来源 |
| 计算节点 → 主控 | `8000/tcp`（HTTPS 需自行在前面加反向代理） | `MASTER_API_BASE`，须能从节点 `curl` 通 |
| 主控内部 | `5432`（Postgres）、`6379`（Redis） | 默认仅 Docker 网络暴露即可 |

---

## 第三部分：从 0 开始 — Linux 主控部署（推荐 Docker）

以下假设：机房一台 **Ubuntu 22.04 LTS**（或兼容的 RHEL 系，命令略作替换），有 `sudo`，可访问互联网（或离线镜像已导入）。

### 3.1 操作系统基础

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates curl
```

### 3.2 安装 Docker Engine + Compose 插件

```bash
# 官方文档为准；以下为常见 Ubuntu 流程示例
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# 重新登录 shell 使 docker 组生效
```

验证：

```bash
docker version
docker compose version
```

### 3.3 获取代码

```bash
sudo mkdir -p /srv && sudo chown "$USER":"$USER" /srv
cd /srv
git clone <你的仓库地址> yolo26
cd yolo26
```

确保存在 `ultralytics-main/`（与 README 一致），否则前端模型/数据集下拉会空。

### 3.4 准备主控环境变量（集群 + 生产建议）

在 **`quudet-yolo-lab-backend/.env`**（或仅用 `docker compose` 的 `environment`，二选一，避免重复）中设置，示例：

```env
# 数据库：Compose 已用 Postgres 时与 compose 中一致
DATABASE_URL=postgresql+psycopg2://quudet:quudet@db:5432/quudet

REDIS_URL=redis://redis:6379/0
SECRET_KEY=请使用 openssl rand -hex 32 生成

DISABLE_AUTH=false
FIRST_SUPERUSER_EMAIL=admin@你的域名
FIRST_SUPERUSER_PASSWORD=强密码

# 前端访问地址（逗号分隔）
CORS_ORIGINS=http://主控内网IP:8080,http://机房跳板机域名:8080

SYNC_JOBS=false

# YOLO 工作区：与 compose 中挂载一致
YOLO_WORK_DIR=/workspace
DATA_DIR=/data

# ===== 集群主控 =====
CLUSTER_ENABLED=true
NODE_SHARED_TOKEN=请改为长随机串-主控与所有节点配置需一致参与哈希
NODE_HEARTBEAT_TIMEOUT_SECONDS=60
```

说明：

- **`CLUSTER_ENABLED=true`** 后，新建任务依赖 Agent；请 **先** 启动至少一个 Agent 再提交训练。
- **`NODE_SHARED_TOKEN`**：主控保存；与各节点环境变量 **`NODE_TOKEN`** 组合参与校验（见第一节）。**不要把 `NODE_SHARED_TOKEN` 原样当作 `NODE_TOKEN` 写到文档里误导**；推荐：主控 `NODE_SHARED_TOKEN=A`，每节点 `NODE_TOKEN=B`（可每机不同），注册时传 `B`，主控用 `SHA256(A:B)` 存 `token_hash`。
- 若暂时仍要 **免登录内网**，可 `DISABLE_AUTH=true`（与现有开发习惯一致），但外网 **禁止**。

### 3.5 修改 `docker-compose.yml`（按需）

默认已包含 `db`、`redis`、`api`、`worker`、`web`。在 **纯集群** 且任务全部由 Agent 执行时，`worker` 对 YOLO 任务可能空闲，但仍可保留用于未来扩展或混合改造。

为 **GPU 训练在 Agent 机器上** 进行，主控 `api` 容器 **通常不需要 GPU**。若你仍在主控本机跑非集群任务，再为 `worker` 配置 `deploy.resources.reservations.devices`（见 Docker NVIDIA 文档）。

启动：

```bash
cd /srv/yolo26
docker compose up -d --build
```

### 3.6 验证主控

```bash
curl -s http://127.0.0.1:8000/api/v1/version
```

浏览器打开：`http://<主控IP>:8080`（或映射后的域名）。

---

## 第四部分：从 0 开始 — Linux 计算节点（Agent）

### 4.1 依赖

- Python **3.11+**（与后端 `Dockerfile` 一致便于对齐）
- 已安装 **CUDA 驱动** 与兼容的 **PyTorch（GPU 版）**、`ultralytics`，且 shell 中能执行：

```bash
which yolo
yolo version
```

### 4.2 代码与虚拟环境

**路径建议与主控 `YOLO_WORK_DIR` 一致**（NFS 同路径最佳）：

```bash
cd /srv/yolo26/quudet-yolo-lab-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

复制并编辑 `.env`（节点侧至少需要与 **工作目录、数据目录** 相关项；**数据库可指向主控不可达**——Agent **不连 DB**，只 HTTP 调主控 API）：

```env
YOLO_WORK_DIR=/srv/yolo26
DATA_DIR=/srv/yolo26-node-data
```

`DATA_DIR` 用于本机缓存 `artifacts/node_datasets` 等，**写本地盘即可**。

### 4.3 环境变量（Agent 专用）

在 systemd 或启动脚本中导出：

```bash
export MASTER_API_BASE="http://主控内网IP:8000"
export NODE_ID="gpu-node-01"          # 唯一
export NODE_NAME="机房1号-3090"
export NODE_TOKEN="与主控注册逻辑匹配的节点口令"
export NODE_MAX_CONCURRENCY="1"      # 单卡建议 1
export POLL_INTERVAL_SECONDS="4"
export HEARTBEAT_INTERVAL_SECONDS="5"
```

**`NODE_TOKEN` 与主控 `NODE_SHARED_TOKEN` 的关系**：注册时主控计算 `SHA256(NODE_SHARED_TOKEN + ":" + NODE_TOKEN)` 并存入 `compute_nodes.token_hash`；之后 `claim-next`、`events`、`job-dataset` 均用同一 `NODE_TOKEN` 校验。

### 4.4 前台试运行 Agent

```bash
cd /srv/yolo26/quudet-yolo-lab-backend
source .venv/bin/activate
python -m app.agent.runner
```

正常应看到类似：`[agent] start node_id=gpu-node-01 master=http://...`

主控侧可用 API 文档或 curl（若开启鉴权需带 JWT；`DISABLE_AUTH=true` 时部分接口仍按代码依赖 `get_current_user`——**列表节点**接口需要已登录用户或访客，见 `nodes.py` 的 `list_nodes`）：

- 浏览器前端 **「节点管理」** 页刷新，应出现节点为 **ONLINE**（见 `index.html` / `app.js`）。

### 4.5 systemd 常驻（推荐）

`/etc/systemd/system/quudet-agent.service`：

```ini
[Unit]
Description=QuuDet YOLO Lab compute agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=你的业务用户
WorkingDirectory=/srv/yolo26/quudet-yolo-lab-backend
Environment=MASTER_API_BASE=http://主控IP:8000
Environment=NODE_ID=gpu-node-01
Environment=NODE_NAME=机房1号-3090
Environment=NODE_TOKEN=你的节点口令
Environment=NODE_MAX_CONCURRENCY=1
ExecStart=/srv/yolo26/quudet-yolo-lab-backend/.venv/bin/python -m app.agent.runner
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now quudet-agent.service
sudo systemctl status quudet-agent.service
```

---

## 第五部分：业务使用与排障

### 5.1 前端与「执行节点」

前端已包含 **「执行节点」** 下拉（`index.html` 中 `sel-train-node` 等）与 **「节点管理」**（`app.js` 会请求节点列表）。**集群关闭时** 相关 API 返回 400，界面可能无数据或报错——属预期。

集群开启、Agent 在线后：

- 创建训练/验证/检测任务时，可选择 **指定节点** 或 **自动**（`claim-next` 会为未分配任务自动绑定节点，见 `dispatch.py`）。

### 5.2 训练任务与 `dataset_id`

- 使用 **上传数据集** 并关联 `dataset_id` 时，Agent 会 **从主控下载数据集 ZIP** 并改写 payload 中的 `data` 为本地绝对路径。
- 仅使用 **内置 coco8.yaml** 等相对路径时，依赖各节点 **`YOLO_WORK_DIR` 下存在相同相对路径文件**。

### 5.3 日志与指标

- 远程任务日志通过 `dispatch/events` 追加到主控 `artifacts/<job_id>/run.log`（与本地执行类似路径逻辑）。
- Agent 会尝试读取本机 `results.csv` 并通过 `metrics` 事件上报快照（见 `runner.py`）；若路径与主控 `job_metrics` 扫描习惯不一致，监控曲线可能不完整——属于 **跨机路径差异** 类问题，需统一 `project`/`name` 与 `YOLO_WORK_DIR` 下 `runs` 布局。

### 5.4 常见问题

1. **任务一直 `PENDING_ASSIGN`**  
   - Agent 未启动 / `MASTER_API_BASE` 不可达 / `NODE_TOKEN` 与主控 `NODE_SHARED_TOKEN` 不匹配导致 401。

2. **节点显示 OFFLINE**  
   - 调大 `NODE_HEARTBEAT_TIMEOUT_SECONDS`；检查 Agent 进程与网络。

3. **数据集下载失败**  
   - 任务未带 `dataset_id` 却依赖上传数据；或主控无法打包 `extracted_path`。

4. **集群开了但想本机 Celery 也跑**  
   - 当前代码 **二选一**：`CLUSTER_ENABLED=true` 时主控不 enqueue。若需混合模式，需二次开发（例如按任务类型分支）。

---

## 第六部分：安全检查清单（上线前必做）

- [ ] 修改 `SECRET_KEY`、数据库密码、管理员密码  
- [ ] 外网部署：`DISABLE_AUTH=false`，仅 HTTPS + 防火墙  
- [ ] `NODE_SHARED_TOKEN` / 各 `NODE_TOKEN` 高强度、轮换流程  
- [ ] 仅内网暴露 `8000`，或经 Nginx `limit_req` / VPN  
- [ ] 备份 PostgreSQL 与 `/data` 卷（上传与 artifacts）  

---

## 附录 A：最小「手工验收」脚本思路

1. 主控 `CLUSTER_ENABLED=true`，启动 Compose。  
2. 一台 GPU 节点启动 Agent，`NODE_ID` 唯一。  
3. 浏览器打开前端 → **节点管理** 看到在线。  
4. 提交小任务 `coco8`、`epochs=1`、选择节点或自动。  
5. 任务管理页状态变为 **RUNNING** → **SUCCESS**，可下载/查看日志。

---

## 附录 B：README 中 Agent 示例与代码的一致性说明

仓库根 `README.md` 中曾出现将 `NODE_TOKEN` 设为与 `NODE_SHARED_TOKEN` 相同字符串的写法；在代码里二者参与 **不同位置** 的拼接哈希。**推荐**按本文 **主控 `NODE_SHARED_TOKEN` + 每节点 `NODE_TOKEN`** 理解配置，避免混淆。

---

*文档版本：与仓库代码 `CLUSTER_ENABLED` / `app.agent.runner` / `dispatch` 路由实现同步整理；若你升级了后端逻辑，请以代码为准并更新本节。*
