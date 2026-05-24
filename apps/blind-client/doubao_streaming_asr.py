import argparse
import asyncio
import audioop
import base64
import gzip
import json
import os
import re
import struct
import sys
import time
import uuid
import urllib.error
import urllib.request
import subprocess
import shutil
from typing import Dict, Optional, Tuple

import websockets
try:
    import edge_tts
except Exception:
    edge_tts = None

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CALL_MODE_ACTIVE = False
SAFETY_PROMPT_SIGNAL_FILE = os.getenv("SAFETY_PROMPT_SIGNAL_FILE", "/tmp/omnivision_safety_prompt_active")


def is_safety_prompt_active() -> bool:
    return bool(SAFETY_PROMPT_SIGNAL_FILE) and os.path.exists(SAFETY_PROMPT_SIGNAL_FILE)


def _tts_preempt_requested(interrupt_event: asyncio.Event) -> bool:
    return interrupt_event.is_set() or is_safety_prompt_active()


async def _wait_async_process_with_interrupt(proc: asyncio.subprocess.Process, interrupt_event: asyncio.Event) -> Tuple[int, bool]:
    while True:
        rc = proc.returncode
        if rc is not None:
            return rc, False
        if _tts_preempt_requested(interrupt_event):
            try:
                proc.terminate()
            except Exception:
                pass
            await asyncio.sleep(0.05)
            if proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                await proc.wait()
            except Exception:
                pass
            return (proc.returncode or -1), True
        await asyncio.sleep(0.03)


def set_call_mode(active: bool):
    global CALL_MODE_ACTIVE
    CALL_MODE_ACTIVE = bool(active)
    print(f"🎛️ [ASR Call Mode] {'paused for video call' if CALL_MODE_ACTIVE else 'resumed after call'}")

