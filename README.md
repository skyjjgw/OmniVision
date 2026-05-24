# 视桥智导 OmniVision

本仓库是面向 GitHub 交付整理后的标准化多端项目仓库，统一收纳云端服务、盲人端客户端、志愿者端 App 三个端的核心代码，并补充共享配置模板、部署脚本与仓库上传规范文件。

## 项目整体介绍
视桥智导是一套面向视障人群的端云协同导盲系统。系统以“边缘端保生存、云端补认知”为核心架构：

- 盲人端部署在 Raspberry Pi 5，负责本地视觉检测、ROI 风险判断、离线安全播报与流式语音采集。
- 云端服务承载导航、场景描述、多模态审核、大屏监控与 WebSocket / WebRTC 协同逻辑。
- 志愿者端采用 Flutter 开发，负责人工求助接入、地图查看、社区标注与争议审核。

## 仓库目录结构
```text
OmniVision_GitHub_Repo/
├── apps/
│   ├── cloud-server/        # 云端服务端（FastAPI + Socket.IO + 静态大屏）
│   ├── blind-client/        # 盲人端（Python + 视觉/语音/硬件接入）
│   └── volunteer-app/       # 志愿者端（Flutter）
├── shared/
│   ├── config/              # 三端共享配置模板
│   └── scripts/             # 可复用脚本预留目录
├── deploy/                  # 一键部署脚本与操作文档
├── docker-compose.yml       # 云端容器化编排入口
├── .gitignore               # GitHub 上传忽略规则
└── README.md
```

## 三个端的功能描述
### 1. 云端服务 `apps/cloud-server`
- 提供 FastAPI 业务接口、场景描述、导航意图识别、后台管理大屏与监控页面。
- 提供 Socket.IO 信令服务，支撑盲人端与志愿者端的音视频协同。
- 提供静态页面 `admin_login.html`、`admin_dashboard.html`、`monitor.html`、`amap_nav.html`。

### 2. 盲人端 `apps/blind-client`
- 负责摄像头视频采集、边缘检测推理、红绿灯/斑马线/障碍物识别。
- 负责本地安全语音播报、流式 ASR、语音唤醒与大模型回复中断控制。
- 附带树莓派自启动服务模板与运行脚本。

### 3. 志愿者端 `apps/volunteer-app`
- 基于 Flutter 实现登录、呼叫接单、地图查看、社区发帖、评论与争议审核。
- 支持 Android、iOS、Web、Windows、Linux、macOS 多平台构建。

## 本地环境搭建步骤
### 云端服务
```bash
cd apps/cloud-server
python -m venv .venv
source .venv/bin/activate  # Windows 使用 .venv\Scripts\activate
pip install -r requirements.txt
cp ../../shared/config/cloud.env.example ../../shared/config/cloud.env
uvicorn backend:app --host 0.0.0.0 --port 8000
```

另开终端启动信令服务：

```bash
cd apps/cloud-server
python signaling_server.py
```

### 盲人端
```bash
cd apps/blind-client
python -m venv .venv
source .venv/bin/activate  # Windows 使用 .venv\Scripts\activate
pip install -r requirements.txt
cp .env.asr.example .env.asr
python blind_client_pi5_csi.py
```

### 志愿者端
```bash
cd apps/volunteer-app
flutter pub get
flutter run
```

## 依赖安装指南
- Python 端统一使用 `pip install -r requirements.txt`。
- 志愿者端依赖由 `flutter pub get` 安装。
- 若采用容器化部署，可直接执行根目录 `docker compose up -d` 启动云端服务。

## 一键部署
- Windows: `./deploy/deploy-all.ps1`
- Linux: `bash ./deploy/deploy-all.sh`
- 详细说明见 `deploy/DEPLOYMENT.md`。

## 安全与上传说明
- 仓库已移除数据库、日志、运行时上传目录与真实密钥。
- 默认公网 IP、TURN 账号、语音 Token 已改为占位模板，提交前请在本地配置。
- 如需生产部署，请先复制模板配置并填入自己的密钥，再启动服务。
