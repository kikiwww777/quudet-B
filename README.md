# QuuDet YOLO Lab（生产化前后端）

**从零使用全流程（环境 → 界面操作顺序）见：[docs/从零开始使用指南.md](docs/从零开始使用指南.md)。**

本仓库包含：

- `quudet-yolo-lab/`：前端静态页（仪表盘 / 数据集 / 训练 / 测试 / 检测 / 任务）
- `quudet-yolo-lab-backend/`：FastAPI + JWT + PostgreSQL + Celery(Redis) + Ultralytics 任务执行器
- 前端下拉选项由 **`GET /api/v1/options/yolo`** 扫描 `ultralytics-main/ultralytics/cfg/models` 与 `.../cfg/datasets` 动态生成（需仓库内存在 `ultralytics-main` 且 `YOLO_WORK_DIR` 指向含该目录的根）
- `docker-compose.yml`：一键启动 API、`worker`、数据库、Redis、Nginx 静态站点

## 登录 / 免登录

- 默认 **`DISABLE_AUTH=true`**：前端**无需登录**，API 使用内置访客账号执行任务（适合内网/本机）。
- 若需登录保护：在后端环境变量设置 **`DISABLE_AUTH=false`**，并修改 `SECRET_KEY`、管理员密码；此时可用 `POST /api/v1/auth/login` 获取 Token（前端已去掉登录页，需自行在请求头带 `Authorization: Bearer ...` 或使用 API 文档调试）。

可选管理员账号（在未禁用访客模式时仍会被创建，便于日后开启登录）：

- 邮箱：`admin@quudet.local`
- 密码：`admin123`（**上线前务必修改**）

## Docker 启动（推荐）

在项目根目录执行：

```bash
docker compose up --build
```

然后打开：

- Web UI：`http://localhost:8080`
- API 文档：`http://localhost:8000/docs`（或通过 Nginx 也可直接访问后端端口）

说明：

- 宿主机的工程目录会挂载到容器的 `/workspace`，`YOLO_WORK_DIR=/workspace`，因此 **Ultralytics 相关路径应写成相对仓库根目录**，例如：
  - `ultralytics-main/ultralytics/cfg/models/v8/yolov8n.yaml`
  - `ultralytics-main/ultralytics/cfg/datasets/coco8.yaml`
  - `yolov8n.pt`（首次会自动下载权重）

## Windows 本机开发（不装 Docker）

1. 安装 Python 3.11+、Redis（或设置 `SYNC_JOBS=true` 走同步执行）
2. 进入后端目录并安装依赖：

```powershell
cd quudet-yolo-lab-backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

3. 启动 API：

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

4. 如需异步任务队列，再开一个终端启动 Celery worker（需 Redis）：

```powershell
cd quudet-yolo-lab-backend
.\.venv\Scripts\activate
celery -A app.celery_app worker -l info
```

5. 前端可直接打开 `quudet-yolo-lab/index.html`（需要浏览器允许跨域，或使用 VS Code Live Server）。

   - 前端默认连接 `http://127.0.0.1:8000`，可在登录页的「API 地址」中修改。

## 生产注意事项

- 更换强随机 `SECRET_KEY`、数据库密码与管理账号密码
- **不要**在生产环境开启 `SYNC_JOBS`
- SQLite 不适合多进程 Worker；Docker Compose 已使用 PostgreSQL
- GPU：需要宿主 NVIDIA 环境 + `nvidia-container-toolkit`，并为 `worker`/`api` 配置 `deploy.resources.reservations.devices`

## 中心调度集群模式（主控 + 多节点）

已支持主控节点统一调度远端工作节点执行 YOLO 任务（**调度型多机**，非多机 DDP 单任务）。**完整架构说明、Linux 机房从 0 部署、systemd、存储与鉴权** 见：

**[docs/计算机集群与Linux机房部署实施方案.md](docs/计算机集群与Linux机房部署实施方案.md)**

简要步骤：

1. 在主控后端 `.env` 开启：

```env
CLUSTER_ENABLED=true
NODE_SHARED_TOKEN=主控侧长随机串
NODE_HEARTBEAT_TIMEOUT_SECONDS=20
```

2. 启动主控 API（与现有启动方式一致）。
3. 在每台工作机启动 agent（能访问主控 API、本机可执行 `yolo`；**`NODE_TOKEN` 为节点口令**，主控用 `SHA256(NODE_SHARED_TOKEN + ":" + NODE_TOKEN)` 校验，勿与文档外的错误示例混淆）：

```powershell
cd quudet-yolo-lab-backend
.\.venv\Scripts\activate
$env:MASTER_API_BASE="http://主控IP:8000"
$env:NODE_ID="node-gpu-01"
$env:NODE_NAME="3090-1号机"
$env:NODE_TOKEN="每节点独立或统一的节点口令"
python -m app.agent.runner
```

4. 前端「节点管理」、训练/测试/检测「执行节点」、任务列表按节点筛选与远端日志/指标回传。

Linux 机房 **systemd、Docker、NFS、防火墙** 等从 0 操作见上文链接文档。
