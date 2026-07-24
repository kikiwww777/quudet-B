下面给你一份**不用 Docker、从 0 开始跑通这个项目**的实操指南，场景按你现在的需求来写：

- **主控端：Windows**
- **计算节点：Linux**
- **同一内网**
- **主控负责页面/API/任务调度**
- **Linux 节点负责实际训练**
- **不使用 Docker**

这份指南分成两段：

1. **先跑通最小可用版本**
2. **再接 Linux 节点形成集群**

## 一、先明确架构
你这个项目在不开 Docker 时，本质上要手动启动这几部分：

### Windows 主控端
- `FastAPI` 后端：`quudet-yolo-lab-backend`
- 数据库：`PostgreSQL` 或 `SQLite`
- Redis：给 Celery/异步任务用
- 前端静态页：`quudet-yolo-lab/index.html`

### Linux 计算节点
- Python 环境
- YOLO 运行环境
- `app.agent.runner`

如果你是**纯集群模式**，训练任务由 Linux 节点 Agent 执行，Windows 主控主要跑：

- API
- 数据库
- Redis
- 前端静态页

---

## 二、推荐的目录规划

### Windows 主控
假设项目放这里：

```powershell
D:\yolo26
```

目录里要有这些：

- `D:\yolo26\quudet-yolo-lab-backend`
- `D:\yolo26\quudet-yolo-lab`
- `D:\yolo26\ultralytics-main`

### Linux 节点
建议每台节点也放成统一路径，比如：

```bash
/srv/yolo26
```

这样后面 `YOLO_WORK_DIR` 更容易统一。

---

## 三、Windows 主控端从 0 开始

## 1. 安装基础软件
在 Windows 主控上安装：

1. **Python 3.11**
2. **Git for Windows**
3. **PostgreSQL**
4. **Redis**
5. 可选：**NSSM**  
   用来把 API/Redis/前端服务做成 Windows 服务，后面长期运行更方便

### 先检查版本
打开 PowerShell：

```powershell
python --version
git --version
```

---

## 2. 拉取项目代码
如果还没拉代码：

```powershell
cd D:\
git clone <你的仓库地址> yolo26
cd D:\yolo26
```

确认这些目录存在：

- `quudet-yolo-lab-backend`
- `quudet-yolo-lab`
- `ultralytics-main`

`ultralytics-main` 很重要，因为前端模型/数据集选项是扫描它生成的。

---

## 3. 配置 Windows 主控后端 Python 环境
进入后端目录：

```powershell
cd D:\yolo26\quudet-yolo-lab-backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
```

---

## 4. 配置主控 `.env`
你仓库里已经有 `.env.example`，直接复制一份：

```powershell
copy .env.example .env
```

然后编辑 `D:\yolo26\quudet-yolo-lab-backend\.env`。  
建议你先用下面这份：

```env
DATABASE_URL=postgresql+psycopg2://quudet:你的数据库密码@127.0.0.1:5432/quudet
REDIS_URL=redis://127.0.0.1:6379/0

SECRET_KEY=换成一个很长的随机字符串
FIRST_SUPERUSER_EMAIL=admin@quudet.local
FIRST_SUPERUSER_PASSWORD=换成强密码

CORS_ORIGINS=http://127.0.0.1:8080,http://localhost:8080,http://你的Windows主机IP:8080

SYNC_JOBS=false
DISABLE_AUTH=true

YOLO_WORK_DIR=D:/yolo26
DATA_DIR=D:/yolo26/quudet-yolo-lab-backend/data

CLUSTER_ENABLED=true
NODE_SHARED_TOKEN=换成主控和节点共用校验的长随机串
NODE_HEARTBEAT_TIMEOUT_SECONDS=60
```

### 这里几个关键点
- `YOLO_WORK_DIR=D:/yolo26`
  - 因为仓库根目录下有 `ultralytics-main`
- `DATA_DIR`
  - 建议明确写绝对路径
- `CLUSTER_ENABLED=true`
  - 开启主控调度模式
- `SYNC_JOBS=false`
  - 既然你要走集群，不建议同步模式
- `DISABLE_AUTH=true`
  - 先在内网跑通，后面再收紧

---

## 5. 准备 PostgreSQL
### 新建数据库
先确保 PostgreSQL 服务已启动。然后创建数据库：

