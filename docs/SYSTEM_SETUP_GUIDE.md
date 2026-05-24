# 从 0 开始的完整系统搭建指南

## 1. 目标
本指南面向第一次接手项目的新开发者，目标是在没有原团队成员协助的情况下完成以下工作：

- 完成硬件接线与基础部署
- 配置云端服务
- 启动盲人端
- 编译并运行志愿者端移动应用
- 完成三端联调与基础验收

## 2. 搭建前准备
### 2.1 硬件准备
- Raspberry Pi 5
- MIPI-CSI 摄像头
- GNSS / GPS 模块
- 麦克风或麦克风阵列
- 扬声器 / 耳机 / 骨传导设备
- 5V 稳定供电设备
- 一台 Linux 云服务器
- 一台 Android / iOS 测试机

### 2.2 软件准备
- Git
- Python 3.10
- Docker 与 Docker Compose
- Flutter 3.2+
- Android Studio 或 Xcode（按目标平台安装）

## 3. 第一步：硬件接线与部署
### 3.1 树莓派与摄像头
1. 关闭树莓派电源
2. 将 MIPI-CSI 摄像头排线连接到摄像头接口
3. 固定排线，避免松动
4. 开机后确认系统可识别摄像头

### 3.2 GNSS / GPS 模块
1. 根据模块规格确认电源和串口电平
2. 连接 TX / RX / GND
3. 开机后检查串口是否可读到 NMEA 数据

### 3.3 音频设备
1. 连接麦克风输入设备
2. 连接扬声器或耳机输出设备
3. 使用系统音频工具确认输入输出设备正常

### 3.4 供电
1. 使用稳定的 5V 供电方案
2. 若使用移动电源，需保证高负载时不会掉压

## 4. 第二步：克隆仓库与准备配置
```bash
git clone <your-repo-url>
cd OmniVision
```

复制配置模板：

```bash
cp shared/config/cloud.env.example shared/config/cloud.env
cp apps/blind-client/.env.asr.example apps/blind-client/.env.asr
```

需要重点填写：

- `shared/config/cloud.env`
  - `LLM_API_KEY`
  - `SMTP_USER`
  - `SMTP_PASS`
  - `PUBLIC_BASE_URL`
  - `ADMIN_USER`
  - `ADMIN_PASS`
- `apps/blind-client/.env.asr`
  - ASR / TTS Token
  - `ASR_PUSH_URL`
  - 音频输入输出设备

## 5. 第三步：启动云端服务
### 方式 A：使用 Docker
```bash
docker compose up -d
```

### 方式 B：手动启动
```bash
cd apps/cloud-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend:app --host 0.0.0.0 --port 8000
```

另开终端：

```bash
cd apps/cloud-server
python signaling_server.py
```

### 启动后验证
- 打开 `http://127.0.0.1:8000/static/admin_login.html`
- 打开 `http://127.0.0.1:8000/static/monitor.html`
- 确认 `6000` 端口处于监听状态

## 6. 第四步：启动盲人端
```bash
cd apps/blind-client
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python blind_client_pi5_csi.py
```

### 启动前检查
- 摄像头可用
- 麦克风输入设备编号正确
- 扬声器输出设备正确
- `.env.asr` 中的服务地址和 Token 已填写

### 运行时应出现
- 与云端 `ws://<server>:8000/ws/client_input` 的连接日志
- 语音模块初始化日志
- 摄像头画面或推理相关日志

## 7. 第五步：编译并运行志愿者端
```bash
cd apps/volunteer-app
flutter pub get
flutter run
```

### Android 打包
```bash
flutter build apk --release
```

### iOS 打包
- 打开 `ios/Runner.xcworkspace`
- 配置证书与签名
- 在 Xcode 中构建归档

### 运行前检查
- 在 App 中填写正确的服务器 IP
- 若需要通话功能，需同步填写 TURN 相关配置

## 8. 第六步：全端联动调试
### 场景 1：后台页面联通
- 登录后台页面
- 查看监控大屏
- 检查设备是否上报状态

### 场景 2：盲人端连接云端
- 启动盲人端
- 确认云端监控页能看到设备信息

### 场景 3：场景描述链路
- 在盲人端发起语音请求
- 检查云端是否收到 `/api/asr/update`
- 确认有返回文本或播报结果

### 场景 4：志愿者链路
- 启动志愿者端
- 登录并进入大厅 / 地图
- 检查与 `6000` 端口 Socket.IO 的连接

### 场景 5：人工求助链路
- 从盲人端发起呼叫
- 志愿者端收到来电
- 检查 WebRTC / 信令链路是否建立

## 9. 第七步：验收清单
### 云端验收
- 后台登录正常
- 监控页面可访问
- API 请求可返回

### 盲人端验收
- 摄像头工作正常
- 音频输入正常
- 音频输出正常
- 语音链路可以触发场景描述

### 志愿者端验收
- App 可启动
- 登录正常
- 地图可显示
- 人工求助链路可连接

## 10. 常见失败点
- 云端只启动了 `backend.py`，没启动 `signaling_server.py`
- `.env` 模板未填写，导致 ASR / TTS 或 LLM 失效
- 树莓派音频设备编号变化，导致录音失败
- 没有部署 TURN 服务，导致 WebRTC 在复杂网络下不稳定
- 使用 USB 摄像头替换 MIPI 摄像头，导致时延明显上升

## 11. 推荐接手顺序
1. 先跑通云端后台
2. 再跑通盲人端上云
3. 最后跑通志愿者端接入
4. 最后做全链路语音 + 人工兜底联调

## 12. 关联文档
- 硬件说明：`docs/HARDWARE_SETUP.md`
- API 文档：`docs/API_REFERENCE.md`
- 项目交接：`docs/PROJECT_HANDOVER.md`
- 部署说明：`deploy/DEPLOYMENT.md`
