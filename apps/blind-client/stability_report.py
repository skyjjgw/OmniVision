import re
import sys
from statistics import mean

if len(sys.argv) < 2:
    print("usage: python stability_report.py <log_file>")
    sys.exit(1)

log_file = sys.argv[1]

metric_re = re.compile(
    r"\[METRIC\]\s+state=(\w+)\s+fps=([\d.]+)\s+upload_fps=([\d.]+)\s+avg_send_ms=([\d.]+)\s+quality=(\d+)\s+interval=([\d.]+)\s+fail=(\d+)\s+reconnect=(\d+)"
)

fps_list = []
upload_fps_list = []
send_ms_list = []
states = {}
reconnect_max = 0
net_fail_lines = 0
metric_count = 0

with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        if "[NET] 上传失败" in line:
            net_fail_lines += 1
        m = metric_re.search(line)
        if not m:
            continue
        metric_count += 1
        state = m.group(1)
        fps = float(m.group(2))
        upload_fps = float(m.group(3))
        send_ms = float(m.group(4))
        reconnect = int(m.group(8))
        fps_list.append(fps)
        upload_fps_list.append(upload_fps)
        send_ms_list.append(send_ms)
        reconnect_max = max(reconnect_max, reconnect)
        states[state] = states.get(state, 0) + 1

def safe_mean(arr):
    return mean(arr) if arr else 0.0

def safe_min(arr):
    return min(arr) if arr else 0.0

def safe_max(arr):
    return max(arr) if arr else 0.0

print("# 树莓派端侧 30 分钟稳定性报告")
print()
print("## 概览")
print(f"- 指标样本数: {metric_count}")
print(f"- 断连重连次数: {reconnect_max}")
print(f"- 上传失败日志条数: {net_fail_lines}")
print()
print("## 性能指标")
print(f"- 平均 FPS: {safe_mean(fps_list):.2f}")
print(f"- 最低 FPS: {safe_min(fps_list):.2f}")
print(f"- 最高 FPS: {safe_max(fps_list):.2f}")
print(f"- 平均上传 FPS: {safe_mean(upload_fps_list):.2f}")
print(f"- 平均发送耗时(ms): {safe_mean(send_ms_list):.1f}")
print(f"- 发送耗时P95近似(ms): {sorted(send_ms_list)[int(len(send_ms_list)*0.95)-1]:.1f}" if send_ms_list else "- 发送耗时P95近似(ms): 0.0")
print()
print("## 状态机分布")
if states:
    for k, v in sorted(states.items(), key=lambda x: x[0]):
        print(f"- {k}: {v}")
else:
    print("- 无状态数据")
