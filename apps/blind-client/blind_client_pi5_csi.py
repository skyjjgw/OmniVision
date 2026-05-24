import cv2
import asyncio
import websockets
import json
import base64
import numpy as np
import time
import pyaudio
import socketio
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, AudioStreamTrack, RTCConfiguration, RTCIceServer, RTCIceCandidate, RTCRtpTransceiver, RTCRtpSender
from aiortc.contrib.media import MediaRelay
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
import sys
import argparse

try:
    from picamera2 import Picamera2
except Exception:
    Picamera2 = None
    print(">>> [CSI] picamera2 不可用：请在树莓派 Bookworm 上安装 python3-picamera2，并先通过 libcamera-hello 验证摄像头")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import pyttsx3
    HAS_TTS = True
except ImportError:
    HAS_TTS = False
    print("Warning: pyttsx3 not installed. TTS will be disabled.")

try:
    import doubao_streaming_asr as doubao_asr
except Exception:
    doubao_asr = None

AI_SCHEDULE_MODE = os.getenv("AI_SCHEDULE_MODE", "auto").strip().lower()
if AI_SCHEDULE_MODE not in {"auto", "obs", "light", "seg", "dual"}:
    AI_SCHEDULE_MODE = "auto"
print(f">>> [AI] 调度模式: {AI_SCHEDULE_MODE}")

# --- 初始化本地离线 YOLO 模型 ---
try:
    from ultralytics import YOLO
    print(">>> [AI] 正在加载本地 YOLOv8 模型...")
    # 根据树莓派上的实际文件结构，模型和代码在同一个目录下
    # 我们使用 os.path 获取当前脚本所在目录的绝对路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    unified_openvino_path = os.path.join(current_dir, "pruned_best_int8_openvino_model")
    unified_pruned_pt_path = os.path.join(current_dir, "pruned_best.pt")
    unified_best_pt_path = os.path.join(current_dir, "best.pt")
    print(f">>> [AI] 模型目录: {current_dir}")
    print(f">>> [AI] OpenVINO INT8 是否存在: {os.path.exists(unified_openvino_path)} -> {unified_openvino_path}")
    print(f">>> [AI] pruned_best.pt 是否存在: {os.path.exists(unified_pruned_pt_path)} -> {unified_pruned_pt_path}")
    print(f">>> [AI] best.pt 是否存在: {os.path.exists(unified_best_pt_path)} -> {unified_best_pt_path}")

    # 旧三模型，作为兼容回退
    obstacle_ncnn_path = os.path.join(current_dir, "det_wotr_obstacles_final_88_81_ncnn_model")
    seg_ncnn_path = os.path.join(current_dir, "seg_blind_zebra_final_94_94_ncnn_model")
    light_ncnn_path = os.path.join(current_dir, "det_pedestrian_lights_final_98_66_ncnn_model")
    obstacle_pt_path = os.path.join(current_dir, "det_wotr_obstacles_final_88_81.pt")
    seg_pt_path = os.path.join(current_dir, "seg_blind_zebra_final_94_94.pt")
    light_pt_path = os.path.join(current_dir, "det_pedestrian_lights_final_98_66.pt")

    unified_model = None
    obstacle_model = None
    seg_model = None
    light_model = None

    if os.path.exists(unified_openvino_path):
        print(">>> [AI] 优先加载最新统一模型: OpenVINO INT8")
        unified_model = YOLO(unified_openvino_path, task="detect")
    elif os.path.exists(unified_pruned_pt_path):
        print(">>> [AI] 加载最新统一模型: pruned_best.pt")
        unified_model = YOLO(unified_pruned_pt_path)
    elif os.path.exists(unified_best_pt_path):
        print(">>> [AI] 加载最新统一模型: best.pt")
        unified_model = YOLO(unified_best_pt_path)
    else:
        required_models = set()
        if AI_SCHEDULE_MODE == "obs":
            required_models = {"obs"}
        elif AI_SCHEDULE_MODE == "light":
            required_models = {"light"}
        elif AI_SCHEDULE_MODE == "seg":
            required_models = {"seg"}
        elif AI_SCHEDULE_MODE == "dual":
            required_models = {"obs", "light"}
        else:
            required_models = {"obs", "light", "seg"}

        print(f">>> [AI] 未发现最新统一模型，回退旧三模型: {sorted(list(required_models))}")

        if "obs" in required_models:
            if os.path.exists(obstacle_ncnn_path):
                obstacle_model = YOLO(obstacle_ncnn_path)
            else:
                obstacle_model = YOLO(obstacle_pt_path)
        if "seg" in required_models:
            if os.path.exists(seg_ncnn_path):
                seg_model = YOLO(seg_ncnn_path)
            else:
                seg_model = YOLO(seg_pt_path)
        if "light" in required_models:
            if os.path.exists(light_ncnn_path):
                light_model = YOLO(light_ncnn_path)
            else:
                light_model = YOLO(light_pt_path)

    USE_LOCAL_AI = True
    last_ai_warn_time = 0
    
    # ==== NCNN 模型预热 (Warm-up) ====
    # NCNN 首次推理时会进行内存分配和计算图构建，在树莓派上耗时极长(10秒以上)。
    # 必须在网络连接前进行预热，否则会阻塞 asyncio 导致 SocketIO 断开！
    print(">>> [AI] 正在预热 YOLO/NCNN 模型，请耐心等待 (约 15-30 秒)...")
    dummy_img = __import__('numpy').zeros((360, 480, 3), dtype=__import__('numpy').uint8)
    try:
        if unified_model is not None:
            unified_model(dummy_img, verbose=False, imgsz=320, conf=0.35)
        if obstacle_model is not None:
            obstacle_model(dummy_img, verbose=False)
        if seg_model is not None:
            seg_model(dummy_img, verbose=False)
        if light_model is not None:
            light_model(dummy_img, verbose=False)
        print(">>> [AI] 模型预热完成！性能已达最佳。")
    except Exception as e:
        print(f">>> [AI] 模型预热失败: {e}")

except ImportError as e:
    print(f">>> [AI] 未安装或无法导入 ultralytics/openvino 相关依赖: {e}")
    print(">>> [AI] 请先在虚拟环境中安装: python -m pip install ultralytics openvino")
    USE_LOCAL_AI = False
except Exception as e:
    print(f">>> [AI] 模型加载失败，请检查路径是否正确或显存是否足够: {e}")
    USE_LOCAL_AI = False

try:
    import pynmea2
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    print("Warning: pynmea2 or serial not installed. GPS functions may fail.")

# --- 日志配置 ---
logging.basicConfig(level=logging.INFO)
# 开启 aiortc 和 aioice 的调试日志
logging.getLogger("aioice").setLevel(logging.WARNING) # 改为 INFO 查看详细 ICE 交互
logging.getLogger("aiortc").setLevel(logging.WARNING)

# --- 配置 ---
SERVER_IP = "127.0.0.1" # 推理服务器
SERVER_PORT = 8000

# 获取或生成唯一 Client ID
CLIENT_ID_FILE = "client_id.txt"
if os.path.exists(CLIENT_ID_FILE):
    with open(CLIENT_ID_FILE, "r") as f:
        CLIENT_ID = f.read().strip()
else:
    CLIENT_ID = f"dev-{uuid.uuid4().hex[:8]}"
    with open(CLIENT_ID_FILE, "w") as f:
        f.write(CLIENT_ID)

print(f">>> Client ID: {CLIENT_ID}")

WS_URL = f"ws://{SERVER_IP}:{SERVER_PORT}/ws/client_input?client_id={CLIENT_ID}"

# Geolocation Helper
import requests
def get_real_location():
    """Fetches approximate location based on IP address."""
    # Try multiple services
    services = [
        ('http://ip-api.com/json', lambda d: (d['lat'], d['lon'], d.get('city', 'Unknown'), d.get('regionName', ''))),
        ('https://ipinfo.io/json', lambda d: (float(d['loc'].split(',')[0]), float(d['loc'].split(',')[1]), d.get('city', 'Unknown'), d.get('region', '')))
    ]
    
    for url, parser in services:
        try:
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                data = response.json()
                lat, lng, city, region = parser(data)
                print(f">>> [Geo] Real Location ({url}): {city}, {region} ({lat}, {lng})")
                return lat, lng
        except Exception as e:
            print(f">>> [Geo] Failed with {url}: {e}")
            continue
    
    # Fallback to Beijing if all failed
    print(">>> [Geo] All IP services failed, using default location.")
    return 39.9042, 116.4074

# --- 坐标转换 (WGS84 -> GCJ02) ---
# 修复国内 IP/GPS 定位在地图上偏移的问题
import math

def wgs84_to_gcj02(lng, lat):
    """
    WGS84转GCJ02(火星坐标系)
    :param lng: WGS84经度
    :param lat: WGS84纬度
    :return: GCJ02经度, GCJ02纬度
    """
    x_pi = 3.14159265358979324 * 3000.0 / 180.0
    a = 6378245.0  # 长半轴
    ee = 0.00669342162296594323  # 偏心率平方
    
    def _transformlat(lng, lat):
        ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + \
              0.1 * lng * lat + 0.2 * math.sqrt(math.fabs(lng))
        ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
        return ret

    def _transformlng(lng, lat):
        ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + \
              0.1 * lng * lat + 0.1 * math.sqrt(math.fabs(lng))
        ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
        return ret

    # 判断是否在国内，不在国内则不做偏移
    if not (73.66 < lng < 135.05 and 3.86 < lat < 53.55):
        return lng, lat
        
    dlat = _transformlat(lng - 105.0, lat - 35.0)
    dlng = _transformlng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
    mglat = lat + dlat
    mglng = lng + dlng
    return mglng, mglat

# --- GPS 模块抽象层 ---
class PedestrianGPSFilter:
    """行人专属 GPS 平滑滤波器 (防止原地漂移、剔除突变飞点)"""
    def __init__(self, alpha=0.3, max_jump_meters=20.0):
        self.lat = None
        self.lng = None
        self.alpha = alpha  # 平滑系数 (0~1)，越小越平滑但跟随越慢
        self.max_jump = max_jump_meters # 每秒最大允许跳跃距离(米)

    def _distance(self, lat1, lon1, lat2, lon2):
        # 简易地球两点测距(米)
        R = 6371000
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi/2.0)**2 + math.cos(phi1)*math.cos(phi2) * math.sin(delta_lambda/2.0)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c

    def update(self, new_lat, new_lng):
        if new_lat == 0.0 or new_lng == 0.0:
            return self.lat, self.lng

        if self.lat is None or self.lng is None:
            self.lat = new_lat
            self.lng = new_lng
            return self.lat, self.lng

        dist = self._distance(self.lat, self.lng, new_lat, new_lng)
        
        # 1. 异常点剔除 (Outlier Rejection)
        if dist > self.max_jump:
            # 遇到瞬间离谱跳跃 (如突然被高楼反射信号)，降低权重为 5%
            current_alpha = 0.05
        else:
            current_alpha = self.alpha

        # 2. 指数移动平均平滑 (EMA)
        self.lat = self.lat * (1 - current_alpha) + new_lat * current_alpha
        self.lng = self.lng * (1 - current_alpha) + new_lng * current_alpha
        
        return self.lat, self.lng

class BaseGPSProvider:
    def get_location(self):
        """返回 (lat, lng) 或 (None, None)"""
        raise NotImplementedError
        
    def get_raw_nmea(self):
        return ""

class MockGPSProvider(BaseGPSProvider):
    def __init__(self):
        self.lat, self.lng = 39.9042, 116.4074 # Default Beijing
        self.raw_nmea = "$GPRMC,081836,A,3751.65,S,14507.36,E,000.0,360.0,130998,011.3,E*62 (MOCK)"
        # Try IP location
        try:
            raw_lat, raw_lng = get_real_location()
            # wgs84_to_gcj02 returns (lng, lat)
            mglng, mglat = wgs84_to_gcj02(raw_lng, raw_lat)
            self.lat = mglat
            self.lng = mglng
            print(f">>> [GPS] Mock Provider initialized with IP Location: {self.lat}, {self.lng}")
        except Exception as e:
            print(f">>> [GPS] IP Location failed: {e}")

    def get_location(self):
        return self.lat, self.lng
        
    def get_raw_nmea(self):
        return self.raw_nmea

class NoGPSProvider(BaseGPSProvider):
    def get_location(self):
        return None, None

    def get_raw_nmea(self):
        return ""

class SerialGPSProvider(BaseGPSProvider):
    def __init__(self, port='/dev/ttyUSB1', baudrate=115200): # 改为 ttyUSB1
        self.port = port
        self.baudrate = baudrate
        self.current_lat = None
        self.current_lng = None
        self.raw_nmea = ""
        self.true_course = None
        self.speed_kmh = 0.0      # 地面速度(km/h)
        self.num_sats = 0         # 锁定卫星数
        self.hdop = 99.9          # 水平精度因子 (HDOP)
        self.altitude = 0.0       # 海拔高度(米)
        self.running = False
        self.thread = None
        self.filter = PedestrianGPSFilter(alpha=0.3, max_jump_meters=15.0) # 挂载行人专属滤波器
        
    def start(self):
        if self.running: return
        if not HAS_SERIAL:
            print(">>> [GPS] pyserial or pynmea2 not found, cannot start Serial Provider.")
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._read_serial, daemon=True)
        self.thread.start()
        print(f">>> [GPS] LC76G Serial Provider started on {self.port} at {self.baudrate}bps")

    def _read_serial(self):
        while self.running:
            try:
                with serial.Serial(self.port, self.baudrate, timeout=1) as ser:
                    print(">>> [GPS] Serial connection established. Waiting for NMEA data...")
                    
                    # 向 LC76G 发送 PAIR050 命令，设置刷新率为 5Hz (200ms)
                    # 命令格式: $PAIR050,200*21\r\n
                    # 校验和 21 是计算出来的: P(50)^A(41)^I(49)^R(52)^0(30)^5(35)^0(30)^,(2C)^2(32)^0(30)^0(30) = 21
                    try:
                        setup_cmd = b'$PAIR050,200*21\r\n'
                        ser.write(setup_cmd)
                        print(">>> [GPS] Set LC76G update rate to 5Hz (200ms).")
                        time.sleep(0.5) # 给模块一点时间响应
                    except Exception as e:
                        print(f">>> [GPS] Warning: Failed to set 5Hz update rate: {e}")

                    while self.running:
                        line = ser.readline().decode('ascii', errors='replace').strip()
                        if not line:
                            continue
                        # 只要是 NMEA 相关开头的，不管里面有没有数据（比如全空的逗号），都直接赋值给 raw_nmea 上报
                        if line.startswith('$GNRMC') or line.startswith('$GPRMC') or line.startswith('$GNGGA') or line.startswith('$GPGGA'):
                            self.raw_nmea = line
                            try:
                                msg = pynmea2.parse(line)
                                
                                # 解析 $GPGGA / $GNGGA 获取卫星数、HDOP、海拔
                                if getattr(msg, 'sentence_type', '') == 'GGA':
                                    try:
                                        if hasattr(msg, 'num_sats') and msg.num_sats:
                                            self.num_sats = int(msg.num_sats)
                                        if hasattr(msg, 'horizontal_dil') and msg.horizontal_dil:
                                            self.hdop = float(msg.horizontal_dil)
                                        if hasattr(msg, 'altitude') and msg.altitude:
                                            self.altitude = float(msg.altitude)
                                    except ValueError:
                                        pass
                                
                                # 解析 $GPRMC / $GNRMC 获取经纬度、速度、航向
                                if hasattr(msg, 'latitude') and msg.latitude and hasattr(msg, 'longitude') and msg.longitude:
                                    # 直接上报硬件真实 WGS-84 坐标，使用行人防抖滤波，不调用车辆绑路
                                    flat, flng = self.filter.update(msg.latitude, msg.longitude)
                                    if flat is not None and flng is not None:
                                        self.current_lat = flat
                                        self.current_lng = flng
                                    
                                    # 提取自带的方向角(True Course)，$GPRMC/$GNRMC 特有
                                    if hasattr(msg, 'true_course') and msg.true_course is not None:
                                        try:
                                            self.true_course = float(msg.true_course)
                                        except ValueError:
                                            pass
                                            
                                    # 提取地面速度(节转km/h)
                                    if hasattr(msg, 'spd_over_grnd') and msg.spd_over_grnd is not None:
                                        try:
                                            # 1 knot = 1.852 km/h
                                            self.speed_kmh = float(msg.spd_over_grnd) * 1.852
                                        except ValueError:
                                            pass
                            except pynmea2.ParseError:
                                pass
            except Exception as e:
                print(f">>> [GPS] Serial Error (retrying in 5s): {e}")
                time.sleep(5)

    def get_location(self):
        return self.current_lat, self.current_lng

    def get_raw_nmea(self):
        return self.raw_nmea
        
    def get_true_course(self):
        """返回硬件 NMEA 自带的真实航向角 (True Course)，如果不可用返回 None"""
        if hasattr(self, 'true_course'):
            return self.true_course
        return None

    def get_gps_details(self):
        """返回速度、卫星数、精度因子、海拔等扩展信息"""
        return {
            "speed": getattr(self, 'speed_kmh', 0.0),
            "sats": getattr(self, 'num_sats', 0),
            "hdop": getattr(self, 'hdop', 99.9),
            "alt": getattr(self, 'altitude', 0.0)
        }

