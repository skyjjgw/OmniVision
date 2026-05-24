# 一键部署说明

## 前置条件
- 云端服务机器已安装 Docker 与 Docker Compose，或已安装 Python 3.10。
- 盲人端运行环境建议为 Raspberry Pi 5 / Ubuntu，已接入摄像头、麦克风、扬声器。
- 志愿者端已安装 Flutter 3.2+，并连接 Android/iOS 设备或浏览器。

## 快速执行
### Windows
```powershell
.\deploy\deploy-all.ps1
```

### Linux
```bash
bash ./deploy/deploy-all.sh
```

## 手动验证
- 云端后台: `http://127.0.0.1:8000/static/admin_login.html`
- 实时监控页: `http://127.0.0.1:8000/static/monitor.html`
- WebSocket 信令端口: `6000`
- 盲人端成功启动后，应看到与 `ws://<server_ip>:8000/ws/client_input` 的连接日志。
- 志愿者端成功启动后，应能访问登录页并连接 Socket.IO 服务。

## 配置文件
- 云端配置模板: `shared/config/cloud.env.example`
- 盲人端语音模板: `apps/blind-client/.env.asr.example`
- 志愿者端地址模板: `shared/config/volunteer-app.env.example`
