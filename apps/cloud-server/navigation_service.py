import requests
import json
import os

try:
    import config_secrets
except ImportError:
    config_secrets = None

AMAP_KEY = (
    getattr(config_secrets, "AMAP_KEY", None) if config_secrets else None
) or os.getenv("AMAP_KEY")

class NavigationService:
    def __init__(self, amap_key=None):
        self.amap_key = amap_key or AMAP_KEY
        if not self.amap_key:
             print(">>> [Nav] WARNING: No AMAP_KEY found. Navigation will fail.")
        else:
             print(f">>> [Nav] Init with Key: {self.amap_key[:6]}...")
        
    def search_place(self, query, city=""):
        """搜索地点"""
        # 即使没有 Key 也不要轻易 fallback 到 mock，除非明确没有
        if not self.amap_key:
            print(">>> [Nav] No Key, returning None")
            return None

        url = "https://restapi.amap.com/v3/place/text"
        candidate_cities = []
        city = str(city or "").strip()
        if city:
            candidate_cities.append(city)
        candidate_cities.extend(["苏州", ""])

        try:
            for city_name in candidate_cities:
                params = {
                    "key": self.amap_key,
                    "keywords": query,
                    "offset": 1,
                    "page": 1,
                    "extensions": "base",
                }
                if city_name:
                    params["city"] = city_name
                response = requests.get(url, params=params, timeout=5)
                res = response.json()

                if res.get("status") == "1" and res.get("pois"):
                    poi = res["pois"][0]
                    return {
                        "name": poi["name"],
                        "address": poi["address"],
                        "location": poi["location"], # lng,lat
                        "id": poi["id"]
                    }
            print(f">>> [Nav] Search failed: {query}")
            return None
        except Exception as e:
            print(f">>> [Nav] API Error: {e}")
            return None

    def get_weather_info(self, location_str):
        # ... (保持原有逻辑) ...
        return "暂不支持天气查询" 

    def plan_walking_route(self, origin, destination):
        detail = self.plan_walking_route_detail(origin, destination)
        if not detail:
            return None, 0
        return detail.get("polyline_coords"), detail.get("distance", 0)

    def plan_walking_route_detail(self, origin, destination):
        """
        高德步行路线规划
        origin: "lng,lat"
        destination: "lng,lat"
        返回: 路线详情 {polyline_coords, distance, steps}
        """
        if not self.amap_key:
            return None
            
        url = "https://restapi.amap.com/v3/direction/walking"
        params = {
            "key": self.amap_key,
            "origin": origin,
            "destination": destination
        }
        try:
            response = requests.get(url, params=params, timeout=5)
            res = response.json()
            if res.get("status") == "1" and res.get("route"):
                route = res["route"]
                paths = route.get("paths", [])
                if not paths:
                    return None
                
                distance = int(paths[0].get("distance", 0))
                steps = paths[0].get("steps", [])
                
                polyline_coords = []
                parsed_steps = []
                def _clean_text(value):
                    if value is None:
                        return ""
                    if isinstance(value, str):
                        return value.strip()
                    if isinstance(value, (list, tuple)):
                        return " ".join(str(item).strip() for item in value if str(item).strip()).strip()
                    return str(value).strip()
                for step in steps:
                    poly = step.get("polyline", "")
                    step_coords = []
                    for pt in poly.split(";"):
                        if pt:
                            lng, lat = map(float, pt.split(","))
                            polyline_coords.append((lng, lat))
                            step_coords.append((lng, lat))
                    if step_coords:
                        parsed_steps.append({
                            "instruction": _clean_text(step.get("instruction", "")),
                            "assistant_action": _clean_text(step.get("assistant_action", "")),
                            "orientation": _clean_text(step.get("orientation", "")),
                            "road": _clean_text(step.get("road", "")),
                            "distance": int(step.get("distance", 0) or 0),
                            "polyline": step_coords,
                            "start_point": step_coords[0],
                            "end_point": step_coords[-1],
                        })
                return {
                    "polyline_coords": polyline_coords,
                    "distance": distance,
                    "steps": parsed_steps,
                }
            return None
        except Exception as e:
            print(f">>> [Nav] Route API Error: {e}")
            return None

    def _mock_search(self, query):
        return None

# 全局单例
nav_service = NavigationService()
