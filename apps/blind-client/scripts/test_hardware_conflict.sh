#!/bin/bash
# test_hardware_conflict.sh
# 用于测试 USB 供电不足/设备抢占的专用脚本

echo "=========================================================="
echo " 视障出行 - 硬件冲突与供电极限测试脚本 "
echo "=========================================================="
echo "目的: 验证摄像头与麦克风阵列同时启动时，是否因供电不足导致音频掉线"
echo ""

cd "$(dirname "$0")"

echo ">>> [步骤 1] 启动 blind_client.py (仅视频和传感器，ASR已禁用)"
echo ">>> 请在终端 1 保持此进程运行，并观察摄像头灯是否亮起..."
export ENABLE_LOCAL_AUDIO_STREAM=0 # 彻底禁用本地普通音频采集
export BLIND_AI_ONLY_MODE=0
export CAMERA_SOURCE="0" # 假设摄像头在 0
python3 blind_client.py &
CLIENT_PID=$!

echo ""
echo ">>> [等待 5 秒] 让视频流稳定并消耗最大 USB 功率..."
sleep 5

echo ""
echo ">>> [步骤 2] 尝试在独立进程中启动 AI 语音通道 (doubao_streaming_asr.py)"
echo ">>> 如果在此步骤报错 'No such device' 或 'ALSA lib ...'，则 100% 确认是供电不足导致麦克风掉线。"
echo ">>> 如果成功识别语音，则说明之前是多线程抢占音频设备导致的软件冲突。"
echo ""

# 使用 arecord -l 动态获取设备，也可以让用户自己传入
export AUDIO_DEVICE="default" 
python3 doubao_streaming_asr.py

echo ""
echo ">>> 测试结束，清理后台视频进程..."
kill -9 $CLIENT_PID 2>/dev/null
echo ">>> 清理完成。"
