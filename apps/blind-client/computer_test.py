import cv2
import asyncio
import websockets
import json
import base64
import numpy as np
import time
import pyaudio
import socketio
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, AudioStreamTrack, RTCConfiguration, RTCIceServer
from av import VideoFrame, AudioFrame
import logging
import av
import uuid
import re
import fractions
from aiortc.mediastreams import MediaStreamError
import threading
import queue
import os
import requests
import math
import serial

# --- 初始化本地离线 YOLO 模型 (NCNN 版) ---
try:
    from ultralytics import YOLO
    import os
    print(">>> [AI] 正在加载本地 YOLOv8 模型 (NCNN 版)...")
    
    # 是否开启树莓派5性能模拟 (限制FPS并增加延迟)
    SIMULATE_PI5 = True 
    if SIMULATE_PI5:
        print(">>> [硬件模拟] ⚠️ 已开启树莓派 5 性能模拟模式！")
        print(">>>   - 增加 AI 推理延迟模拟 (ARM CPU): +40~60ms")
        print(">>>   - 限制视频捕获和上传帧率。")
    
    # 获取当前文件所在目录作为基准路径
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # 使用已经转换为 NCNN 格式的模型目录
    model_path = os.path.join(base_dir, "det_wotr_obstacles_final_88_81_ncnn_model")
    
    if os.path.exists(model_path):
        # ultralytics 原生支持直接加载 *_ncnn_model 目录
        obstacle_model = YOLO(model_path, task='detect')
        print(f">>> [AI] 本地障碍物检测模型(NCNN)加载成功！({model_path})")
        USE_LOCAL_AI = True
        last_ai_warn_time = 0
    else:
        print(f">>> [AI] 未找到 NCNN 模型目录: {model_path}")
        USE_LOCAL_AI = False

except ImportError:
    print(">>> [AI] 未安装 ultralytics，离线 AI 识别将被禁用。")
    USE_LOCAL_AI = False
except Exception as e:
    print(f">>> [AI] 模型加载失败: {e}")
    USE_LOCAL_AI = False

# --- 日志配置 ---
logging.basicConfig(level=logging.INFO)
logging.getLogger("aioice").setLevel(logging.WARNING)
logging.getLogger("aiortc").setLevel(logging.WARNING)

# --- 配置 ---
SERVER_IP = "127.0.0.1" # 推理服务器
SERVER_PORT = 8000
SIGNALING_URL = "http://127.0.0.1:6000" # 志愿者信令服务器

CLIENT_ID = f"pc-test-{uuid.uuid4().hex[:8]}"
print(f">>> PC Test Client ID: {CLIENT_ID}")

WS_URL = f"ws://{SERVER_IP}:{SERVER_PORT}/ws/client_input?client_id={CLIENT_ID}"

# --- 模拟与真实 GPS 位置 ---
# 默认坐标：苏州大学应用技术学院 (GCJ-02)
CURRENT_LAT = 31.1125
CURRENT_LNG = 120.8442
CURRENT_ANGLE = 0.0
CURRENT_RAW_NMEA = ""

# 串口配置
SERIAL_PORT = 'COM13'
BAUD_RATE = 115200

def get_real_location():
    try:
        response = requests.get('http://ip-api.com/json', timeout=3)
        if response.status_code == 200:
            d = response.json()
            return d['lat'], d['lon']
    except: pass
    return 31.1125, 120.8442

try:
    lat, lng = get_real_location()
    CURRENT_LAT, CURRENT_LNG = lat, lng
    print(f">>> [GPS] 已获取 IP 定位: {CURRENT_LAT}, {CURRENT_LNG}")
except:
    print(f">>> [GPS] 使用默认定位: {CURRENT_LAT}, {CURRENT_LNG}")

