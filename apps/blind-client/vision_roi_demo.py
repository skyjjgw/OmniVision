import cv2
import time
import os
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox
from ultralytics import YOLO

def start_detection(use_video_file=False, video_path="", simulate_pi5=False):
    # NCNN 模型路径
    base_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(base_dir, "det_wotr_obstacles_final_88_81_ncnn_model")

    print(">>> [AI] 正在加载本地 YOLOv8 模型 (NCNN 版)...")
    if simulate_pi5:
        print(">>> [硬件模拟] ⚠️ 已开启树莓派 5 性能模拟模式！")
        print(">>>   - 限制最大摄像头帧率: ~15-20 FPS")
        print(">>>   - 增加 AI 推理延迟模拟 (ARM CPU): +40~60ms")
    try:
        if os.path.exists(model_path):
            obstacle_model = YOLO(model_path, task='detect')
            print(f">>> [AI] 本地障碍物检测模型(NCNN)加载成功！")
        else:
            messagebox.showerror("错误", f"未找到 NCNN 模型目录: \n{model_path}")
            return
    except Exception as e:
        messagebox.showerror("错误", f"模型加载失败: {e}")
        return

    # 初始化视频源
    if use_video_file:
        if not os.path.exists(video_path):
            messagebox.showerror("错误", f"找不到视频文件: \n{video_path}")
            return
        cap = cv2.VideoCapture(video_path)
        print(f">>> [视频模式] 正在播放文件: {video_path}")
    else:
        cap = cv2.VideoCapture(0)
        print(">>> [摄像头模式] 正在打开电脑摄像头...")

    if not cap.isOpened():
        messagebox.showerror("错误", "无法打开视频源！可能是摄像头被占用。")
        return

    print("\n===========================================")
    print(">>> 🎮 演示操作指南:")
    print(">>>   - 按下 'Q' 键或 'ESC' 键: 退出演示")
    print("===========================================\n")

    last_ai_warn_time = 0
    frame_count = 0
    
    # 性能评估指标初始化
    prev_frame_time = 0
    inference_time_ms = 0
    total_objects_detected = 0
    dangerous_objects = 0
    avg_conf = 0.0

    while True:
        ret, frame = cap.read()
        if not ret:
            print(">>> 视频播放结束或无法读取画面。")
            break
            
        # 树莓派 5 性能模拟：限制最大帧率 (15-20 FPS)
        if simulate_pi5:
            # 保证每帧之间至少间隔 50ms (20fps)
            elapsed = time.time() - prev_frame_time
            if elapsed < 0.05:
                time.sleep(0.05 - elapsed)

        # 计算总体 FPS (包含读取、缩放、绘制等所有开销)
        current_time = time.time()
        fps = 1 / (current_time - prev_frame_time) if prev_frame_time > 0 else 0
        prev_frame_time = current_time
            
        frame_count += 1
        
        # 获取原始画面尺寸
        orig_h, orig_w = frame.shape[:2]
        
        # 很多手机拍摄的竖屏视频分辨率是 1080x1920，直接显示会超出屏幕。
        # 我们限制最大高度不超过 800 像素，宽度等比缩放
        if orig_h > 800:
            target_h = 800
            target_w = int(orig_w * (target_h / orig_h))
            frame = cv2.resize(frame, (target_w, target_h))
        elif orig_w > 1200: # 限制一下横屏过大的情况
            target_w = 1200
            target_h = int(orig_h * (target_w / orig_w))
            frame = cv2.resize(frame, (target_w, target_h))
            
        # 获取缩放后的画面尺寸
        h, w = frame.shape[:2]
        
        # ================= 梯形 ROI 设计 (透视投影) =================
        # 盲人行走时的真实盲区在二维图像上呈现为“近宽远窄”的梯形（Trapezoid）。
        # 根据视觉无障碍论文 (e.g. "Image guided navigation for visually impaired") 的安全区设计原则：
        # - 近端（底部）需要覆盖盲人身体宽度加上一定的横向摆臂/盲杖挥动空间（通常需覆盖画面底部 90%-100% 的宽度）。
        # - 远端（顶部）只需覆盖行进方向上的核心通道（通常覆盖画面中央 50%-60% 的宽度）。
        
        # 定义梯形的四个顶点 (左上, 右上, 右下, 左下)
        # 远处 (画面 55% 高度处)：宽度扩大到 50% (x: 0.25 到 0.75)
        top_left = [int(w * 0.25), int(h * 0.55)]
        top_right = [int(w * 0.75), int(h * 0.55)]
        
        # 近处脚下 (画面 95% 高度处)：宽度扩大到 90% (x: 0.05 到 0.95)，几乎覆盖整个路面底部
        bottom_right = [int(w * 0.95), int(h * 0.95)]
        bottom_left = [int(w * 0.05), int(h * 0.95)]
        
        pts = np.array([top_left, top_right, bottom_right, bottom_left], np.int32)
        pts = pts.reshape((-1, 1, 2))

        # 绘制半透明的绿色梯形安全区域
        overlay = frame.copy()
        cv2.fillPoly(overlay, [pts], (0, 255, 0))
        cv2.addWeighted(overlay, 0.2, frame, 0.8, 0, frame)
        
        # 在梯形左上角稍微偏上的位置画文字
        cv2.putText(frame, "Safe Zone (Trapezoid)", (top_left[0], top_left[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        # 为了保证画面流畅度，不一定每一帧都做推理，可以每 2 帧推理一次
        # 【优化点1】如果使用 NCNN 模型，速度其实很快，可以尝试每帧推理或每 2 帧推理。这里改为 2。
        if frame_count % 2 == 0:
            try:
                # 记录推理开始时间
                inf_start_time = time.time()
                
                # 执行推理 (NCNN 速度较快)
                results = obstacle_model(frame, verbose=False, conf=0.5)
                
                # 树莓派 5 推理延迟模拟：强行增加 40~60 毫秒的阻塞时间
                if simulate_pi5:
                    time.sleep(np.random.uniform(0.04, 0.06))

                detections = results[0].boxes
                
                # 计算单帧推理耗时 (毫秒)
                inference_time_ms = (time.time() - inf_start_time) * 1000
                
                in_zone_objs = []
                confidences = []
                total_objects_detected = len(detections)
                
                if total_objects_detected > 0:
                    for box in detections:
                        # 提取坐标、类别和置信度
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        cls_id = int(box.cls[0].item())
                        conf = float(box.conf[0].item())
                        obj_name = results[0].names[cls_id]
                        
                        confidences.append(conf)
                        
                        # 计算障碍物底部中心点（因为障碍物是否挡路，取决于它接触地面的位置）
                        obj_cx = (x1 + x2) // 2
                        obj_bottom_y = y2 
                        
                        # 判断底部中心点是否落入梯形安全区域 (使用 cv2.pointPolygonTest)
                        is_inside = cv2.pointPolygonTest(pts, (obj_cx, obj_bottom_y), False)
                        
                        label_text = f"{obj_name} {conf:.2f}"
                        
                        if is_inside >= 0: # 返回 1(在内部) 或 0(在边界上)
                            in_zone_objs.append(obj_name)
                            # 危险！用红色粗框标出，并标注 WARNING
                            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2) 
                            cv2.putText(frame, f"WARN: {label_text}", (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                        else:
                            # 安全。用青色细框标出，不报警
                            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 0), 1) 
                            cv2.putText(frame, label_text, (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

                dangerous_objects = len(in_zone_objs)
                avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

                # 触发语音预警逻辑（模拟）
                if in_zone_objs:
                    warn_time = time.time()
                    # 【优化点2】冷却时间由 3.0 秒降低到 1.5 秒。因为盲人走路时，障碍物在画面中停留的时间很短。
                    # 【优化点3】去重合并。如果有多个物体，去重后拼接播报，而不是只播报第一个。
                    if warn_time - last_ai_warn_time > 1.5:
                        unique_objs = list(set(in_zone_objs))
                        obj_str = "、".join(unique_objs)
                        warning_text = f"前方有 {obj_str}"
                        print(f">>> [语音播报模拟] 🔊 {warning_text} | (检测延迟: {inference_time_ms:.1f}ms)")
                        last_ai_warn_time = warn_time
                            
            except Exception as e:
                print(f">>> [错误] 推理异常: {e}")

        # ================= 在画面左上角绘制实时评测指标面板 =================
        panel_y = 20
        cv2.rectangle(frame, (10, 10), (320, 185), (0, 0, 0), -1) # 黑色半透明背景框，高度拉长一点
        cv2.addWeighted(frame[10:185, 10:320], 0.6, overlay[10:185, 10:320], 0.4, 0, frame[10:185, 10:320]) # 让背景透出一点画面
        
        cv2.putText(frame, f"Performance Metrics", (20, panel_y+15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(frame, f"System FPS: {fps:.1f}", (20, panel_y+40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.putText(frame, f"AI Latency: {inference_time_ms:.1f} ms", (20, panel_y+65), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255) if inference_time_ms < 100 else (0, 0, 255), 1)
        cv2.putText(frame, f"Total Objects: {total_objects_detected}", (20, panel_y+90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(frame, f"Danger (In ROI): {dangerous_objects}", (20, panel_y+115), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255) if dangerous_objects > 0 else (0, 255, 0), 1)
        
        # 新增置信度显示 (检测质量指标)
        cv2.putText(frame, f"Avg Confidence: {avg_conf:.2f}", (20, panel_y+140), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 1)

        if simulate_pi5:
            cv2.putText(frame, "SIMULATING RASPBERRY PI 5", (20, panel_y+165), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)




        # 显示画面
        cv2.imshow("Vision & ROI Demo (NCNN)", frame)
        
        # 退出控制
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == 27: # 'q' 或 ESC
            break

    # 释放资源
    cap.release()
    cv2.destroyAllWindows()
    print(">>> 演示结束。")

# ================= GUI 界面 =================
def create_gui():
    root = tk.Tk()
    root.title("视桥智导 - 视觉感知演示启动器")
    root.geometry("400x250")
    root.eval('tk::PlaceWindow . center')
    
    label = tk.Label(root, text="请选择视觉演示模式", font=("微软雅黑", 14, "bold"))
    label.pack(pady=20)
    
    # 模拟树莓派选项
    simulate_var = tk.BooleanVar()
    chk_simulate = tk.Checkbutton(root, text="模拟树莓派5性能限制 (限制FPS/增加延迟)", variable=simulate_var)
    chk_simulate.pack(pady=5)
    
    def on_camera_click():
        root.destroy()
        start_detection(use_video_file=False, simulate_pi5=simulate_var.get())
        
    def on_video_click():
        video_path = filedialog.askopenfilename(
            title="选择测试视频",
            filetypes=[("视频文件", "*.mp4 *.avi *.mov *.mkv"), ("所有文件", "*.*")]
        )
        if video_path:
            root.destroy()
            start_detection(use_video_file=True, video_path=video_path, simulate_pi5=simulate_var.get())

    btn_camera = tk.Button(root, text="📷 开启电脑摄像头实时检测", font=("微软雅黑", 12), width=25, height=2, command=on_camera_click)
    btn_camera.pack(pady=10)
    
    btn_video = tk.Button(root, text="🎬 选择本地视频文件检测", font=("微软雅黑", 12), width=25, height=2, command=on_video_click)
    btn_video.pack(pady=10)
    
    root.mainloop()

if __name__ == "__main__":
    create_gui()
