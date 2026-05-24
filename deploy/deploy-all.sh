#!/usr/bin/env bash
set -e
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f shared/config/cloud.env ]; then cp shared/config/cloud.env.example shared/config/cloud.env; fi
if [ ! -f apps/blind-client/.env.asr ]; then cp apps/blind-client/.env.asr.example apps/blind-client/.env.asr; fi

echo "[1/3] 启动云端服务"
docker compose up -d cloud-backend cloud-signaling

echo "[2/3] 准备盲人端依赖"
python -m pip install -r apps/blind-client/requirements.txt

echo "[3/3] 启动志愿者端（需本机已安装 Flutter）"
echo "cd apps/volunteer-app && flutter pub get && flutter run"
echo "盲人端启动命令：cd apps/blind-client && python blind_client_pi5_csi.py"
echo "云端验证地址：http://127.0.0.1:8000/static/admin_login.html"
