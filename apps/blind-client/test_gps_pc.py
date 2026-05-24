import asyncio
import websockets
import json
import time
import os
import uuid
import threading
import pynmea2
import serial
import math

# --- 坐标转换 (WGS84 -> GCJ02 高德地图) ---
def wgs84_to_gcj02(lng, lat):
    """
    WGS84转GCJ02(火星坐标系)
    :param lng: WGS84坐标系的经度
    :param lat: WGS84坐标系的纬度
    :return: 转换后的GCJ02经纬度 (lng, lat)
    """
    pi = 3.1415926535897932384626
    a = 6378245.0  # 长半轴
    ee = 0.00669342162296594323  # 偏心率平方

    def _transformlat(lng, lat):
        ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
        ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lat * pi) + 40.0 * math.sin(lat / 3.0 * pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(lat / 12.0 * pi) + 320 * math.sin(lat * pi / 30.0)) * 2.0 / 3.0
        return ret

    def _transformlng(lng, lat):
        ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
        ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lng * pi) + 40.0 * math.sin(lng / 3.0 * pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(lng / 12.0 * pi) + 300.0 * math.sin(lng / 30.0 * pi)) * 2.0 / 3.0
        return ret

    # 判断是否在国内
    def out_of_china(lng, lat):
        return not (lng > 73.66 and lng < 135.05 and lat > 3.86 and lat < 53.55)

    if out_of_china(lng, lat):
        return lng, lat
        
    dlat = _transformlat(lng - 105.0, lat - 35.0)
    dlng = _transformlng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi)
    dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * pi)
    mglat = lat + dlat
    mglng = lng + dlng
    return mglng, mglat

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

# 全局 GPS 坐标 (默认坐标作为后备)
CURRENT_LAT = 31.307116
CURRENT_LNG = 120.606170
CURRENT_ANGLE = 0.0
CURRENT_RAW_NMEA = ""
GPS_ACTIVE = False

# ================= 硬件 GPS 读取模块 (Windows COM 口版) =================
class LC76G_Provider:
    """LC76G GPS 模块串行读取器 (Windows)"""
    def __init__(self, port="COM13", baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.serial_conn = None
        self.running = False
        self.thread = None
        
    def start(self):
        try:
            self.serial_conn = serial.Serial(self.port, self.baudrate, timeout=1)
            self.running = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()
            print(f">>> [GPS] LC76G 模块已在 {self.port} 启动")
        except Exception as e:
            print(f"❌ [GPS] 无法打开串口 {self.port}: {e}")
            
    def _read_loop(self):
        global CURRENT_LAT, CURRENT_LNG, CURRENT_ANGLE, CURRENT_RAW_NMEA, GPS_ACTIVE
        print(">>> [GPS] 开始监听 NMEA 数据...")
        while self.running:
            try:
                if self.serial_conn.in_waiting:
                    line = self.serial_conn.readline().decode('ascii', errors='replace').strip()
                    if line.startswith('$'):
                        CURRENT_RAW_NMEA = line
                        
                        # 解析 GNRMC 或 GPRMC 获取经纬度
                        if "RMC" in line:
                            try:
                                msg = pynmea2.parse(line)
                                if msg.status == 'A': # A = Active/Valid
                                    # 原始 WGS84 坐标
                                    raw_lat = msg.latitude
                                    raw_lng = msg.longitude
                                    
                                    # 转换为 GCJ02 (高德地图) 坐标
                                    gcj_lng, gcj_lat = wgs84_to_gcj02(raw_lng, raw_lat)
                                    
                                    CURRENT_LAT = gcj_lat
                                    CURRENT_LNG = gcj_lng
                                    
                                    if msg.true_course:
                                        CURRENT_ANGLE = float(msg.true_course)
                                    GPS_ACTIVE = True
                                    print(f"[硬件 GPS 更新] WGS84: {raw_lat:.6f}, {raw_lng:.6f} -> 高德 GCJ02: {CURRENT_LAT:.6f}, {CURRENT_LNG:.6f}")
                                else:
                                    # V = Void/Invalid (比如在室内)
                                    print(f"[硬件 GPS 无信号] 保持最后已知位置。原始数据: {line}")
                            except pynmea2.ParseError:
                                pass
            except Exception as e:
                print(f"[GPS] 读取错误: {e}")
                time.sleep(1)

# 初始化硬件 GPS (Windows COM13)
gps_provider = LC76G_Provider(port="COM13", baudrate=115200)
gps_provider.start()

async def send_gps_loop():
    print(f"正在连接服务器: {WS_URL} ...")
    
    while True:
        try:
            async with websockets.connect(WS_URL) as websocket:
                print(">>> 服务器连接成功! 开始发送 GPS 心跳包...")
                
                while True:
                    # 构造与 blind_client.py 相同的 payload 结构 (不包含图片)
                    # 当没有图片时，服务器能识别 type="heartbeat" 并更新位置
                    payload = {
                        "type": "heartbeat",
                        "lat": CURRENT_LAT,
                        "lng": CURRENT_LNG,
                        "angle": CURRENT_ANGLE,
                        "nmea": CURRENT_RAW_NMEA
                    }
                    
                    await websocket.send(json.dumps(payload))
                    status = "真实硬件" if GPS_ACTIVE else ("室内无信号(发默认)" if CURRENT_RAW_NMEA else "默认")
                    print(f">>> 发送位置 [{status}]: {CURRENT_LAT:.6f}, {CURRENT_LNG:.6f} | NMEA: {CURRENT_RAW_NMEA[:20]}...")
                    
                    # 每 2 秒发送一次
                    await asyncio.sleep(2)
                    
        except Exception as e:
            print(f"❌ 连接断开或错误: {e}")
            print(">>> 3秒后尝试重连...")
            await asyncio.sleep(3)

if __name__ == "__main__":
    print("==================================================")
    print("      GPS 定位发送测试脚本 (电脑端硬件版 COM13)")
    print("==================================================")
    try:
        asyncio.run(send_gps_loop())
    except KeyboardInterrupt:
        print("\n>>> 用户停止测试")
