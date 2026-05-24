$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path "shared/config/cloud.env")) { Copy-Item "shared/config/cloud.env.example" "shared/config/cloud.env" }
if (-not (Test-Path "apps/blind-client/.env.asr")) { Copy-Item "apps/blind-client/.env.asr.example" "apps/blind-client/.env.asr" }

Write-Host "[1/3] 启动云端服务"
docker compose up -d cloud-backend cloud-signaling

Write-Host "[2/3] 安装盲人端依赖"
python -m pip install -r apps/blind-client/requirements.txt

Write-Host "[3/3] 志愿者端与盲人端启动说明"
Write-Host "cd apps/volunteer-app; flutter pub get; flutter run"
Write-Host "cd apps/blind-client; python blind_client_pi5_csi.py"
Write-Host "云端验证地址: http://127.0.0.1:8000/static/admin_login.html"