# 自动选择 GPS 提供者
gps_provider = None
# Check possible serial ports for Raspberry Pi (USB or UART)
target_port = None
for port in ['/dev/ttyUSB1', '/dev/ttyUSB0', '/dev/ttyACM0', '/dev/ttyS0', '/dev/ttyAMA0', '/dev/ttyAMA10']:
    if os.path.exists(port):
        target_port = port
        break

if target_port and HAS_SERIAL:
    print(f">>> [GPS] Detected hardware port {target_port}, initializing LC76G...")
    gps_provider = SerialGPSProvider(port=target_port, baudrate=115200) # 波特率改为115200
    gps_provider.start()
else:
    print(">>> [GPS] No hardware serial port found, GPS disabled (no mock fallback).")
    gps_provider = NoGPSProvider()

# 全局变量更新
CURRENT_ANGLE = 0.0
LAST_LAT = None
LAST_LNG = None
CURRENT_LAT = None
CURRENT_LNG = None
CURRENT_RAW_NMEA = ""

def calculate_bearing(lat1, lng1, lat2, lng2):
    """Calculate bearing between two points"""
    if lat1 is None or lat2 is None: return 0.0
    
    # Convert to radians
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    
    d_lng = lng2 - lng1
    
    y = math.sin(d_lng) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lng)
    
    bearing = math.atan2(y, x)
    bearing = math.degrees(bearing)
    
    return (bearing + 360) % 360

def update_global_location():
    global CURRENT_LAT, CURRENT_LNG, CURRENT_ANGLE, LAST_LAT, LAST_LNG, CURRENT_RAW_NMEA
    
    # 尝试获取 GPS
    lat, lng = gps_provider.get_location()
    CURRENT_RAW_NMEA = gps_provider.get_raw_nmea()
    hw_angle = getattr(gps_provider, 'get_true_course', lambda: None)()
    details = getattr(gps_provider, 'get_gps_details', lambda: {})()
    
    # 如果 GPS 获取失败，保持上一次有效值，不注入任何默认虚拟坐标
    if lat is None or lng is None:
        return None # 保持上一次的值

    # 如果获取到了有效值
    if lat is not None and lng is not None:
        # 盲人持盲杖步速较慢，通常在 0.5 ~ 1.5 km/h。
        # 我们将速度阈值降低至 0.5 km/h (约 0.14 m/s)，兼顾慢速导航与抗静止漂移。
        speed = details.get('speed', 0.0)
        
        if hw_angle is not None and speed > 0.5:
            # 优先使用硬件解析出的真北方向角
            CURRENT_ANGLE = hw_angle
            LAST_LAT, LAST_LNG = lat, lng
        else:
            # 如果处于极低速或静止 (<0.5km/h)，硬件方向角不可信。
            # 此时回退为：仅当产生明确位移（约 2.2 米，即 0.00002 度）时，才根据两点坐标重算方向。
            if LAST_LAT is not None and LAST_LNG is not None:
                if abs(lat - LAST_LAT) > 0.00002 or abs(lng - LAST_LNG) > 0.00002:
                    CURRENT_ANGLE = calculate_bearing(LAST_LAT, LAST_LNG, lat, lng)
                    LAST_LAT = lat
                    LAST_LNG = lng
            else:
                LAST_LAT = lat
                LAST_LNG = lng
            
        CURRENT_LAT, CURRENT_LNG = lat, lng
        return details

# Initial Location (Global) - Converted
update_global_location()
# raw_lat, raw_lng = get_real_location()
# CURRENT_LNG, CURRENT_LAT = wgs84_to_gcj02(raw_lng, raw_lat) # 注意函数返回顺序和变量接收顺序
# print(f">>> [Geo] Converted to GCJ-02: {CURRENT_LAT}, {CURRENT_LNG}")

# --- EdgeTTS 集成 (晓晓语音) ---
import edge_tts
import tempfile
import pygame

class EdgeTTSWorker(threading.Thread):
    def __init__(self):
        super().__init__()
        self.queue = queue.Queue()
        self.daemon = True
        self.running = True
        
    def run(self):
        print(">>> EdgeTTS Worker Thread Started (High Quality)")
        
        # 初始化 PyGame Mixer
        try:
            # 解决 Windows 下 CoInitialize 问题
            import pythoncom
            pythoncom.CoInitialize()
            
            pygame.mixer.init()
        except Exception as e:
            print(f"Pygame Mixer Init Error: {e}")
            
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        while self.running:
            try:
                text = self.queue.get()
                if text is None: break
                
                # Run async tts in sync thread
                loop.run_until_complete(self._speak(text))
                
            except Exception as e:
                print(f"EdgeTTS Error: {e}")
                
    async def _speak(self, text):
        global is_ai_talking
        is_ai_talking = True
        try:
            # 使用临时文件播放
            tmp_path = os.path.join(tempfile.gettempdir(), f"tts_{uuid.uuid4().hex}.mp3")
            
            communicate = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
            await communicate.save(tmp_path)
            
            # Play
            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
                
            # Cleanup
            pygame.mixer.music.unload()
            try:
                os.remove(tmp_path)
            except: pass
            
        except Exception as e:
            print(f"TTS Gen Error: {e}")
        finally:
            is_ai_talking = False

# 全局 TTS Worker
tts_worker = EdgeTTSWorker()
tts_worker.start()

# --- 注释掉硬件 GPS 相关代码 (模拟环境使用本地 IP 定位) ---
# class GPSWorker(threading.Thread):
#     def __init__(self, port='COM3', baudrate=9600):
#         super().__init__()
#         self.port = port
#         self.baudrate = baudrate
#         self.daemon = True
#         self.running = True
#         self.connected = False
        
#     def run(self):
#         global CURRENT_LAT, CURRENT_LNG
#         print(f">>> [GPS] 尝试连接 GPS 模块 ({self.port})...")
        
#         try:
#             with serial.Serial(self.port, self.baudrate, timeout=1) as ser:
#                 print(f">>> [GPS] 模块已连接! 正在搜星...")
#                 self.connected = True
                
#                 while self.running:
#                     try:
#                         line = ser.readline().decode('ascii', errors='replace').strip()
#                         if line.startswith('$GNGGA') or line.startswith('$GPGGA'):
#                             msg = pynmea2.parse(line)
#                             if msg.latitude and msg.longitude:
#                                 CURRENT_LAT = msg.latitude
#                                 CURRENT_LNG = msg.longitude
#                                 # print(f">>> [GPS] Location Updated: {CURRENT_LAT}, {CURRENT_LNG}")
#                     except Exception as e:
#                         # print(f">>> [GPS] Parse Error: {e}")
#                         pass
#         except serial.SerialException:
#             print(f">>> [GPS] 无法打开串口 {self.port}，将使用 IP 定位作为备选。")
#             # Fallback to IP Location
#             lat, lng = get_real_location()
#             CURRENT_LAT, CURRENT_LNG = lat, lng

# # 启动 GPS 线程 (尝试常见端口，Windows通常是COM3/4，Linux是/dev/ttyUSB0)
# gps_port = 'COM3' if os.name == 'nt' else '/dev/ttyUSB0'
# gps_thread = GPSWorker(port=gps_port)
# gps_thread.start()

# --- Browser-based Location Service (已禁用) ---
# from http.server import BaseHTTPRequestHandler, HTTPServer
# import webbrowser
# import threading
# import json

# class BrowserLocationServer(BaseHTTPRequestHandler):
#     # ... (code commented out) ...
#     pass

def get_location_via_browser():
    """Starts a local web server to get high-precision location from browser"""
    # Disabled by user request
    raise NotImplementedError("Browser location disabled")

# 启动定位逻辑
# 仅在 Mock/IP Provider 下才允许使用 IP 回退，避免覆盖真实串口 GPS
if isinstance(gps_provider, MockGPSProvider):
    try:
        raw_lat, raw_lng = get_real_location()
        CURRENT_LNG, CURRENT_LAT = wgs84_to_gcj02(raw_lng, raw_lat)
        print(f">>> [Geo] Fallback IP Location (GCJ-02): {CURRENT_LAT}, {CURRENT_LNG}")
    except Exception as e:
        print(f">>> [Geo] Fallback IP Location failed: {e}")

# --- 强制指定测试坐标 (苏州大学应用技术学院) ---
# print(">>> [GPS] 模拟模式：强制定位到苏州大学应用技术学院")
# # 高德坐标 (GCJ-02)
# CURRENT_LAT = 31.1125
# CURRENT_LNG = 120.8442
# print(f">>> [Geo] Fixed Location: {CURRENT_LAT}, {CURRENT_LNG}")

SIGNALING_URL = "http://127.0.0.1:6000" # 志愿者信令服务器

# WebRTC ICE 配置
ICE_SERVERS = [
    # 替换 Google STUN 为国内可用或自建 STUN，防止连接超时
    RTCIceServer(urls="stun:127.0.0.1:3478"),
    RTCIceServer(urls="turn:127.0.0.1:3478", username="turn_user", credential="turn_password")
]

# 音频配置
CHUNK = 160 # 10ms at 16kHz, 更偏向超低延迟通话
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000 

import edge_tts
import shutil
import os
import subprocess
import pygame
import tempfile

# TTS Worker using EdgeTTS (Online Neural Voice) + FFplay/Pygame (Local Playback)
# Replaces pyttsx3 for better quality
class EdgeTTSWorker(threading.Thread):
    def __init__(self):
        super().__init__()
        self.queue = queue.Queue()
        self.daemon = True
        self.start()
        
        # Locate ffplay
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.ffplay_path = os.path.join(current_dir, "ffplay.exe")
        self.use_ffplay = False
        
        if os.path.exists(self.ffplay_path):
            self.use_ffplay = True
        elif shutil.which("ffplay"):
            self.ffplay_path = "ffplay"
            self.use_ffplay = True
        else:
            print(">>> Warning: ffplay.exe not found! Fallback to Pygame player.")
            try:
                pygame.mixer.init()
            except Exception as e:
                print(f">>> Pygame Init Error: {e}")

    def run(self):
        print(">>> EdgeTTS Worker Thread Started (High Quality)")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        while True:
            text = self.queue.get()
            if text is None: break
            
            try:
                if self.use_ffplay:
                    loop.run_until_complete(self.speak_async_ffplay(text))
                else:
                    loop.run_until_complete(self.speak_async_pygame(text))
                
            except Exception as e:
                print(f"EdgeTTS Speak Error: {e}")
            finally:
                self.queue.task_done()
                
    async def speak_async_ffplay(self, text):
        try:
            voice = "zh-CN-XiaoxiaoNeural" 
            communicate = edge_tts.Communicate(text, voice)
            
            cmd = [self.ffplay_path, "-nodisp", "-autoexit", "-loglevel", "quiet", "-"]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    proc.stdin.write(chunk["data"])
                    proc.stdin.flush()
            
            proc.stdin.close()
            proc.wait()
        except Exception as e:
            print(f"FFplay Error: {e}")

    async def speak_async_pygame(self, text):
        try:
            voice = "zh-CN-XiaoxiaoNeural"
            communicate = edge_tts.Communicate(text, voice)
            
            # Save to temp file because pygame needs file path or file-like object
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                await communicate.save(tmp_file.name)
                tmp_path = tmp_file.name
                
            # Play using pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init()
                
            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.play()
            
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
                
            # Cleanup
            pygame.mixer.music.unload()
            try:
                os.remove(tmp_path)
            except: pass
            
        except Exception as e:
            if "ALSA" in str(e) or "audio device" in str(e).lower():
                print(f"Pygame Play Error: 无法播放声音 (未检测到音频输出设备)。已静音。")
            else:
                print(f"Pygame Play Error: {e}")

    def speak(self, text):
        self.queue.put(text)

enable_legacy_tts_worker = os.getenv("ENABLE_LEGACY_TTS_WORKER", "0") == "1"
tts_worker = EdgeTTSWorker() if enable_legacy_tts_worker else None

SAFETY_PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_prompts", "safety")
SAFETY_PROMPT_MAP = {
    "obs_center_person": "前方有行人",
    "obs_left_person": "前方偏左有行人",
    "obs_right_person": "前方偏右有行人",
    "obs_center_vehicle": "前方有车辆",
    "obs_left_vehicle": "前方偏左有车辆",
    "obs_right_vehicle": "前方偏右有车辆",
    "obs_center_bollard": "前方有路桩",
    "obs_left_bollard": "前方偏左有路桩",
    "obs_right_bollard": "前方偏右有路桩",
    "obs_center_obstacle": "前方有障碍物",
    "obs_left_obstacle": "前方偏左有障碍物",
    "obs_right_obstacle": "前方偏右有障碍物",
    "nav_red_wait": "红灯，请等待",
    "nav_intersection": "前方路口，注意斑马线",
    "nav_go": "绿灯，可以通过",
    "nav_passed": "已通过路口",
    "cross_left": "请向左调整",
    "cross_right": "请向右调整",
    "cross_lost": "偏离斑马线，请停止并调整方向",
}
SAFETY_POSITION_PROMPT_PREFIX = {
    "": "center",
    "偏左": "left",
    "偏右": "right",
}