```sql
CREATE DATABASE quudet;
CREATE USER quudet WITH PASSWORD '你的数据库密码';
GRANT ALL PRIVILEGES ON DATABASE quudet TO quudet;
```

如果你习惯用 pgAdmin，就直接图形界面创建也行。

### 测试数据库连通
确保 `.env` 里的：

```env
DATABASE_URL=postgresql+psycopg2://quudet:你的数据库密码@127.0.0.1:5432/quudet
```

和你实际配置一致。

---

## 6. 准备 Redis
你不用 Docker，就需要自己装 Redis。

### 推荐方式
- 如果只是内网跑通，装一个 Windows 版 Redis 或者直接用一台 Linux 上的 Redis
- 如果你已经有 Linux 服务器，也可以直接把 `REDIS_URL` 指向它，比如：

```env
REDIS_URL=redis://192.168.1.20:6379/0
```

### 本机 Redis 测试
如果本机装好了：

```powershell
redis-cli ping
```

返回 `PONG` 就行。

---

## 7. 启动主控后端 API
在后端目录：

```powershell
cd D:\yolo26\quudet-yolo-lab-backend
.\.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动成功后，本机打开：

- [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- [http://127.0.0.1:8000/api/v1/version](http://127.0.0.1:8000/api/v1/version)

如果能返回版本信息，说明 API 跑起来了。

---

## 8. 启动前端静态页
这个项目前端是静态页面，不需要单独编译服务端。  
你只要把 `quudet-yolo-lab` 目录通过一个静态文件服务器跑起来即可。

最简单方式，在新终端执行：

```powershell
cd D:\yolo26\quudet-yolo-lab
python -m http.server 8080
```

然后浏览器打开：

- [http://127.0.0.1:8080](http://127.0.0.1:8080)

如果前端页面能出来，并且能访问后端接口，就说明主控端最基础部分已经跑通。

---

## 9. 放行 Windows 防火墙端口
主控机至少要允许内网访问：

- `8000/tcp`：后端 API
- `8080/tcp`：前端页面

这样 Linux 节点才能访问主控的 API。

---

## 四、先做一次主控自检
在 Windows 主控本机验证：

1. 打开前端页面：`http://127.0.0.1:8080`
2. 打开 API 文档：`http://127.0.0.1:8000/docs`
3. 从同内网其他电脑访问：
   - `http://Windows主机IP:8080`
   - `http://Windows主机IP:8000/docs`

如果这些都通，主控已经基本可用。

---

## 五、Linux 节点从 0 开始

## 1. 安装基础环境
每台 Linux 节点需要：

- Python 3.11+
- Git
- CUDA 驱动
- 与 CUDA 匹配的 PyTorch
- `ultralytics`
- 能执行 `yolo`

先检查：

```bash
python3 --version
which yolo
yolo version
```

---

## 2. 拉代码
建议每台节点都放同样路径：

```bash
sudo mkdir -p /srv
sudo chown $USER:$USER /srv
cd /srv
git clone <你的仓库地址> yolo26
cd /srv/yolo26
```

确认仓库里有：

- `quudet-yolo-lab-backend`
- `ultralytics-main`

---

## 3. 配置节点 Python 环境
```bash
cd /srv/yolo26/quudet-yolo-lab-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

---

## 4. 配置节点 `.env`
节点侧也建议有一个 `.env`，至少写这两个：

```env
YOLO_WORK_DIR=/srv/yolo26
DATA_DIR=/srv/yolo26-node-data
```

说明：

- `YOLO_WORK_DIR` 指向仓库根目录
- `DATA_DIR` 是本机缓存数据集、运行产物的地方

---

## 5. 配置节点 Agent 环境变量
每台节点启动前，先设这些变量：

```bash
export MASTER_API_BASE="http://Windows主机IP:8000"
export NODE_ID="gpu-node-01"
export NODE_NAME="3090-01"
export NODE_TOKEN="这台节点自己的口令"
export NODE_MAX_CONCURRENCY="1"
export POLL_INTERVAL_SECONDS="4"
export HEARTBEAT_INTERVAL_SECONDS="5"
```

### 重要说明
- `MASTER_API_BASE` 必须能访问到 Windows 主控的 `8000`
- `NODE_TOKEN` 是节点自己的口令
- 主控会拿 `NODE_SHARED_TOKEN + ":" + NODE_TOKEN` 做哈希校验
- `NODE_ID` 每台机器必须唯一

---

## 6. 启动节点 Agent
```bash
cd /srv/yolo26/quudet-yolo-lab-backend
source .venv/bin/activate
python -m app.agent.runner
```

正常情况下会看到类似：

```text
[agent] start node_id=gpu-node-01 master=http://...
```

---

## 六、连通性验证
现在你做这几步：

1. Windows 主控 API 已启动
2. 前端已启动
3. 至少一台 Linux 节点 Agent 已启动

然后到前端页面看“节点管理”，应该能看到节点在线。

如果节点不在线，优先排查：

- Windows 防火墙是否放行 `8000`
- Linux 节点能否 `curl http://Windows主机IP:8000/api/v1/version`
- `NODE_SHARED_TOKEN` / `NODE_TOKEN` 是否匹配
- 节点时间是否严重漂移

