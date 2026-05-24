import argparse
import datetime
import os
import re
import shlex
import subprocess
import sys
import time
from typing import List, Tuple
import wave


def ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_text(cmd: List[str], timeout: float = 3.0) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return p.returncode, p.stdout or "", p.stderr or ""


def list_capture_cards() -> List[Tuple[int, int, str]]:
    code, out, err = run_text(["arecord", "-l"], timeout=4.0)
    text = (out + "\n" + err).strip()
    if code != 0:
        return []
    lines = text.splitlines()
    result: List[Tuple[int, int, str]] = []
    card_id = None
    card_name = ""
    for line in lines:
        m_card = re.search(r"card\s+(\d+):\s*([^\[]+)\[([^\]]+)\]", line)
        if m_card:
            card_id = int(m_card.group(1))
            card_name = m_card.group(3).strip()
            continue
        m_dev = re.search(r"device\s+(\d+):", line)
        if m_dev is not None and card_id is not None:
            dev = int(m_dev.group(1))
            result.append((card_id, dev, card_name))
    return result


def _wav_rms(path: str) -> float:
    try:
        with wave.open(path, "rb") as wf:
            if wf.getsampwidth() != 2:
                return 0.0
            frames = wf.readframes(wf.getnframes())
        if not frames:
            return 0.0
        count = len(frames) // 2
        if count <= 0:
            return 0.0
        total = 0.0
        for i in range(0, len(frames), 2):
            s = int.from_bytes(frames[i:i + 2], byteorder="little", signed=True)
            total += float(s) * float(s)
        return (total / float(count)) ** 0.5
    except Exception:
        return 0.0


def test_record_device(device: str, duration_s: float = 1.0, require_rms: float = 0.0) -> Tuple[bool, int, float, str]:
    out_path = "/tmp/watchdog_probe.wav"
    cmd = ["arecord", "-D", device, "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(int(duration_s)), out_path]
    p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    rms = _wav_rms(out_path) if size > 0 else 0.0
    try:
        if os.path.exists(out_path):
            os.remove(out_path)
    except Exception:
        pass
    ok = p.returncode == 0 and size > 1000 and (rms >= require_rms)
    return ok, size, rms, (p.stderr or "").strip()


def pick_best_device(prefer: str = "", require_rms: float = 0.0) -> str:
    candidates: List[str] = []
    if prefer:
        candidates.append(prefer)
    cards = list_capture_cards()
    for c, d, _ in cards:
        candidates.append(f"hw:{c},{d}")
        candidates.append(f"plughw:{c},{d}")
    candidates.extend(["default", "pulse"])
    seen = set()
    uniq = []
    for x in candidates:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    for dev in uniq:
        ok, _, _, _ = test_record_device(dev, duration_s=1.0, require_rms=require_rms)
        if ok:
            return dev
    return ""


def run_cmd_template(template: str, device: str):
    if not template:
        return
    cmd = template.replace("{device}", device)
    subprocess.Popen(cmd, shell=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--prefer", default="")
    parser.add_argument("--on-device-change", default="")
    parser.add_argument("--on-device-lost", default="")
    parser.add_argument("--on-device-ok", default="")
    parser.add_argument("--print-cards", action="store_true")
    parser.add_argument("--require-rms", type=float, default=0.0)
    args = parser.parse_args()

    last_dev = ""
    last_ok = None
    while True:
        cards = list_capture_cards()
        if args.print_cards:
            card_text = ", ".join([f"card={c},dev={d},name={n}" for c, d, n in cards]) if cards else "none"
            print(f"[{ts()}] cards: {card_text}", flush=True)
        dev = pick_best_device(args.prefer, require_rms=args.require_rms)
        if dev != last_dev:
            print(f"[{ts()}] device_change: {last_dev or 'none'} -> {dev or 'none'}", flush=True)
            run_cmd_template(args.on_device_change, dev)
            last_dev = dev
        if not dev:
            if last_ok is not False:
                print(f"[{ts()}] device_lost", flush=True)
                run_cmd_template(args.on_device_lost, "")
            last_ok = False
            time.sleep(args.interval)
            continue
        ok, size, rms, err = test_record_device(dev, duration_s=1.0, require_rms=args.require_rms)
        if ok:
            if last_ok is not True:
                print(f"[{ts()}] device_ok: {dev}, bytes={size}, rms={rms:.1f}", flush=True)
                run_cmd_template(args.on_device_ok, dev)
            last_ok = True
        else:
            print(f"[{ts()}] device_bad: {dev}, bytes={size}, rms={rms:.1f}, err={err}", flush=True)
            if last_ok is not False:
                run_cmd_template(args.on_device_lost, "")
            last_ok = False
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"[{ts()}] stopped", flush=True)
        sys.exit(0)