def _doubao_tts_stream_sync(text: str, base_url: str, api_key: str, model: str, voice: str) -> bool:
    ffplay_path = shutil.which("ffplay")
    if not ffplay_path:
        return False
    if not api_key:
        return False
    if is_safety_prompt_active():
        print("⏭️ [TTS 跳过]: 安全播报锁激活，取消 doubao-tts-stream")
        return False
    endpoint = base_url.rstrip("/") + "/audio/speech"
    body = {
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": "mp3",
        "stream": True
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )
    ffplay_proc = None
    try:
        ffplay_proc = subprocess.Popen(
            [ffplay_path, "-nodisp", "-autoexit", "-loglevel", "quiet", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            while True:
                if is_safety_prompt_active():
                    print("⏹️ [TTS 已让路]: doubao-tts-stream -> safety prompt")
                    return False
                chunk = resp.read(4096)
                if not chunk:
                    break
                if ffplay_proc.stdin:
                    ffplay_proc.stdin.write(chunk)
                    ffplay_proc.stdin.flush()
        if ffplay_proc.stdin:
            ffplay_proc.stdin.close()
        ffplay_proc.wait(timeout=30)
        return ffplay_proc.returncode == 0 and (not is_safety_prompt_active())
    except Exception:
        return False
    finally:
        try:
            if ffplay_proc and ffplay_proc.poll() is None:
                ffplay_proc.terminate()
        except Exception:
            pass

def _volc_tts_http_query_sync(
    text: str,
    appid: str,
    token: str,
    access_key: str,
    cluster: str,
    voice_type: str,
    api_url: str,
    mp3_path: str
) -> bool:
    if not appid or not token:
        return False
    body = {
        "app": {"appid": appid, "token": token, "cluster": cluster},
        "user": {"uid": "local-tts"},
        "audio": {
            "voice_type": voice_type,
            "encoding": "mp3",
            "speed": 10,
            "volume": 10,
            "pitch": 10
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": text,
            "text_type": "plain",
            "operation": "query"
        }
    }
    headers = {"Content-Type": "application/json"}
    if access_key:
        headers["Authorization"] = f"Bearer; {access_key}"
    req = urllib.request.Request(
        api_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_text = resp.read().decode("utf-8", errors="ignore")
        payload = json.loads(resp_text)
        code = payload.get("code", -1)
        if code != 3000:
            print(f"⚠️ [Volc TTS 返回异常]: code={code}, message={payload.get('message')}")
            return False
        audio_b64 = payload.get("data", "")
        if not audio_b64:
            print("⚠️ [Volc TTS 返回空音频]")
            return False
        audio_bytes = base64.b64decode(audio_b64)
        with open(mp3_path, "wb") as f:
            f.write(audio_bytes)
        return True
    except urllib.error.HTTPError as e:
        try:
            err_detail = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err_detail = str(e)
        print(f"⚠️ [Volc TTS HTTP错误]: {e.code}, detail={err_detail}")
        return False
    except Exception as e:
        print(f"⚠️ [Volc TTS 调用失败]: {e}")
        return False

def _build_v3_full_request_with_event(event: int, payload: dict, session_id: Optional[str] = None) -> bytes:
    header = bytes([0x11, 0x14, 0x10, 0x00])
    body = struct.pack(">i", event)
    if session_id is not None:
        sid = session_id.encode("utf-8")
        body += struct.pack(">I", len(sid)) + sid
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    body += struct.pack(">I", len(payload_bytes)) + payload_bytes
    return header + body

def _parse_v3_frame(frame: bytes):
    if len(frame) < 8:
        return None
    b1 = frame[1]
    msg_type = (b1 >> 4) & 0x0F
    flags = b1 & 0x0F
    offset = 4
    event = None
    session_id = None
    if flags == 0x4:
        event = struct.unpack(">i", frame[offset:offset + 4])[0]
        offset += 4
    if msg_type in (0x9, 0xB) and flags == 0x4:
        sid_len = struct.unpack(">I", frame[offset:offset + 4])[0]
        offset += 4
        session_id = frame[offset:offset + sid_len].decode("utf-8", errors="ignore")
        offset += sid_len
    if len(frame) < offset + 4:
        return None
    payload_len = struct.unpack(">I", frame[offset:offset + 4])[0]
    offset += 4
    payload = frame[offset:offset + payload_len]
    return msg_type, flags, event, session_id, payload

async def _wait_popen_with_interrupt(proc: subprocess.Popen, interrupt_event: asyncio.Event) -> Tuple[int, bool]:
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc, False
        if _tts_preempt_requested(interrupt_event):
            try:
                proc.terminate()
            except Exception:
                pass
            await asyncio.sleep(0.05)
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            return (proc.poll() or -1), True
        await asyncio.sleep(0.03)

async def _volc_tts_v3_bidirection_async(
    text: str,
    appid: str,
    access_token: str,
    resource_id: str,
    voice_type: str,
    api_url: str,
    mp3_path: str,
    interrupt_event: asyncio.Event
) -> Tuple[bool, bool]:
    if not appid or not access_token or not resource_id:
        return False, False
    connect_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    headers = {
        "X-Api-App-Key": appid,
        "X-Api-Access-Key": access_token,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": connect_id,
    }
    ws_version = getattr(websockets, "__version__", "0")
    ws_major = int(str(ws_version).split(".")[0]) if str(ws_version).split(".")[0].isdigit() else 0
    connect_kwargs = {"max_size": 32 * 1024 * 1024, "ping_interval": 20, "ping_timeout": 20}
    if ws_major >= 15:
        connect_kwargs["additional_headers"] = headers
    else:
        connect_kwargs["extra_headers"] = headers

    audio_chunks = []
    ffplay_path = shutil.which("ffplay")
    ffplay_proc = None
    stream_played = False
    try:
        if ffplay_path:
            ffplay_proc = subprocess.Popen(
                [ffplay_path, "-nodisp", "-autoexit", "-loglevel", "quiet", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        async with websockets.connect(api_url, **connect_kwargs) as ws:
            await ws.send(_build_v3_full_request_with_event(1, {}))
            await ws.send(
                _build_v3_full_request_with_event(
                    100,
                    {
                        "user": {"uid": "local-tts"},
                        "req_params": {
                            "speaker": voice_type,
                            "audio_params": {"format": "mp3", "sample_rate": 24000},
                        },
                    },
                    session_id=session_id,
                )
            )
            await ws.send(_build_v3_full_request_with_event(200, {"req_params": {"text": text}}, session_id=session_id))
            await ws.send(_build_v3_full_request_with_event(102, {}, session_id=session_id))

            while True:
                if _tts_preempt_requested(interrupt_event):
                    try:
                        await ws.send(_build_v3_full_request_with_event(101, {}, session_id=session_id))
                    except Exception:
                        pass
                    break
                msg = await ws.recv()
                if isinstance(msg, str):
                    continue
                parsed = _parse_v3_frame(msg)
                if not parsed:
                    continue
                msg_type, _flags, event, _sid, payload = parsed
                if msg_type == 0xB and event == 352 and payload:
                    audio_chunks.append(payload)
                    if ffplay_proc and ffplay_proc.stdin:
                        ffplay_proc.stdin.write(payload)
                        ffplay_proc.stdin.flush()
                        stream_played = True
                if msg_type == 0x9 and event == 152:
                    break

            await ws.send(_build_v3_full_request_with_event(2, {}))

        if ffplay_proc and ffplay_proc.stdin:
            ffplay_proc.stdin.close()
        if ffplay_proc:
            rc, interrupted = await _wait_popen_with_interrupt(ffplay_proc, interrupt_event)
            if interrupted:
                print("⏹️ [TTS 已打断]: provider=volc-tts-v3-stream")
                return True, True
            if rc == 0 and stream_played:
                return True, True
        audio = b"".join(audio_chunks)
        if audio:
            with open(mp3_path, "wb") as f:
                f.write(audio)
            return True, False
        print("⚠️ [Volc TTS V3 返回空音频]")
        return False, False
    except Exception as e:
        print(f"⚠️ [Volc TTS V3 调用失败]: {e}")
        return False, False
    finally:
        try:
            if ffplay_proc and ffplay_proc.poll() is None:
                ffplay_proc.terminate()
        except Exception:
            pass

# ==================== TTS 播报逻辑 ====================
async def play_tts_async(text: str, output_device: str, voice: str, tts_playing_event: asyncio.Event, tts_interrupt_event: asyncio.Event, tts_config: Dict[str, str]):
    if is_safety_prompt_active():
        print("⏭️ [TTS 跳过]: 安全播报锁激活，暂不启动助手播报")
        return
    print(f"\n🔊 [TTS 开始播报]: {text}")
    try:
        tts_interrupt_event.clear()
        tts_playing_event.set()
        subprocess.run(["pkill", "-f", "aplay"], stderr=subprocess.DEVNULL)

        file_id = str(uuid.uuid4())
        mp3_path = f"/tmp/reply_{file_id}.mp3"
        wav_path = f"/tmp/reply_{file_id}.wav"
        fast_played = False
        ffplay_path = shutil.which("ffplay")
        volc_generated = False
        if tts_config.get("use_volc_tts_v3", "1") == "1":
            volc_generated, volc_stream_played = await _volc_tts_v3_bidirection_async(
                text,
                tts_config.get("volc_tts_appid", ""),
                tts_config.get("volc_tts_access_token", ""),
                tts_config.get("volc_tts_resource_id", "seed-tts-2.0"),
                tts_config.get("volc_tts_voice_type", "zh_female_vv_uranus_bigtts"),
                tts_config.get("volc_tts_v3_api_url", "wss://openspeech.bytedance.com/api/v3/tts/bidirection"),
                mp3_path,
                tts_interrupt_event,
            )
            if volc_generated:
                if volc_stream_played:
                    print("🔊 [TTS 播放成功]: provider=volc-tts-v3-stream")
                    return
                print("🔊 [TTS 生成成功]: provider=volc-tts-v3")

        if is_safety_prompt_active():
            print("⏹️ [TTS 已让路]: 安全播报锁激活，终止当前助手播报")
            return

        if (not volc_generated) and tts_config.get("use_volc_tts_v1", "0") == "1":
            volc_generated = await asyncio.to_thread(
                _volc_tts_http_query_sync,
                text,
                tts_config.get("volc_tts_appid", ""),
                tts_config.get("volc_tts_token", ""),
                tts_config.get("volc_tts_access_key", ""),
                tts_config.get("volc_tts_cluster", "volcano_tts"),
                tts_config.get("volc_tts_voice_type", "zh_female_vv_uranus_bigtts"),
                tts_config.get("volc_tts_api_url", "https://openspeech.bytedance.com/api/v1/tts"),
                mp3_path
            )
            if volc_generated:
                print("🔊 [TTS 生成成功]: provider=volc-tts-v1")

        if is_safety_prompt_active():
            print("⏹️ [TTS 已让路]: 安全播报锁激活，终止当前助手播报")
            return

        doubao_played = False
        if (not volc_generated) and tts_config.get("use_doubao_tts", "0") == "1":
            doubao_played = await asyncio.to_thread(
                _doubao_tts_stream_sync,
                text,
                tts_config.get("doubao_tts_base_url", "https://ark.cn-beijing.volces.com/api/v3"),
                tts_config.get("doubao_tts_api_key", ""),
                tts_config.get("doubao_tts_model", "doubao-tts"),
                tts_config.get("doubao_tts_voice", "zh_male_qn_qingse")
            )
            if doubao_played:
                print("🔊 [TTS 播放成功]: player=doubao-tts-stream")
                return
            print("⚠️ [Doubao TTS 不可用]: 自动回退 edge-tts")

        if (not volc_generated) and edge_tts is not None and ffplay_path:
            try:
                ffplay_proc = subprocess.Popen(
                    [ffplay_path, "-nodisp", "-autoexit", "-loglevel", "quiet", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                communicate = edge_tts.Communicate(text, voice)
                async for chunk in communicate.stream():
                    if _tts_preempt_requested(tts_interrupt_event):
                        break
                    if chunk.get("type") == "audio" and ffplay_proc.stdin:
                        ffplay_proc.stdin.write(chunk["data"])
                        ffplay_proc.stdin.flush()
                if ffplay_proc.stdin:
                    ffplay_proc.stdin.close()
                rc, interrupted = await _wait_popen_with_interrupt(ffplay_proc, tts_interrupt_event)
                if interrupted:
                    print("⏹️ [TTS 已打断]: player=ffplay-stream")
                    return
                if rc == 0:
                    fast_played = True
                    print("🔊 [TTS 播放成功]: player=ffplay-stream")
            except Exception as e:
                print(f"⚠️ [TTS 快速流播失败]: {e}")

        if is_safety_prompt_active():
            print("⏹️ [TTS 已让路]: 安全播报锁激活，终止当前助手播报")
            return

        if (not volc_generated) and (not fast_played) and edge_tts is not None:
            try:
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(mp3_path)
            except Exception as e:
                print(f"⚠️ [TTS 生成失败: edge_tts SDK]: {e}")
        elif (not volc_generated) and (not fast_played):
            tts_cmd = [
                "edge-tts",
                "--voice", voice,
                "--text", text,
                "--write-media", mp3_path
            ]
            proc = await asyncio.create_subprocess_exec(*tts_cmd, stderr=asyncio.subprocess.PIPE)
            _, tts_err = await proc.communicate()
            if proc.returncode != 0:
                err_text = (tts_err or b"").decode("utf-8", errors="ignore").strip()
                print(f"⚠️ [TTS 生成失败: edge-tts CLI]: {err_text}")

        if is_safety_prompt_active():
            print("⏹️ [TTS 已让路]: 安全播报锁激活，终止当前助手播报")
            return

        if os.path.exists(mp3_path):
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", mp3_path, wav_path]
            proc = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, ffmpeg_err = await proc.communicate()
            if proc.returncode != 0:
                err_text = (ffmpeg_err or b"").decode("utf-8", errors="ignore").strip()
                print(f"⚠️ [TTS 转码失败]: {err_text}")

            if os.path.exists(wav_path):
                device_candidates = []
                if output_device:
                    device_candidates.append(output_device)
                device_candidates.extend(["default", "sysdefault"])
                played = False
                for device_name in device_candidates:
                    if is_safety_prompt_active():
                        print("⏹️ [TTS 已让路]: 安全播报锁激活，取消 aplay 播放")
                        return
                    play_cmd = ["aplay", "-D", device_name, wav_path]
                    play_proc = await asyncio.create_subprocess_exec(
                        *play_cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE
                    )
                    rc, interrupted = await _wait_async_process_with_interrupt(play_proc, tts_interrupt_event)
                    if interrupted:
                        print(f"⏹️ [TTS 已打断]: player=aplay, device={device_name}")
                        return
                    play_err = b""
                    if play_proc.stderr is not None:
                        play_err = await play_proc.stderr.read()
                    if rc == 0:
                        print(f"🔊 [TTS 播放成功]: device={device_name}")
                        played = True
                        break
                    err_text = (play_err or b"").decode("utf-8", errors="ignore").strip()
                    if err_text:
                        print(f"⚠️ [TTS 设备失败]: device={device_name}, err={err_text}")
                if not played:
                    print(f"⚠️ [TTS 播放失败]: 所有输出设备都不可用，当前首选={output_device}")
        try:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
            if os.path.exists(wav_path):
                os.remove(wav_path)
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ [TTS 播放失败]: {e}")
    finally:
        tts_playing_event.clear()
        tts_interrupt_event.clear()

# ==================== 监听服务器回复 ====================
async def tts_worker(tts_queue: asyncio.Queue, output_device: str, voice: str, tts_playing_event: asyncio.Event, tts_interrupt_event: asyncio.Event, tts_config: Dict[str, str]):
    while True:
        text = await tts_queue.get()
        try:
            if CALL_MODE_ACTIVE:
                continue
            while is_safety_prompt_active():
                if tts_playing_event.is_set() and not tts_interrupt_event.is_set():
                    tts_interrupt_event.set()
                await asyncio.sleep(0.05)
            await play_tts_async(text, output_device, voice, tts_playing_event, tts_interrupt_event, tts_config)
        finally:
            tts_queue.task_done()

def _drain_tts_queue(tts_queue: asyncio.Queue):
    while not tts_queue.empty():
        try:
            tts_queue.get_nowait()
            tts_queue.task_done()
        except asyncio.QueueEmpty:
            break

def _test_capture_device_once(device: str, sample_rate: int) -> Tuple[bool, str]:
    cmd = [
        "arecord",
        "-D", device,
        "-q",
        "-d", "1",
        "-t", "raw",
        "-f", "S16_LE",
        "-r", str(sample_rate),
        "-c", "1",
        "/dev/null",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
        if proc.returncode == 0:
            return True, ""
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err
    except Exception as e:
        return False, str(e)

async def listen_to_server(server_ws_url: str, tts_queue: asyncio.Queue, push_client_id: str, tts_interrupt_event: asyncio.Event):
    """后台任务：保持连接到配置的云端服务，接收大模型回复并播报"""
    print(f"🔌 [连接后端]: 正在连接 {server_ws_url} 接收大模型指令...")
    while True:
        try:
            async with websockets.connect(server_ws_url) as ws:
                print("✅ [后端已连接]: 随时准备接收大模型回复")
                while True:
                    msg = await ws.recv()
                    if CALL_MODE_ACTIVE:
                        _drain_tts_queue(tts_queue)
                        tts_interrupt_event.set()
                        continue
                    try:
                        data = json.loads(msg)
                        msg_type = data.get("type")
                        msg_client_id = str(data.get("client_id", push_client_id))
                        if msg_client_id and msg_client_id != push_client_id:
                            continue
                        if msg_type == "assistant_reply":
                            reply_text = data.get("text", "")
                            is_chunk = data.get("is_chunk", False)
                            is_first_chunk = data.get("is_first_chunk", False)
                            
                            if reply_text:
                                if is_first_chunk or not is_chunk:
                                    _drain_tts_queue(tts_queue)
                                    tts_interrupt_event.set()
                                tts_queue.put_nowait(reply_text)
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            print(f"⚠️ [后端连接断开]: {e}，5秒后重试...")
            await asyncio.sleep(5)


# ==================== 原有的 ASR 逻辑 ====================
def load_env_file(path: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not os.path.exists(path):
        return data
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data

def build_frame(message_type: int, flags: int, serialization: int, compression: int, payload: bytes) -> bytes:
    b0 = (1 << 4) | 1
    b1 = ((message_type & 0x0F) << 4) | (flags & 0x0F)
    b2 = ((serialization & 0x0F) << 4) | (compression & 0x0F)
    b3 = 0
    header = bytes([b0, b1, b2, b3])
    size = len(payload).to_bytes(4, byteorder="big", signed=False)
    return header + size + payload

def parse_frame(data: bytes) -> Tuple[int, int, int, int, Optional[int], bytes]:
    if len(data) < 8:
        return 0, 0, 0, 0, None, b""
    b0, b1, b2, _b3 = data[0], data[1], data[2], data[3]
    header_size_words = b0 & 0x0F
    header_size = header_size_words * 4
    message_type = (b1 >> 4) & 0x0F
    flags = b1 & 0x0F
    serialization = (b2 >> 4) & 0x0F
    compression = b2 & 0x0F
    start = header_size
    seq: Optional[int] = None
    if flags in (0x1, 0x3):
        if len(data) < start + 4:
            return message_type, flags, serialization, compression, None, b""
        seq = int.from_bytes(data[start:start + 4], byteorder="big", signed=True)
        start += 4
    if len(data) < start + 4:
        return message_type, flags, serialization, compression, seq, b""
    payload_size = int.from_bytes(data[start:start + 4], byteorder="big", signed=False)
    payload = data[start + 4:start + 4 + payload_size]
    return message_type, flags, serialization, compression, seq, payload

def make_init_payload(app_id: str, token: str, resource_id: str, sample_rate: int, uid: str, audio_format: str, audio_codec: str) -> bytes:
    body = {
        "app": {"appid": app_id, "token": token, "cluster": resource_id},
        "user": {"uid": uid},
        "audio": {"format": audio_format, "codec": audio_codec, "rate": sample_rate, "bits": 16, "channel": 1},
        "request": {"reqid": str(uuid.uuid4()), "sequence": 1, "show_utterances": True, "result_type": "single"}
    }
    return gzip.compress(json.dumps(body, ensure_ascii=False).encode("utf-8"))

def post_asr_text(push_url: str, client_id: str, text: str, intent: Optional[str] = None):
    payload = {"client_id": client_id, "asr_text": text, "intent": intent}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(push_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        _ = resp.read()

async def recv_loop(ws, push_url: Optional[str], push_client_id: str):
    last_sent_text = ""
    last_sent_ts = 0.0
    last_sent_len = 0
    last_preview_text = ""
    last_preview_ts = 0.0
    last_partial_text = ""
    last_partial_count = 0
    push_only_final = os.environ.get("ASR_PUSH_ONLY_FINAL", "1") == "1"
    allow_partial_fallback = os.environ.get("ASR_ALLOW_PARTIAL_FALLBACK", "0") == "1"
    final_fallback_seconds = float(os.environ.get("ASR_FINAL_FALLBACK_SECONDS", "1.5"))
    sentence_end_fast = os.environ.get("ASR_PUSH_SENTENCE_END_FAST", "0") == "1"
    stable_partial_enabled = os.environ.get("ASR_STABLE_PARTIAL_ENABLED", "1") == "1"
    stable_partial_repeat = int(os.environ.get("ASR_STABLE_PARTIAL_REPEAT", "2"))
    preview_url = os.environ.get("ASR_PREVIEW_URL", "").strip()
    if (not preview_url) and push_url:
        preview_url = push_url.replace("/api/asr/update", "/api/asr/preview")
    preview_interval = float(os.environ.get("ASR_PREVIEW_MIN_INTERVAL", "0.3"))
    while True:
        msg = await ws.recv()
        if isinstance(msg, str): continue
        message_type, flags, serialization, compression, seq, payload = parse_frame(msg)
        if compression == 1 and payload:
            try: payload = gzip.decompress(payload)
            except Exception: pass
        text = ""
        obj = None
        if serialization == 1 and payload:
            try:
                obj = json.loads(payload.decode("utf-8", errors="ignore"))
                text = json.dumps(obj, ensure_ascii=False)
            except Exception:
                text = payload.decode("utf-8", errors="ignore")
        else:
            text = payload.decode("utf-8", errors="ignore") if payload else ""
            
        if obj is not None:
            if "result" in obj and isinstance(obj["result"], dict):
                result = obj["result"]
                if "text" in result and result.get("text"):
                    text_value = str(result.get("text")).strip()
                    is_final = bool(result.get("is_final"))
                    now = time.time()
                    if not is_final and text_value:
                        if text_value == last_partial_text:
                            last_partial_count += 1
                        else:
                            last_partial_text = text_value
                            last_partial_count = 1
                    elif is_final:
                        last_partial_text = ""
                        last_partial_count = 0
                    if is_final or len(text_value) >= 2:
                        # 仅在文本内容真正发生变化，或者是最终结果时，才打印到终端
                        if text_value != last_preview_text or is_final:
                            print(f"👂 [听到声音]: {text_value}")
                    if preview_url and len(text_value) >= 2:
                        if (text_value != last_preview_text) or (now - last_preview_ts >= preview_interval):
                            try:
                                await asyncio.to_thread(post_asr_text, preview_url, push_client_id, text_value, None)
                                last_preview_text = text_value
                                last_preview_ts = now
                            except (urllib.error.URLError, TimeoutError):
                                pass
                    fallback_ready = (
                        (not is_final)
                        and push_only_final
                        and allow_partial_fallback
                        and len(text_value) >= 4
                        and (now - last_sent_ts) >= final_fallback_seconds
                        and text_value != last_sent_text
                    )
                    sentence_end_ready = (
                        (not is_final)
                        and sentence_end_fast
                        and len(text_value) >= 4
                        and bool(re.search(r"[。！？!?\.]$", text_value))
                        and text_value != last_sent_text
                    )
                    stable_partial_ready = (
                        (not is_final)
                        and stable_partial_enabled
                        and len(text_value) >= 4
                        and last_partial_count >= max(1, stable_partial_repeat)
                        and text_value != last_sent_text
                        and (now - last_sent_ts) > 0.8
                    )
                    should_push = bool(text_value) and (
                        (is_final if push_only_final else (is_final or len(text_value) >= 2))
                        or fallback_ready
                        or sentence_end_ready
                        or stable_partial_ready
                    )
                    if (not push_only_final) and (not is_final) and should_push:
                        growth = len(text_value) - last_sent_len
                        has_end = bool(re.search(r"[。！？!?\.]$", text_value))
                        if (growth < 8) and (not has_end) and ((now - last_sent_ts) < 1.2):
                            should_push = False
                    if push_url and should_push:
                        if text_value != last_sent_text or now - last_sent_ts > 1.0:
                            try:
                                await asyncio.to_thread(post_asr_text, push_url, push_client_id, text_value, None)
                                push_reason = "final" if is_final else ("stable_partial" if stable_partial_ready else ("sentence_end_partial" if sentence_end_ready else ("fallback_partial" if fallback_ready else "partial")))
                                print(f"📤 [ASR->LLM]: reason={push_reason}, text={text_value}")
                                last_sent_text = text_value
                                last_sent_ts = now
                                last_sent_len = len(text_value)
                            except (urllib.error.URLError, TimeoutError):
                                pass
        if message_type == 0x0F or flags in (0x2, 0x3):
            break

async def send_audio(ws, device: str, sample_rate: int, chunk_ms: int, tts_playing_event: asyncio.Event, tts_interrupt_event: asyncio.Event, barge_in_enabled: bool, barge_in_rms_threshold: int, barge_in_hold_ms: int, barge_in_grace_ms: int):
    bytes_per_sample = 2
    chunk_size = int(sample_rate * bytes_per_sample * (chunk_ms / 1000.0))
    safety_prompt_signal_file = os.getenv("SAFETY_PROMPT_SIGNAL_FILE", "/tmp/omnivision_safety_prompt_active")
    output_tail_hold_ms = int(os.getenv("ASR_OUTPUT_TAIL_HOLD_MS", "450"))
    output_tail_hold_s = max(0.0, output_tail_hold_ms / 1000.0)
    cmd = ["arecord", "-D", device, "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate), "-c", "1"]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    if proc.stdout is None or proc.stderr is None:
        raise RuntimeError("arecord stdout/stderr is None")
    hold_frames = max(1, int(barge_in_hold_ms / max(chunk_ms, 1)))
    speech_frames = 0
    tts_active_since = 0.0
    peak_rms = 0
    resume_hold_until = 0.0
    try:
        while True:
            chunk = await proc.stdout.readexactly(chunk_size)
            now_monotonic = time.monotonic()
            safety_active = bool(safety_prompt_signal_file) and os.path.exists(safety_prompt_signal_file)
            tts_active = tts_playing_event.is_set()
            if safety_active:
                resume_hold_until = max(resume_hold_until, now_monotonic + output_tail_hold_s)
                speech_frames = 0
                tts_active_since = 0.0
                peak_rms = 0
                if tts_active and not tts_interrupt_event.is_set():
                    tts_interrupt_event.set()
                    print("⏹️ [TTS 抢占]: 安全播报触发，停止助手播报")
                await asyncio.sleep(0.01)
                continue
            if CALL_MODE_ACTIVE:
                resume_hold_until = max(resume_hold_until, now_monotonic + output_tail_hold_s)
                speech_frames = 0
                tts_active_since = 0.0
                peak_rms = 0
                if tts_playing_event.is_set():
                    tts_interrupt_event.set()
                await asyncio.sleep(0.02)
                continue
            if tts_active:
                resume_hold_until = max(resume_hold_until, now_monotonic + output_tail_hold_s)
                if tts_active_since <= 0:
                    tts_active_since = now_monotonic
                    peak_rms = 0
                if barge_in_enabled:
                    elapsed_ms = (now_monotonic - tts_active_since) * 1000.0
                    if elapsed_ms < barge_in_grace_ms:
                        continue
                    rms = audioop.rms(chunk, 2)
                    if rms > peak_rms:
                        peak_rms = rms
                    if rms >= barge_in_rms_threshold:
                        speech_frames += 2
                    elif rms >= int(barge_in_rms_threshold * 0.75):
                        speech_frames += 1
                    else:
                        speech_frames = max(0, speech_frames - 1)
                    if speech_frames >= hold_frames and not tts_interrupt_event.is_set():
                        tts_interrupt_event.set()
                        print(f"⏹️ [Barge-in]: 检测到用户打断，rms={rms}, peak={peak_rms}, threshold={barge_in_rms_threshold}")
                continue
            if now_monotonic < resume_hold_until:
                speech_frames = 0
                tts_active_since = 0.0
                peak_rms = 0
                await asyncio.sleep(0.01)
                continue
            speech_frames = 0
            tts_active_since = 0.0
            frame = build_frame(message_type=0x2, flags=0x0, serialization=0x0, compression=0x0, payload=chunk)
            await ws.send(frame)
            await asyncio.sleep(0.001)
    except asyncio.IncompleteReadError as e:
        if e.partial:
            frame = build_frame(message_type=0x2, flags=0x0, serialization=0x0, compression=0x0, payload=e.partial)
            await ws.send(frame)
        err_text = ""
        try:
            if proc.stderr is not None:
                err_text = (await proc.stderr.read()).decode("utf-8", errors="ignore").strip()
        except Exception:
            err_text = ""
        try:
            if proc.returncode is None:
                await proc.wait()
        except Exception:
            pass
        raise RuntimeError(f"arecord_exit rc={proc.returncode}, device={device}, err={err_text[:300]}")
    except asyncio.CancelledError:
        try: await ws.send(build_frame(message_type=0x2, flags=0x2, serialization=0x0, compression=0x0, payload=b""))
        except Exception: pass
        raise
    finally:
        try: proc.terminate(); await proc.wait()
        except Exception: pass

async def run(args):
    env = load_env_file(args.env)
    app_id = args.app_id or env.get("DOUBAO_ASR_APP_ID", env.get("DOUBAO_APP_ID", ""))
    token = args.access_key or env.get("DOUBAO_ASR_ACCESS_TOKEN", env.get("DOUBAO_ACCESS_TOKEN", ""))
    resource_id = args.resource_id or env.get("DOUBAO_ASR_RESOURCE_ID", "")
    device = args.device or env.get("AUDIO_DEVICE", "plughw:3,0")
    sample_rate = int(args.sample_rate or env.get("AUDIO_SAMPLE_RATE", "16000"))
    audio_format = (args.audio_format or env.get("AUDIO_FORMAT", "pcm")).strip().lower()
    audio_codec = (args.audio_codec or env.get("AUDIO_CODEC", "raw")).strip().lower()
    push_url = args.push_url or env.get("ASR_PUSH_URL", "")
    push_client_id = args.push_client_id or env.get("ASR_PUSH_CLIENT_ID", "asr_client")
    tts_voice = args.tts_voice or env.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
    output_device = args.tts_output_device or env.get("AUDIO_PLAYBACK_DEVICE", "default")
    tts_config = {
        "use_volc_tts_v3": args.use_volc_tts_v3 or env.get("USE_VOLC_TTS_V3", "1"),
        "use_volc_tts_v1": args.use_volc_tts_v1 or env.get("USE_VOLC_TTS_V1", "1"),
        "volc_tts_appid": args.volc_tts_appid or env.get("VOLC_TTS_APPID", ""),
        "volc_tts_token": args.volc_tts_token or env.get("VOLC_TTS_TOKEN", ""),
        "volc_tts_access_token": args.volc_tts_access_token or env.get("VOLC_TTS_ACCESS_TOKEN", ""),
        "volc_tts_access_key": args.volc_tts_access_key or env.get("VOLC_TTS_ACCESS_KEY", ""),
        "volc_tts_resource_id": args.volc_tts_resource_id or env.get("VOLC_TTS_RESOURCE_ID", "seed-tts-2.0"),
        "volc_tts_cluster": args.volc_tts_cluster or env.get("VOLC_TTS_CLUSTER", "volcano_tts"),
        "volc_tts_voice_type": args.volc_tts_voice_type or env.get("VOLC_TTS_VOICE_TYPE", "zh_female_vv_uranus_bigtts"),
        "volc_tts_api_url": args.volc_tts_api_url or env.get("VOLC_TTS_API_URL", "https://openspeech.bytedance.com/api/v1/tts"),
        "volc_tts_v3_api_url": args.volc_tts_v3_api_url or env.get("VOLC_TTS_V3_API_URL", "wss://openspeech.bytedance.com/api/v3/tts/bidirection"),
        "use_doubao_tts": args.use_doubao_tts or env.get("USE_DOUBAO_TTS", "0"),
        "doubao_tts_api_key": args.doubao_tts_api_key or env.get("DOUBAO_TTS_API_KEY", ""),
        "doubao_tts_base_url": args.doubao_tts_base_url or env.get("DOUBAO_TTS_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        "doubao_tts_model": args.doubao_tts_model or env.get("DOUBAO_TTS_MODEL", "doubao-tts"),
        "doubao_tts_voice": args.doubao_tts_voice or env.get("DOUBAO_TTS_VOICE", "zh_male_qn_qingse"),
    }
    if not app_id or not token or not resource_id:
        print("❌ [ASR 配置缺失]: DOUBAO_ASR_APP_ID / DOUBAO_ASR_ACCESS_TOKEN / DOUBAO_ASR_RESOURCE_ID 不能为空")
        print(f"当前值 app_id={'set' if bool(app_id) else 'empty'}, token={'set' if bool(token) else 'empty'}, resource_id={resource_id or 'empty'}")
        return
    fallback_devices = [d.strip() for d in env.get("AUDIO_DEVICE_FALLBACKS", "default,plughw:1,0,plughw:0,0").split(",") if d.strip()]
    base_probe_candidates = [device] + [d for d in fallback_devices if d != device]
    reprobe_each_round = env.get("ASR_REPROBE_EACH_ROUND", "1") == "1"
    max_backoff_s = int(env.get("ASR_MAX_BACKOFF_SECONDS", "8"))
    min_backoff_s = int(env.get("ASR_MIN_BACKOFF_SECONDS", "1"))
    failure_streak = 0
    active_device = device

    async def _pick_device(preferred: str, announce_unavailable: bool = True) -> str:
        candidates = [preferred] + [d for d in base_probe_candidates if d != preferred]
        for idx, dev in enumerate(candidates):
            ok, err = await asyncio.to_thread(_test_capture_device_once, dev, sample_rate)
            if ok:
                if idx > 0:
                    print(f"🎤 [录音设备回退成功]: {preferred} -> {dev}")
                else:
                    print(f"🎤 [录音设备可用]: {dev}")
                return dev
            if announce_unavailable:
                print(f"⚠️ [录音设备不可用]: {dev}, err={err[:160]}")
        return preferred

    active_device = await _pick_device(device, announce_unavailable=True)
    
    # 提取后端的 IP 和 端口来构造 websocket 地址
    server_ws_url = "ws://127.0.0.1:8000/ws/stream"
    if push_url and "http" in push_url:
        base = push_url.split("/api")[0].replace("http://", "ws://").replace("https://", "wss://")
        server_ws_url = f"{base}/ws/stream"

    # 启动后台的 WebSocket 监听任务（用来接收大模型回答并播放）
    # 调试 blind_client 本地播报时，可通过 ASR_ENABLE_SERVER_TTS=0 关闭这里的播放链路。
    tts_queue: asyncio.Queue[str] = asyncio.Queue()
    tts_playing_event = asyncio.Event()
    tts_interrupt_event = asyncio.Event()
    push_only_final = (env.get("ASR_PUSH_ONLY_FINAL", "1") == "1")
    final_fallback_seconds = float(env.get("ASR_FINAL_FALLBACK_SECONDS", "1.5"))
    sentence_end_fast = (env.get("ASR_PUSH_SENTENCE_END_FAST", "1") == "1")
    barge_in_enabled = (env.get("BARGE_IN_ENABLED", "1") == "1")
    barge_in_rms_threshold = int(env.get("BARGE_IN_RMS_THRESHOLD", "2200"))
    barge_in_hold_ms = int(env.get("BARGE_IN_HOLD_MS", "650"))
    barge_in_grace_ms = int(env.get("BARGE_IN_GRACE_MS", "1100"))
    asr_enable_server_tts = (env.get("ASR_ENABLE_SERVER_TTS", "1") == "1")
    if asr_enable_server_tts:
        asyncio.create_task(tts_worker(tts_queue, output_device=output_device, voice=tts_voice, tts_playing_event=tts_playing_event, tts_interrupt_event=tts_interrupt_event, tts_config=tts_config))
        asyncio.create_task(listen_to_server(server_ws_url, tts_queue=tts_queue, push_client_id=push_client_id, tts_interrupt_event=tts_interrupt_event))
        print("🔊 [ASR 播报链]: 已启用服务器回复播报")
    else:
        print("🔇 [ASR 播报链]: 已禁用服务器回复播报，交由 blind_client 本地播报")

    print(f"🎤 [麦克风启动]: 正在连接火山引擎大模型流式识别...")
    ws_version = getattr(websockets, "__version__", "0")
    ws_major = int(str(ws_version).split(".")[0]) if str(ws_version).split(".")[0].isdigit() else 0
    while True:
        if reprobe_each_round or failure_streak > 0:
            active_device = await _pick_device(active_device, announce_unavailable=(failure_streak == 0))
        connect_id = str(uuid.uuid4())
        headers = {"X-Api-App-Key": app_id, "X-Api-Access-Key": token, "X-Api-Resource-Id": resource_id, "X-Api-Connect-Id": connect_id}
        init_payload = make_init_payload(app_id, token, resource_id, sample_rate, uid=connect_id, audio_format=audio_format, audio_codec=audio_codec)
        init_frame = build_frame(message_type=0x1, flags=0x0, serialization=0x1, compression=0x1, payload=init_payload)
        connect_kwargs = {"max_size": 8 * 1024 * 1024, "ping_interval": 20, "ping_timeout": 20}
        if ws_major >= 15:
            connect_kwargs["additional_headers"] = headers
        else:
            connect_kwargs["extra_headers"] = headers
        try:
            async with websockets.connect(args.url, **connect_kwargs) as ws:
                await ws.send(init_frame)
                recv_task = asyncio.create_task(recv_loop(ws, push_url=push_url or None, push_client_id=push_client_id))
                send_task = asyncio.create_task(
                    send_audio(
                        ws,
                        device=active_device,
                        sample_rate=sample_rate,
                        chunk_ms=args.chunk_ms,
                        tts_playing_event=tts_playing_event,
                        tts_interrupt_event=tts_interrupt_event,
                        barge_in_enabled=barge_in_enabled,
                        barge_in_rms_threshold=barge_in_rms_threshold,
                        barge_in_hold_ms=barge_in_hold_ms,
                        barge_in_grace_ms=barge_in_grace_ms,
                    )
                )
                done, pending = await asyncio.wait([recv_task, send_task], return_when=asyncio.FIRST_COMPLETED)
                for p in pending:
                    p.cancel()
                results = await asyncio.gather(recv_task, send_task, return_exceptions=True)
                session_failed = False
                for result in results:
                    if isinstance(result, asyncio.CancelledError):
                        continue
                    if isinstance(result, BaseException):
                        print(f"⚠️ [ASR 子任务异常]: {result}")
                        session_failed = True
                if session_failed:
                    failure_streak = min(failure_streak + 1, 10)
                else:
                    failure_streak = 0
                if failure_streak == 0:
                    print("ℹ️ [ASR 会话结束]: 子任务完成，准备重连")
                retry_delay = min(max_backoff_s, max(min_backoff_s, (2 ** min(failure_streak, 3))))
                await asyncio.sleep(retry_delay)
        except KeyboardInterrupt:
            print("stopped by user")
            break
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                print("stopped by user")
                break
            err_text = str(e)
            if "HTTP 400" in err_text:
                masked_token = (token[:6] + "***") if token else "empty"
                print("❌ [ASR 鉴权失败/资源ID不匹配]: WebSocket 握手返回 HTTP 400")
                print(f"当前 app_id={app_id}, access_token={masked_token}, resource_id={resource_id}")
                print("请确认该 app_id 与 access_token 已开通 ASR 大模型流式识别，并且 resource_id 与控制台一致。")
            failure_streak += 1
            retry_delay = min(max_backoff_s, max(min_backoff_s, (2 ** min(failure_streak, 3))))
            print(f"⚠️ [ASR 连接异常]: {e}，{retry_delay}秒后重连...")
            await asyncio.sleep(retry_delay)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="/home/tian/client/.env.asr")
    parser.add_argument("--url", default="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async")
    parser.add_argument("--app-id")
    parser.add_argument("--access-key")
    parser.add_argument("--secret-key")
    parser.add_argument("--resource-id")
    parser.add_argument("--device")
    parser.add_argument("--sample-rate", type=int)
    parser.add_argument("--chunk-ms", type=int, default=200)
    parser.add_argument("--audio-format")
    parser.add_argument("--audio-codec")
    parser.add_argument("--push-url")
    parser.add_argument("--push-client-id")
    parser.add_argument("--tts-output-device")
    parser.add_argument("--tts-voice")
    parser.add_argument("--use-volc-tts-v3")
    parser.add_argument("--use-volc-tts-v1")
    parser.add_argument("--volc-tts-appid")
    parser.add_argument("--volc-tts-token")
    parser.add_argument("--volc-tts-access-token")
    parser.add_argument("--volc-tts-access-key")
    parser.add_argument("--volc-tts-resource-id")
    parser.add_argument("--volc-tts-cluster")
    parser.add_argument("--volc-tts-voice-type")
    parser.add_argument("--volc-tts-api-url")
    parser.add_argument("--volc-tts-v3-api-url")
    parser.add_argument("--use-doubao-tts")
    parser.add_argument("--doubao-tts-api-key")
    parser.add_argument("--doubao-tts-base-url")
    parser.add_argument("--doubao-tts-model")
    parser.add_argument("--doubao-tts-voice")
    args = parser.parse_args()
    asyncio.run(run(args))

if __name__ == "__main__":
    main()