# 坐标转换辅助函数
def convert_nmea_to_degrees(nmea_value, direction):
    """
    将 NMEA 格式的经纬度 (如 3108.904311, 12050.437811) 转换为十进制度数
    3108.904311 -> 31度 + 08.904311分
    """
    if not nmea_value:
        return 0.0
    
    nmea_value = float(nmea_value)
    degrees = int(nmea_value / 100)
    minutes = nmea_value - (degrees * 100)
    decimal_degrees = degrees + (minutes / 60)
    
    if direction == 'S' or direction == 'W':
        decimal_degrees = -decimal_degrees
        
    return decimal_degrees

class GPSReaderWorker(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.serial_port = None
        self.running = True
        self.start()

    def run(self):
        global CURRENT_LAT, CURRENT_LNG, CURRENT_ANGLE, CURRENT_RAW_NMEA
        print(f">>> [GPS] 尝试连接真实串口设备: {SERIAL_PORT} @ {BAUD_RATE}")
        try:
            self.serial_port = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            print(f">>> [GPS] 串口 {SERIAL_PORT} 打开成功！开始读取真实 GPS 数据。")
        except Exception as e:
            print(f">>> [GPS] 警告：无法打开串口 {SERIAL_PORT} ({e})。将继续使用 IP/模拟定位。")
            return

        while self.running:
            try:
                line = self.serial_port.readline().decode('ascii', errors='ignore').strip()
                if line.startswith('$GNGGA') or line.startswith('$GPGGA') or line.startswith('$GNRMC') or line.startswith('$GPRMC'):
                    CURRENT_RAW_NMEA = line
                    parts = line.split(',')
                    if len(parts) > 5 and parts[2] and parts[4]:
                        lat_val = parts[2]
                        lat_dir = parts[3]
                        lng_val = parts[4]
                        lng_dir = parts[5]
                        
                        lat_deg = convert_nmea_to_degrees(lat_val, lat_dir)
                        lng_deg = convert_nmea_to_degrees(lng_val, lng_dir)
                        
                        # 简单的防抖：只在坐标确实变化时才更新
                        if abs(CURRENT_LAT - lat_deg) > 0.000001 or abs(CURRENT_LNG - lng_deg) > 0.000001:
                            CURRENT_LAT = lat_deg
                            CURRENT_LNG = lng_deg
                            # 注意：真实的设备输出的坐标是 WGS-84，前端高德地图是 GCJ-02，可能在地图上会有几百米偏差。
                            # 生产环境中建议加上坐标系转换，这里为了测试方便直接使用。
                            print(f"\r>>> [GPS] 真实坐标更新: {CURRENT_LAT:.6f}, {CURRENT_LNG:.6f}", end="")
            except Exception as e:
                time.sleep(1)

    def stop(self):
        self.running = False
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()

# 启动 GPS 读取线程
gps_worker = GPSReaderWorker()

# WebRTC ICE 配置
ICE_SERVERS = [
    RTCIceServer(urls="stun:127.0.0.1:3478"),
    RTCIceServer(urls="turn:127.0.0.1:3478", username="turn_user", credential="turn_password")
]

# 音频配置
CHUNK = 320 
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000 

import edge_tts
import pygame
import tempfile

class EdgeTTSWorker(threading.Thread):
    def __init__(self):
        super().__init__()
        self.queue = queue.Queue()
        self.daemon = True
        try:
            import pythoncom
            pythoncom.CoInitialize()
            pygame.mixer.init()
        except Exception as e:
            print(f"Pygame Init Error: {e}")
        self.start()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            text = self.queue.get()
            if text is None: break
            try:
                loop.run_until_complete(self.speak_async(text))
            except Exception as e:
                print(f"EdgeTTS Error: {e}")
            finally:
                self.queue.task_done()
                
    async def speak_async(self, text):
        global is_ai_talking
        is_ai_talking = True
        try:
            voice = "zh-CN-XiaoxiaoNeural"
            communicate = edge_tts.Communicate(text, voice)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                await communicate.save(tmp_file.name)
                tmp_path = tmp_file.name
                
            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
                
            pygame.mixer.music.unload()
            try: os.remove(tmp_path)
            except: pass
        except Exception as e:
            print(f"Pygame Play Error: {e}")
        finally:
            is_ai_talking = False

    def speak(self, text):
        self.queue.put(text)

tts_worker = EdgeTTSWorker()

def speak_text(text):
    if text: tts_worker.speak(text)


def build_safety_trapezoid(frame_w, frame_h, nav_state="walk"):
    top_y = int(frame_h * 0.60)
    bottom_y = int(frame_h * 0.94)
    top_ratio = 0.28
    bottom_ratio = 0.74

    if nav_state == "crossing":
        bottom_ratio = 0.82
    elif nav_state == "intersection":
        bottom_ratio = 0.70

    top_half = int(frame_w * top_ratio / 2)
    bottom_half = int(frame_w * bottom_ratio / 2)
    cx = frame_w // 2
    return np.array([
        [cx - top_half, top_y],
        [cx + top_half, top_y],
        [cx + bottom_half, bottom_y],
        [cx - bottom_half, bottom_y],
    ], dtype=np.int32)


def bottom_center_in_roi(x1, y1, x2, y2, roi_pts):
    point = ((int(x1) + int(x2)) // 2, int(y2))
    return cv2.pointPolygonTest(roi_pts, point, False) >= 0, point


def map_obstacle_name(label):
    label = str(label).lower()
    if label == 'person':
        return '行人'
    if label in {'vehicle', 'motorcycle', 'bicycle'}:
        return '车辆'
    if label == 'bollard':
        return '路桩'
    return '障碍物'

# 全局状态
latest_frame = None
audio_queue = asyncio.Queue(maxsize=10) 
is_volunteer_active = False
is_ai_talking = False
volunteer_audio_energy = 0.0 
volunteer_client = None

def force_vp8_in_sdp(sdp):
    try:
        rtpmap_matches = re.findall(r'a=rtpmap:(\d+) VP8/90000', sdp)
        if not rtpmap_matches: return sdp
        vp8_pt = rtpmap_matches[0]
        m_line_pattern = r'(m=video \d+ [A-Z/]+ )(.*)'
        match = re.search(m_line_pattern, sdp)
        if match:
            prefix = match.group(1)
            payloads = match.group(2).split()
            if vp8_pt in payloads:
                payloads.remove(vp8_pt)
                payloads.insert(0, vp8_pt)
                sdp = sdp.replace(match.group(0), f"{prefix}{' '.join(payloads)}")
    except: pass
    return sdp

class CustomVideoTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.frame_count = 0

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        self.frame_count += 1
        if latest_frame is not None:
            frame = cv2.cvtColor(latest_frame, cv2.COLOR_BGR2RGB)
            cv2.rectangle(frame, (10, 10), (30, 30), (255, 0, 0), -1)
            if volunteer_audio_energy > 0:
                h, w = frame.shape[:2]
                bar_height = int(min(volunteer_audio_energy * 200, 100))
                cv2.rectangle(frame, (w-20, h-10), (w-10, h-10-bar_height), (0, 255, 0), -1)
        else:
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            frame[:, :] = (0, 0, 255)
            cv2.putText(frame, "NO CAMERA", (50, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

        video_frame = VideoFrame.from_ndarray(frame, format="rgb24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

class CustomAudioTrack(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self.pts = 0

    async def recv(self):
        data = await audio_queue.get()
        frame = AudioFrame(format='s16', layout='mono', samples=CHUNK)
        frame.planes[0].update(data)
        frame.sample_rate = 16000
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, 16000)
        self.pts += CHUNK
        return frame

class VolunteerClient:
    def __init__(self):
        self.sio = socketio.AsyncClient()
        self.pc = None
        self.connected = False
        self.accepted_event = asyncio.Event()
        self.remote_sid = None
        self.setup_listeners()

    async def connect_server(self):
        if self.connected: return
        print(f">>> [信令] 正在连接: {SIGNALING_URL} ...")
        try:
            await self.sio.connect(SIGNALING_URL, transports=['websocket'])
            self.connected = True
            await self.sio.emit('join', {'role': 'user', 'room': 'stream_room'})
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            self.connected = False

    async def start_call(self):
        if not self.connected:
            await self.connect_server()
            if not self.connected:
                speak_text("无法连接服务器")
                return
        self.accepted_event.clear()
        print(">>> 正在呼叫志愿者...")
        speak_text("正在呼叫志愿者，请稍候")
        await self.sio.emit('call_request', {})
        try:
            await asyncio.wait_for(self.accepted_event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            print(">>> 呼叫超时")
            speak_text("呼叫超时，请稍后再试")
            if self.connected: await self.sio.emit('cancel_request', {})
            self.reset_call_state()

    def reset_call_state(self):
        global is_volunteer_active
        if is_volunteer_active:
             speak_text("通话已结束")
        is_volunteer_active = False
        pc_to_close = self.pc
        self.pc = None
        if pc_to_close: asyncio.ensure_future(pc_to_close.close())

    async def end_call(self):
        print(">>> 挂断通话...")
        if self.connected and self.remote_sid:
            await self.sio.emit('bye', {'target': self.remote_sid})
        self.reset_call_state()

    def setup_listeners(self):
        @self.sio.on('connect')
        async def on_connect(): self.connected = True
        @self.sio.on('disconnect')
        async def on_disconnect(): 
            self.connected = False
            self.reset_call_state()
        @self.sio.on('no_volunteers')
        async def on_no_volunteers(data):
            speak_text("暂时没有志愿者接听")
            self.accepted_event.set()
            self.reset_call_state()
        @self.sio.on('volunteer_accepted')
        async def on_accepted(data):
            print(">>> 志愿者已接单！")
            self.accepted_event.set()
            await self.start_webrtc(data['volunteer_sid'])
        @self.sio.on('answer')
        async def on_answer(data):
            if self.pc:
                await self.pc.setRemoteDescription(RTCSessionDescription(sdp=data['sdp'], type=data['type']))
        @self.sio.on('bye')
        async def on_bye(data): self.reset_call_state()

    async def start_webrtc(self, volunteer_sid):
        self.remote_sid = volunteer_sid
        config = RTCConfiguration(iceServers=ICE_SERVERS)
        config.iceTransportPolicy = 'relay'
        self.pc = RTCPeerConnection(configuration=config)
        self.pc.addTrack(CustomVideoTrack())
        self.pc.addTrack(CustomAudioTrack())

        @self.pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                asyncio.ensure_future(self.play_audio(track))

        @self.pc.on("connectionstatechange")
        async def on_connection_state_change():
            if self.pc and self.pc.connectionState in ['failed', 'closed']:
                self.reset_call_state()

        offer = await self.pc.createOffer()
        sdp = force_vp8_in_sdp(offer.sdp)
        await self.pc.setLocalDescription(RTCSessionDescription(sdp=sdp, type=offer.type))
        await asyncio.sleep(0.5)
        await self.sio.emit('offer', {'target': volunteer_sid, 'sdp': self.pc.localDescription.sdp, 'type': self.pc.localDescription.type})

    async def play_audio(self, track):
        global volunteer_audio_energy
        p = pyaudio.PyAudio()
        output_stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, output=True)
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        try:
            while True:
                frame = await track.recv()
                for resampled_frame in resampler.resample(frame):
                    data_bytes = resampled_frame.to_ndarray().tobytes()
                    output_stream.write(data_bytes)
                    audio_np = np.frombuffer(data_bytes, dtype=np.int16)
                    if len(audio_np) > 0:
                        rms = np.sqrt(np.mean(audio_np.astype(np.float32)**2))
                        volunteer_audio_energy = rms / 5000.0 
        except Exception as e: pass
        finally:
            output_stream.stop_stream()
            output_stream.close()
            p.terminate()
            volunteer_audio_energy = 0.0

    async def close(self):
        if self.pc: await self.pc.close()
        if self.connected: await self.sio.disconnect()

async def start_client():
    global latest_frame, is_volunteer_active, volunteer_client, is_ai_talking
    
    print(">>> 正在打开电脑摄像头...")
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) if os.name == 'nt' else cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ 无法打开摄像头！")
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # 启用本地预览窗口
    cv2.namedWindow("PC Client - VisionBridge", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("PC Client - VisionBridge", 640, 480)

    print(">>> 正在初始化麦克风...")
    p = pyaudio.PyAudio()
    try:
        audio_stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
    except Exception as e:
        print(f"❌ 无法打开麦克风: {e}")
        audio_stream = None

    volunteer_client = VolunteerClient()
    asyncio.create_task(volunteer_client.connect_server())
    
    while True:
        try:
            async with websockets.connect(WS_URL, max_size=10*1024*1024) as websocket:
                print("\n=======================================================")
                print(">>> ✅ 已连接到云端 AI 服务器！")
                print(">>> 🎮 电脑端操作指南 (请在弹出的视频窗口中按键):")
                print(">>>   - 按下并按住 'T' 键: 说话与 AI 交流")
                print(">>>   - 松开 'T' 键: 发送语音给 AI")
                print(">>>   - 按下 '空格键' (SPACE): 呼叫/挂断志愿者")
                print(">>>   - 按下 'Q' 键: 退出程序")
                print("=======================================================\n")
                
                await websocket.send(json.dumps({
                    "type": "heartbeat", "lat": CURRENT_LAT, "lng": CURRENT_LNG, "angle": CURRENT_ANGLE, "nmea": CURRENT_RAW_NMEA
                }))

                stop_signal = asyncio.Event()

                async def process_audio():
                    global is_volunteer_active, is_ai_talking
                    loop = asyncio.get_running_loop()
                    while not stop_signal.is_set():
                        await asyncio.sleep(0)
                        if audio_stream and audio_stream.get_read_available() >= CHUNK:
                            try: data = await loop.run_in_executor(None, audio_stream.read, CHUNK, False)
                            except: continue
                            
                            if is_volunteer_active:
                                if audio_queue.full():
                                    try: _ = audio_queue.get_nowait()
                                    except: pass
                                await audio_queue.put(data)
                            elif is_ai_talking:
                                try: await websocket.send(data)
                                except: pass
                        else:
                            await asyncio.sleep(0.005)

                async def process_video():
                    global latest_frame, is_volunteer_active, is_ai_talking
                    loop = asyncio.get_running_loop()
                    
                    while not stop_signal.is_set():
                        await asyncio.sleep(0)
                        
                        ret, frame = await loop.run_in_executor(None, cap.read)
                        if not ret: break
                        latest_frame = frame
                        
                        # 模拟树莓派 5 处理视频流的 FPS 限制 (20fps = 50ms per frame)
                        if USE_LOCAL_AI and getattr(sys.modules[__name__], 'SIMULATE_PI5', False):
                            elapsed = time.time() - getattr(process_video, 'last_frame_time', 0)
                            if elapsed < 0.05:
                                await asyncio.sleep(0.05 - elapsed)
                        setattr(process_video, 'last_frame_time', time.time())
                        
                        # ================= 本地离线 AI 识别 (YOLO NCNN) =================
                        # 拷贝一份用于绘制的帧，最终上传的就是这份画过框的帧
                        display_frame = frame.copy()
                        
                        if USE_LOCAL_AI and not is_volunteer_active:
                            global last_ai_warn_time
                            
                            # 划定中心安全区域 ROI (梯形 Trapezoid)
                            h, w = frame.shape[:2]
                            pts = build_safety_trapezoid(w, h, "walk").reshape((-1, 1, 2))

                            # 在画面上画出半透明的梯形安全区域
                            overlay = display_frame.copy()
                            cv2.fillPoly(overlay, [pts], (0, 255, 0))
                            cv2.addWeighted(overlay, 0.12, display_frame, 0.88, 0, display_frame)
                            cv2.polylines(display_frame, [pts], True, (0, 255, 0), 2)
                            cv2.putText(display_frame, "SAFE ROI", (int(w * 0.36), int(h * 0.56)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                            # 控制推理频率，每 2 帧推理一次 (提高响应速度)
                            setattr(process_video, 'frame_count', getattr(process_video, 'frame_count', 0) + 1)
                            if getattr(process_video, 'frame_count', 0) % 2 == 0:
                                try:
                                    # 推理耗时模拟
                                    if getattr(sys.modules[__name__], 'SIMULATE_PI5', False):
                                        await asyncio.sleep(np.random.uniform(0.04, 0.06))

                                    results = obstacle_model(frame, verbose=False, conf=0.5)
                                    detections = results[0].boxes
                                    
                                    in_zone_objs = []
                                    confidences = []
                                    if len(detections) > 0:
                                        for box in detections:
                                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                                            cls_id = int(box.cls[0].item())
                                            conf = float(box.conf[0].item())
                                            obj_name = results[0].names[cls_id]
                                            spoken_name = map_obstacle_name(obj_name)

                                            confidences.append(conf)

                                            is_inside, bottom_pt = bottom_center_in_roi(x1, y1, x2, y2, pts.reshape(-1, 2))
                                            obj_cx, obj_bottom_y = bottom_pt

                                            label_text = f"{spoken_name} {conf:.2f}"

                                            if is_inside:
                                                in_zone_objs.append((spoken_name, obj_cx))
                                                cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                                                cv2.putText(display_frame, f"WARN: {label_text}", (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                                                cv2.circle(display_frame, (obj_cx, obj_bottom_y), 4, (0, 0, 255), -1)
                                            else:
                                                cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 255, 0), 1)
                                                cv2.putText(display_frame, label_text, (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
                                                cv2.circle(display_frame, (obj_cx, obj_bottom_y), 4, (255, 255, 0), -1)

                                        # 保存本帧指标用于显示
                                        setattr(process_video, 'last_total_objs', len(detections))
                                        setattr(process_video, 'last_danger_objs', len(in_zone_objs))
                                        setattr(process_video, 'last_avg_conf', sum(confidences) / len(confidences) if confidences else 0.0)

                                        # 如果安全区内有障碍物，触发语音预警
                                        if in_zone_objs:
                                            current_time = time.time()
                                            if current_time - last_ai_warn_time > 1.5:
                                                spoken_name, obj_cx = in_zone_objs[0]
                                                if obj_cx < w * 0.4:
                                                    warning_text = f"前方偏左有{spoken_name}"
                                                elif obj_cx > w * 0.6:
                                                    warning_text = f"前方偏右有{spoken_name}"
                                                else:
                                                    warning_text = f"前方有{spoken_name}"
                                                print(f">>> [本地 AI] {warning_text}")
                                                speak_text(warning_text)
                                                last_ai_warn_time = current_time
                                                
                                except Exception as e:
                                    print(f"本地 AI 推理异常: {e}")

                        # --- 上传到服务器后台 ---
                        if not is_volunteer_active:
                            # 注意：上传使用画过框且带安全区的 display_frame，并压缩分辨率以降低网络负担
                            upload_frame = cv2.resize(display_frame, (416, 234))
                            _, buffer = cv2.imencode('.jpg', upload_frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                            payload = {
                                "image": base64.b64encode(buffer).decode('utf-8'),
                                "lat": CURRENT_LAT, "lng": CURRENT_LNG, "angle": CURRENT_ANGLE, "nmea": CURRENT_RAW_NMEA
                            }
                            try: await websocket.send(json.dumps(payload))
                            except: pass
                        else:
                            if time.time() % 2 < 0.1:
                                try: await websocket.send(json.dumps({
                                    "type": "heartbeat", "lat": CURRENT_LAT, "lng": CURRENT_LNG, "angle": CURRENT_ANGLE, "nmea": CURRENT_RAW_NMEA
                                }))
                                except: pass

                        # --- 界面显示和按键处理 ---
                        status = "Call Active" if is_volunteer_active else ("AI Listening..." if is_ai_talking else "Online")
                        color = (0, 0, 255) if is_volunteer_active else ((255, 0, 0) if is_ai_talking else (0, 255, 0))
                        
                        cv2.putText(display_frame, f"Status: {status}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                        cv2.putText(display_frame, "Hold 'T': AI Talk | 'SPACE': Call Vol | 'Q': Quit", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

                        
                        # 新增：如果在本地离线 AI 模式下，绘制检测性能指标面板
                        if USE_LOCAL_AI and not is_volunteer_active:
                            panel_y = 80
                            cv2.rectangle(display_frame, (10, panel_y), (320, panel_y+155), (0, 0, 0), -1)
                            cv2.addWeighted(display_frame[panel_y:panel_y+155, 10:320], 0.6, display_frame[panel_y:panel_y+155, 10:320], 0.4, 0, display_frame[panel_y:panel_y+155, 10:320])
                            
                            total_objs = getattr(process_video, 'last_total_objs', 0)
                            danger_objs = getattr(process_video, 'last_danger_objs', 0)
                            avg_conf = getattr(process_video, 'last_avg_conf', 0.0)
                            
                            cv2.putText(display_frame, f"Performance Metrics", (20, panel_y+20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                            cv2.putText(display_frame, f"Total Objects: {total_objs}", (20, panel_y+50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                            cv2.putText(display_frame, f"Danger (In ROI): {danger_objs}", (20, panel_y+80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255) if danger_objs > 0 else (0, 255, 0), 1)
                            cv2.putText(display_frame, f"Avg Confidence: {avg_conf:.2f}", (20, panel_y+110), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 1)

                            if getattr(sys.modules[__name__], 'SIMULATE_PI5', False):
                                cv2.putText(display_frame, "SIMULATING RASPBERRY PI 5", (20, panel_y+135), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

                        cv2.imshow("PC Client - VisionBridge", display_frame)
                        
                        key = cv2.waitKey(1) & 0xFF
                        if key == ord('q'):
                            stop_signal.set()
                            break
                        elif key == 32: # SPACE
                            if not is_volunteer_active:
                                asyncio.create_task(volunteer_client.start_call())
                                is_volunteer_active = True
                            else:
                                asyncio.create_task(volunteer_client.end_call())
                        
                        if key == ord('t'):
                            is_ai_talking = True
                            process_video.last_key_time = time.time()
                        else:
                            if is_ai_talking and (time.time() - getattr(process_video, 'last_key_time', 0) > 0.2):
                                is_ai_talking = False

                        await asyncio.sleep(0.066)

                async def process_response():
                    while not stop_signal.is_set():
                        await asyncio.sleep(0)
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                            data = json.loads(message)
                            if data.get("type") == "ai_terminal_output":
                                text = data.get("text", "")
                                print(f"\r{text}")
                                if text.startswith("AI: "):
                                    speak_text(text[4:])
                        except: pass

                t1 = asyncio.create_task(process_video())
                t2 = asyncio.create_task(process_audio()) 
                t3 = asyncio.create_task(process_response())
                await stop_signal.wait()
                t1.cancel(); t2.cancel(); t3.cancel()
                
        except Exception as e:
            print(f"连接断开: {e} - 2秒后重试...")
            await asyncio.sleep(2)
        finally:
            cv2.destroyAllWindows()
            if audio_stream: audio_stream.close()
            p.terminate()
            if volunteer_client: await volunteer_client.close()
            gps_worker.stop()
            break # 测试端退出循环不重连

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(start_client())
