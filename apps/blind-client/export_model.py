from ultralytics import YOLO
import os

# 模型路径
model_path = r"d:\aivoice\new_server_unified\client\det_blind_nav_unified_v1\weights\best.pt"

print(f"正在加载 YOLO 模型: {model_path}")
model = YOLO(model_path)

# 1. 导出为 ONNX 格式 (最通用，树莓派必装)
print("\n正在导出为 ONNX 格式...")
try:
    # half=True 开启半精度 FP16 进一步提速，simplify=True 简化网络结构
    model.export(format="onnx", half=True, simplify=True)
    print("✅ ONNX 导出成功！")
except Exception as e:
    print(f"❌ ONNX 导出失败: {e}")

# 2. 导出为 NCNN 格式 (腾讯开源，ARM CPU/边缘板神级加速器)
print("\n正在导出为 NCNN 格式...")
try:
    model.export(format="ncnn", half=True)
    print("✅ NCNN 导出成功！")
except Exception as e:
    print(f"❌ NCNN 导出失败: {e}")

print("\n🎉 导出任务完成！")
