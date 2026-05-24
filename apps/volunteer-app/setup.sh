#!/bin/bash
cd /root/aivoice_unified
export PIP_BREAK_SYSTEM_PACKAGES=1
apt-get update
apt-get install -y portaudio19-dev python3-pyaudio
pip3 install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pip3 install dashscope opencv-python-headless python-socketio aiohttp uvicorn fastapi python-multipart websockets pyaudio paramiko aiortc passlib jinja2 pyjwt websockets==10.4 aiohttp_cors -i https://pypi.tuna.tsinghua.edu.cn/simple
nohup python3 backend.py > output.log 2>&1 &
nohup python3 signaling_server.py > signaling.log 2>&1 &