class SafetyPromptWorker(threading.Thread):
    def __init__(self, prompt_dir):
        super().__init__()
        self.prompt_dir = prompt_dir
        self.queue = queue.Queue(maxsize=6)
        self.daemon = True
        self.audio_device = os.getenv("AUDIO_PLAYBACK_DEVICE", "").strip() or os.getenv("ASR_TTS_OUTPUT_DEVICE", "").strip() or "default"
        self.ffplay_path = None
        self.play_timeout_s = max(1.0, float(os.getenv("SAFETY_PROMPT_TIMEOUT_SECONDS", "4.0")))
        self.tail_hold_s = max(0.0, float(os.getenv("SAFETY_PROMPT_TAIL_SECONDS", os.getenv("SAFETY_PROMPT_TAIL_MS", "400"))) / (1000.0 if os.getenv("SAFETY_PROMPT_TAIL_SECONDS") is None else 1.0))
        self._signal_lock = threading.Lock()
        self._signal_active = False
        local_ffplay = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffplay.exe")
        if os.path.exists(local_ffplay):
            self.ffplay_path = local_ffplay
        elif shutil.which("ffplay"):
            self.ffplay_path = "ffplay"
        self.aplay_path = shutil.which("aplay")
        self.signal_file = os.getenv("SAFETY_PROMPT_SIGNAL_FILE", "/tmp/omnivision_safety_prompt_active")
        if not self.ffplay_path and not self.aplay_path:
            print(">>> [SAFETY PROMPT] 未找到 ffplay/aplay，安全提示将回退到 TTS")
        self.start()

    def _set_signal_active(self, active: bool):
        try:
            if active:
                with open(self.signal_file, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
            elif os.path.exists(self.signal_file):
                os.remove(self.signal_file)
        except Exception as e:
            print(f">>> [SAFETY PROMPT] 更新信号文件失败: {e}")

    def _activate_signal_lock(self):
        with self._signal_lock:
            if self._signal_active:
                self._set_signal_active(True)
                return
            self._signal_active = True
            self._set_signal_active(True)
            print(">>> [SAFETY PROMPT] 安全播报锁已激活")

    def _release_signal_lock(self):
        with self._signal_lock:
            if not self._signal_active:
                return
            self._signal_active = False
            self._set_signal_active(False)
            print(">>> [SAFETY PROMPT] 安全播报锁已释放")

    def enqueue_prompt(self, prompt_id, fallback_text=""):
        wav_path = os.path.join(self.prompt_dir, f"{prompt_id}.wav")
        if not os.path.exists(wav_path):
            print(f">>> [SAFETY PROMPT] 缺少素材: {prompt_id} -> {wav_path}")
            return False
        if not self.ffplay_path and not self.aplay_path:
            print(f">>> [SAFETY PROMPT] 无可用播放器，回退 TTS: {prompt_id}")
            return False
        item = (prompt_id, wav_path, fallback_text)
        if self.queue.full():
            try:
                dropped = self.queue.get_nowait()
                self.queue.task_done()
                print(f">>> [SAFETY PROMPT] 队列已满，丢弃旧播报: {dropped[0]}")
            except queue.Empty:
                pass
        try:
            self.queue.put_nowait(item)
            self._activate_signal_lock()
            update_runtime_status(safety_prompt_state="queued", last_safety_prompt=prompt_id, audio_output_state="safety_prompt_wait")
            return True
        except queue.Full:
            print(f">>> [SAFETY PROMPT] 队列拥堵，忽略播报: {prompt_id}")
            update_runtime_status(safety_prompt_state="queue_full", last_safety_prompt=prompt_id)
            return False

    def _run_player(self, cmd):
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f">>> [SAFETY PROMPT] 启动播放器失败: {e}")
            update_runtime_status(safety_prompt_state="player_start_failed", last_audio_error=str(e))
            return False
        try:
            proc.wait(timeout=self.play_timeout_s)
        except subprocess.TimeoutExpired:
            print(f">>> [SAFETY PROMPT] 播放超时，终止播放器: {' '.join(map(str, cmd))}")
            update_runtime_status(safety_prompt_state="timeout", last_audio_error="safety prompt timeout")
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
            return False
        return proc.returncode == 0

    def _play_with_ffplay(self, wav_path):
        return self._run_player([
            self.ffplay_path,
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "quiet",
            wav_path,
        ])

    def _play_with_aplay(self, wav_path):
        device_candidates = []
        if self.audio_device:
            device_candidates.append(self.audio_device)
        if "default" not in device_candidates:
            device_candidates.append("default")
        if "sysdefault" not in device_candidates:
            device_candidates.append("sysdefault")
        for device_name in device_candidates:
            cmd = [self.aplay_path]
            if device_name:
                cmd.extend(["-D", device_name])
            cmd.append(wav_path)
            if self._run_player(cmd):
                return True
        return False

    def play_file(self, wav_path):
        if os.name != "nt" and self.aplay_path:
            if self._play_with_aplay(wav_path):
                return True
            print(">>> [SAFETY PROMPT] aplay 播放失败，尝试回退 ffplay")
        if self.ffplay_path:
            ok = self._play_with_ffplay(wav_path)
            if ok:
                return True
            print(">>> [SAFETY PROMPT] ffplay 播放失败，尝试回退 aplay")
        if self.aplay_path:
            return self._play_with_aplay(wav_path)
        return False

    def run(self):
        while True:
            item = self.queue.get()
            if item is None:
                break
            prompt_id, wav_path, fallback_text = item
            update_runtime_status(safety_prompt_state="playing", last_safety_prompt=prompt_id, audio_output_state="safety_prompt")
            self._activate_signal_lock()
            try:
                if self.play_file(wav_path):
                    print(f">>> [SAFETY PROMPT] 播放成功: {prompt_id}")
                    update_runtime_status(safety_prompt_state="played", audio_output_state="safety_prompt")
                elif fallback_text:
                    print(f">>> [SAFETY PROMPT] 播放失败，回退 TTS: {prompt_id}")
                    update_runtime_status(safety_prompt_state="fallback_tts", audio_output_state="tts_fallback")
                    speak_text(fallback_text)
                else:
                    print(f">>> [SAFETY PROMPT] 播放失败且无回退文本: {prompt_id}")
                    update_runtime_status(safety_prompt_state="failed", audio_output_state="idle")
            except Exception as e:
                print(f">>> [SAFETY PROMPT] 播放异常 {prompt_id}: {e}")
                update_runtime_status(safety_prompt_state="exception", audio_output_state="idle", last_audio_error=str(e))
                if fallback_text:
                    speak_text(fallback_text)
            finally:
                try:
                    if self.queue.empty() and self.tail_hold_s > 0:
                        time.sleep(self.tail_hold_s)
                finally:
                    if self.queue.empty():
                        self._release_signal_lock()
                        update_runtime_status(audio_output_state="idle")
                    else:
                        self._activate_signal_lock()
                    self.queue.task_done()


safety_prompt_worker = SafetyPromptWorker(SAFETY_PROMPT_DIR)


def log_safety_prompt_assets():
    total = len(SAFETY_PROMPT_MAP)
    existing = []
    missing = []
    for prompt_id in SAFETY_PROMPT_MAP:
        wav_path = os.path.join(SAFETY_PROMPT_DIR, f"{prompt_id}.wav")
        if os.path.exists(wav_path):
            existing.append(prompt_id)
        else:
            missing.append(prompt_id)
    print(f">>> [SAFETY PROMPT] 目录: {SAFETY_PROMPT_DIR}")
    print(f">>> [SAFETY PROMPT] 素材数量: {len(existing)}/{total}")
    if missing:
        print(f">>> [SAFETY PROMPT] 缺失素材: {', '.join(missing)}")


log_safety_prompt_assets()

# 尝试导入 RPi.GPIO 用于树莓派物理按键
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
    print(">>> [硬件] 检测到 RPi.GPIO，已启用物理按键功能")
except ImportError:
    HAS_GPIO = False
    print(">>> [硬件] 未检测到 RPi.GPIO，物理按键功能被禁用 (非树莓派环境)")

# 树莓派物理按键配置 (BCM 编码)
BTN_CALL_PIN = 17 # 呼叫志愿者按键 (接 GPIO 17 和 GND)
BTN_AI_PIN = 27   # AI 对话按键 (接 GPIO 27 和 GND)

if HAS_GPIO:
    GPIO.setmode(GPIO.BCM)
    # 启用内部上拉电阻，按键按下时接地变为低电平
    GPIO.setup(BTN_CALL_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BTN_AI_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# 全局共享状态
latest_frame = None
latest_webrtc_frame = None
audio_queue = asyncio.Queue(maxsize=2) # 近实时通话队列，只保留最新少量音频帧
is_volunteer_active = False
is_ai_talking = False
volunteer_audio_energy = 0.0 # 志愿者音量能量值
volunteer_client = None

DEVICE_RUNTIME_STATUS_LOCK = threading.Lock()
DEVICE_RUNTIME_STATUS = {
    "camera_state": "init",
    "audio_output_state": "idle",
    "safety_prompt_state": "idle",
    "last_heartbeat_sent_at": 0.0,
    "last_camera_frame_at": 0.0,
    "last_audio_output_at": 0.0,
    "last_camera_error": "",
    "last_audio_error": "",
    "last_safety_prompt": "",
}


def update_runtime_status(**kwargs):
    with DEVICE_RUNTIME_STATUS_LOCK:
        for key, value in kwargs.items():
            if value is not None:
                DEVICE_RUNTIME_STATUS[key] = value



def snapshot_runtime_status():
    with DEVICE_RUNTIME_STATUS_LOCK:
        return dict(DEVICE_RUNTIME_STATUS)

def force_vp8_in_sdp(sdp):
    """修改 SDP 以优先使用 VP8 编码"""
    try:
        # 1. 找到 VP8 的 payload type
        rtpmap_matches = re.findall(r'a=rtpmap:(\d+) VP8/90000', sdp)
        if not rtpmap_matches:
            print("DEBUG: SDP 中未找到 VP8 定义")
            return sdp

        vp8_pt = rtpmap_matches[0]
        print(f"DEBUG: 找到 VP8 Payload Type: {vp8_pt}")

        # 2. 找到 m=video 行
        m_line_pattern = r'(m=video \d+ [A-Z/]+ )(.*)'
        match = re.search(m_line_pattern, sdp)

        if match:
            prefix = match.group(1)
            payloads = match.group(2).split()

            if vp8_pt in payloads:
                # 将 VP8 移到第一位
                payloads.remove(vp8_pt)
                payloads.insert(0, vp8_pt)
                new_payloads = " ".join(payloads)
                new_m_line = f"{prefix}{new_payloads}"
                sdp = sdp.replace(match.group(0), new_m_line)
                print("DEBUG: 已将 VP8 设为首选编码")

    except Exception as e:
        print(f"WARNING: 修改 SDP 失败: {e}")

    return sdp


def build_ice_candidate(candidate_info):
    if not candidate_info:
        return None

    cand_str = str(candidate_info.get('candidate') or '').strip()
    if not cand_str:
        return None

    if cand_str.startswith('candidate:'):
        cand_str = cand_str[len('candidate:'):]

    parts = cand_str.split()
    if len(parts) < 8:
        raise ValueError(f"candidate 字段格式无效: {cand_str}")

    extras = {}
    idx = 8
    while idx + 1 < len(parts):
        key = parts[idx]
        value = parts[idx + 1]
        if key == 'tcptype':
            extras['tcpType'] = value
        elif key == 'raddr':
            extras['relatedAddress'] = value
        elif key == 'rport':
            extras['relatedPort'] = int(value)
        idx += 2

    return RTCIceCandidate(
        component=int(parts[1]),
        foundation=parts[0],
        ip=parts[4],
        port=int(parts[5]),
        priority=int(parts[3]),
        protocol=parts[2].lower(),
        type=parts[7],
        sdpMid=candidate_info.get('sdpMid'),
        sdpMLineIndex=candidate_info.get('sdpMLineIndex'),
        **extras,
    )

class CustomVideoTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.rate = 12 # 超低延迟模式，进一步压低帧率减少编码与网络积压
        self.frame_count = 0

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        self.frame_count += 1

        current_frame = latest_webrtc_frame if latest_webrtc_frame is not None else latest_frame
        if current_frame is not None:
            frame = cv2.cvtColor(current_frame, cv2.COLOR_BGR2RGB)
        else:
            frame = np.zeros((108, 192, 3), dtype=np.uint8)
            frame[:, :] = (0, 0, 255) # Blue in RGB
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
        # 从队列获取音频数据 (PCM bytes)
        # 如果队列为空，这里会阻塞，aiortc 会等待
        data = await audio_queue.get()
        
        # 创建音频帧
        # ⚠️ 注意: samples 必须与 capture 时的 CHUNK 一致
        frame = AudioFrame(format='s16', layout='mono', samples=CHUNK)
        frame.planes[0].update(data)
        frame.sample_rate = 16000
        
        # 手动计算时间戳，修复 AttributeError: 'CustomAudioTrack' object has no attribute 'next_timestamp'
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, 16000)
        self.pts += CHUNK
        
        # Debug log every 50 frames (~1 second)
        if self.pts % (50 * CHUNK) == 0:
           # Calculate energy to verify mic is capturing sound
           audio_np = np.frombuffer(data, dtype=np.int16)
           energy = np.sqrt(np.mean(audio_np.astype(np.float32)**2)) if len(audio_np) > 0 else 0
           print(f"DEBUG: Sent audio frame pts={self.pts}, size={len(data)}, energy={energy:.2f}")
            
        return frame

