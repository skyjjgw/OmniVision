#!/usr/bin/env bash
set -euo pipefail

cd /home/tian/client

# ==========================================
# 1. 激活最新的 CSI 虚拟环境
# ==========================================
source /home/tian/client/.venv-csi/bin/activate

# ==========================================
# 2. 清理可能残留的僵尸进程
# ==========================================
pkill -f blind_client_pi5_csi.py || true
pkill -f doubao_streaming_asr.py || true
sleep 1
pkill -9 -f blind_client_pi5_csi.py || true
pkill -9 -f doubao_streaming_asr.py || true
sleep 1

# ==========================================
# 3. 动态获取麦克风硬件设备号 (排查 ALSA 索引漂移)
# ==========================================
MIC_CARD=$(arecord -l | awk '/card [0-9]+:.*(USB|XFM|Microphone)/ {print $2}' | tr -d ':' | head -n1)
if [ -z "$MIC_CARD" ]; then
    MIC_CARD=$(arecord -l | grep -v vc4 | awk '/card [0-9]+/ {print $2}' | tr -d ':' | head -n1)
fi
if [ -n "$MIC_CARD" ]; then
    export ASR_DEVICE_OVERRIDE="hw:${MIC_CARD},0"
    echo "🎯 自动检测到麦克风设备: $ASR_DEVICE_OVERRIDE"
else
    export ASR_DEVICE_OVERRIDE="default"
    echo "⚠️ 未检测到有效麦克风，回退到 default"
fi

# ==========================================
# 4. 配置运行环境变量
# ==========================================
export BLIND_AI_ONLY_MODE=0
export ENABLE_ASR_INTEGRATED=1
export ENABLE_LOCAL_AUDIO_STREAM=1
export ENABLE_BLIND_REPLY_LISTENER=1
export ENABLE_LEGACY_TTS_WORKER=0
export ASR_ENABLE_SERVER_TTS=0
export ASR_REPROBE_EACH_ROUND=1
export ASR_MIN_BACKOFF_SECONDS=1
export ASR_MAX_BACKOFF_SECONDS=8

# CSI 摄像头默认不需要指定 video 节点，底层通过 libcamera 调用
export AI_SCHEDULE_MODE=obs

echo "🚀 [启动] 正在启动基于 CSI 摄像头与豆包云端大模型的边缘端主程序..."
python /home/tian/client/blind_client_pi5_csi.py