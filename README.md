# 视桥智导 OmniVision

本仓库是面向 GitHub 交付整理后的标准化多端项目仓库，统一收纳云端服务、盲人端客户端、志愿者端 App 三个端的核心代码，并补充共享配置模板、部署脚本与交接文档，目标是让新的开发者能够在不了解项目历史的前提下快速接手。

## 项目整体介绍
视桥智导是一套面向视障人群的端云协同导盲系统。系统以“边缘端保生存、云端补认知”为核心架构：

- 盲人端部署在 Raspberry Pi 5，负责本地视觉检测、ROI 风险判断、离线安全播报与流式语音采集。
- 云端服务承载导航、场景描述、多模态审核、大屏监控与 WebSocket / WebRTC 协同逻辑。
- 志愿者端采用 Flutter 开发，负责人工求助接入、地图查看、社区标注与争议审核。
- 整体设计遵循“高风险低延迟走边缘端，复杂理解与辅助决策走云端”的原则。

## 仓库目录结构
```text
OmniVision/
├── apps/
│   ├── cloud-server/        # 云端服务端（FastAPI + Socket.IO + 静态大屏）
│   ├── blind-client/        # 盲人端（Python + 视觉/语音/硬件接入）
│   └── volunteer-app/       # 志愿者端（Flutter）
├── deploy/                  # 一键部署脚本与部署说明
├── docs/                    # 项目交接与维护文档
├── shared/
│   ├── config/              # 三端共享配置模板
│   └── scripts/             # 可复用脚本预留目录
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
- 负责摄像头视频采集、边缘检测推理、红绿灯、斑马线和障碍物识别。
- 负责本地安全语音播报、流式 ASR、语音唤醒与大模型回复中断控制。
- 附带树莓派自启动 `systemd` 模板与本地运行脚本。

### 3. 志愿者端 `apps/volunteer-app`
- 基于 Flutter 实现登录、呼叫接单、地图查看、社区发帖、评论与争议审核。
- 支持 Android、iOS、Web、Windows、Linux、macOS 多平台构建。

## 系统架构概览
### 端侧职责
- 使用树莓派 5 承载本地视觉推理和语音播报，优先保障危险场景的实时响应。
- 通过 ROI 规则与本地音频矩阵实现毫秒级安全提示，避免完全依赖云端。

### 云侧职责
- 负责导航意图识别、场景白描、大屏管理、数据审核和多端状态同步。
- 通过正则和状态机拦截“带我去”“我要去”等导航语句，减少无意义的大模型调用。

### 人工兜底职责
- 志愿者端承接人工求助、地图查看、争议审核和辅助判断。
- 当云端无法可靠确认时，允许切换到人机协同链路。

## 硬件与运行环境
### 核心硬件清单
- 主控板：Raspberry Pi 5
- 摄像头：MIPI-CSI 摄像头
- 语音输入：麦克风或麦克风阵列
- 语音输出：扬声器或骨传导/耳机类播放设备
- 云端：Linux 服务器或支持 Docker 的云主机
- 志愿者端：Android / iOS 手机，或本地 Flutter 调试设备

### 选型说明
- 选择 Raspberry Pi 5 是为了在边缘端部署检测模型，保证断网时仍可工作。
- 选择 MIPI-CSI 摄像头而不是 USB 摄像头，是为了降低传输延迟和 CPU 额外开销。
- 将危险提示保留在端侧，是为了避免云端延迟或大模型幻觉影响安全。

### 推荐软件环境
- 云端：Ubuntu 20.04+ 或其他 Linux 发行版，Python 3.10，Docker / Docker Compose
- 盲人端：Raspberry Pi OS 或 Ubuntu，Python 3.10，音频驱动和摄像头驱动可用
- 志愿者端：Flutter 3.2+，Android SDK / Xcode 根据目标平台安装

## 快速接手路径
1. 阅读 `docs/PROJECT_HANDOVER.md` 了解整体交接信息。
2. 根据 `shared/config/*.example` 复制并填写本地环境变量。
3. 先启动云端，再连接盲人端，最后运行志愿者端。
4. 用 `deploy/DEPLOYMENT.md` 中的验证步骤确认三端连通。

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

## 配置文件说明
- `shared/config/cloud.env.example`：云端 API Key、后台账号、公共访问地址配置模板。
- `shared/config/blind-client.env.example`：盲人端 ASR / TTS / 推送地址模板。
- `shared/config/volunteer-app.env.example`：志愿者端服务地址与 TURN 凭据模板。

## 一键部署
- Windows: `./deploy/deploy-all.ps1`
- Linux: `bash ./deploy/deploy-all.sh`
- 详细说明见 `deploy/DEPLOYMENT.md`。

## 交接与维护说明
- 详细接手说明见 `docs/PROJECT_HANDOVER.md`。
- 当前仓库已去除数据库、日志、运行时上传目录与真实密钥。
- 当前仓库未附开源许可证文件。如需对外公开分发，请先明确许可证策略后再补充 `LICENSE` 文件。
- 如需生产部署，请先复制模板配置并填入自己的密钥，再启动服务。
