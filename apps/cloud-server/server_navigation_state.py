import time
import math
import numpy as np
from typing import Dict, Optional, Tuple
from blind_travel_doubao_api import DoubaoVisionNavigator
from navigation_service import nav_service

class NavigationSession:
    def __init__(self, user_id: str, destination: str):
        self.user_id = user_id
        self.destination_name = destination
        self.destination_coords = None # (lng, lat)
        self.destination_voice_prompt = ""
        self.destination_scene_hint = ""
        self.destination_source = "gaode_poi"
        self.status = "INIT" # INIT -> MACRO -> MICRO -> ARRIVED
        self.last_update_time = time.time()
        self.created_at = time.time()
        self.route_generated_at = 0.0
        self.last_mode_switch_at = 0.0
        self.last_macro_message = ""
        self.last_obstacle_warning = ""
        self.last_visual_frame_at = 0.0
        self.last_visual_analysis_at = 0.0
        self.last_visual_instruction = ""
        self.vision_navigator = DoubaoVisionNavigator()
        self.route_polyline = [] # [(lng, lat), ...]
        self.route_steps = []
        self.current_route_step_index = 0
        self.last_route_instruction = ""
        self.warned_obstacles = set() # 记录已预警过的障碍物ID，防止重复播报
        
        # 尝试通过高德搜索目的地坐标
        poi = nav_service.search_place(destination)
        if poi:
            self.destination_coords = tuple(map(float, poi["location"].split(",")))
            self.destination_name = poi["name"] # 更新为标准名称
            self.status = "MACRO"
            print(f">>> [Nav Session] Created for {user_id} -> {self.destination_name} @ {self.destination_coords}")
        else:
            print(f">>> [Nav Session] Failed to find POI for {destination}")
            self.status = "ERROR_POI_NOT_FOUND"

    def _build_micro_entry_message(self) -> str:
        guidance = str(self.destination_voice_prompt or "").strip()
        if guidance:
            return f"即将到达 {self.destination_name} 附近。{guidance}"
        scene_hint = str(self.destination_scene_hint or "").strip()
        if scene_hint:
            return f"即将到达 {self.destination_name} 附近，{scene_hint}，请举起手机摄像头。"
        return f"即将到达 {self.destination_name} 附近，已为您开启视觉搜索模式，请举起手机摄像头。"

    def update_gps(self, lng: float, lat: float, obstacles_data: list = None) -> Dict:
        """
        处理 GPS 更新，返回导航指令
        """
        if self.status in ["INIT", "ERROR_POI_NOT_FOUND", "ARRIVED"]:
            return {"action": "idle", "message": ""}
            
        self.last_update_time = time.time()
        response = {"distance": 0.0, "mode": self.status}
        
        # 0. 如果刚启动 MACRO，请求路线
        if self.status == "MACRO" and not self.route_polyline:
            origin_str = f"{lng},{lat}"
            dest_str = f"{self.destination_coords[0]},{self.destination_coords[1]}"
            route_detail = nav_service.plan_walking_route_detail(origin_str, dest_str)
            if route_detail and route_detail.get("polyline_coords"):
                self.route_polyline = route_detail["polyline_coords"]
                self.route_steps = route_detail.get("steps", [])
                self.current_route_step_index = 0
                self.route_generated_at = time.time()
                print(f">>> [Nav Session] Route planned, {len(self.route_polyline)} points.")
                response["new_route"] = self.route_polyline # 通知外部有一条新路线产生
        
        # 1. 计算剩余距离
        dist = self._calc_distance((lng, lat), self.destination_coords)
        
        # 2. 状态机切换
        response["distance"] = dist
        response["mode"] = self.status
        
        if self.status == "MACRO":
            if dist < 50.0: # 50米内切换
                self.status = "MICRO"
                self.last_mode_switch_at = time.time()
                response["action"] = "switch_mode"
                response["message"] = self._build_micro_entry_message()
                response["mode"] = "MICRO"
                self.last_macro_message = response["message"]
            else:
                response["action"] = "continue"
                msg = ""
                # --- 融合志愿者标注障碍物预警逻辑 ---
                if obstacles_data and self.route_polyline:
                    # 查找路线上前方 20 米内的障碍物
                    nearby_obstacle = self._check_obstacles_ahead(lng, lat, obstacles_data)
                    if nearby_obstacle:
                        obs_id = nearby_obstacle.get("id")
                        if obs_id not in self.warned_obstacles:
                            obs_name = nearby_obstacle.get("title", "障碍物")
                            obs_desc = nearby_obstacle.get("description", "")
                            msg = f"注意，高德路线前方大约15米处有志愿者标注的：{obs_name}。{obs_desc}。请使用盲杖谨慎探路。"
                            self.warned_obstacles.add(obs_id)
                            self.last_obstacle_warning = msg
                
                if not msg:
                    route_msg = self._get_route_instruction(lng, lat)
                    if route_msg:
                        self.last_route_instruction = route_msg
                        if route_msg != self.last_macro_message:
                            msg = route_msg
                    else:
                        msg = "" 
                
                response["message"] = msg
                if msg:
                    self.last_macro_message = msg
                
        elif self.status == "MICRO":
            if dist > 80.0: # 偏离太远切回宏观
                self.status = "MACRO"
                self.last_mode_switch_at = time.time()
                response["action"] = "switch_mode"
                response["message"] = "偏离目的地较远，已切换回地图导航模式。"
                response["mode"] = "MACRO"
                self.last_macro_message = response["message"]
            else:
                response["action"] = "visual_search"
                response["message"] = "" # 视觉模式下，指令由 process_vision_frame 生成
        
        return response

    def process_vision_frame(self, image_np: np.ndarray) -> Optional[str]:
        """
        处理视觉帧 (仅在 MICRO 模式下有效)
        """
        if self.status != "MICRO":
            return None
        self.last_visual_frame_at = time.time()
            
        # 调用豆包 Vision API 寻找店铺
        instruction = self.vision_navigator.analyze_scene(
            image_np, 
            prompt_type="find_shop", 
            target_name=self.destination_name
        )
        self.last_visual_analysis_at = time.time()
        if instruction:
            self.last_visual_instruction = instruction
        return instruction

    def _get_route_instruction(self, lng: float, lat: float) -> str:
        if not self.route_steps:
            return ""

        current_pos = (lng, lat)
        while self.current_route_step_index < len(self.route_steps):
            step = self.route_steps[self.current_route_step_index]
            end_point = step.get("end_point")
            if not end_point:
                self.current_route_step_index += 1
                continue

            dist_to_end = self._calc_distance(current_pos, end_point)
            if dist_to_end <= 8.0 and self.current_route_step_index < len(self.route_steps) - 1:
                self.current_route_step_index += 1
                continue

            instruction = step.get("instruction") or step.get("assistant_action") or "继续沿路线前进"
            road_name = step.get("road") or ""
            if road_name and road_name not in instruction:
                instruction = f"{instruction}，沿 {road_name} 前进"

            if dist_to_end > 20:
                return f"{instruction}，前方约 {int(dist_to_end)} 米。"
            if dist_to_end > 8:
                return f"{instruction}，即将执行。"
            return f"{instruction}，请注意脚下并准备执行。"

        return "已接近目的地，请根据语音与视觉提示继续前行。"

    def _check_obstacles_ahead(self, lng: float, lat: float, obstacles_data: list) -> Optional[Dict]:
        """
        检查路径前方 20 米内是否有标注的障碍物。
        简化的空间融合逻辑：在障碍物列表中寻找距离当前位置 5~25 米，且与路线片段横向距离小于 5 米的点。
        """
        for obs in obstacles_data:
            # obs: {"id": "...", "lat": ..., "lng": ..., "title": ...}
            obs_lat = obs.get("lat")
            obs_lng = obs.get("lng")
            if obs_lat is None or obs_lng is None:
                continue
                
            dist_to_user = self._calc_distance((lng, lat), (obs_lng, obs_lat))
            
            # 如果在前方 5 ~ 25 米范围内
            if 5.0 < dist_to_user < 25.0:
                # 检查该障碍物是否在规划的路线上 (简单投影距离 < 5m)
                # 为简化计算，只要该障碍物离 route_polyline 的某个节点距离 < 5m，即认为在路线上
                for pt in self.route_polyline:
                    if self._calc_distance(pt, (obs_lng, obs_lat)) < 5.0:
                        return obs
        return None

    def _calc_distance(self, p1, p2):
        """计算两点间的大致距离(米)"""
        lng1, lat1 = p1
        lng2, lat2 = p2
        R = 6371000 # earth radius in meters
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1)
        
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        c = 2*math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R*c

