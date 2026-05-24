import cv2
import base64
import time
import requests
import json
import os
from typing import Optional, Dict, Any

try:
    import config_secrets
except ImportError:
    config_secrets = None

# --- Configuration ---
ARK_API_KEY = (
    getattr(config_secrets, "ARK_API_KEY", None) if config_secrets else None
) or os.getenv("ARK_API_KEY") or os.getenv("DOUBAO_TTS_API_KEY")
ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
MODEL_ENDPOINT = os.getenv("DOUBAO_VISION_MODEL", "doubao-1-5-pro-32k-250115")

class DoubaoVisionNavigator:
    def __init__(self):
        self.api_key = ARK_API_KEY
        self.base_url = ARK_BASE_URL.rstrip("/")
        self.model = MODEL_ENDPOINT
        self.last_analysis_time = 0
        self.analysis_interval = 3.0 # 每3秒分析一次，节省成本且足够应付步行
        
    def encode_image_to_base64(self, image_np):
        _, buffer = cv2.imencode('.jpg', image_np, [int(cv2.IMWRITE_JPEG_QUALITY), 60]) # 压缩图片以加快传输
        return base64.b64encode(buffer).decode('utf-8')

    def analyze_scene(self, image_np, prompt_type="general", target_name=None) -> str:
        current_time = time.time()
        if current_time - self.last_analysis_time < self.analysis_interval:
            return None
            
        self.last_analysis_time = current_time
        base64_image = self.encode_image_to_base64(image_np)
        
        # 构建 Prompt
        if prompt_type == "find_shop":
            system_prompt = "你是一个盲人导航助手。用户正在寻找特定的店铺。请仔细观察图片，寻找招牌或特征。"
            user_text = f"我要去'{target_name}'。请告诉我它是否在画面中？如果在，请准确描述它的位置（如'左前方10米'）和特征（如'红色招牌'）。如果不在，请描述画面中有什么显著的地标。"
        else: # general navigation
            system_prompt = "你是一个盲人导航助手。请简要描述前方路况，重点关注安全隐患。"
            user_text = "前方有什么？如果有红绿灯、车辆、台阶或障碍物，请务必指出。如果没有危险，请说'前方道路通畅'。"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                        }
                    ]
                }
            ],
            "max_tokens": 200
        }
        
        print(f"[{time.strftime('%H:%M:%S')}] Calling Doubao Vision API ({prompt_type})...")
        if not self.api_key:
            print("[DoubaoVisionNavigator] Missing ARK_API_KEY, skip visual navigation request.")
            return None
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()
            content = (((result.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if not content:
                return None
            return content
        except Exception as e:
            print(f"API Error: {e}")
            return None

def main():
    navigator = DoubaoVisionNavigator()
    cap = cv2.VideoCapture(0)
    
    mode = "general" # general | find_shop
    target_shop = "王记五金店"
    
    print("=== Doubao Vision Blind Navigation Demo ===")
    print("Press 'f' to switch to 'Find Shop' mode")
    print("Press 'g' to switch to 'General Navigation' mode")
    print("Press 'q' to quit")
    
    if not cap.isOpened():
        print("Camera not found. Using static mock image.")
        frame = cv2.imread("mock_test_image.jpg")
        if frame is None: frame = cv2.imread("d:/aivoice/mock_test_image.jpg") # Try absolute path
    
    while True:
        if cap.isOpened():
            ret, frame = cap.read()
            if not ret: break
        else:
            time.sleep(0.1) # Loop static image
            
        # Draw Status
        display_frame = frame.copy()
        status_text = f"Mode: {mode.upper()}"
        if mode == "find_shop": status_text += f" -> {target_shop}"
        cv2.putText(display_frame, status_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        
        # Analyze
        instruction = navigator.analyze_scene(frame, prompt_type=mode, target_name=target_shop if mode == "find_shop" else None)
        
        if instruction:
            print(f"🔊 AI: {instruction}")
            # In real app, send to TTS engine here
            
            # Show last instruction on screen
            # Wrap text simply
            y = 80
            for line in [instruction[i:i+20] for i in range(0, len(instruction), 20)]:
                cv2.putText(display_frame, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                y += 30

        cv2.imshow("Doubao Nav", display_frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'): break
        if key == ord('f'): mode = "find_shop"; print(f"Switched to Find Shop: {target_shop}")
        if key == ord('g'): mode = "general"; print("Switched to General Mode")
        
    if cap.isOpened(): cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
