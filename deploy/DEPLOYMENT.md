# 一键部署说明

## 前置条件
- 云端服务机器已安装 Docker 与 Docker Compose，或已安装 Python 3.10。
- 盲人端运行环境建议为 Raspberry Pi 5 / Ubuntu，已接入摄像头、麦克风、扬声器。
- 志愿者端已安装 Flutter 3.2+，并连接 Android/iOS 设备或浏览器。
- 若要使用语音识别、语音合成和场景描述，请准备好对应服务的 API Key / Token。

## 部署目标
- 云端服务负责 API、静态页面、导航逻辑和 Socket.IO 信令。
- 盲人端负责本地推理、语音采集和安全播报。
- 志愿者端负责人工求助、地图与社区相关功能。

## 推荐启动顺序
1. 启动云端服务。
2. 验证 `8000` 和 `6000` 端口可访问。
3. 配置并启动盲人端。
4. 运行志愿者端并验证登录、地图和接单链路。

## 快速执行
### Windows
```powershell
.\deploy\deploy-all.ps1
```

### Linux
```bash
bash ./deploy/deploy-all.sh
```

## 手动部署步骤
### 1. 云端服务
```bash
cd apps/cloud-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../../shared/config/cloud.env.example ../../shared/config/cloud.env
uvicorn backend:app --host 0.0.0.0 --port 8000
```

另开终端运行：

```bash
cd apps/cloud-server
python signaling_server.py
```

### 2. 盲人端
```bash
cd apps/blind-client
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.asr.example .env.asr
python blind_client_pi5_csi.py
```

### 3. 志愿者端
```bash
cd apps/volunteer-app
flutter pub get
flutter run
```

## 端口与网络要求
- `8000`：云端 FastAPI 与静态页面
- `6000`：Socket.IO / WebRTC 信令服务
- `3478`：STUN / TURN，若启用实时音视频中继需额外部署
- 云端地址变更后，需要同步更新盲人端 `.env.asr`、志愿者端 IP 设置和相关环境变量

## 手动验证
- 云端后台: `http://127.0.0.1:8000/static/admin_login.html`
- 实时监控页: `http://127.0.0.1:8000/static/monitor.html`
- WebSocket 信令端口: `6000`
- 盲人端成功启动后，应看到与 `ws://<server_ip>:8000/ws/client_input` 的连接日志。
- 志愿者端成功启动后，应能访问登录页并连接 Socket.IO 服务。

## 交接时必须补充的配置
- `shared/config/cloud.env` 中的 `LLM_API_KEY`、邮件 SMTP 信息、后台账号口令
- `apps/blind-client/.env.asr` 中的 ASR / TTS Token、推送地址、播放设备
- 志愿者端运行时使用的服务 IP、TURN 用户名和 TURN 密码

## 常见交接风险
- 仅启动 `backend.py` 而未启动 `signaling_server.py`，会导致志愿者链路不可用。
- 忘记填写 `.env` 模板，语音与场景描述能力会直接失效。
- 使用 USB 摄像头替代 MIPI-CSI 摄像头时，边缘端时延和稳定性可能下降。
- 若未部署 TURN 服务，弱网环境下的 WebRTC 通话成功率可能下降。

## 配置文件
- 云端配置模板: `shared/config/cloud.env.example`
- 盲人端语音模板: `apps/blind-client/.env.asr.example`
- 志愿者端地址模板: `shared/config/volunteer-app.env.example`