class NavigationManager:
    """
    全局导航管理器 (单例)
    管理所有用户的导航会话
    """
    def __init__(self):
        self.sessions: Dict[str, NavigationSession] = {}

    def start_navigation(self, user_id: str, destination: str) -> str:
        session = NavigationSession(user_id, destination)
        if session.status == "ERROR_POI_NOT_FOUND":
            return f"抱歉，未找到地点：{destination}"
        
        self.sessions[user_id] = session
        return f"开始导航前往 {session.destination_name}。"

    def start_navigation_to_coords(self, user_id: str, destination_name: str, lng: float, lat: float, context_meta: dict = None) -> str:
        session = NavigationSession.__new__(NavigationSession)
        session.user_id = user_id
        session.destination_name = destination_name
        session.destination_coords = (float(lng), float(lat))
        session.destination_voice_prompt = str((context_meta or {}).get("voice_prompt") or (context_meta or {}).get("blind_guidance_summary") or "").strip()
        session.destination_scene_hint = str((context_meta or {}).get("description") or "").strip()
        session.destination_source = str((context_meta or {}).get("source") or "local_coords").strip()
        session.status = "MACRO"
        session.last_update_time = time.time()
        session.created_at = time.time()
        session.route_generated_at = 0.0
        session.last_mode_switch_at = 0.0
        session.last_macro_message = ""
        session.last_obstacle_warning = ""
        session.last_visual_frame_at = 0.0
        session.last_visual_analysis_at = 0.0
        session.last_visual_instruction = ""
        session.vision_navigator = DoubaoVisionNavigator()
        session.route_polyline = []
        session.route_steps = []
        session.current_route_step_index = 0
        session.last_route_instruction = ""
        session.warned_obstacles = set()
        self.sessions[user_id] = session
        print(f">>> [Nav Session] Created from memory for {user_id} -> {destination_name} @ {session.destination_coords}")
        return f"开始导航前往 {destination_name}。"

    def stop_navigation(self, user_id: str):
        if user_id in self.sessions:
            del self.sessions[user_id]
            return "导航已结束。"
        return "当前没有正在进行的导航。"

    def on_gps_update(self, user_id: str, lng: float, lat: float, obstacles_data: list = None) -> Dict:
        if user_id not in self.sessions:
            return {}
        return self.sessions[user_id].update_gps(lng, lat, obstacles_data)

    def on_video_frame(self, user_id: str, frame: np.ndarray) -> Optional[str]:
        if user_id not in self.sessions:
            return None
        return self.sessions[user_id].process_vision_frame(frame)

# 全局实例
nav_manager = NavigationManager()