class VolunteerClient:
    def __init__(self):
        self.sio = socketio.AsyncClient()
        self.pc = None
        self.sid = str(uuid.uuid4())
        self.connected = False
        self.accepted_event = asyncio.Event()
        self.connection_monitor_task = None
        self.disconnect_grace_task = None
        self.audio_playback_stop = None
        self.audio_playback_thread = None
        self.audio_playback_queue = None
        self.audio_playback_lock = threading.Lock()
        self.audio_playback_restart_count = 0
        self.remote_sid = None # 存储当前连接的志愿者 SID
        self.task = None
        self.setup_listeners()

    def _cancel_disconnect_grace(self):
        if self.disconnect_grace_task:
            self.disconnect_grace_task.cancel()
            self.disconnect_grace_task = None

    def _schedule_disconnect_grace(self, source: str, seconds: float = 8.0):
        if not self.pc:
            return
        if self.disconnect_grace_task and not self.disconnect_grace_task.done():
            return

        async def _grace():
            try:
                print(f">>> [Call] {source} 进入断线宽限期 {seconds:.1f}s，等待自动恢复...")
                await asyncio.sleep(seconds)
                if not self.pc:
                    return
                ice_state = getattr(self.pc, "iceConnectionState", "")
                conn_state = getattr(self.pc, "connectionState", "")
                if ice_state == "disconnected" or conn_state == "disconnected":
                    print(f">>> [Call] 宽限期后仍未恢复，执行 reset (ice={ice_state}, conn={conn_state})")
                    self.reset_call_state()
            except asyncio.CancelledError:
                pass
            finally:
                self.disconnect_grace_task = None

        self.disconnect_grace_task = asyncio.create_task(_grace())

    async def _monitor_connection(self):
        """后台监控连接状态"""
        print(">>> 启动连接状态监控...")
        while is_volunteer_active and self.pc:
            try:
                # 检查 PC 状态
                if self.pc.iceConnectionState in ['failed', 'closed']:
                    print(f"⚠️ [Monitor] 检测到 ICE 状态异常: {self.pc.iceConnectionState}")
                    self.reset_call_state()
                    break
                if self.pc.iceConnectionState == 'disconnected':
                    self._schedule_disconnect_grace("ICE")
                
                if self.pc.connectionState in ['failed', 'closed']:
                    print(f"⚠️ [Monitor] 检测到 DTLS 状态异常: {self.pc.connectionState}")
                    self.reset_call_state()
                    break
                if self.pc.connectionState == 'disconnected':
                    self._schedule_disconnect_grace("DTLS")
                    
                await asyncio.sleep(2)
            except Exception as e:
                # print(f"Monitor error: {e}")
                pass
        print(">>> 连接状态监控结束")

    async def connect_server(self):
        """程序启动时预先连接信令服务器"""
        if self.connected:
            return

        print(f">>> [预连接] 正在连接信令服务器: {SIGNALING_URL} ...")
        try:
            await self.sio.connect(SIGNALING_URL, transports=['websocket'])
            self.connected = True
            print(">>> [预连接] 信令服务器连接成功! 加入房间...")
            await self.sio.emit('join', {'role': 'user', 'room': 'stream_room'})
        except Exception as e:
            print(f"❌ [预连接] 连接失败: {e} (将在呼叫时重试)")
            self.connected = False

    async def start_call(self):
        """按下空格键时仅需发送呼叫请求"""
        # 如果未连接（如启动时连接失败），尝试重连
        if not self.connected:
            await self.connect_server()
            if not self.connected:
                print("❌ 无法连接服务器，呼叫取消")
                speak_text("无法连接服务器")
                return

        self.accepted_event.clear()
        
        # 发起呼叫
        print(">>> 正在发送呼叫请求 (call_request)...")
        print(">>> 正在等待志愿者接听 (批量轮询)...")
        speak_text("正在呼叫志愿者，请稍候")
        await self.sio.emit('call_request', {})
        
        # 等待接听，超时 90 秒 (每批30秒 * 3轮 或更多)
        try:
            await asyncio.wait_for(self.accepted_event.wait(), timeout=90.0)
            
            # 检查是否是真的接听，还是因为 no_volunteers 解除了 wait
            if not is_volunteer_active and not self.pc:
                 # 被动解除，说明失败
                 return

        except asyncio.TimeoutError:
            print(">>> ⚠️ 呼叫超时，取消呼叫。")
            speak_text("呼叫超时，请稍后再试")
            # 发送取消请求
            if self.connected:
                await self.sio.emit('cancel_request', {})
            # 超时后重置通话状态，但保持连接
            self.reset_call_state()

    def reset_call_state(self):
        """重置通话状态但保持 WebSocket 连接"""
        global is_volunteer_active
        if is_volunteer_active:
             print(">>> [Call] Connection lost or ended.")
             speak_text("通话已结束")
        
        is_volunteer_active = False
        if doubao_asr is not None and hasattr(doubao_asr, "set_call_mode"):
            try:
                doubao_asr.set_call_mode(False)
            except Exception as e:
                print(f">>> [ASR] 恢复失败: {e}")
        
        # 安全地获取并清空 pc 引用
        pc_to_close = self.pc
        self.pc = None # 立即置空防止后续事件访问
        self.remote_sid = None # 清除远程 SID
        
        # 取消监控任务
        if self.connection_monitor_task:
            self.connection_monitor_task.cancel()
            self.connection_monitor_task = None
        self._cancel_disconnect_grace()
        self._stop_audio_playback()

        if pc_to_close:
            print(">>> [Call] Closing PeerConnection...")
            asyncio.ensure_future(self.safe_close_pc(pc_to_close))

    def _start_audio_playback(self):
        self._stop_audio_playback()
        self.audio_playback_queue = queue.Queue(maxsize=24)
        self.audio_playback_stop = threading.Event()

        def _worker():
            global volunteer_audio_energy
            p = pyaudio.PyAudio()
            output_stream = None
            output_device_index_env = os.getenv("VOLUNTEER_AUDIO_OUTPUT_DEVICE_INDEX", "").strip() or os.getenv("AUDIO_OUTPUT_DEVICE_INDEX", "").strip()
            update_runtime_status(audio_output_state="volunteer_starting")
            try:
                open_kwargs = dict(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    output=True,
                )
                if output_device_index_env.isdigit():
                    open_kwargs["output_device_index"] = int(output_device_index_env)
                    print(f">>> [Volunteer Audio] 使用输出设备 index={output_device_index_env}")
                output_stream = p.open(**open_kwargs)
                update_runtime_status(audio_output_state="volunteer_active", last_audio_error="")
                while not self.audio_playback_stop.is_set():
                    try:
                        data_bytes = self.audio_playback_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    try:
                        output_stream.write(data_bytes)
                        update_runtime_status(audio_output_state="volunteer_active", last_audio_output_at=time.time())
                    except Exception as write_error:
                        self.audio_playback_restart_count += 1
                        update_runtime_status(audio_output_state="volunteer_write_error", last_audio_error=str(write_error))
                        print(f">>> [Audio Playback] output write error: {write_error}")
                        break

                    audio_np = np.frombuffer(data_bytes, dtype=np.int16)
                    if len(audio_np) > 0:
                        audio_float = audio_np.astype(np.float32)
                        rms = np.sqrt(np.mean(audio_float ** 2))
                        volunteer_audio_energy = rms / 5000.0
            except Exception as e:
                update_runtime_status(audio_output_state="volunteer_worker_error", last_audio_error=str(e))
                print(f">>> [Audio Playback] worker error: {e}")
            finally:
                volunteer_audio_energy = 0.0
                if output_stream is not None:
                    try:
                        output_stream.stop_stream()
                        output_stream.close()
                    except Exception:
                        pass
                p.terminate()
                if self.audio_playback_stop is not None:
                    self.audio_playback_stop.set()
                if self.audio_playback_queue is not None:
                    self.audio_playback_queue = queue.Queue(maxsize=24)
                update_runtime_status(audio_output_state="idle")
                print(">>> 音频播放 worker 已结束")

        self.audio_playback_thread = threading.Thread(target=_worker, daemon=True)
        self.audio_playback_thread.start()

    def _stop_audio_playback(self):
        if self.audio_playback_stop:
            self.audio_playback_stop.set()
        if self.audio_playback_thread and self.audio_playback_thread.is_alive():
            self.audio_playback_thread.join(timeout=1.0)
        self.audio_playback_thread = None
        self.audio_playback_stop = None
        self.audio_playback_queue = None

    async def safe_close_pc(self, pc):
        """安全关闭 PeerConnection"""
        if pc:
            try:
                # 尝试移除事件监听器（如果 aiortc 版本支持）
                if hasattr(pc, 'remove_all_listeners'):
                    pc.remove_all_listeners()
                await pc.close()
            except Exception as e:
                print(f"⚠️ Error closing PC: {e}")

    async def end_call(self):
        """挂断通话，但保持 SocketIO 连接以便下次呼叫"""
        print(">>> [Call] Ending call...")
        if self.connected:
            if self.remote_sid or self.pc:
                payload = {}
                if self.remote_sid:
                    payload['target'] = self.remote_sid
                await self.sio.emit('bye', payload)
            else:
                await self.sio.emit('cancel_request', {})
            
        self.reset_call_state()
        print(">>> [Call] Call ended, ready for next call.")

    def setup_listeners(self):
        @self.sio.on('connect')
        async def on_connect():
            print(">>> SocketIO Connected")
            self.connected = True

        @self.sio.on('disconnect')
        async def on_disconnect():
            print(">>> ⚠️ SocketIO Disconnected from server")
            self.connected = False
            if is_volunteer_active:
                print(">>> 检测到掉线，自动结束通话...")
                self.reset_call_state()

        @self.sio.on('connect_error')
        async def on_connect_error(data):
            print(f"❌ SocketIO Connection Error: {data}")
            self.connected = False

        @self.sio.on('no_volunteers')
        async def on_no_volunteers(data):
            print(">>> ⚠️ 所有志愿者正忙或无人接听。")
            speak_text("暂时没有志愿者接听，请稍后再试")
            self.accepted_event.set() # 解除 wait
            self.reset_call_state()

        @self.sio.on('volunteer_accepted')
        async def on_accepted(data):
            global is_volunteer_active
            print(">>> ✅ 志愿者已接单！正在建立视频通话...")
            is_volunteer_active = True
            if doubao_asr is not None and hasattr(doubao_asr, "set_call_mode"):
                try:
                    doubao_asr.set_call_mode(True)
                except Exception as e:
                    print(f">>> [ASR] 切换到通话暂停模式失败: {e}")
            self.accepted_event.set()
            await self.start_webrtc(data['volunteer_sid'])

        @self.sio.on('answer')
        async def on_answer(data):
            if self.pc:
                print(">>> 收到志愿者 Answer SDP")
                remote_desc = RTCSessionDescription(sdp=data['sdp'], type=data['type'])
                await self.pc.setRemoteDescription(remote_desc)

        @self.sio.on('candidate')
        async def on_candidate(data):
            try:
                if not self.pc:
                    return
                candidate_info = data.get('candidate')
                candidate = build_ice_candidate(candidate_info)
                if candidate is None:
                    return
                await self.pc.addIceCandidate(candidate)
                print(f">>> 添加远端 Candidate 成功: {candidate_info.get('candidate')}")
            except Exception as e:
                print(f">>> 添加远端 Candidate 失败: {e}; payload={data}")

        @self.sio.on('bye')
        async def on_bye(data):
            print(">>> 志愿者挂断")
            self.reset_call_state()

    async def start_webrtc(self, volunteer_sid):
        print(f">>> 初始化 WebRTC PeerConnection (Remote: {volunteer_sid})...")
        self.remote_sid = volunteer_sid
        config = RTCConfiguration(iceServers=ICE_SERVERS)

        self.pc = RTCPeerConnection(configuration=config)
        self._start_audio_playback()

        # 添加本地轨道 (发送)
        self.pc.addTrack(CustomVideoTrack())
        self.pc.addTrack(CustomAudioTrack())

        # 监听远程轨道 (接收音频)
        @self.pc.on("track")
        def on_track(track):
            print(f">>> 收到远程轨道: {track.kind}")
            if track.kind == "audio":
                print(">>> 🔊 收到志愿者音频流，启动播放任务...")
                asyncio.ensure_future(self.play_audio(track))

        @self.pc.on("iceconnectionstatechange")
        async def on_ice_connection_state_change():
            if not self.pc: return
            state = self.pc.iceConnectionState
            print(f">>> ❄️ ICE Connection State Changed: {state}")
            if state in ['connected', 'completed']:
                self._cancel_disconnect_grace()
            elif state in ['failed', 'closed']:
                print(f"❌ ICE Connection {state}! Resetting call state...")
                self.reset_call_state()
            elif state == 'disconnected':
                self._schedule_disconnect_grace("ICE")

        @self.pc.on("connectionstatechange")
        async def on_connection_state_change():
            if not self.pc: return
            state = self.pc.connectionState
            print(f">>> 🔗 DTLS Connection State Changed: {state}")
            if state in ['connected']:
                self._cancel_disconnect_grace()
            elif state in ['failed', 'closed']:
                print(f"❌ DTLS Connection {state}! Resetting call state...")
                self.reset_call_state()
            elif state == 'disconnected':
                self._schedule_disconnect_grace("DTLS")

        # --- 移除强制 VP8 (让 WebRTC 自动协商) ---
        # 强制指定可能导致协商失败 (Android WebRTC 对 Profile Level ID 很敏感)
        # transceiver = self.pc.addTransceiver("video", direction="sendrecv")
        # ...
        
        # 创建 Offer
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        # 启动监控
        self.connection_monitor_task = asyncio.create_task(self._monitor_connection())

        gather_wait = max(0.1, min(0.5, float(os.getenv("WEBRTC_ICE_GATHER_WAIT_SECONDS", "0.35"))))
        print(f">>> 正在收集 ICE Candidates (最多等待 {gather_wait:.2f} 秒)...")
        await asyncio.sleep(gather_wait)

        final_sdp = self.pc.localDescription.sdp
        print("\n>>> [DEBUG] 本地生成的 Candidates (SDP):")
        for line in final_sdp.splitlines():
            if line.startswith("a=candidate"):
                print(f"    {line}")
        print(">>> -------------------------------------\n")
        
        print(">>> 发送 Offer SDP...")
        await self.sio.emit('offer', {
            'target': volunteer_sid,
            'sdp': final_sdp,
            'type': self.pc.localDescription.type
        })

    async def play_audio(self, track):
        """接收远程音频并交给独立播放 worker，避免阻塞 async 热路径"""
        global volunteer_audio_energy
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)

        try:
            print(f">>> 正在接收志愿者声音 (Track State: {track.readyState})...")
            while True:
                try:
                    frame = await track.recv()
                    playback_queue = self.audio_playback_queue
                    if playback_queue is None:
                        continue

                    for resampled_frame in resampler.resample(frame):
                        data_bytes = resampled_frame.to_ndarray().tobytes()
                        while playback_queue.full():
                            try:
                                playback_queue.get_nowait()
                            except queue.Empty:
                                break
                        playback_queue.put_nowait(data_bytes)

                except MediaStreamError:
                    print(">>> 音频流已结束 (MediaStreamError)")
                    break
                except Exception as e:
                    print(f"音频流读取错误: {repr(e)}")
                    break
        except Exception as e:
            print(f"播放器错误: {e}")
        finally:
            volunteer_audio_energy = 0.0
            print(">>> 音频接收结束")

    async def close(self):
        try:
            if hasattr(self, 'task') and self.task:
                self.task.cancel()
            
            if self.pc:
                print(">>> Closing PeerConnection...")
                try:
                    # Timeout to prevent hanging
                    await asyncio.wait_for(self.pc.close(), timeout=2.0)
                except Exception as e:
                    print(f"⚠️ Error closing PeerConnection: {e}")
                self.pc = None
                
            if self.connected:
                print(">>> Disconnecting SocketIO...")
                try:
                    await asyncio.wait_for(self.sio.disconnect(), timeout=2.0)
                except Exception as e:
                    print(f"⚠️ Error disconnecting SocketIO: {e}")
                self.connected = False
                
        except Exception as e:
            print(f"⚠️ Error during cleanup: {e}")
        finally:
            # Always reset state
            global is_volunteer_active
            if is_volunteer_active:
                print(">>> [状态重置] 志愿者通话结束，恢复监控推流")
            is_volunteer_active = False
            print(">>> 资源清理完毕")

