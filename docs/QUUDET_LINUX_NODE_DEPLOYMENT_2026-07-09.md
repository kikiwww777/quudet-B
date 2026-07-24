# QuuDet Linux 节点部署指南

> **日期**: 2026-07-09  
> **目标**: 在远程 Linux 服务器上部署 quudet agent，使其作为计算节点接入主控。  
> **前置**: 主控已完成方案 C（PostgreSQL + Redis + Celery）+ 统一节点调度改造。

---

## 部署基线

| 项目 | 要求 |
|------|------|
| OS | Ubuntu 22.04 LTS |
| Python | 3.11+ |
| CUDA | 可选（按 GPU 需求） |
| 磁盘 | 至少 50GB 可用 |
| 网络 | 可访问主控 API（`MASTER_API_BASE`） |

---

## 目录结构

```
/srv/quudet/
├── quudet-yolo-lab-backend/      # git clone 或 rsync 同步
│   ├── .venv/                     # Python 虚拟环境
│   ├── app/                       # 应用代码
│   ├── deploy/
│   │   └── quudet-agent.service   # systemd 模板
│   └── requirements.txt
├── ultralytics-main/              # YOLO 配置（与主控一致）
├── data/
│   ├── artifacts/                 # 任务产物缓存
│   └── uploads/                   # 数据集缓存
└── yolo11n.pt                     # 预训练权重（可选）
```

---

## 部署步骤

### 1. 环境准备

```bash
# 系统依赖
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev git

# 创建用户
sudo useradd -r -s /bin/false -m -d /srv/quudet quudet

# 创建工作目录
sudo mkdir -p /srv/quudet/data/{artifacts,uploads}
sudo chown -R quudet:quudet /srv/quudet
```

### 2. 同步代码

```bash
# 从主控节点同步（示例使用 rsync）
rsync -avz --exclude '.venv' --exclude '__pycache__' \
  user@master-host:/path/to/quudet/quudet-yolo-lab-backend/ \
  /srv/quudet/quudet-yolo-lab-backend/

# 同步 ultralytics-main
rsync -avz user@master-host:/path/to/quudet/ultralytics-main/ \
  /srv/quudet/ultralytics-main/

# 同步权重文件
rsync -avz user@master-host:/path/to/quudet/yolo11n.pt \
  /srv/quudet/yolo11n.pt
```

### 3. Python 环境

```bash
cd /srv/quudet/quudet-yolo-lab-backend
sudo -u quudet python3.11 -m venv .venv
sudo -u quudet .venv/bin/pip install -r requirements.txt

# 验证
sudo -u quudet .venv/bin/python -c "import torch; print(f'torch: {torch.__version__}, cuda: {torch.cuda.is_available()}')"
sudo -u quudet .venv/bin/python -c "import ultralytics; print(f'ultralytics: {ultralytics.__version__}')"
```

### 4. 配置 systemd

```bash
# 编辑配置（按实际环境修改）
sudo vi /srv/quudet/quudet-yolo-lab-backend/deploy/quudet-agent.service

# 关键配置项：
#   MASTER_API_BASE=http://主控IP:8000
#   NODE_ID=linux-gpu-01
#   NODE_KIND=remote
#   NODE_TOKEN=<生成随机token，与主控 NODE_SHARED_TOKEN 配合使用>

# 安装服务
sudo cp /srv/quudet/quudet-yolo-lab-backend/deploy/quudet-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable quudet-agent
```

### 5. 启动

```bash
sudo systemctl start quudet-agent

# 查看状态
sudo systemctl status quudet-agent

# 查看日志
sudo journalctl -u quudet-agent -f
```

### 6. 验证

在主控节点验证节点已注册：

```bash
# 查看所有节点
curl http://主控IP:8000/api/v1/nodes

# 应能看到新注册的 Linux 节点
# 输出示例：
# [linux-gpu-01] status=ONLINE os=linux gpu=True python=3.11.9
```

---

## 环境变量参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MASTER_API_BASE` | `http://127.0.0.1:8000` | 主控 API 地址 |
| `NODE_ID` | 主机名 | 节点唯一标识 |
| `NODE_NAME` | `NODE_ID` | 节点显示名 |
| `NODE_TOKEN` | 自动生成 | 认证令牌（32 位 hex） |
| `NODE_KIND` | `local` | 节点类型（`local` / `remote`） |
| `NODE_MAX_CONCURRENCY` | `1` | 最大并行任务数 |
| `POLL_INTERVAL_SECONDS` | `4` | claim-next 轮询间隔 |
| `HEARTBEAT_INTERVAL_SECONDS` | `5` | 心跳间隔 |
| `YOLO_WORK_DIR` | 自动检测 | YOLO 工作目录 |
| `DATA_DIR` | 自动检测 | 数据目录 |

---

## 路径兼容注意

| 项 | Windows | Linux | 兼容 |
|---|---------|-------|------|
| 路径分隔符 | `\` | `/` | ✅ pathlib 跨平台 |
| artifact 缓存 | `data/artifacts` | `data/artifacts` | ✅ 相对路径 |
| YOLO work dir | 自动检测 | 自动检测 | ✅ 可通过环境变量覆盖 |
| dataset yaml | 相对路径 | 相对路径 | ✅ payload 相对 YOLO_WORK_DIR 解析 |
| job bundle | zip 下载 | zip 下载 | ✅ 标准格式 |

---

## 故障排查

### 节点注册失败

```bash
# 检查 agent 日志
sudo journalctl -u quudet-agent -n 50

# 常见原因：
# - MASTER_API_BASE 不可达 → 检查网络和防火墙
# - NODE_TOKEN 不匹配 → 检查主控 NODE_SHARED_TOKEN + 节点 NODE_TOKEN
# - Python 版本不兼容 → 确保 python3.11+
```

### 任务领取后卡住

```bash
# 检查节点能力上报
curl http://主控IP:8000/api/v1/nodes | python -m json.tool

# 确认字段：
# - has_gpu: true（当任务需要 GPU 时）
# - yolo_cli_available: true（当任务需要执行 yolo 时）
```

### 路径找不到

```bash
# 确认 YOLO_WORK_DIR 指向正确
# 确认 data 路径在 payload 中是相对 YOLO_WORK_DIR 的
```