---

## 七、第一次跑通训练任务
建议先做最小验证，不要一开始就大任务。

### 推荐测试任务
- 训练类型：`train`
- 数据集：`coco8`
- `epochs=1`
- 模型：`yolov8n.yaml` 或类似最小模型
- 指定一个在线节点执行

这样你要观察的是：

1. 任务创建后状态变成 `PENDING_ASSIGN`
2. 某个节点领取后变成 `RUNNING_REMOTE`
3. 日志开始回传
4. 训练完成后变成 `SUCCESS`

---

## 八、关于“续训”的正确使用方式
你前面关心续训，这里直接给你最稳的做法：

### 推荐原则
- **续训尽量固定回原节点**
- 保留原训练目录
- 保留 `last.pt`
- 保留 `project` / `name`

### 为什么
当前系统是“主控调度 + 节点执行”，不是“自动 checkpoint 全局分发平台”。  
所以最稳妥的续训方式，是让续训任务仍然回原节点执行。

### 实际建议
- 每次训练时手动设置明确的 `project` 和 `name`
- 节点本地保留训练目录，不要自动清理
- 续训时把 `resume` 或 `model=.../last.pt` 传入新的训练任务 payload

---

## 九、最小可用方案和生产建议

## 最小可用方案
如果你只是先跑通，建议这样：

### Windows 主控
- 后端：`uvicorn`
- 数据库：PostgreSQL
- Redis：本机或内网一台 Redis
- 前端：`python -m http.server 8080`

### Linux 节点
- 直接前台启动 Agent

这样最简单。

## 生产建议
跑稳之后建议再做：

- Windows 上用 `NSSM` 把 API 和前端静态服务做成服务
- Linux 上用 `systemd` 托管 Agent
- PostgreSQL 定期备份
- `SECRET_KEY`、管理员密码、`NODE_SHARED_TOKEN` 全部改成强随机值
- 收紧 `DISABLE_AUTH`
- 给 checkpoint 和数据集做固定存储策略

---

## 十、你可以直接照着执行的最短流程

### Windows 主控
```powershell
cd D:\yolo26\quudet-yolo-lab-backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
copy .env.example .env
```

编辑 `.env` 后启动：

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

另开一个终端：

```powershell
cd D:\yolo26\quudet-yolo-lab
python -m http.server 8080
```

### Linux 节点
```bash
cd /srv/yolo26/quudet-yolo-lab-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
export MASTER_API_BASE="http://Windows主机IP:8000"
export NODE_ID="gpu-node-01"
export NODE_NAME="3090-01"
export NODE_TOKEN="你的节点口令"
python -m app.agent.runner
```

---

## 十一、我对你这个项目的具体建议
你现在最适合走这条路：

- **Windows 主控不用 Docker**
- **PostgreSQL 用本机安装版**
- **Redis 用本机或独立 Linux 一台**
- **前端先用简单静态服务跑**
- **Linux 节点用 Agent 接入**
- **续训固定回原节点**

这样最容易从 0 跑通，而且和你现在代码实现最匹配。

如果你愿意，我下一步可以继续直接给你两份现成内容：

1. **Windows 主控 `.env` 完整示例**
2. **Linux 节点启动脚本 / systemd 服务文件模板**

这样你可以直接复制用了。