async def start_client():
    global latest_frame, is_volunteer_active, volunteer_client
    ai_only_mode = os.getenv("BLIND_AI_ONLY_MODE", "0") == "1"
    cap = None
    if ai_only_mode:
        print(">>> [AI ONLY] 已启用：跳过摄像头/视频推流，仅运行 ASR+LLM 交互")
    else:
        print(">>> 正在打开 Pi 5 MIPI-CSI 摄像头...")
        if os.name == 'nt':
            print("错误: blind_client_pi5_csi.py 仅支持树莓派 Linux / Bookworm 环境")
            return
        if Picamera2 is None:
            print("错误: 未安装 picamera2。请先在树莓派 Bookworm 上安装 python3-picamera2，并先用 libcamera-hello 验证摄像头。")
            return

        target_width = int(os.getenv("CAMERA_TARGET_WIDTH", "320"))
        target_height = int(os.getenv("CAMERA_TARGET_HEIGHT", "240"))
        target_fps = max(1, int(os.getenv("CAMERA_TARGET_FPS", "15")))
        csi_sensor = os.getenv("CSI_CAMERA_MODEL", "imx219").strip() or "imx219"
        csi_stream_role = os.getenv("CSI_STREAM_ROLE", "main").strip().lower() or "main"
        csi_frame_format = os.getenv("CSI_FRAME_FORMAT", "RGB888").strip() or "RGB888"
        csi_color_mode = os.getenv("CSI_COLOR_MODE", "bgr").strip().lower() or "bgr"
        csi_backend_label = f"picamera2:{csi_sensor}"
        cam_source = csi_backend_label
        auto_camera = False
        print(f">>> 使用 CSI 摄像头后端: {csi_backend_label}")
        print(f">>> 目标采集参数: {target_width}x{target_height} @ {target_fps}fps, role={csi_stream_role}, format={csi_frame_format}, color_mode={csi_color_mode}")

        def close_csi_camera(local_cap):
            if local_cap is None:
                return
            try:
                local_cap.stop()
            except Exception:
                pass
            try:
                local_cap.close()
            except Exception:
                pass

        def normalize_csi_frame(frame):
            if frame is None or getattr(frame, "size", 0) <= 0:
                return None
            try:
                if len(frame.shape) == 2:
                    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                channels = frame.shape[2]
                if channels == 4:
                    if csi_color_mode in ("rgba", "rgb"):
                        return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
                    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                if channels == 3:
                    if csi_color_mode == "rgb":
                        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    return frame.copy()
                return frame.copy()
            except Exception:
                try:
                    return frame.copy()
                except Exception:
                    return None

        def read_csi_frame(local_cap):
            if local_cap is None:
                return False, None
            try:
                raw = local_cap.capture_array(csi_stream_role)
            except TypeError:
                try:
                    raw = local_cap.capture_array()
                except Exception:
                    return False, None
            except Exception:
                return False, None
            frame = normalize_csi_frame(raw)
            if frame is None or getattr(frame, "size", 0) <= 0:
                return False, None
            return True, frame

        def open_csi_camera():
            try:
                local_cap = Picamera2()
                config_kwargs = {"main": {"size": (target_width, target_height), "format": csi_frame_format}, "buffer_count": 2}
                try:
                    controls = {"FrameRate": float(target_fps)}
                    local_cap.set_controls(controls)
                except Exception:
                    controls = None
                try:
                    camera_config = local_cap.create_video_configuration(**config_kwargs)
                except Exception:
                    camera_config = local_cap.create_preview_configuration(**config_kwargs)
                local_cap.configure(camera_config)
                if controls:
                    try:
                        local_cap.set_controls(controls)
                    except Exception:
                        pass
                local_cap.start()
                time.sleep(0.4)
                ok, test_frame = read_csi_frame(local_cap)
                if not ok:
                    close_csi_camera(local_cap)
                    return None
                actual_h, actual_w = test_frame.shape[:2]
                print(f">>> CSI 摄像头参数: {actual_w}x{actual_h} @ {target_fps:.2f}fps")
                return local_cap
            except Exception as e:
                print(f">>> CSI 摄像头打开失败: {e}")
                return None

        def rebuild_csi_camera():
            print(">>> 尝试重建 Pi 5 MIPI-CSI 摄像头...")
            return open_csi_camera()

        try:
            cap = open_csi_camera()
            if cap is None:
                raise RuntimeError("picamera2 open failed")
            print(">>> Pi 5 MIPI-CSI 摄像头打开成功！")
        except Exception as e:
            print(f">>> CSI 摄像头启动失败 ({e})")
            print(f"错误: 无法打开 {csi_sensor} 摄像头。请确认：1) 运行 Bookworm；2) 摄像头插在 Pi 5 的 J4；3) /boot/firmware/config.txt 已设置 dtoverlay={csi_sensor}；4) libcamera-hello 可正常出图；5) 已安装 python3-picamera2。")
            return

        print(f">>> 最终使用摄像头源: {cam_source}")

    # 树莓派无头模式，注释掉窗口创建
    # cv2.namedWindow("Local Camera (Source)", cv2.WINDOW_NORMAL)
    # cv2.resizeWindow("Local Camera (Source)", 640, 480)
    # print(">>> 视频窗口已创建 (必须保持此窗口激活才能接收按键)")

    p = None
    audio_stream = None
    enable_asr_integrated = os.getenv("ENABLE_ASR_INTEGRATED", "1") == "1"
    enable_local_audio_stream_env = os.getenv("ENABLE_LOCAL_AUDIO_STREAM")
    if enable_local_audio_stream_env is None:
        enable_local_audio_stream = True
    else:
        enable_local_audio_stream = enable_local_audio_stream_env == "1"
    if enable_local_audio_stream:
        print("正在初始化本地音频采集...")
        try:
            p = pyaudio.PyAudio()
            audio_input_index_env = os.getenv("AUDIO_INPUT_DEVICE_INDEX", "").strip()
            open_kwargs = dict(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            if audio_input_index_env.isdigit():
                open_kwargs["input_device_index"] = int(audio_input_index_env)
            audio_stream = p.open(**open_kwargs)
            print(">>> 本地音频采集初始化成功")
        except Exception as e:
            print(f"警告: 无法打开麦克风 ({e})，将继续仅视频模式")
            audio_stream = None
    else:
        print(">>> 本地音频采集已禁用 (ENABLE_LOCAL_AUDIO_STREAM=0)")

    asr_thread = None
    if enable_asr_integrated and doubao_asr is not None:
        asr_env_path = os.getenv("ASR_ENV_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.asr"))
        asr_chunk_ms = int(os.getenv("ASR_CHUNK_MS", "100"))
        asr_tts_output_device = os.getenv("ASR_TTS_OUTPUT_DEVICE", "default")
        asr_device_env = os.getenv("ASR_DEVICE_OVERRIDE", "").strip() or os.getenv("AUDIO_DEVICE", "").strip()
        asr_device_selected = asr_device_env if asr_device_env else None
        if (not asr_device_env) or (asr_device_env.lower() in {"default", "pulse"}):
            try:
                proc = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=3, check=False)
                text_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
                m = re.search(r"card\s+(\d+):[^\n]*\n\s*device\s+(\d+):", text_out, flags=re.IGNORECASE)
                if m:
                    auto_dev = f"hw:{m.group(1)},{m.group(2)}"
                    asr_device_selected = auto_dev
                    print(f">>> [ASR 自动设备]: {auto_dev}")
            except Exception as e:
                print(f">>> [ASR 自动设备失败]: {e}")
        try:
            os.environ["ASR_PUSH_CLIENT_ID"] = CLIENT_ID
            asr_push_url = os.getenv("ASR_PUSH_URL", f"http://{SERVER_IP}:{SERVER_PORT}/api/asr/update")
            os.environ["ASR_PUSH_URL"] = asr_push_url
            asr_args = argparse.Namespace(
                env=asr_env_path, url="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
                app_id=None, access_key=None, secret_key=None, resource_id=None, device=asr_device_selected,
                sample_rate=None, chunk_ms=asr_chunk_ms, audio_format=None, audio_codec=None,
                push_url=asr_push_url, push_client_id=CLIENT_ID, tts_output_device=asr_tts_output_device, tts_voice=None,
                use_volc_tts_v3=None, use_volc_tts_v1=None, volc_tts_appid=None, volc_tts_token=None,
                volc_tts_access_token=None, volc_tts_access_key=None, volc_tts_resource_id=None,
                volc_tts_cluster=None, volc_tts_voice_type=None, volc_tts_api_url=None, volc_tts_v3_api_url=None,
                use_doubao_tts=None, doubao_tts_api_key=None, doubao_tts_base_url=None, doubao_tts_model=None, doubao_tts_voice=None
            )
            print(f">>> [ASR 使用设备]: {asr_device_selected or 'default'}")
            print(f">>> [ASR 推送地址]: {asr_push_url}")
            def _run_asr_thread():
                try:
                    asyncio.run(doubao_asr.run(asr_args))
                    print(">>> [ASR 集成线程结束]")
                except Exception as e:
                    print(f">>> [ASR 集成线程异常退出]: {e}")
            asr_thread = threading.Thread(target=_run_asr_thread, daemon=True)
            asr_thread.start()
            print(">>> 已启动内置 ASR 集成任务")
            print(f">>> [ASR 推送ID]: {CLIENT_ID}")
        except Exception as e:
            print(f"警告: 内置 ASR 启动失败 ({e})")

    if ai_only_mode:
        print(">>> [AI ONLY] 主程序进入交互守护模式")
        while True:
            await asyncio.sleep(1.0)

    volunteer_client = None
    reconnect_count = 0

    print(f"正在连接推理服务器: {WS_URL} ...")
    
    while True:
        try:
            # Increase max_size to allow large messages (e.g. 10MB) and ping_interval
            async with websockets.connect(
                WS_URL, 
                max_size=10*1024*1024, 
                ping_interval=20, 
                ping_timeout=20
            ) as websocket:
                print(f">>> 推理服务连接成功! (reconnect={reconnect_count})")
                print(">>> [测试模式] 当前仅运行本地检测与视频上传。")

                stop_signal = asyncio.Event()
                ws_send_lock = asyncio.Lock()
                nav_runtime = type('NavRuntime', (), {})()
                nav_runtime.last_nav_utterance = ""
                nav_runtime.last_nav_utterance_time = 0.0
                nav_runtime.nav_active = False
                nav_runtime.nav_mode = "idle"
                nav_runtime.nav_destination = ""
                nav_runtime.nav_route_summary = ""
                nav_runtime.nav_last_instruction = ""
                nav_runtime.nav_last_instruction_time = 0.0
                nav_runtime.nav_last_route_signature = ""
                nav_runtime.nav_has_announced_route = False
                nav_runtime.nav_route_updated_at = 0.0
                nav_runtime.nav_recent_safety_until = 0.0

                def build_runtime_payload(payload_type="heartbeat"):
                    status_snapshot = snapshot_runtime_status()
                    payload = {
                        "client_id": CLIENT_ID,
                        "type": payload_type,
                        "angle": CURRENT_ANGLE,
                        "nmea": CURRENT_RAW_NMEA,
                        "camera_state": status_snapshot.get("camera_state", "unknown"),
                        "audio_output_state": status_snapshot.get("audio_output_state", "idle"),
                        "safety_prompt_state": status_snapshot.get("safety_prompt_state", "idle"),
                        "last_heartbeat_sent_at": status_snapshot.get("last_heartbeat_sent_at", 0.0),
                        "last_camera_frame_at": status_snapshot.get("last_camera_frame_at", 0.0),
                        "last_audio_output_at": status_snapshot.get("last_audio_output_at", 0.0),
                        "last_camera_error": status_snapshot.get("last_camera_error", ""),
                        "last_audio_error": status_snapshot.get("last_audio_error", ""),
                        "last_safety_prompt": status_snapshot.get("last_safety_prompt", ""),
                    }
                    gps_payload = update_global_location() or {}
                    payload.update(gps_payload)
                    if CURRENT_LAT is not None and CURRENT_LNG is not None:
                        payload["lat"] = CURRENT_LAT
                        payload["lng"] = CURRENT_LNG
                    return payload

                async def send_ws_message(payload, *, binary=False, send_context="generic"):
                    async with ws_send_lock:
                        await websocket.send(payload if binary else json.dumps(payload))
                    if not binary and isinstance(payload, dict) and payload.get("type") == "heartbeat":
                        update_runtime_status(last_heartbeat_sent_at=time.time())
                    return True

                async def heartbeat_loop():
                    print(">>> [Heartbeat] 保活任务已启动")
                    while not stop_signal.is_set():
                        await asyncio.sleep(1.0)
                        try:
                            payload = build_runtime_payload("heartbeat")
                            await send_ws_message(payload, send_context="heartbeat")
                        except websockets.exceptions.ConnectionClosed:
                            print(">>> [Heartbeat] WebSocket 已关闭")
                            stop_signal.set()
                            break
                        except Exception as e:
                            print(f">>> [Heartbeat] 发送失败: {e}")
                    print(">>> [Heartbeat] 保活任务已结束")

                # 立即发送初始位置信息
                try:
                    init_payload = build_runtime_payload("heartbeat")
                    await send_ws_message(init_payload, send_context="initial")
                    print(f">>> 发送初始位置: {CURRENT_LAT}, {CURRENT_LNG}")
                except Exception as e:
                    print(f">>> 发送初始位置失败: {e}")

                if volunteer_client is None:
                    volunteer_client = VolunteerClient()
                if not volunteer_client.connected:
                    try:
                        await volunteer_client.connect_server()
                    except Exception as e:
                        print(f">>> [Volunteer] 预连接信令失败: {e}")

                print(">>> [Volunteer Video] WebRTC 使用独立纯净视频帧源 (192x108 @ 12fps), audio_queue=2, chunk=10ms")

                async def handle_volunteer_request_message(data):
                    global volunteer_client
                    request_status = data.get("status")
                    target_client = data.get("client_id")
                    if target_client and target_client != CLIENT_ID:
                        return False
                    if volunteer_client is None:
                        volunteer_client = VolunteerClient()
                    if request_status == "pending":
                        if getattr(volunteer_client, "task", None) and not volunteer_client.task.done():
                            print(">>> [Volunteer] 已有人工求助任务在进行，忽略重复请求")
                            return True
                        volunteer_client.task = asyncio.create_task(volunteer_client.start_call())
                        print(">>> [Volunteer] 已收到人工求助控制，开始发起视频呼叫")
                        return True
                    if request_status == "cancelled":
                        if volunteer_client:
                            await volunteer_client.end_call()
                        print(">>> [Volunteer] 已收到取消人工求助控制")
                        return True
                    return False

                async def process_audio():
                    """独立音频采集任务，确保不被视频卡顿影响"""
                    global is_volunteer_active, is_ai_talking
                    loop = asyncio.get_running_loop()
                    
                    print(">>> 音频采集任务已启动")
                    while not stop_signal.is_set():
                        # Yield control
                        await asyncio.sleep(0)
                        
                        if audio_stream and audio_stream.get_read_available() >= CHUNK:
                            # audio_stream.read is blocking, run in executor
                            try:
                                data = await loop.run_in_executor(None, audio_stream.read, CHUNK, False)
                            except Exception as e:
                                print(f"音频采集错误: {e}")
                                await asyncio.sleep(0.1)
                                continue
                            
                            # 1. 如果志愿者模式开启，放入队列
                            if is_volunteer_active:
                                # 策略：如果队列满了，丢弃最旧的帧，确保低延迟
                                if audio_queue.full():
                                    try: 
                                        _ = audio_queue.get_nowait()
                                        # print("DEBUG: Audio queue full, dropped oldest frame")
                                    except: pass
                                
                                await audio_queue.put(data)
                                if not hasattr(process_audio, 'last_volunteer_audio_log_ts'):
                                    process_audio.last_volunteer_audio_log_ts = 0.0
                                now_ts = time.time()
                                if now_ts - process_audio.last_volunteer_audio_log_ts > 2.0:
                                    audio_np = np.frombuffer(data, dtype=np.int16)
                                    energy = np.sqrt(np.mean(audio_np.astype(np.float32) ** 2)) if len(audio_np) > 0 else 0.0
                                    print(f">>> [Volunteer Audio] 入队成功 size={len(data)} energy={energy:.2f} qsize={audio_queue.qsize()}")
                                    process_audio.last_volunteer_audio_log_ts = now_ts
                            
                            # 2. 发送给 AI 推理服务器 (ASR)
                            elif not is_volunteer_active:
                                try:
                                    if enable_asr_integrated:
                                        # 集成 ASR 已自行从麦克风持续采集，此处不再重复把 PCM 发到推理 WS
                                        pass
                                    elif is_ai_talking:
                                        # 兼容旧的推理服务器 PTT 语音链路
                                        await send_ws_message(data, binary=True, send_context="ptt_audio")
                                except Exception as e:
                                    print(f"音频发送失败: {e}")
                                    
                        else:
                            # 避免空转占用 CPU
                            await asyncio.sleep(0.005)

                async def process_video():
                    nonlocal cap
                    global latest_frame, latest_webrtc_frame, is_volunteer_active, volunteer_client, is_ai_talking
                    loop = asyncio.get_running_loop()
                    
                    print(">>> 视频采集任务已启动")
                    if not hasattr(process_video, 'read_fail_count'):
                        process_video.read_fail_count = 0
                    if not hasattr(process_video, 'frame_count'):
                        process_video.frame_count = 0
                    if not hasattr(process_video, 'load_level'):
                        process_video.load_level = 0
                    if not hasattr(process_video, 'nav_state'):
                        process_video.nav_state = "walk"
                    if not hasattr(process_video, 'capture_lock'):
                        process_video.capture_lock = threading.Lock()
                    if not hasattr(process_video, 'latest_raw_frame'):
                        process_video.latest_raw_frame = None
                    if not hasattr(process_video, 'latest_raw_seq'):
                        process_video.latest_raw_seq = 0
                    if not hasattr(process_video, 'last_processed_seq'):
                        process_video.last_processed_seq = -1
                    if not hasattr(process_video, 'capture_thread_started'):
                        process_video.capture_thread_started = False

                    if not process_video.capture_thread_started:
                        process_video.capture_thread_started = True

                        def capture_worker():
                            nonlocal cap, cam_source
                            local_fail_streak = 0
                            last_rebuild_attempt_ts = 0.0
                            rebuild_cooldown_s = max(0.5, float(os.getenv("CAMERA_REBUILD_COOLDOWN_SECONDS", "1.5")))
                            while not stop_signal.is_set():
                                if cap is None:
                                    time.sleep(0.05)
                                    continue
                                ok, raw_frame = read_csi_frame(cap)

                                if ok and raw_frame is not None and getattr(raw_frame, "size", 0) > 0:
                                    with process_video.capture_lock:
                                        process_video.latest_raw_frame = raw_frame
                                        process_video.latest_raw_seq += 1
                                    process_video.read_fail_count = 0
                                    local_fail_streak = 0
                                    update_runtime_status(camera_state="streaming", last_camera_frame_at=time.time(), last_camera_error="")
                                    continue

                                process_video.read_fail_count += 1
                                local_fail_streak += 1
                                if local_fail_streak <= 5:
                                    time.sleep(0.01)
                                    continue

                                now_ts = time.monotonic()
                                if (now_ts - last_rebuild_attempt_ts) < rebuild_cooldown_s:
                                    time.sleep(0.05)
                                    continue
                                last_rebuild_attempt_ts = now_ts
                                print(">>> CSI 摄像头后台线程检测到读取失败，尝试重建...")
                                update_runtime_status(camera_state="reconnecting", last_camera_error="csi camera read failed")
                                close_csi_camera(cap)

                                new_cap = rebuild_csi_camera()
                                if new_cap is not None:
                                    cap = new_cap
                                    process_video.read_fail_count = 0
                                    local_fail_streak = 0
                                    update_runtime_status(camera_state="streaming", last_camera_error="")
                                    print(">>> CSI 摄像头后台重建成功")
                                else:
                                    cap = None
                                    update_runtime_status(camera_state="disconnected", last_camera_error="csi camera rebuild failed")
                                    time.sleep(min(0.5, rebuild_cooldown_s))

                        threading.Thread(target=capture_worker, daemon=True).start()

                    while not stop_signal.is_set():
                        # Yield control to allow WebRTC/Audio tasks to run
                        await asyncio.sleep(0)
                        
                        # 检查 WebSocket 连接状态
                        # websockets 库版本差异兼容
                        ws_closed = False
                        if hasattr(websocket, 'closed'):
                            ws_closed = websocket.closed
                        elif hasattr(websocket, 'state'):
                            # State.CLOSED = 3
                            ws_closed = (websocket.state == 3)
                            
                        if ws_closed:
                            print(">>> WebSocket 连接已断开，停止视频采集...")
                            stop_signal.set()
                            break
                        
                        # 更新全局 GPS 坐标
                        gps_details = update_global_location() or {}

                        with process_video.capture_lock:
                            current_seq = process_video.latest_raw_seq
                            current_frame = process_video.latest_raw_frame

                        if current_seq == process_video.last_processed_seq or current_frame is None:
                            if process_video.read_fail_count > 20:
                                if not hasattr(process_video, 'last_camera_lost_log_ts'):
                                    process_video.last_camera_lost_log_ts = 0.0
                                now_ts = time.monotonic()
                                if now_ts - process_video.last_camera_lost_log_ts > 2.0:
                                    print(">>> 摄像头连续读取失败，保持语音链路在线并等待摄像头自动恢复...")
                                    update_runtime_status(camera_state="waiting_recover", last_camera_error="continuous read failure")
                                    process_video.last_camera_lost_log_ts = now_ts
                            await asyncio.sleep(0.005)
                            continue

                        process_video.last_processed_seq = current_seq
                        frame = current_frame.copy()
                        process_video.read_fail_count = 0

                        # WebRTC 通话单独走纯净视频帧，避免叠加 ROI/检测框并降低额外处理延迟
                        try:
                            latest_webrtc_frame = cv2.resize(current_frame, (192, 108))
                        except Exception:
                            latest_webrtc_frame = current_frame.copy()

                        if is_volunteer_active:
                            latest_frame = latest_webrtc_frame
                            await asyncio.sleep(0.01)
                            continue

                        # 保持本地处理尺寸适中，优先保证低延迟和高吞吐
                        frame = cv2.resize(frame, (480, 360))
                        frame_h, frame_w = frame.shape[:2]
                        roi_pts = build_safety_trapezoid(frame_w, frame_h, getattr(process_video, 'nav_state', 'walk'))

                        process_video.frame_count += 1

                        # ================= 本地离线 AI 识别 (YOLO) =================
                        if USE_LOCAL_AI and not is_volunteer_active:
                            if not hasattr(process_video, 'last_ai_warn_time'):
                                process_video.last_ai_warn_time = 0
                            if not hasattr(process_video, 'infer_task'):
                                process_video.infer_task = None
                            if not hasattr(process_video, 'infer_model'):
                                process_video.infer_model = None
                            if not hasattr(process_video, 'nav_state'):
                                process_video.nav_state = "walk"
                            if not hasattr(process_video, 'state_hold_until'):
                                process_video.state_hold_until = 0
                            if not hasattr(process_video, 'zebra_seen_until'):
                                process_video.zebra_seen_until = 0
                            if not hasattr(process_video, 'green_seen_until'):
                                process_video.green_seen_until = 0
                            if not hasattr(process_video, 'red_seen_until'):
                                process_video.red_seen_until = 0
                            if not hasattr(process_video, 'last_state_print'):
                                process_video.last_state_print = ""
                            if not hasattr(process_video, 'load_level'):
                                process_video.load_level = 0
                            if not hasattr(process_video, 'roi_debug_enabled'):
                                process_video.roi_debug_enabled = True
                            if not hasattr(process_video, 'last_nav_utterance'):
                                process_video.last_nav_utterance = ""
                            if not hasattr(process_video, 'last_nav_utterance_time'):
                                process_video.last_nav_utterance_time = 0.0
                            if not hasattr(process_video, 'wait_go_state'):
                                process_video.wait_go_state = "unknown"
                            if not hasattr(process_video, 'crossing_center_x'):
                                process_video.crossing_center_x = None
                            if not hasattr(process_video, 'crossing_deviation_px'):
                                process_video.crossing_deviation_px = 0
                            if not hasattr(process_video, 'crossing_guidance_cooldown_until'):
                                process_video.crossing_guidance_cooldown_until = 0.0
                            if not hasattr(process_video, 'zebra_heading_hint'):
                                process_video.zebra_heading_hint = "center"
                            if not hasattr(process_video, 'zebra_deviation_streak'):
                                process_video.zebra_deviation_streak = 0
                            if not hasattr(process_video, 'last_roi_obstacles'):
                                process_video.last_roi_obstacles = []
                            if not hasattr(process_video, 'roi_obstacle_alert_active'):
                                process_video.roi_obstacle_alert_active = False
                            if not hasattr(process_video, 'roi_obstacle_hold_until'):
                                process_video.roi_obstacle_hold_until = 0.0

                            if unified_model is not None:
                                if not hasattr(process_video, 'last_unified_results'):
                                    process_video.last_unified_results = None
                                if not hasattr(process_video, 'last_unified_frame_idx'):
                                    process_video.last_unified_frame_idx = -9999
                                if not hasattr(process_video, 'unified_interval'):
                                    process_video.unified_interval = 2
                                if not hasattr(process_video, 'unified_due'):
                                    process_video.unified_due = 0

                                if (process_video.infer_task is None or process_video.infer_task.done()) and process_video.frame_count >= process_video.unified_due:
                                    def do_inference_unified(f):
                                        return unified_model(f, verbose=False, conf=0.35, imgsz=320)[0]
                                    process_video.infer_model = "unified"
                                    process_video.infer_task = loop.run_in_executor(None, do_inference_unified, frame.copy())
                                    process_video.unified_due = process_video.frame_count + process_video.unified_interval

                                if process_video.infer_task is not None and process_video.infer_task.done():
                                    try:
                                        model_res = process_video.infer_task.result()
                                        process_video.last_unified_results = model_res
                                        process_video.last_unified_frame_idx = process_video.frame_count
                                        if model_res.boxes is not None and len(model_res.boxes) > 0:
                                            current_time = time.monotonic()
                                            roi_obstacles = []
                                            for b in model_res.boxes:
                                                cls_id = int(b.cls[0].item())
                                                if isinstance(unified_model.names, dict):
                                                    label = str(unified_model.names.get(cls_id, cls_id)).lower()
                                                else:
                                                    label = str(unified_model.names[cls_id]).lower()

                                                xyxy = b.xyxy[0].tolist()
                                                x1, y1, x2, y2 = map(int, xyxy)
                                                inside_roi, bottom_pt = bottom_center_in_roi(x1, y1, x2, y2, roi_pts)

                                                if "traffic_light_green" in label:
                                                    process_video.green_seen_until = process_video.frame_count + 45
                                                elif "traffic_light_red" in label:
                                                    process_video.red_seen_until = process_video.frame_count + 45
                                                elif "zebra_crossing" in label:
                                                    process_video.zebra_seen_until = process_video.frame_count + 65
                                                elif label in {"person", "bicycle", "motorcycle", "vehicle", "bollard", "obstacle_other"}:
                                                    roi_obstacles.append({
                                                        "label": label,
                                                        "inside": inside_roi,
                                                        "bottom_pt": bottom_pt,
                                                    })

                                            process_video.last_roi_obstacles = roi_obstacles
                                            dangerous = [x for x in roi_obstacles if x["inside"]]
                                            if dangerous:
                                                process_video.roi_obstacle_hold_until = current_time + 0.8
                                            elif current_time >= process_video.roi_obstacle_hold_until:
                                                process_video.roi_obstacle_alert_active = False
                                            if dangerous and (not process_video.roi_obstacle_alert_active):
                                                nav_runtime.nav_recent_safety_until = max(nav_runtime.nav_recent_safety_until, current_time + 1.8)
                                                danger = dangerous[0]
                                                cx = danger["bottom_pt"][0]
                                                if cx < frame_w * 0.4:
                                                    pos = "偏左"
                                                elif cx > frame_w * 0.6:
                                                    pos = "偏右"
                                                else:
                                                    pos = ""
                                                prompt_id, warn_text = normalize_obstacle_prompt(danger['label'], pos)
                                                if speak_safety_prompt(process_video, prompt_id, fallback_text=warn_text, cooldown_s=1.5, dedupe_key=f"obs:{prompt_id}", now_ts=current_time):
                                                    process_video.roi_obstacle_alert_active = True
                                                    process_video.last_ai_warn_time = current_time
                                    except Exception as e:
                                        print(f"本地 AI 推理异常: {e}")
                                    finally:
                                        process_video.infer_task = None
                                        process_video.infer_model = None

                                zebra_recent = process_video.frame_count < process_video.zebra_seen_until
                                green_recent = process_video.frame_count < process_video.green_seen_until
                                red_recent = process_video.frame_count < process_video.red_seen_until

                                prev_nav_state = process_video.nav_state
                                if process_video.nav_state == "walk":
                                    if zebra_recent:
                                        process_video.nav_state = "intersection"
                                        process_video.state_hold_until = process_video.frame_count + 90
                                elif process_video.nav_state == "intersection":
                                    if zebra_recent and green_recent:
                                        process_video.nav_state = "crossing"
                                        process_video.state_hold_until = process_video.frame_count + 140
                                    elif process_video.frame_count > process_video.state_hold_until and (not zebra_recent):
                                        process_video.nav_state = "walk"
                                else:
                                    if red_recent:
                                        process_video.nav_state = "intersection"
                                        process_video.state_hold_until = process_video.frame_count + 80
                                    elif process_video.frame_count > process_video.state_hold_until and (not zebra_recent):
                                        process_video.nav_state = "walk"

                                now_nav_ts = time.monotonic()
                                if prev_nav_state != process_video.nav_state:
                                    if process_video.nav_state == "intersection":
                                        nav_runtime.nav_recent_safety_until = max(nav_runtime.nav_recent_safety_until, now_nav_ts + 2.0)
                                        if red_recent:
                                            speak_safety_prompt(process_video, "nav_red_wait", cooldown_s=2.0, dedupe_key="nav:red_wait", now_ts=now_nav_ts)
                                            process_video.wait_go_state = "wait"
                                        else:
                                            speak_safety_prompt(process_video, "nav_intersection", cooldown_s=2.0, dedupe_key="nav:intersection", now_ts=now_nav_ts)
                                    elif process_video.nav_state == "crossing":
                                        nav_runtime.nav_recent_safety_until = max(nav_runtime.nav_recent_safety_until, now_nav_ts + 2.0)
                                        speak_safety_prompt(process_video, "nav_go", cooldown_s=2.0, dedupe_key="nav:go", now_ts=now_nav_ts)
                                        process_video.wait_go_state = "go"
                                    elif prev_nav_state == "crossing" and process_video.nav_state == "walk":
                                        speak_safety_prompt(process_video, "nav_passed", cooldown_s=2.0, dedupe_key="nav:passed", now_ts=now_nav_ts)

                                if process_video.nav_state == "intersection" and red_recent and process_video.wait_go_state != "wait":
                                    nav_runtime.nav_recent_safety_until = max(nav_runtime.nav_recent_safety_until, now_nav_ts + 2.0)
                                    speak_debounced(process_video, "红灯，请等待", cooldown_s=2.0, dedupe_key="nav:red_wait", now_ts=now_nav_ts)
                                    process_video.wait_go_state = "wait"

                                if process_video.nav_state != process_video.last_state_print:
                                    print(f">>> [NAV] 状态切换: {process_video.nav_state}")
                                    process_video.last_state_print = process_video.nav_state

                                if process_video.last_unified_results is not None and (process_video.frame_count - process_video.last_unified_frame_idx <= 10):
                                    frame = process_video.last_unified_results.plot(img=frame)
                            else:
                                if not hasattr(process_video, 'last_obs_results'):
                                    process_video.last_obs_results = None
                                if not hasattr(process_video, 'last_light_results'):
                                    process_video.last_light_results = None
                                if not hasattr(process_video, 'last_seg_results'):
                                    process_video.last_seg_results = None
                                if not hasattr(process_video, 'last_obs_frame_idx'):
                                    process_video.last_obs_frame_idx = -9999
                                if not hasattr(process_video, 'last_light_frame_idx'):
                                    process_video.last_light_frame_idx = -9999
                                if not hasattr(process_video, 'last_seg_frame_idx'):
                                    process_video.last_seg_frame_idx = -9999
                                if not hasattr(process_video, 'model_cursor'):
                                    process_video.model_cursor = 0
                                if not hasattr(process_video, 'model_order'):
                                    process_video.model_order = ["obs", "light", "seg"]
                                if not hasattr(process_video, 'model_interval'):
                                    process_video.model_interval = {"obs": 6, "light": 12, "seg": 18}
                                if not hasattr(process_video, 'model_due'):
                                    process_video.model_due = {"obs": 6, "light": 12, "seg": 18}
                                if not hasattr(process_video, 'focus_model'):
                                    process_video.focus_model = None
                                if not hasattr(process_video, 'focus_until'):
                                    process_video.focus_until = 0
                                if process_video.infer_task is None or process_video.infer_task.done():
                                    model_to_run = None
                                    if AI_SCHEDULE_MODE in {"obs", "light", "seg"}:
                                        model_to_run = AI_SCHEDULE_MODE
                                    elif AI_SCHEDULE_MODE == "dual":
                                        if process_video.nav_state == "crossing" or process_video.nav_state == "intersection":
                                            process_video.model_order = ["light", "obs"]
                                        else:
                                            process_video.model_order = ["obs", "light"]
                                        process_video.model_interval["obs"] = min(10, max(3, 3 + process_video.load_level))
                                        process_video.model_interval["light"] = min(16, max(5, 7 + process_video.load_level * 2))
                                        process_video.model_due["seg"] = process_video.frame_count + 999999
                                        if process_video.focus_model in {"obs", "light"} and process_video.frame_count < process_video.focus_until:
                                            model_to_run = process_video.focus_model
                                        if model_to_run is None:
                                            for i in range(len(process_video.model_order)):
                                                idx = (process_video.model_cursor + i) % len(process_video.model_order)
                                                name = process_video.model_order[idx]
                                                if process_video.frame_count >= process_video.model_due[name]:
                                                    model_to_run = name
                                                    process_video.model_cursor = (idx + 1) % len(process_video.model_order)
                                                    process_video.model_due[name] = process_video.frame_count + process_video.model_interval[name]
                                                    break
                                    else:
                                        process_video.model_order = ["obs", "light", "seg"]
                                        if process_video.nav_state == "walk":
                                            base_obs, base_light, base_seg = 6, 14, 18
                                        elif process_video.nav_state == "intersection":
                                            base_obs, base_light, base_seg = 8, 6, 8
                                        else:
                                            base_obs, base_light, base_seg = 10, 4, 6

                                        process_video.model_interval["obs"] = min(14, max(4, base_obs + process_video.load_level))
                                        process_video.model_interval["light"] = min(30, max(4, base_light + process_video.load_level * 2))
                                        process_video.model_interval["seg"] = min(42, max(6, base_seg + process_video.load_level * 3))

                                        if process_video.focus_model and process_video.frame_count < process_video.focus_until:
                                            model_to_run = process_video.focus_model

                                        if model_to_run is None:
                                            for i in range(len(process_video.model_order)):
                                                idx = (process_video.model_cursor + i) % len(process_video.model_order)
                                                name = process_video.model_order[idx]
                                                if process_video.frame_count >= process_video.model_due[name]:
                                                    model_to_run = name
                                                    process_video.model_cursor = (idx + 1) % len(process_video.model_order)
                                                    process_video.model_due[name] = process_video.frame_count + process_video.model_interval[name]
                                                    break

                                    if model_to_run is not None:
                                        process_video.infer_model = model_to_run
                                        if model_to_run == "obs":
                                            def do_inference_obs(f):
                                                return obstacle_model(f, verbose=False, conf=0.30)[0]
                                            process_video.infer_task = loop.run_in_executor(None, do_inference_obs, frame.copy())
                                        elif model_to_run == "light":
                                            def do_inference_light(f):
                                                return light_model(f, verbose=False, conf=0.35)[0]
                                            process_video.infer_task = loop.run_in_executor(None, do_inference_light, frame.copy())
                                        else:
                                            def do_inference_seg(f):
                                                return seg_model(f, verbose=False, conf=0.40)[0]
                                            process_video.infer_task = loop.run_in_executor(None, do_inference_seg, frame.copy())

                                if process_video.infer_task is not None and process_video.infer_task.done():
                                    try:
                                        model_res = process_video.infer_task.result()
                                        if process_video.infer_model == "obs":
                                            process_video.last_obs_results = model_res
                                            process_video.last_obs_frame_idx = process_video.frame_count
                                            detections = model_res.boxes
                                            process_video.last_roi_obstacles = []
                                            current_time = time.monotonic()
                                            if len(detections) > 0:
                                                process_video.focus_model = "obs"
                                                process_video.focus_until = process_video.frame_count + 45
                                                dangerous = []
                                                for det in detections:
                                                    cls_id = int(det.cls[0].item())
                                                    obj_name = str(obstacle_model.names[cls_id]).lower()
                                                    x1, y1, x2, y2 = map(int, det.xyxy[0].tolist())
                                                    inside_roi, bottom_pt = bottom_center_in_roi(x1, y1, x2, y2, roi_pts)
                                                    process_video.last_roi_obstacles.append({
                                                        "label": obj_name,
                                                        "inside": inside_roi,
                                                        "bottom_pt": bottom_pt,
                                                    })
                                                    if inside_roi:
                                                        dangerous.append((obj_name, bottom_pt))

                                                if dangerous:
                                                    process_video.roi_obstacle_hold_until = current_time + 0.8
                                                elif current_time >= process_video.roi_obstacle_hold_until:
                                                    process_video.roi_obstacle_alert_active = False

                                                if dangerous and (not process_video.roi_obstacle_alert_active):
                                                    obj_name, bottom_pt = dangerous[0]
                                                    cx = bottom_pt[0]
                                                    if cx < frame_w * 0.4:
                                                        pos = "偏左"
                                                    elif cx > frame_w * 0.6:
                                                        pos = "偏右"
                                                    else:
                                                        pos = ""
                                                    prompt_id, warn_text = normalize_obstacle_prompt(obj_name, pos)
                                                    if speak_safety_prompt(process_video, prompt_id, fallback_text=warn_text, cooldown_s=1.5, dedupe_key=f"obs:{prompt_id}", now_ts=current_time):
                                                        process_video.roi_obstacle_alert_active = True
                                                        process_video.last_ai_warn_time = current_time
                                            elif current_time >= process_video.roi_obstacle_hold_until:
                                                process_video.roi_obstacle_alert_active = False
                                        elif process_video.infer_model == "light":
                                            process_video.last_light_results = model_res
                                            process_video.last_light_frame_idx = process_video.frame_count
                                            if model_res.boxes is not None and len(model_res.boxes) > 0:
                                                process_video.focus_model = "light"
                                                process_video.focus_until = process_video.frame_count + 45
                                                for b in model_res.boxes:
                                                    cls_id = int(b.cls[0].item())
                                                    if isinstance(light_model.names, dict):
                                                        label = str(light_model.names.get(cls_id, cls_id)).lower()
                                                    else:
                                                        label = str(light_model.names[cls_id]).lower()
                                                    if "green" in label:
                                                        process_video.green_seen_until = process_video.frame_count + 50
                                                    if "red" in label:
                                                        process_video.red_seen_until = process_video.frame_count + 50
                                        elif process_video.infer_model == "seg":
                                            process_video.last_seg_results = model_res
                                            process_video.last_seg_frame_idx = process_video.frame_count
                                            process_video.crossing_center_x = None
                                            if model_res.masks is not None:
                                                process_video.focus_model = "seg"
                                                process_video.focus_until = process_video.frame_count + 45
                                                try:
                                                    mask_data = model_res.masks.data
                                                    if mask_data is not None and len(mask_data) > 0:
                                                        mask_np = mask_data[0].cpu().numpy()
                                                        mask_bin = (mask_np > 0.5).astype(np.uint8)
                                                        ys, xs = np.where(mask_bin > 0)
                                                        if len(xs) > 0:
                                                            bottom_band_start = int(mask_bin.shape[0] * 0.55)
                                                            bottom_band = ys >= bottom_band_start
                                                            usable_xs = xs[bottom_band] if np.any(bottom_band) else xs
                                                            if len(usable_xs) > 0:
                                                                process_video.crossing_center_x = int(np.median(usable_xs) * frame_w / mask_bin.shape[1])
                                                except Exception:
                                                    pass
                                            if model_res.boxes is not None and len(model_res.boxes) > 0:
                                                largest_zebra = None
                                                largest_area = 0
                                                for b in model_res.boxes:
                                                    cls_id = int(b.cls[0].item())
                                                    if isinstance(seg_model.names, dict):
                                                        label = str(seg_model.names.get(cls_id, cls_id)).lower()
                                                    else:
                                                        label = str(seg_model.names[cls_id]).lower()
                                                    if "zebra" in label:
                                                        process_video.zebra_seen_until = process_video.frame_count + 65
                                                        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                                                        area = max(0, x2 - x1) * max(0, y2 - y1)
                                                        if area > largest_area:
                                                            largest_area = area
                                                            largest_zebra = (x1 + x2) // 2
                                                if process_video.crossing_center_x is None and largest_zebra is not None:
                                                    process_video.crossing_center_x = largest_zebra
                                    except Exception as e:
                                        print(f"本地 AI 推理异常: {e}")
                                    finally:
                                        process_video.infer_task = None
                                        process_video.infer_model = None

                                if AI_SCHEDULE_MODE == "auto":
                                    zebra_recent = process_video.frame_count < process_video.zebra_seen_until
                                    green_recent = process_video.frame_count < process_video.green_seen_until
                                    red_recent = process_video.frame_count < process_video.red_seen_until

                                    prev_nav_state = process_video.nav_state
                                    if process_video.nav_state == "walk":
                                        if zebra_recent:
                                            process_video.nav_state = "intersection"
                                            process_video.state_hold_until = process_video.frame_count + 90
                                    elif process_video.nav_state == "intersection":
                                        if zebra_recent and green_recent:
                                            process_video.nav_state = "crossing"
                                            process_video.state_hold_until = process_video.frame_count + 140
                                            process_video.focus_model = "light"
                                            process_video.focus_until = process_video.frame_count + 120
                                        elif process_video.frame_count > process_video.state_hold_until and (not zebra_recent):
                                            process_video.nav_state = "walk"
                                    else:
                                        if red_recent:
                                            process_video.nav_state = "intersection"
                                            process_video.state_hold_until = process_video.frame_count + 80
                                        elif process_video.frame_count > process_video.state_hold_until and (not zebra_recent):
                                            process_video.nav_state = "walk"

                                    now_nav_ts = time.monotonic()
                                    if prev_nav_state != process_video.nav_state:
                                        if process_video.nav_state == "intersection":
                                            if red_recent:
                                                speak_safety_prompt(process_video, "nav_red_wait", cooldown_s=2.0, dedupe_key="nav:red_wait", now_ts=now_nav_ts)
                                                process_video.wait_go_state = "wait"
                                            else:
                                                speak_safety_prompt(process_video, "nav_intersection", cooldown_s=2.0, dedupe_key="nav:intersection", now_ts=now_nav_ts)
                                        elif process_video.nav_state == "crossing":
                                            speak_safety_prompt(process_video, "nav_go", cooldown_s=2.0, dedupe_key="nav:go", now_ts=now_nav_ts)
                                            process_video.wait_go_state = "go"
                                        elif prev_nav_state == "crossing" and process_video.nav_state == "walk":
                                            speak_safety_prompt(process_video, "nav_passed", cooldown_s=2.0, dedupe_key="nav:passed", now_ts=now_nav_ts)

                                    if process_video.nav_state == "intersection" and red_recent and process_video.wait_go_state != "wait":
                                        speak_debounced(process_video, "红灯，请等待", cooldown_s=2.0, dedupe_key="nav:red_wait", now_ts=now_nav_ts)
                                        process_video.wait_go_state = "wait"

                                    if process_video.nav_state == "crossing":
                                        frame_center_x = frame_w // 2
                                        if process_video.crossing_center_x is not None:
                                            deviation = process_video.crossing_center_x - frame_center_x
                                            process_video.crossing_deviation_px = deviation
                                            threshold = int(frame_w * 0.08)
                                            if abs(deviation) > threshold:
                                                process_video.zebra_deviation_streak += 1
                                            else:
                                                process_video.zebra_deviation_streak = 0

                                            if process_video.zebra_deviation_streak >= 2 and now_nav_ts >= process_video.crossing_guidance_cooldown_until:
                                                if deviation < 0:
                                                    process_video.zebra_heading_hint = "left"
                                                    speak_safety_prompt(process_video, "cross_left", cooldown_s=2.0, dedupe_key="cross:left", now_ts=now_nav_ts)
                                                else:
                                                    process_video.zebra_heading_hint = "right"
                                                    speak_safety_prompt(process_video, "cross_right", cooldown_s=2.0, dedupe_key="cross:right", now_ts=now_nav_ts)
                                                process_video.crossing_guidance_cooldown_until = now_nav_ts + 2.0
                                                process_video.zebra_deviation_streak = 0
                                            elif abs(deviation) <= threshold:
                                                process_video.zebra_heading_hint = "center"
                                        elif now_nav_ts >= process_video.crossing_guidance_cooldown_until:
                                            speak_safety_prompt(process_video, "cross_lost", cooldown_s=2.5, dedupe_key="cross:lost", now_ts=now_nav_ts)
                                            process_video.crossing_guidance_cooldown_until = now_nav_ts + 2.5

                                    if process_video.nav_state != process_video.last_state_print:
                                        print(f">>> [NAV] 状态切换: {process_video.nav_state}")
                                        process_video.last_state_print = process_video.nav_state

                                if process_video.last_obs_results is not None and (process_video.frame_count - process_video.last_obs_frame_idx <= 12):
                                    frame = process_video.last_obs_results.plot(img=frame)
                                if process_video.last_light_results is not None and (process_video.frame_count - process_video.last_light_frame_idx <= 15):
                                    frame = process_video.last_light_results.plot(img=frame)
                                if process_video.last_seg_results is not None and (process_video.frame_count - process_video.last_seg_frame_idx <= 20):
                                    frame = process_video.last_seg_results.plot(img=frame)

                        if getattr(process_video, 'roi_debug_enabled', False):
                            overlay = frame.copy()
                            cv2.fillPoly(overlay, [roi_pts.reshape((-1, 1, 2))], (0, 255, 0))
                            cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
                            cv2.polylines(frame, [roi_pts.reshape((-1, 1, 2))], True, (0, 255, 0), 2)
                            cv2.putText(frame, "SAFE ROI", (roi_pts[0][0], max(20, roi_pts[0][1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                            for item in getattr(process_video, 'last_roi_obstacles', []):
                                color = (0, 0, 255) if item.get('inside') else (255, 255, 0)
                                bx, by = item.get('bottom_pt', (0, 0))
                                cv2.circle(frame, (int(bx), int(by)), 4, color, -1)
                            if getattr(process_video, 'crossing_center_x', None) is not None:
                                center_x = frame_w // 2
                                zebra_x = int(process_video.crossing_center_x)
                                cv2.line(frame, (center_x, int(frame_h * 0.55)), (center_x, frame_h), (255, 255, 255), 1)
                                cv2.line(frame, (zebra_x, int(frame_h * 0.55)), (zebra_x, frame_h), (0, 255, 255), 1)
                                cv2.putText(frame, f"cross dev: {int(process_video.crossing_deviation_px)} px", (10, frame_h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
                        
                        # 更新 latest_frame 给 WebRTC (志愿者通话) 使用
                        latest_frame = frame

                        # 统一在最终带框画面上生成上传帧，保证服务器看到检测框
                        upload_frame = cv2.resize(frame, (416, 234))

                        # 逻辑修改：志愿者通话期间，完全停止向 WebSocket 发送图片数据
                        # 仅发送空心跳包以保持连接活跃（可选，或者依赖 WebSocket 自身的 ping/pong）
                        if not is_volunteer_active:
                            # 正常模式：推流给 AI/Monitor
                            if not hasattr(process_video, 'jpeg_quality'):
                                process_video.jpeg_quality = 28
                            if not hasattr(process_video, 'send_slow_streak'):
                                process_video.send_slow_streak = 0
                            if not hasattr(process_video, 'send_fast_streak'):
                                process_video.send_fast_streak = 0
                            if not hasattr(process_video, 'upload_interval'):
                                process_video.upload_interval = 0.03
                            if not hasattr(process_video, 'last_upload_ts'):
                                process_video.last_upload_ts = 0.0
                            if not hasattr(process_video, 'send_fail_count'):
                                process_video.send_fail_count = 0
                            if not hasattr(process_video, 'metric_window_start'):
                                process_video.metric_window_start = time.monotonic()
                            if not hasattr(process_video, 'metric_window_send_count'):
                                process_video.metric_window_send_count = 0
                            if not hasattr(process_video, 'metric_window_send_cost_sum'):
                                process_video.metric_window_send_cost_sum = 0.0
                            if not hasattr(process_video, 'metric_last_frame_idx'):
                                process_video.metric_last_frame_idx = process_video.frame_count
                            if not hasattr(process_video, 'upload_task'):
                                process_video.upload_task = None
                            if not hasattr(process_video, 'upload_drop_count'):
                                process_video.upload_drop_count = 0
                            if not hasattr(process_video, 'pending_upload_frame'):
                                process_video.pending_upload_frame = None
                            if not hasattr(process_video, 'pending_upload_gps'):
                                process_video.pending_upload_gps = None
                            if not hasattr(process_video, 'pending_upload_quality'):
                                process_video.pending_upload_quality = None

                            now_ts = time.monotonic()
                            async def upload_frame_async(img, gps_payload, quality):
                                def encode_frame_task(local_img, local_quality):
                                    ok, buffer = cv2.imencode('.jpg', local_img, [cv2.IMWRITE_JPEG_QUALITY, local_quality])
                                    if not ok:
                                        raise RuntimeError("JPEG 编码失败")
                                    return base64.b64encode(buffer).decode('utf-8')

                                img_base64 = await loop.run_in_executor(None, encode_frame_task, img, quality)
                                payload = build_runtime_payload("frame")
                                payload["image"] = img_base64
                                payload.update(gps_payload)

                                send_t0 = time.monotonic()
                                await send_ws_message(payload, send_context="frame")
                                return time.monotonic() - send_t0

                            if process_video.upload_task is not None and process_video.upload_task.done():
                                try:
                                    send_cost = process_video.upload_task.result()
                                    process_video.send_fail_count = 0
                                    process_video.metric_window_send_count += 1
                                    process_video.metric_window_send_cost_sum += send_cost
                                    if send_cost > 0.07:
                                        process_video.send_slow_streak += 1
                                        process_video.send_fast_streak = 0
                                    elif send_cost < 0.022:
                                        process_video.send_fast_streak += 1
                                        process_video.send_slow_streak = 0
                                    else:
                                        process_video.send_fast_streak = 0
                                        process_video.send_slow_streak = 0

                                    if process_video.send_slow_streak >= 2:
                                        process_video.jpeg_quality = max(20, process_video.jpeg_quality - 4)
                                        process_video.load_level = min(4, process_video.load_level + 1)
                                        process_video.upload_interval = min(0.12, process_video.upload_interval + 0.012)
                                        process_video.send_slow_streak = 0
                                    elif process_video.send_fast_streak >= 16:
                                        process_video.jpeg_quality = min(38, process_video.jpeg_quality + 2)
                                        process_video.load_level = max(0, process_video.load_level - 1)
                                        process_video.upload_interval = max(0.03, process_video.upload_interval - 0.002)
                                        process_video.send_fast_streak = 0
                                except Exception as e:
                                    process_video.send_fail_count += 1
                                    print(f">>> [NET] 上传失败({process_video.send_fail_count}): {e}")
                                    if process_video.send_fail_count >= 8:
                                        stop_signal.set()
                                        break
                                finally:
                                    process_video.upload_task = None

                            if process_video.upload_task is None and process_video.pending_upload_frame is not None:
                                pending_frame = process_video.pending_upload_frame
                                pending_gps = process_video.pending_upload_gps or {}
                                pending_quality = process_video.pending_upload_quality or process_video.jpeg_quality
                                process_video.pending_upload_frame = None
                                process_video.pending_upload_gps = None
                                process_video.pending_upload_quality = None
                                process_video.upload_task = asyncio.create_task(
                                    upload_frame_async(pending_frame, pending_gps, pending_quality)
                                )

                            if now_ts - process_video.last_upload_ts >= process_video.upload_interval:
                                process_video.last_upload_ts = now_ts
                                process_video.pending_upload_frame = upload_frame
                                process_video.pending_upload_gps = dict(gps_details)
                                process_video.pending_upload_quality = process_video.jpeg_quality
                                if process_video.upload_task is None:
                                    pending_frame = process_video.pending_upload_frame
                                    pending_gps = process_video.pending_upload_gps
                                    pending_quality = process_video.pending_upload_quality
                                    process_video.pending_upload_frame = None
                                    process_video.pending_upload_gps = None
                                    process_video.pending_upload_quality = None
                                    process_video.upload_task = asyncio.create_task(
                                        upload_frame_async(pending_frame, pending_gps, pending_quality)
                                    )
                                else:
                                    process_video.upload_drop_count += 1

                            now_metric = time.monotonic()
                            metric_elapsed = now_metric - process_video.metric_window_start
                            if metric_elapsed >= 5.0:
                                frame_delta = process_video.frame_count - process_video.metric_last_frame_idx
                                fps = frame_delta / metric_elapsed if metric_elapsed > 0 else 0.0
                                upload_fps = process_video.metric_window_send_count / metric_elapsed if metric_elapsed > 0 else 0.0
                                avg_send_ms = (process_video.metric_window_send_cost_sum / process_video.metric_window_send_count * 1000.0) if process_video.metric_window_send_count > 0 else 0.0
                                print(f">>> [METRIC] state={process_video.nav_state} fps={fps:.2f} upload_fps={upload_fps:.2f} avg_send_ms={avg_send_ms:.1f} quality={process_video.jpeg_quality} interval={process_video.upload_interval:.3f} drop={process_video.upload_drop_count} fail={process_video.send_fail_count} reconnect={reconnect_count}")
                                process_video.metric_window_start = now_metric
                                process_video.metric_window_send_count = 0
                                process_video.metric_window_send_cost_sum = 0.0
                                process_video.metric_last_frame_idx = process_video.frame_count
                                process_video.upload_drop_count = 0
                        else:
                            # 志愿者模式下由独立 heartbeat_loop 保活，这里不再依赖时窗判断
                            await asyncio.sleep(0.01)

                        pass
                            # if not getattr(process_video, 'auto_called', False):
                            #     if not hasattr(process_video, 'start_time'):
                            #         process_video.start_time = time.time()
                            #     
                            #     if time.time() - process_video.start_time > 15.0: # 等待 15 秒让客户端连上
                            #         print(">>> [自动测试] 15秒已到，自动触发呼叫志愿者测试...")
                            #         process_video.auto_called = True
                            #         if not is_volunteer_active:
                            #             if volunteer_client is None:
                            #                 volunteer_client = VolunteerClient()
                            #                 if not volunteer_client.connected:
                            #                     await volunteer_client.connect_server()
                            #             volunteer_client.task = asyncio.create_task(volunteer_client.start_call())
                            #             is_volunteer_active = True

                        # # OpenCV 窗口必须获得焦点才能接收按键
                        # key = cv2.waitKey(1) & 0xFF
                        # if key == ord('q'):
                        #     stop_signal.set()
                        #     break
                        # elif key == 32: # SPACE
                        #     if not is_volunteer_active:
                        #         print(">>> [用户] 请求呼叫志愿者...")
                        #         # 调用志愿者连接
                        #         if volunteer_client is None:
                        #             volunteer_client = VolunteerClient()
                        #             # 确保连接
                        #             if not volunteer_client.connected:
                        #                 await volunteer_client.connect_server()
                        #                 
                        #         volunteer_client.task = asyncio.create_task(volunteer_client.start_call())
                        #         is_volunteer_active = True
                        #     else:
                        #         print(">>> [用户] 挂断通话...")
                        #         # 挂断逻辑
                        #         if volunteer_client:
                        #             await volunteer_client.end_call()
                        #         is_volunteer_active = False
                        # 
                        # # AI 语音控制 (按住 T 键说话)
                        # if key == ord('t'):
                        #     if not is_ai_talking:
                        #         print(">>> 开始 ASR 录音 (Talking...)")
                        #         is_ai_talking = True
                        #     process_video.last_key_time = time.time()
                        # else:
                        #     # 如果超过 0.2 秒没有检测到 T 键，则认为已松开
                        #     if is_ai_talking and (time.time() - getattr(process_video, 'last_key_time', 0) > 0.2):
                        #         print(">>> ASR 录音结束 (Stopped)")
                        #         is_ai_talking = False

                        # 3. 实时显示 (本地预览) - 树莓派环境注释掉
                        # if latest_frame is not None:
                        #     display_frame = latest_frame.copy()
                        #     # 叠加状态文字
                        #     status_text = "Status: Online"
                        #     color = (0, 255, 0)
                        #     if is_volunteer_active:
                        #         status_text = "Status: Call Active"
                        #         color = (0, 0, 255)
                        #     elif is_ai_talking:
                        #         status_text = "Status: AI Talking"
                        #         color = (255, 0, 0)
                        #     
                        #     cv2.putText(display_frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                        #     
                        #     # 叠加位置信息
                        #     geo_text = f"Loc: {CURRENT_LAT:.4f}, {CURRENT_LNG:.4f}"
                        #     cv2.putText(display_frame, geo_text, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
                        # 
                        #     cv2.imshow('Local Camera (Source)', display_frame)
                        
                        # 控制帧率 (约 15 FPS)
                        await asyncio.sleep(0.066)

                async def process_response():
                    """接收 AI 回复任务"""
                    print(">>> 接收任务已启动 (监听 AI 回复)")
                    while not stop_signal.is_set():
                        # Yield control
                        await asyncio.sleep(0)
                        
                        try:
                            # Use timeout to allow checking stop_signal regularly
                            # But wait_for on receive() is okay
                            message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                            
                            # 解析 JSON 消息
                            try:
                                data = json.loads(message)
                                msg_type = data.get("type")
                                
                                if msg_type == "ai_terminal_output":
                                    text = data.get("text", "")
                                    print(f"\r{text}") # Print to terminal

                                    # 如果是 AI 回复，尝试朗读
                                    if text.startswith("AI: "):
                                        ai_content = text[4:]
                                        print(f">>> [TTS] Queuing: {ai_content[:20]}...")
                                        speak_text(ai_content)
                                elif msg_type == "volunteer_request":
                                    handled = await handle_volunteer_request_message(data)
                                    if handled:
                                        continue
                                elif msg_type == "device_control" and data.get("action") == "set_volume":
                                    ok, detail = apply_device_volume_control(data.get("mode"), data.get("value"))
                                    if ok:
                                        print(f">>> [VOLUME] 已应用音量控制: {data.get('mode')} {data.get('value')}")
                                    else:
                                        print(f">>> [VOLUME] 音量控制失败: {detail}")
                                elif handle_navigation_message(nav_runtime, data):
                                    pass
                            except json.JSONDecodeError:
                                pass # Ignore non-json
                                
                        except asyncio.TimeoutError:
                            continue
                        except websockets.exceptions.ConnectionClosed:
                            print(">>> WebSocket 连接关闭 (接收端)")
                            stop_signal.set()
                            break
                        except Exception as e:
                            # print(f"接收错误: {e}")
                            pass

                # 并发运行
                # Create tasks explicitly to manage them
                t1 = asyncio.create_task(process_video())
                t_hb = asyncio.create_task(heartbeat_loop())
                enable_blind_reply_listener_env = os.getenv("ENABLE_BLIND_REPLY_LISTENER")
                if enable_blind_reply_listener_env is None:
                    enable_blind_reply_listener = not enable_asr_integrated
                else:
                    enable_blind_reply_listener = (enable_blind_reply_listener_env == "1")
                t2 = asyncio.create_task(process_response())
                if enable_blind_reply_listener:
                    print(">>> 接收任务已启用 (AI回复/导航/人工求助控制)")
                else:
                    print(">>> 接收任务以控制模式运行 (ASR 模块负责播报，仍接收导航/人工求助控制)")
                t3 = None
                if audio_stream is not None:
                    t3 = asyncio.create_task(process_audio())
                
                # Wait for stop signal
                await stop_signal.wait()
                
                # Cancel tasks
                t1.cancel()
                t_hb.cancel()
                if t2 is not None:
                    t2.cancel()
                if t3 is not None:
                    t3.cancel()
                try:
                    await t1
                except asyncio.CancelledError:
                    pass
                try:
                    await t_hb
                except asyncio.CancelledError:
                    pass
                if t2 is not None:
                    try:
                        await t2
                    except asyncio.CancelledError:
                        pass
                if t3 is not None:
                    try:
                        await t3
                    except asyncio.CancelledError:
                        pass
                
        except Exception as e:
            reconnect_count += 1
            print(f"连接断开: {e} - 2秒后重试...")
            await asyncio.sleep(2)
        finally:
            cv2.destroyAllWindows()
            close_csi_camera(cap)
            if audio_stream:
                audio_stream.stop_stream()
                audio_stream.close()
            if p:
                p.terminate()

def speak_text(text):
    """本地 TTS 朗读 (调用 EdgeTTS Worker)"""
    if not text:
        return
    if tts_worker is None:
        return
    update_runtime_status(audio_output_state="tts", last_safety_prompt=text[:48])
    # 将文本放入队列，由独立线程异步处理
    tts_worker.queue.put(text)


def normalize_obstacle_prompt(label, pos_text):
    normalized_pos = SAFETY_POSITION_PROMPT_PREFIX.get(pos_text or "", "center")
    if label == 'person':
        object_key = 'person'
    elif label in {'vehicle', 'motorcycle', 'bicycle'}:
        object_key = 'vehicle'
    elif label == 'bollard':
        object_key = 'bollard'
    else:
        object_key = 'obstacle'
    prompt_id = f"obs_{normalized_pos}_{object_key}"
    fallback_text = SAFETY_PROMPT_MAP.get(prompt_id, "前方有障碍物")
    return prompt_id, fallback_text


def _get_volume_control_candidates():
    raw = os.getenv("AUDIO_VOLUME_CONTROLS", "Master,Speaker,PCM,Headphone")
    return [item.strip() for item in raw.split(",") if item.strip()]


def apply_device_volume_control(mode, value=None):
    amixer_path = shutil.which("amixer")
    if not amixer_path:
        return False, "未找到 amixer"

    try:
        value_num = None if value is None else int(value)
    except Exception:
        value_num = None

    last_error = ""
    for control_name in _get_volume_control_candidates():
        if mode == "mute":
            cmd = [amixer_path, "-q", "sset", control_name, "mute"]
        elif mode == "unmute":
            cmd = [amixer_path, "-q", "sset", control_name, "unmute"]
        elif mode == "absolute":
            volume = max(0, min(100, value_num if value_num is not None else 50))
            cmd = [amixer_path, "-q", "sset", control_name, f"{volume}%"]
            if volume > 0:
                cmd.append("unmute")
        elif mode == "relative":
            delta = value_num if value_num is not None else 10
            delta = max(-100, min(100, delta))
            if delta == 0:
                delta = 10
            suffix = f"{abs(delta)}%+" if delta > 0 else f"{abs(delta)}%-"
            cmd = [amixer_path, "-q", "sset", control_name, suffix]
            if delta > 0:
                cmd.append("unmute")
        else:
            return False, f"未知音量模式: {mode}"

        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            print(f">>> [VOLUME] 控制成功: control={control_name}, mode={mode}, value={value_num}")
            return True, control_name
        err = (proc.stderr or proc.stdout or "").strip()
        if err:
            last_error = err

    return False, last_error or "没有可用的音量控制通道"


def play_prerecorded_prompt(prompt_id, fallback_text=""):
    if not prompt_id:
        return False
    if safety_prompt_worker is None:
        return False
    return safety_prompt_worker.enqueue_prompt(prompt_id, fallback_text=fallback_text)


def speak_safety_prompt(state_holder, prompt_id, fallback_text=None, cooldown_s=1.5, dedupe_key=None, now_ts=None):
    if not prompt_id:
        return False
    if fallback_text is None:
        fallback_text = SAFETY_PROMPT_MAP.get(prompt_id, "")
    if not fallback_text:
        fallback_text = prompt_id
    if now_ts is None:
        now_ts = time.monotonic()
    if dedupe_key is None:
        dedupe_key = f"safety:{prompt_id}"

    last_key = getattr(state_holder, 'last_nav_utterance', '')
    last_time = getattr(state_holder, 'last_nav_utterance_time', 0.0)
    if last_key == dedupe_key and (now_ts - last_time) < cooldown_s:
        return False

    played = play_prerecorded_prompt(prompt_id, fallback_text=fallback_text)
    if not played:
        print(f">>> [SAFETY PROMPT] 回退 TTS: {prompt_id} -> {fallback_text}")
        speak_text(fallback_text)

    state_holder.last_nav_utterance = dedupe_key
    state_holder.last_nav_utterance_time = now_ts
    return True


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

    pts = np.array([
        [cx - top_half, top_y],
        [cx + top_half, top_y],
        [cx + bottom_half, bottom_y],
        [cx - bottom_half, bottom_y],
    ], dtype=np.int32)
    return pts


def bottom_center_in_roi(x1, y1, x2, y2, roi_pts):
    point = ((int(x1) + int(x2)) // 2, int(y2))
    return cv2.pointPolygonTest(roi_pts, point, False) >= 0, point


def speak_debounced(state_holder, text, cooldown_s=1.5, dedupe_key=None, now_ts=None):
    if not text:
        return False
    if now_ts is None:
        now_ts = time.monotonic()
    if dedupe_key is None:
        dedupe_key = text

    last_key = getattr(state_holder, 'last_nav_utterance', '')
    last_time = getattr(state_holder, 'last_nav_utterance_time', 0.0)
    if last_key == dedupe_key and (now_ts - last_time) < cooldown_s:
        return False

    speak_text(text)
    state_holder.last_nav_utterance = dedupe_key
    state_holder.last_nav_utterance_time = now_ts
    return True


def summarize_route(route):
    if not route:
        return ""
    if isinstance(route, dict):
        distance = route.get("distance")
        destination = route.get("destination_name") or route.get("destination") or ""
        steps = route.get("steps") or []
        parts = []
        if destination:
            parts.append(f"前往{destination}")
        if distance not in (None, ""):
            parts.append(f"约{distance}米")
        if isinstance(steps, list) and steps:
            parts.append(f"共{len(steps)}步")
        return "，".join(parts)
    if isinstance(route, list):
        return f"路线点数 {len(route)}"
    return str(route)


def extract_nav_instruction_text(data):
    if not isinstance(data, dict):
        return ""
    for key in ("text", "message", "instruction", "reply", "content"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def handle_route_update(nav_runtime, data):
    route = data.get("route")
    nav_runtime.nav_route_updated_at = time.time()
    if not route:
        if nav_runtime.nav_active:
            print(">>> [NAV] 路线已清空，导航结束")
        nav_runtime.nav_active = False
        nav_runtime.nav_mode = "idle"
        nav_runtime.nav_route_summary = ""
        nav_runtime.nav_destination = ""
        nav_runtime.nav_last_route_signature = ""
        nav_runtime.nav_has_announced_route = False
        return

    nav_runtime.nav_active = True
    nav_runtime.nav_mode = str(data.get("mode") or data.get("nav_status") or "macro").lower()
    route_payload = route if isinstance(route, dict) else {
        "route": route,
        "destination_name": data.get("destination_name") or "",
        "distance": data.get("distance_remaining"),
        "steps": data.get("steps") or [],
    }
    route_summary = summarize_route(route_payload)
    route_signature = json.dumps(route_payload, ensure_ascii=False, sort_keys=True) if isinstance(route_payload, dict) else str(route_payload)
    destination = str(route_payload.get("destination_name") or data.get("destination_name") or "").strip()
    nav_runtime.nav_destination = destination or nav_runtime.nav_destination
    nav_runtime.nav_route_summary = route_summary

    if route_signature != nav_runtime.nav_last_route_signature:
        nav_runtime.nav_last_route_signature = route_signature
        nav_runtime.nav_has_announced_route = False
        print(f">>> [NAV] 收到路线更新: {route_summary or '已生成路线'}")


def handle_navigation_message(nav_runtime, data):
    msg_type = data.get("type")
    now_ts = time.monotonic()
    if msg_type == "route_update":
        handle_route_update(nav_runtime, data)
        if nav_runtime.nav_active and not nav_runtime.nav_has_announced_route:
            route_text = nav_runtime.nav_destination or nav_runtime.nav_route_summary
            if route_text:
                spoken = f"已开始导航，前往{nav_runtime.nav_destination}" if nav_runtime.nav_destination else "路线已更新，开始为你导航"
                if speak_debounced(nav_runtime, spoken, cooldown_s=3.0, dedupe_key=f"route:{nav_runtime.nav_last_route_signature}", now_ts=now_ts):
                    nav_runtime.nav_has_announced_route = True
        return True

    if msg_type not in {"navigation_instruction", "assistant_reply"}:
        return False

    text = extract_nav_instruction_text(data)
    if not text:
        return True

    priority = "nav"
    if any(token in text for token in ["到达", "开始导航", "停止导航", "已停止导航", "路线已更新", "重新规划"]):
        priority = "important"
    elif msg_type == "assistant_reply":
        priority = "assist"

    if priority == "assist" and now_ts < getattr(nav_runtime, 'nav_recent_safety_until', 0.0):
        print(f">>> [NAV] 安全播报优先，暂缓辅助提示: {text}")
        return True

    nav_runtime.nav_active = nav_runtime.nav_active or (msg_type in {"navigation_instruction", "assistant_reply"})
    nav_runtime.nav_last_instruction = text
    nav_runtime.nav_last_instruction_time = now_ts
    if priority == "important":
        nav_runtime.nav_recent_safety_until = max(nav_runtime.nav_recent_safety_until, now_ts + 0.5)

    dedupe_key = f"{msg_type}:{text}"
    cooldown = 2.0 if priority != "assist" else 3.0
    if speak_debounced(nav_runtime, text, cooldown_s=cooldown, dedupe_key=dedupe_key, now_ts=now_ts):
        print(f">>> [NAV] {msg_type}: {text}")
    return True


if __name__ == "__main__":
    import sys
    try:
        # Run the async loop
        # Windows selector event loop policy fix for Python 3.8+
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            
        asyncio.run(start_client())
    except KeyboardInterrupt:
        print("\n>>> 程序已停止 (用户中断)")
