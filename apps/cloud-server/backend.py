import cv2
import numpy as np
import asyncio
import json
import base64
import time
import random
import subprocess
import re
import platform
import sys
import os
import threading
import serial
import serial.tools.list_ports
import urllib.parse
import urllib.request
import uuid
import shutil
import sqlite3
import logging
import dashscope
import config_secrets
import smtplib
import hashlib
import hmac
import base64
import random
from collections import deque
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, List, Optional
from datetime import datetime
from fastapi import FastAPI, WebSocket, Request, HTTPException, File, UploadFile, Body, WebSocketDisconnect, Form, Query, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from openai import AsyncOpenAI

app = FastAPI()

# LLM Client Configuration
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
LLM_MODEL = "doubao-seed-1-6-flash-250828"
AUDIT_AGENT_API_KEY = os.getenv("ARK_AUDIT_AGENT_API_KEY") or LLM_API_KEY
DECISION_AGENT_API_KEY = os.getenv("ARK_DECISION_AGENT_API_KEY") or LLM_API_KEY
GUIDANCE_AGENT_API_KEY = os.getenv("ARK_GUIDANCE_AGENT_API_KEY") or DECISION_AGENT_API_KEY
AUDIT_AGENT_MODEL = os.getenv("ARK_AUDIT_AGENT_MODEL", "doubao-seed-1-6-flash-250828")
DECISION_AGENT_MODEL = os.getenv("ARK_DECISION_AGENT_MODEL", "doubao-seed-1-6-flash-250828")
GUIDANCE_AGENT_MODEL = os.getenv("ARK_GUIDANCE_AGENT_MODEL", DECISION_AGENT_MODEL)
import httpx

# 优化点1：使用 httpx.AsyncClient 并配置 http2=True 开启 HTTP/2 连接复用。
# 全量审核会串行跑审核分析、审核决策、导盲摘要三个智能体，请求时间明显长于普通对话。
custom_http_client = httpx.AsyncClient(http2=True, timeout=45.0)

# 优化点2：将客户端重新初始化以使用优化的 HTTP 客户端
llm_client = AsyncOpenAI(
    api_key=LLM_API_KEY, 
    base_url=LLM_BASE_URL,
    http_client=custom_http_client
)


def _build_ark_client(api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        base_url=LLM_BASE_URL,
        http_client=custom_http_client,
    )


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


audit_agent_client = _build_ark_client(AUDIT_AGENT_API_KEY)
decision_agent_client = _build_ark_client(DECISION_AGENT_API_KEY)
guidance_agent_client = _build_ark_client(GUIDANCE_AGENT_API_KEY)
llm_prewarm_started = False
ASR_LLM_DEBOUNCE_SECONDS = 0.2
ASR_LLM_DUPLICATE_TTL_SECONDS = 2.0
VOICE_STANDBY_ENABLED = os.getenv("VOICE_STANDBY_ENABLED", "1") == "1"
VOICE_DEFAULT_MODE = os.getenv("VOICE_DEFAULT_MODE", "standby").strip().lower()
if VOICE_DEFAULT_MODE not in {"standby", "awake"}:
    VOICE_DEFAULT_MODE = "standby"
VOICE_AWAKE_TIMEOUT_SECONDS = max(5.0, float(os.getenv("VOICE_AWAKE_TIMEOUT_SECONDS", "15")))
VOICE_WAKE_REPLY = os.getenv("VOICE_WAKE_REPLY", "我在").strip()
VOICE_SLEEP_REPLY = os.getenv("VOICE_SLEEP_REPLY", "已进入待机").strip()
VOICE_WAKE_WORDS = [
    item.strip() for item in os.getenv(
        "VOICE_WAKE_WORDS",
        "小爱,小艾,你好小爱,你好小艾,小爱同学,小艾同学,你好小爱同学,你好小艾同学"
    ).split(",") if item.strip()
]
VOICE_SLEEP_WORDS = [
    item.strip() for item in os.getenv("VOICE_SLEEP_WORDS", "进入待机,休眠,关闭语音助手").split(",") if item.strip()
]
asr_pending_text_by_client: Dict[str, str] = {}
asr_debounce_task_by_client: Dict[str, asyncio.Task] = {}
asr_last_sent_norm_by_client: Dict[str, str] = {}
asr_last_sent_ts_by_client: Dict[str, float] = {}

EMERGENCY_ASSIST_KEYWORDS = [
    "呼叫人工", "呼叫志愿者", "人工帮助", "人工协助", "请求帮助", "请求人工帮助",
    "我要人工帮助", "帮帮我", "救救我", "求助", "人工"
]
ASSIST_CANCEL_KEYWORDS = [
    "取消求助", "取消人工帮助", "取消人工协助", "不用帮助了", "不用人工帮助了",
    "结束人工帮助", "关闭人工协助", "取消人工", "不用人工"
]
FAST_COMMAND_KEYWORDS = EMERGENCY_ASSIST_KEYWORDS + ASSIST_CANCEL_KEYWORDS + [
    "停止导航", "结束导航", "取消导航", "退出导航", "关闭导航",
    "再说一遍", "重复一遍", "重复刚才的导航", "再重复一下", "刚才说什么", "上一条指令",
    "保存为", "记为", "把这里保存为", "把当前位置保存为", "把现在的位置作为", "把当前位置作为", "把这里记为"
]


async def _prewarm_llm():
    global llm_prewarm_started
    if llm_prewarm_started:
        return
    llm_prewarm_started = True
    try:
        await llm_client.responses.create(
            model=LLM_MODEL,
            max_output_tokens=8,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "回复：好"
                        }
                    ],
                }
            ]
        )
        print("✅ [LLM 预热完成]")
    except Exception as e:
        print(f"⚠️ [LLM 预热失败]: {e}")

@app.on_event("startup")
async def _startup_warmup():
    _load_scheduled_audit_state()
    asyncio.create_task(_prewarm_llm())
    asyncio.create_task(_poll_recognition_updates())
    asyncio.create_task(_scheduled_audit_loop())


async def _poll_recognition_updates():
    while True:
        try:
            await manager.push_recognition_update()
        except Exception as e:
            print(f"⚠️ recognition poll error: {e}")
        await asyncio.sleep(0.35)

# Add Exception Handler for Validation Errors (Fix 422 Login Issues)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
        body_str = body.decode(errors='ignore')
    except:
        body_str = "Could not read body"
        
    print(f"❌ Validation Error: {exc}\nBody: {body_str}")
    return JSONResponse(
        status_code=422,
        content={"success": False, "message": f"Data validation failed: {exc}", "detail": str(exc)},
    )

# 添加导航模块路径
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")
SCHEDULED_AUDIT_FILE = os.path.join(CURRENT_DIR, "scheduled_audit.json")
ENABLE_DEV_ENDPOINTS = _env_flag("ENABLE_DEV_ENDPOINTS", False)
ALLOW_ADMIN_NAV_SIMULATE = _env_flag("ALLOW_ADMIN_NAV_SIMULATE", ENABLE_DEV_ENDPOINTS)
ALLOW_MOCK_ROUTE_UPDATE = _env_flag("ALLOW_MOCK_ROUTE_UPDATE", ENABLE_DEV_ENDPOINTS)
# Mount Uploads Directory
UPLOAD_DIR = os.path.join(CURRENT_DIR, "uploads")
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Mount Static Directory (for frontend assets if any)
STATIC_DIR = os.path.join(CURRENT_DIR, "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

scheduled_audit_state: Dict[str, Any] = {
    "enabled": False,
    "scheduled_time": "",
    "last_run_at": "",
    "last_run_date": "",
    "last_result": {},
}
scheduled_audit_lock = asyncio.Lock()

# --- 集成 NavigationManager (高德+豆包) ---
from server_navigation_state import nav_manager
# ----------------------------------------

# 优先尝试导入当前目录下的导航模块
NAV_PATH = os.path.join(CURRENT_DIR, "navigation")
if os.path.exists(NAV_PATH):
    sys.path.append(NAV_PATH)
    try:
        from workflow_blindpath import BlindPathNavigator
        HAS_NAVIGATOR = True
        print("成功导入 BlindPathNavigator (Local Unified)")
    except ImportError as e:
        print(f"导入 BlindPathNavigator 失败: {e}")
        HAS_NAVIGATOR = False
else:
    print(f"导航模块路径不存在: {NAV_PATH}")
    HAS_NAVIGATOR = False

# 尝试导入音频播放模块 (已根据用户要求禁用)
AUDIO_PATH = CURRENT_DIR # 默认当前目录
try:
    from audio_player import play_voice_text
    # HAS_AUDIO = True # 暂时禁用，除非确认需要
    HAS_AUDIO = False
    print("成功导入 audio_player")
except ImportError:
    HAS_AUDIO = False

from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List

import math
import datetime

# --- Contribution Management (New Community) ---
CONTRIBUTIONS_FILE = os.path.join(CURRENT_DIR, "contributions.json")
contributions_data = []

# --- User Login / Registration (App Endpoint) ---
class UserLoginRequest(BaseModel):
    email: str
    password: Optional[str] = None
    deviceId: Optional[str] = None

@app.post("/api/user/login")
async def user_login(req: UserLoginRequest):
    print(f"User Login Attempt: {req.email}")
    
    # 1. Check existing users (Memory + DB)
    user = next((u for u in users_data if u['id'] == req.email), None)
    
    if user:
        if user.get('status') == 'banned':
             return JSONResponse(status_code=403, content={"success": False, "message": "Account banned"})
        print(f"User Login Success: {req.email}")
        return {"success": True, "user": user, "token": str(uuid.uuid4())}
    
    # 2. If not found, Auto-Register (Since we are in development/demo mode)
    # Check if DB is accessible first
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT email FROM users WHERE email=?", (req.email,))
        if c.fetchone():
            # Should have been found in memory, but reload just in case
            load_users()
            user = next((u for u in users_data if u['id'] == req.email), None)
            if user:
                return {"success": True, "user": user, "token": str(uuid.uuid4())}
        
        # Register new
        new_user = {
            "id": req.email,
            "nickname": req.email.split('@')[0],
            "avatar": None,
            "status": "active",
            "createdAt": time.time(),
            "trust_score": 100
        }
        
        # Insert to DB
        c.execute("INSERT INTO users (email, nickname, avatar_path, status, trust_score, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (new_user['id'], new_user['nickname'], new_user['avatar'], new_user['status'], new_user['trust_score'], new_user['createdAt']))
        conn.commit()
        conn.close()
        
        # Update Memory
        users_data.append(new_user)
        save_users() # Sync JSON
        
        print(f"New User Registered: {req.email}")
        return {"success": True, "user": new_user, "token": str(uuid.uuid4())}
        
    except Exception as e:
        print(f"Login Error (DB Access): {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": f"Database Error: {str(e)}"})

# Also support /api/login just in case
@app.post("/api/login")
async def generic_login(req: UserLoginRequest):
    return await user_login(req)

# --- Action Logs Management ---
ACTION_LOGS_FILE = os.path.join(CURRENT_DIR, "action_logs.json")
action_logs = []

def load_action_logs():
    global action_logs
    if os.path.exists(ACTION_LOGS_FILE):
        try:
            with open(ACTION_LOGS_FILE, "r", encoding="utf-8") as f:
                action_logs = json.load(f)
        except Exception as e:
            print(f"Error loading action logs: {e}")
            action_logs = []

def save_action_logs():
    try:
        with open(ACTION_LOGS_FILE, "w", encoding="utf-8") as f:
            json.dump(action_logs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving action logs: {e}")

load_action_logs()


# --- Dispute Management ---
DISPUTES_FILE = os.path.join(CURRENT_DIR, "disputes.json")
disputes_data = []

def load_disputes():
    global disputes_data
    disputes_data = []
    if os.path.exists(DISPUTES_FILE):
        try:
            with open(DISPUTES_FILE, "r", encoding="utf-8") as f:
                disputes_data = json.load(f)
        except Exception as e:
            print(f"Error loading disputes: {e}")
            disputes_data = []

    disputes_data = [_normalize_region_fields(d, fallback_forum="障碍争议") for d in disputes_data if isinstance(d, dict)]

def save_disputes():
    try:
        with open(DISPUTES_FILE, "w", encoding="utf-8") as f:
            json.dump(disputes_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving disputes: {e}")

def _normalize_region_fields(record: dict, fallback_forum: str = "默认论坛") -> dict:
    if not isinstance(record, dict):
        return {}
    record.setdefault("regionCode", record.get("region_code") or "default")
    record.setdefault("regionName", record.get("region_name") or "默认地区")
    record.setdefault("cityCode", record.get("city_code") or "")
    record.setdefault("cityName", record.get("city_name") or "")
    zones = record.get("zones") if isinstance(record.get("zones"), list) else []
    inferred_forum = zones[0] if zones else fallback_forum
    record.setdefault("forumKey", inferred_forum or fallback_forum)
    record.setdefault("forumName", inferred_forum or fallback_forum)
    record.setdefault("targetType", record.get("target_type") or "shop")
    return record


def _inherit_region_fields(target: dict, source: Optional[dict]) -> dict:
    normalized = _normalize_region_fields(dict(target or {}))
    if isinstance(source, dict):
        source_norm = _normalize_region_fields(dict(source))
        for key in ["regionCode", "regionName", "cityCode", "cityName"]:
            if not normalized.get(key):
                normalized[key] = source_norm.get(key)
    return normalized


def _get_forum_catalog(posts: list[dict]) -> list[dict]:
    forums = {}
    for post in posts:
        normalized = _normalize_region_fields(dict(post))
        region_code = normalized.get("regionCode") or "default"
        region_name = normalized.get("regionName") or "默认地区"
        forum_key = normalized.get("forumKey") or "默认论坛"
        forum_name = normalized.get("forumName") or forum_key
        bucket = forums.setdefault(region_code, {
            "regionCode": region_code,
            "regionName": region_name,
            "forums": {}
        })
        bucket["forums"][forum_key] = {
            "forumKey": forum_key,
            "forumName": forum_name,
        }
    return sorted(
        [
            {
                "regionCode": item["regionCode"],
                "regionName": item["regionName"],
                "forums": sorted(list(item["forums"].values()), key=lambda f: str(f.get("forumName") or f.get("forumKey") or "")),
            }
            for item in forums.values()
        ],
        key=lambda item: str(item.get("regionName") or item.get("regionCode") or "")
    )


def _get_region_catalog(items: list[dict]) -> list[dict]:
    regions = {}
    for item in items:
        normalized = _normalize_region_fields(dict(item))
        region_code = normalized.get("regionCode") or "default"
        region_name = normalized.get("regionName") or region_code
        regions[region_code] = {
            "regionCode": region_code,
            "regionName": region_name,
        }
    return sorted(list(regions.values()), key=lambda item: str(item.get("regionName") or item.get("regionCode") or ""))


def _normalize_dispute_record(dispute: dict) -> dict:
    normalized = _normalize_region_fields(dict(dispute), fallback_forum="障碍争议")
    normalized.setdefault("candidateA", normalized.get("target_id") or "")
    normalized.setdefault("candidateB", "")
    normalized.setdefault("target_id", normalized.get("candidateA") or "")
    normalized.setdefault("target_type", normalized.get("targetType") or "obstacle")
    normalized.setdefault("type", "conflict")
    normalized.setdefault("source", normalized.get("reporter") or "user")
    votes = normalized.get("votes") if isinstance(normalized.get("votes"), dict) else {}
    normalized["votes"] = {
        "merge": list(dict.fromkeys([str(v) for v in votes.get("merge", []) if str(v).strip()])),
        "separate": list(dict.fromkeys([str(v) for v in votes.get("separate", []) if str(v).strip()])),
        "reject": list(dict.fromkeys([str(v) for v in votes.get("reject", []) if str(v).strip()])),
    }
    return normalized


def _build_runtime_obstacle_dispute(obstacle: dict) -> dict:
    normalized_obs = _normalize_region_fields(dict(obstacle), fallback_forum="障碍争议")
    obstacle_id = str(normalized_obs.get("id") or "")
    return _normalize_dispute_record({
        "id": f"runtime_disp_{obstacle_id}",
        "candidateA": obstacle_id,
        "candidateB": "",
        "target_id": obstacle_id,
        "target_type": "obstacle",
        "targetType": "obstacle",
        "type": "conflict",
        "votes": {"merge": [], "separate": [], "reject": []},
        "status": "pending",
        "reason": "当前仅存在障碍物标注，尚无独立争议记录；此条用于后台展示现有障碍物争议入口。",
        "reporter": "system_runtime",
        "source": "runtime_obstacle",
        "created_at": _safe_created_at_sort_value(normalized_obs.get("createdAt") or normalized_obs.get("created_at") or time.time()),
        "regionCode": normalized_obs.get("regionCode"),
        "regionName": normalized_obs.get("regionName"),
        "cityCode": normalized_obs.get("cityCode"),
        "cityName": normalized_obs.get("cityName"),
        "forumKey": normalized_obs.get("forumKey") or "障碍争议",
        "forumName": normalized_obs.get("forumName") or "障碍争议",
    })


def _ensure_runtime_obstacle_disputes() -> bool:
    global disputes_data
    changed = False
    existing_obstacle_targets = set()
    normalized_disputes = []
    for raw in disputes_data:
        normalized = _normalize_dispute_record(raw)
        normalized_disputes.append(normalized)
        target_type = str(normalized.get("target_type") or normalized.get("targetType") or "").lower()
        target_id = str(normalized.get("candidateA") or normalized.get("target_id") or "")
        if target_type == "obstacle" and target_id:
            existing_obstacle_targets.add(target_id)
    if normalized_disputes != disputes_data:
        disputes_data = normalized_disputes
        changed = True

    for obstacle in obstacles_data:
        normalized_obs = _normalize_region_fields(dict(obstacle), fallback_forum="障碍争议")
        if int(normalized_obs.get("status", 0) or 0) == 2:
            continue
        obstacle_id = str(normalized_obs.get("id") or "")
        if not obstacle_id or obstacle_id in existing_obstacle_targets:
            continue
        disputes_data.insert(0, _build_runtime_obstacle_dispute(normalized_obs))
        existing_obstacle_targets.add(obstacle_id)
        changed = True

    if changed:
        save_disputes()
    return changed


def _format_dispute_payload(dispute: dict) -> dict:
    normalized_dispute = _normalize_dispute_record(dispute)
    target_id = normalized_dispute.get("candidateA") or normalized_dispute.get("target_id")
    contrib_id = normalized_dispute.get("candidateB") or None
    data_a = next((obs for obs in obstacles_data if str(obs.get("id")) == str(target_id)), None)
    if not data_a:
        data_a = next((shop for shop in shops_data if str(shop.get("id")) == str(target_id)), None)
    data_b = next((c for c in contributions_data if str(c.get("id")) == str(contrib_id)), None) if contrib_id else None
    merged_region = _inherit_region_fields(normalized_dispute, data_a or data_b or {})
    target_type = str(
        merged_region.get("target_type")
        or merged_region.get("targetType")
        or normalized_dispute.get("target_type")
        or normalized_dispute.get("type")
        or "unknown"
    ).lower()
    return {
        "id": normalized_dispute["id"],
        "candidateA": str(target_id or ""),
        "candidateB": str(contrib_id or ""),
        "type": normalized_dispute.get("type", "conflict"),
        "votes": normalized_dispute.get("votes", {"merge": [], "separate": [], "reject": []}),
        "status": normalized_dispute.get("status", "pending"),
        "dataA": _normalize_region_fields(data_a or {}, fallback_forum="障碍争议"),
        "dataB": _normalize_region_fields(data_b or {}, fallback_forum="障碍争议"),
        "target_id": str(target_id or ""),
        "target_type": target_type,
        "reason": normalized_dispute.get("reason", ""),
        "reporter": normalized_dispute.get("reporter", ""),
        "created_at": _safe_created_at_sort_value(normalized_dispute.get("created_at") or normalized_dispute.get("createdAt") or time.time()),
        "regionCode": merged_region.get("regionCode", "default"),
        "regionName": merged_region.get("regionName", "默认地区"),
        "cityCode": merged_region.get("cityCode", ""),
        "cityName": merged_region.get("cityName", ""),
        "forumKey": merged_region.get("forumKey", "障碍争议"),
        "forumName": merged_region.get("forumName", "障碍争议"),
        "source": normalized_dispute.get("source", "user"),
        "resolution": normalized_dispute.get("resolution"),
        "resolved_at": normalized_dispute.get("resolved_at"),
    }


def _build_dispute_region_catalog(target_type: str = "") -> list[dict]:
    normalized_target_type = str(target_type or "").lower().strip()
    region_items = []
    if normalized_target_type in {"", "obstacle"}:
        region_items.extend([
            _normalize_region_fields(dict(obs), fallback_forum="障碍争议")
            for obs in obstacles_data
            if int(dict(obs).get("status", 0) or 0) != 2
        ])
    for dispute in disputes_data:
        normalized_dispute = _normalize_dispute_record(dispute)
        dispute_target_type = str(normalized_dispute.get("target_type") or normalized_dispute.get("targetType") or "").lower()
        if normalized_target_type and dispute_target_type != normalized_target_type:
            continue
        region_items.append(normalized_dispute)
    return _get_region_catalog(region_items)


def _append_unique_image(target: dict, image_url: Optional[str]):
    if not target or not image_url:
        return
    image_urls = target.get("imageUrls") if isinstance(target.get("imageUrls"), list) else []
    if image_url not in image_urls:
        image_urls.append(image_url)
    target["imageUrls"] = image_urls


def _create_or_update_marker_from_contribution(contrib: dict) -> Optional[str]:
    if not isinstance(contrib, dict):
        return None
    marker_id = str(contrib.get("markerId") or contrib.get("id") or "")
    if not marker_id:
        return None
    target_type = str(contrib.get("targetType") or ("shop" if contrib.get("type") in (0, 4) else "obstacle")).lower()
    base_payload = {
        "id": marker_id,
        "lat": contrib.get("lat"),
        "lng": contrib.get("lng"),
        "createdAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
        "title": contrib.get("markerTitle"),
        "description": contrib.get("content"),
        "regionCode": contrib.get("regionCode"),
        "regionName": contrib.get("regionName"),
        "cityCode": contrib.get("cityCode"),
        "cityName": contrib.get("cityName"),
        "targetType": target_type,
        "forumKey": contrib.get("forumKey"),
        "forumName": contrib.get("forumName"),
    }
    if target_type == "shop":
        existing_shop = next((s for s in shops_data if str(s.get("id")) == marker_id), None)
        if existing_shop:
            if contrib.get("content"):
                existing_shop["description"] = contrib.get("content")
            if contrib.get("markerTitle"):
                existing_shop["title"] = contrib.get("markerTitle")
            _append_unique_image(existing_shop, contrib.get("imageUrl"))
            existing_shop["status"] = int(existing_shop.get("status", 0) or 0)
            existing_shop.update(_normalize_region_fields(base_payload, fallback_forum="探店分享"))
        else:
            shops_data.append(_normalize_region_fields({
                **base_payload,
                "type": 4,
                "status": 0,
                "imageUrls": [contrib["imageUrl"]] if contrib.get("imageUrl") else [],
                "comments": [],
            }, fallback_forum="探店分享"))
        save_shops()
        return "shop"

    existing_obstacle = next((o for o in obstacles_data if str(o.get("id")) == marker_id), None)
    proposed_status = contrib.get("proposedStatus")
    next_status = 0 if proposed_status in (None, 0, "0", "active") else 2
    if existing_obstacle:
        if contrib.get("content"):
            existing_obstacle["description"] = contrib.get("content")
        if contrib.get("markerTitle"):
            existing_obstacle["title"] = contrib.get("markerTitle")
        existing_obstacle["status"] = next_status
        existing_obstacle["lastVerifiedAt"] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
        _append_unique_image(existing_obstacle, contrib.get("imageUrl"))
        existing_obstacle.update(_normalize_region_fields(base_payload, fallback_forum="障碍论坛"))
    else:
        obstacles_data.append(_normalize_region_fields({
            **base_payload,
            "type": 3,
            "radius": 20.0,
            "status": next_status,
            "clearCount": 0,
            "lastVerifiedAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
            "imageUrls": [contrib["imageUrl"]] if contrib.get("imageUrl") else [],
            "comments": [],
        }, fallback_forum="障碍论坛"))
    save_obstacles()
    return "obstacle"


def _resolve_dispute_record(dispute: dict, resolution: str) -> dict:
    normalized_resolution = str(resolution or "").strip().lower()
    if normalized_resolution not in {"approve", "reject", "merge", "separate"}:
        raise HTTPException(status_code=400, detail="Unsupported resolution")

    contrib_id = str(dispute.get("candidateB") or "")
    marker_id = str(dispute.get("candidateA") or dispute.get("target_id") or "")
    contrib = next((c for c in contributions_data if str(c.get("id")) == contrib_id), None) if contrib_id else None

    if normalized_resolution in {"approve", "merge", "separate"} and contrib:
        _create_or_update_marker_from_contribution(contrib)
        contributions_data[:] = [c for c in contributions_data if str(c.get("id")) != contrib_id]
        save_contributions()
    elif normalized_resolution == "reject" and contrib:
        contributions_data[:] = [c for c in contributions_data if str(c.get("id")) != contrib_id]
        save_contributions()

    if marker_id:
        for obstacle in obstacles_data:
            if str(obstacle.get("id")) == marker_id:
                obstacle["disputeStatus"] = None
                break
        for shop in shops_data:
            if str(shop.get("id")) == marker_id:
                shop["disputeStatus"] = None
                break

    dispute["status"] = "resolved"
    dispute["resolution"] = normalized_resolution
    dispute["resolved_at"] = time.time()
    return _format_dispute_payload(dispute)


load_disputes()

def load_contributions():
    global contributions_data
    if os.path.exists(CONTRIBUTIONS_FILE):
        try:
            with open(CONTRIBUTIONS_FILE, "r", encoding="utf-8") as f:
                contributions_data = json.load(f)
        except Exception as e:
            print(f"Error loading contributions: {e}")
            contributions_data = []

    contributions_data = [_normalize_region_fields(c) for c in contributions_data]

def save_contributions():
    try:
        with open(CONTRIBUTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(contributions_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving contributions: {e}")

load_contributions()

# --- Obstacle Management ---
OBSTACLES_FILE = os.path.join(CURRENT_DIR, "obstacles.json")
obstacles_data = []

def load_obstacles():
    global obstacles_data
    changed = False
    if os.path.exists(OBSTACLES_FILE):
        try:
            with open(OBSTACLES_FILE, "r", encoding="utf-8") as f:
                obstacles_data = json.load(f)
        except Exception as e:
            print(f"Error loading obstacles: {e}")
            obstacles_data = []

    cleaned = []
    for obs in obstacles_data:
        obs = _normalize_region_fields(obs, fallback_forum="障碍论坛")
        obs_id = str(obs.get("id", ""))
        if obs_id.startswith("obs_mock_"):
            changed = True
            continue
        cleaned.append(obs)
    obstacles_data = cleaned

    if changed:
        save_obstacles()

def save_obstacles():
    try:
        with open(OBSTACLES_FILE, "w", encoding="utf-8") as f:
            json.dump(obstacles_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving obstacles: {e}")

load_obstacles()

# --- Shop Management ---
# SHOPS_FILE = os.path.join(CURRENT_DIR, "shops.json")
# 优先使用数据库
DB_PATH = os.path.join(CURRENT_DIR, "users.db")

shops_data = []

def load_shops():
    global shops_data
    # 优先从数据库加载
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # 检查 shops 表是否存在
            try:
                c.execute("SELECT id, title, description, lat, lng, type, status, created_at FROM shops")
                rows = c.fetchall()
                db_shops = []
                for r in rows:
                    shop = {
                        "id": r[0],
                        "title": r[1],
                        "description": r[2],
                        "lat": r[3],
                        "lng": r[4],
                        "type": r[5] if r[5] is not None else 4,
                        "status": r[6] if r[6] is not None else 0,
                        "createdAt": r[7] if r[7] else time.time(),
                        "imageUrls": [],
                        "regionCode": "default",
                        "regionName": "默认地区",
                        "cityCode": "",
                        "cityName": "",
                        "forumKey": "默认论坛",
                        "forumName": "默认论坛",
                        "targetType": "shop",
                    }
                    # 尝试加载图片
                    try:
                        c.execute("SELECT image_url FROM shop_images WHERE shop_id=?", (r[0],))
                        imgs = c.fetchall()
                        shop["imageUrls"] = [i[0] for i in imgs]
                    except:
                        pass
                    db_shops.append(shop)
                shops_data = db_shops
                print(f"✅ DB Read Success: Found {len(shops_data)} shops")
                conn.close()
                return
            except sqlite3.OperationalError:
                print("⚠️ Table 'shops' not found in DB, falling back to JSON")
        except Exception as e:
            print(f"❌ DB Error loading shops: {e}")

    # Fallback to JSON
    SHOPS_FILE = os.path.join(CURRENT_DIR, "shops.json")
    if os.path.exists(SHOPS_FILE):
        try:
            with open(SHOPS_FILE, "r", encoding="utf-8") as f:
                shops_data = [_normalize_region_fields(s) for s in json.load(f)]
        except Exception as e:
            print(f"Error loading shops JSON: {e}")
            shops_data = []

def save_shops():
    # Save to DB if possible
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Ensure table
            c.execute('''CREATE TABLE IF NOT EXISTS shops
                         (id TEXT PRIMARY KEY, title TEXT, description TEXT, lat REAL, lng REAL, type INTEGER, status INTEGER, created_at REAL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS shop_images
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id TEXT, image_url TEXT)''')
            
            for s in shops_data:
                c.execute("REPLACE INTO shops (id, title, description, lat, lng, type, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (s['id'], s['title'], s.get('description'), s.get('lat'), s.get('lng'), s.get('type', 4), s.get('status', 0), s.get('createdAt')))
                
                # Update images (Delete all and re-add for simplicity)
                c.execute("DELETE FROM shop_images WHERE shop_id=?", (s['id'],))
                if s.get('imageUrls'):
                    for url in s['imageUrls']:
                        c.execute("INSERT INTO shop_images (shop_id, image_url) VALUES (?, ?)", (s['id'], url))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error saving shops to DB: {e}")

    # Also save to JSON as backup
    SHOPS_FILE = os.path.join(CURRENT_DIR, "shops.json")
    try:
        with open(SHOPS_FILE, "w", encoding="utf-8") as f:
            json.dump(shops_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving shops JSON: {e}")

# --- User Management ---
USERS_FILE = os.path.join(CURRENT_DIR, "users.json")

# FORCE LOCAL DB PATH (Unified Environment)
DB_PATH = os.path.join(CURRENT_DIR, "users.db")
print(f"USING DB PATH: {DB_PATH}")

def get_db_connection():
    """Helper to get a robust DB connection"""
    try:
        # check_same_thread=False is needed for FastAPI async/threading
        conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
        return conn
    except Exception as e:
        print(f"CRITICAL: Failed to connect to DB at {DB_PATH}: {e}")
        raise e

users_data = []

# 缓存 Users 数据，避免频繁 IO
_users_cache = {
    "data": [],
    "last_loaded": 0
}

def load_users():
    global users_data, _users_cache
    
    # 简单的缓存策略：5秒内不重载
    if time.time() - _users_cache["last_loaded"] < 5.0 and _users_cache["data"]:
        users_data = _users_cache["data"]
        return

    print(f"--- Loading Users from {DB_PATH} ---")
    
    db_users = []
    
    # 1. Direct DB Read
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Check columns to construct query dynamically or use try-except
            # We want: email, nickname, avatar_path, created_at, status, trust_score
            try:
                c.execute("SELECT email, nickname, avatar_path, created_at, status, trust_score FROM users")
                rows = c.fetchall()
                print(f"✅ DB Read Success (Full Schema): Found {len(rows)} users")
                
                for r in rows:
                    user = {
                        "id": r[0],
                        "nickname": r[1] if r[1] else r[0].split('@')[0],
                        "avatar": r[2],
                        "status": r[4] if r[4] else "active",
                        "createdAt": r[3] if r[3] else time.time(),
                        "trust_score": r[5] if r[5] is not None else 100
                    }
                    db_users.append(user)
            except sqlite3.OperationalError:
                # Fallback for older schema
                print("⚠️ DB Schema mismatch (missing columns), falling back to basic schema")
                c.execute("SELECT email, nickname, avatar_path, created_at FROM users")
                rows = c.fetchall()
                print(f"✅ DB Read Success (Basic Schema): Found {len(rows)} users")
                
                for r in rows:
                    user = {
                        "id": r[0],
                        "nickname": r[1] if r[1] else r[0].split('@')[0],
                        "avatar": r[2],
                        "status": "active", # Default
                        "createdAt": r[3] if r[3] else time.time(),
                        "trust_score": 100 # Default
                    }
                    db_users.append(user)
                    
            conn.close()
        except Exception as e:
            print(f"❌ DB Error: {e}")
    else:
        print(f"❌ DB File Not Found at {DB_PATH}")
    
    # --- FALLBACK / MERGE WITH JSON ---
    # If DB is empty, try loading from JSON
    if not db_users:
        print("⚠️ DB is empty, attempting to load from users.json fallback")
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, "r", encoding="utf-8") as f:
                    json_users = json.load(f)
                    print(f"✅ Loaded {len(json_users)} users from JSON fallback")
                    db_users = json_users
                    # Optional: Sync back to DB immediately?
                    # Let's do it lazily or via save_users later
            except Exception as e:
                print(f"Error loading users.json: {e}")
    
    users_data = db_users
    # Sort by createdAt desc
    users_data.sort(key=lambda x: x.get('createdAt', 0), reverse=True)
    
    # Update cache
    _users_cache["data"] = users_data
    _users_cache["last_loaded"] = time.time()

def save_users():
    # 1. Save to JSON
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving users.json: {e}")
        
    # 2. Sync to DB (Update only - nickname, avatar, trust_score if column exists)
    # Note: DB might not have trust_score column yet. We will just use JSON for trust score for now.
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Ensure table exists (create if not) - simplified for safety
            # In production, we assume schema is managed elsewhere, but for robustness:
            c.execute('''CREATE TABLE IF NOT EXISTS users
                         (email TEXT PRIMARY KEY, nickname TEXT, avatar_path TEXT, created_at REAL, trust_score INTEGER, status TEXT)''')

            # Check if columns exist (simple migration)
            try:
                c.execute("SELECT status FROM users LIMIT 1")
            except:
                print("Adding 'status' column to users table")
                c.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
            
            try:
                c.execute("SELECT trust_score FROM users LIMIT 1")
            except:
                print("Adding 'trust_score' column to users table")
                c.execute("ALTER TABLE users ADD COLUMN trust_score INTEGER DEFAULT 100")
            
            for u in users_data:
                # Update nickname, avatar, status, trust_score
                # We use REPLACE or UPDATE. Since ID is primary key (email), let's try UPDATE first, then INSERT if needed?
                # Actually, simpler to just UPDATE existing ones. New ones are added via registration (not here).
                # But for sync_users_from_contributions, we might have new ones.
                
                # Check if exists
                c.execute("SELECT email FROM users WHERE email=?", (u['id'],))
                if c.fetchone():
                    c.execute("UPDATE users SET nickname=?, avatar_path=?, status=?, trust_score=? WHERE email=?", 
                              (u.get('nickname'), u.get('avatar'), u.get('status', 'active'), u.get('trust_score', 100), u['id']))
                else:
                    c.execute("INSERT INTO users (email, nickname, avatar_path, status, trust_score, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                              (u['id'], u.get('nickname'), u.get('avatar'), u.get('status', 'active'), u.get('trust_score', 100), u.get('createdAt', time.time())))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error syncing to users.db: {e}")

load_users()

# --- Helper for Status Update ---
def update_user_status_in_db(user_id, status):
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE users SET status=? WHERE email=?", (status, user_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error updating user status in DB: {e}")
            return False
    return False

# Helper to sync users from contributions (Since we don't have a registration system yet)
# We will extract unique users from contributions
def sync_users_from_contributions():
    global users_data
    # Map existing users for update
    users_map = {u['id']: u for u in users_data}
    
    # contributions_data is ordered Newest -> Oldest
    for c in contributions_data:
        uid = c['userId']
        
        # Only add if missing (DB is source of truth now)
        if uid not in users_map:
            # Add new user
            new_user = {
                "id": uid,
                "nickname": c['userNickname'],
                "avatar": c.get('userAvatar'),
                "status": "active", # active, banned
                "createdAt": c['createdAt'],
                "trust_score": 100
            }
            users_data.append(new_user)
            users_map[uid] = new_user
            
    save_users()

def get_user_trust_score(user_id):
    user = next((u for u in users_data if u["id"] == user_id), None)
    return user.get("trust_score", 100) if user else 100


# 尝试导入 YOLO
try:
    from ultralytics import YOLO
    HAS_YOLO = True
except ImportError:
    print("警告: 未安装 ultralytics，将无法加载真实模型")
    HAS_YOLO = False

# ---------------------------
# --- Volcengine RTC Integration ---
# (DISABLED FOR PURE VIDEO MODE)

# import hashlib
# import hmac
# import datetime
# import requests
# 
# def sign(key, msg):
#     return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()
# 
# def getSignatureKey(key, dateStamp, regionName, serviceName):
#     kDate = sign(key.encode('utf-8'), dateStamp)
#     kRegion = sign(kDate, regionName)
#     kService = sign(kRegion, serviceName)
#     kSigning = sign(kService, "request")
#     return kSigning
# 
# # Load secrets from file or env
# RTC_AK = getattr(config_secrets, "RTC_AK", "AKLTN2UxMTE4MDI1N2FhNDU5Nzg2YWFiNjM3MWVjNjk1YjA")
# RTC_SK = getattr(config_secrets, "RTC_SK", "TkRBeU16QmpaVEkwT1RBeU5EZ3daVGcyWW1ZMFptSmpOR1l3WkRNMk5USQ==")
# RTC_APPID = getattr(config_secrets, "RTC_APPID", "69a14eebe963ae01754ea81b")
# 
# @app.post("/api/rtc/start")
# async def start_rtc_task(req: dict):
#     """
#     Start Cloud AI Voice Chat Task via Volcengine API
#     """
#     return {"status": "disabled", "message": "AI features are disabled in this mode."}
# 
# @app.get("/api/rtc/token")
# async def get_rtc_token(room_id: str, user_id: str):
#     """
#     Generate RTC Token for client join
#     """
#     return {
#         "appId": RTC_APPID,
#         "roomId": room_id,
#         "userId": user_id,
#         "token": "mock_token_disabled"
#     }

# 尝试导入 YOLO
try:
    from ultralytics import YOLO
    HAS_YOLO = True
except ImportError:
    print("警告: 未安装 ultralytics，将无法加载真实模型")
    HAS_YOLO = False

# app = FastAPI() # Removed duplicate definition at the bottom if exists

load_shops() # Ensure shops are loaded after DB_PATH is defined

def bootstrap_domain_data():
    """
    Demo/bootstrap 数据默认禁用，避免正式链路被系统自动造数污染。
    仅在显式开启 ENABLE_BOOTSTRAP_DOMAIN_DATA=1 时才执行。
    """
    if os.getenv("ENABLE_BOOTSTRAP_DOMAIN_DATA", "0") != "1":
        print("ℹ️ bootstrap_domain_data skipped (strict real-data mode)")
        return

    global contributions_data, disputes_data
    changed_contrib = False
    changed_disputes = False

    has_shop_posts = any(
        str(_normalize_region_fields(dict(c)).get("targetType") or "").lower() == "shop"
        for c in contributions_data
    )
    if (not has_shop_posts) and shops_data:
        for idx, shop in enumerate(shops_data):
            s = _normalize_region_fields(dict(shop))
            created_at = s.get("createdAt") or s.get("created_at") or time.time()
            try:
                created_at = float(created_at)
            except Exception:
                created_at = time.time()

            post = _normalize_region_fields({
                "id": f"shop_bootstrap_{s.get('id', idx)}",
                "markerId": str(s.get("id", f"shop_{idx}")),
                "markerTitle": s.get("title", "店铺标注"),
                "type": 0,
                "userId": "system_bootstrap",
                "userNickname": "系统初始化",
                "content": s.get("description") or "由店铺标注自动生成的社区话题。",
                "imageUrl": (s.get("imageUrls") or [None])[0],
                "zones": [s.get("forumName") or "探店分享"],
                "proposedStatus": None,
                "lat": s.get("lat"),
                "lng": s.get("lng"),
                "regionCode": s.get("regionCode"),
                "regionName": s.get("regionName"),
                "cityCode": s.get("cityCode"),
                "cityName": s.get("cityName"),
                "forumKey": s.get("forumKey") or "share",
                "forumName": s.get("forumName") or "探店分享",
                "targetType": "shop",
                "createdAt": created_at,
                "upvotes": [],
                "downvotes": [],
                "comments": [],
            })
            contributions_data.append(post)
            changed_contrib = True

    has_obstacle_disputes = any(
        str(d.get("target_type") or d.get("targetType") or "").lower() == "obstacle"
        for d in disputes_data
    )
    if (not has_obstacle_disputes) and obstacles_data:
        seed_obstacle = None
        for o in obstacles_data:
            if int(o.get("status", 0)) != 2:
                seed_obstacle = _normalize_region_fields(dict(o), fallback_forum="障碍争议")
                break
        if seed_obstacle:
            dispute = _normalize_region_fields({
                "id": f"disp_bootstrap_{seed_obstacle.get('id', 'obs')}",
                "status": "pending",
                "target_id": seed_obstacle.get("id"),
                "target_type": "obstacle",
                "reporter": "system_bootstrap",
                "reason": "初始化：基于现有障碍物标注生成的待处理争议样例",
                "created_at": time.time(),
                "regionCode": seed_obstacle.get("regionCode"),
                "regionName": seed_obstacle.get("regionName"),
                "cityCode": seed_obstacle.get("cityCode"),
                "cityName": seed_obstacle.get("cityName"),
                "forumKey": "障碍争议",
                "forumName": "障碍争议",
                "targetType": "obstacle",
            }, fallback_forum="障碍争议")
            disputes_data.insert(0, dispute)
            changed_disputes = True

    if changed_contrib:
        save_contributions()
        print(f"✅ Bootstrap: generated {len([c for c in contributions_data if str(c.get('id','')).startswith('shop_bootstrap_')])} shop community posts")
    if changed_disputes:
        save_disputes()
        print("✅ Bootstrap: generated obstacle dispute seed")

# 严格真实链路下不默认注入 bootstrap 数据
bootstrap_domain_data()



# Add a debug endpoint to diagnose from browser
@app.get("/api/admin/debug_db")
async def debug_db():
    status = {
        "path": DB_PATH,
        "exists": os.path.exists(DB_PATH),
        "abs_path": os.path.abspath(DB_PATH),
        "permissions": oct(os.stat(DB_PATH).st_mode)[-3:] if os.path.exists(DB_PATH) else "N/A",
        "size": os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
        "users_in_memory": len(users_data)
    }
    
    try:
        if status["exists"]:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT count(*) FROM users")
            status["db_count"] = c.fetchone()[0]
            
            c.execute("SELECT email FROM users LIMIT 5")
            status["sample_emails"] = [r[0] for r in c.fetchall()]
            conn.close()
    except Exception as e:
        status["error"] = str(e)
        
    return status

@app.post("/api/debug/set_user_trust")
async def set_user_trust(data: dict = Body(...)):
    uid = data.get('userId')
    score = data.get('score')
    
    found = False
    for u in users_data:
        if u['id'] == uid:
            u['trust_score'] = score
            found = True
            break
            
    if not found:
        # Create temp user for testing
        users_data.append({
            "id": uid, 
            "nickname": f"TestUser_{uid}", 
            "trust_score": score, 
            "createdAt": time.time(),
            "status": "active"
        })
        
    return {"success": True, "userId": uid, "newScore": score}

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"Validation Error for {request.url}: {exc}")
    try:
        body = await request.json()
        print(f"Request Body: {body}")
    except:
        print("Could not read body")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

# 挂载静态文件目录，用于服务前端页面
app.mount("/static", StaticFiles(directory=os.path.join(CURRENT_DIR, "static")), name="static")

# 挂载 Uploads 目录 (头像等)
UPLOADS_DIR = os.path.join(os.path.dirname(DB_PATH), "uploads")
if os.path.exists(UPLOADS_DIR):
    print(f"Mounting Uploads Dir: {UPLOADS_DIR}")
    app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
else:
    print(f"Warning: Uploads directory not found at {UPLOADS_DIR}")

def _normalize_ai_process(item: dict, marker_type: str) -> dict:
    pending = item.get("pendingContribution") if isinstance(item.get("pendingContribution"), dict) else {}
    existing_process = item.get("ai_process") if isinstance(item.get("ai_process"), dict) else {}
    if not existing_process and isinstance(pending.get("ai_process"), dict):
        existing_process = pending.get("ai_process")
    existing_steps = existing_process.get("flow_steps") if isinstance(existing_process.get("flow_steps"), list) else []
    has_real_ai = bool(
        item.get("ai_analysis_result")
        or pending.get("ai_analysis_result")
        or item.get("blind_guidance_summary")
        or pending.get("blind_guidance_summary")
        or item.get("voice_prompt")
        or pending.get("voice_prompt")
        or existing_steps
    )
    confidence = item.get("ai_confidence")
    if confidence is None:
        confidence = pending.get("ai_confidence")
    if confidence is None:
        confidence = item.get("confidence")

    return {
        "has_real_ai": has_real_ai,
        "confidence": confidence,
        "flow_steps": existing_steps if has_real_ai else [],
    }


def _build_agent_flow_steps(workflow_logs: list, success: bool = True) -> list:
    if not isinstance(workflow_logs, list):
        return []

    stage_defs = [
        ("证据整理智能体", ["读取统一标注数据", "装载图片输入", "当前标注没有图片"]),
        ("审核分析智能体", ["审核分析智能体", "分析模型调用失败"]),
        ("审核决策智能体", ["审核决策智能体", "图文校验", "审核决策"]),
        ("归档处置智能体", ["归档处置智能体", "自动审核触发", "转人工复核", "审核驳回", "待审核投稿已同步通过", "待审核投稿已转正入库", "保存失败", "未在主库中找到对应记录"]),
        ("导盲摘要智能体", ["导盲摘要智能体", "导盲提示", "盲人摘要", "位置摘要"]),
    ]

    flow_steps = []
    for stage_name, keywords in stage_defs:
        details = [log for log in workflow_logs if any(keyword in str(log) for keyword in keywords)]
        if not details:
            continue

        status = "done"
        if any(("失败" in str(log)) or ("错误" in str(log)) for log in details):
            status = "error"
        elif not success and stage_name in ["多模态理解智能体", "审核决策智能体", "归档处置智能体"]:
            status = "error" if details else "pending"

        flow_steps.append({
            "step": stage_name,
            "status": status,
            "detail": "\n".join(details),
        })

    return flow_steps


def _merge_analysis_into_description(description: str, analysis_text: str) -> str:
    base = str(description or "").strip()
    analysis = str(analysis_text or "").strip()
    if not analysis:
        return base

    marker = "[AI检测]:"
    old_marker = "AI分析："
    idx = base.find(marker)
    if idx != -1:
        base = base[:idx].strip()
    else:
        idx = base.find(old_marker)
        if idx != -1:
            base = base[:idx].strip()

    if not base:
        return f"{marker} {analysis}"
    return f"{base}\n{marker} {analysis}"


def _extract_annotation_evidence(annotation: dict) -> dict:
    pending = annotation.get("pendingContribution") if isinstance(annotation.get("pendingContribution"), dict) else {}
    image_urls = []
    if pending.get("imageUrl"):
        image_urls.append(pending.get("imageUrl"))
    if isinstance(pending.get("imageUrls"), list):
        image_urls.extend([x for x in pending.get("imageUrls", []) if x])
    if not image_urls:
        image_urls = [x for x in (annotation.get("imageUrls") or []) if x]

    text_parts = []
    if pending.get("content"):
        text_parts.append(str(pending.get("content")).strip())
    all_comments = annotation.get("all_comments") or []
    text_parts.extend([str(c.get("content", "")).strip() for c in all_comments if str(c.get("content", "")).strip()])
    if not text_parts:
        for key in ("description", "title"):
            value = str(annotation.get(key, "")).strip()
            if value:
                text_parts.append(value)

    text_parts = [x for x in text_parts if x and "[AI检测]:" not in x and "AI分析：" not in x]
    return {
        "has_image": bool(image_urls),
        "has_text": bool(text_parts),
        "image_count": len(image_urls),
        "text_count": len(text_parts),
    }


def _normalize_bool_flag(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "matched", "same"}:
        return True
    if normalized in {"false", "0", "no", "n", "different", "mismatch"}:
        return False
    return None


def _has_positive_match_signal(validation: dict) -> bool:
    text = " ".join([
        str(validation.get("match_reason") or ""),
        str(validation.get("summary") or ""),
        str(validation.get("blind_audio_desc") or ""),
    ])
    normalized = str(text).strip()
    if not normalized:
        return False
    positive_keywords = ["图文一致", "标注合理", "证据充分", "匹配合理", "店名一致", "招牌一致", "可以通过", "审核通过"]
    negative_keywords = ["不匹配", "不一致", "错误标注", "待确认", "待核实", "人工复核", "存疑"]
    return any(keyword in normalized for keyword in positive_keywords) and not any(keyword in normalized for keyword in negative_keywords)


def _decide_annotation_review(annotation: dict, validation: dict) -> dict:
    evidence = _extract_annotation_evidence(annotation)
    match_status = str(validation.get("match_status") or "uncertain").lower()
    confidence_score = validation.get("confidence_score")
    entity_name_match = _normalize_bool_flag(validation.get("entity_name_match"))
    business_match = _normalize_bool_flag(validation.get("business_match"))
    manual_review_required = _normalize_bool_flag(validation.get("manual_review_required"))
    evidence_level = str(validation.get("evidence_level") or "").strip().lower()
    marker_type = str(annotation.get("marker_type") or annotation.get("source") or "").lower()
    try:
        confidence_score = float(confidence_score) if confidence_score is not None else None
    except Exception:
        confidence_score = None

    if not evidence["has_image"] or not evidence["has_text"]:
        return {
            "decision": "manual_review",
            "reason": "证据不完整，仅图片或仅文字，需人工复核",
            "evidence": evidence,
        }

    if match_status == "mismatch":
        return {
            "decision": "reject",
            "reason": "图文不匹配，AI 审核不通过",
            "evidence": evidence,
        }

    if match_status == "match":
        return {
            "decision": "approve",
            "reason": "图文匹配且证据完整，自动审核通过",
            "evidence": evidence,
        }

    # 对店铺类标注更看重“店名/业态可对上”，避免明显匹配样本落入人工复核。
    if "shop" in marker_type and entity_name_match is True and (business_match is not False):
        return {
            "decision": "approve",
            "reason": "店名与业态匹配，图文证据充分，自动审核通过",
            "evidence": evidence,
        }

    if manual_review_required is False and confidence_score is not None and confidence_score >= 75:
        return {
            "decision": "approve",
            "reason": f"图文匹配度较高({int(confidence_score)}分)且无需人工复核，自动审核通过",
            "evidence": evidence,
        }

    if confidence_score is not None and confidence_score >= 85:
        return {
            "decision": "approve",
            "reason": f"图文匹配度较高({int(confidence_score)}分)且证据完整，自动审核通过",
            "evidence": evidence,
        }

    if evidence_level in {"strong", "high"} and _has_positive_match_signal(validation):
        return {
            "decision": "approve",
            "reason": "图文一致且证据强，自动审核通过",
            "evidence": evidence,
        }

    return {
        "decision": "manual_review",
        "reason": "图文关系不确定，需人工复核",
        "evidence": evidence,
    }


def _normalize_agent_decision(parsed: dict, fallback: dict) -> dict:
    decision = str(parsed.get("decision") or parsed.get("review_decision") or fallback.get("decision") or "manual_review").strip().lower()
    if decision not in {"approve", "manual_review", "reject"}:
        decision = fallback.get("decision", "manual_review")

    reason = _clean_short_text(parsed.get("reason") or parsed.get("decision_reason") or fallback.get("reason") or "", 80)
    archive_instruction = _clean_short_text(
        parsed.get("archive_instruction")
        or parsed.get("next_agent_prompt")
        or parsed.get("next_step")
        or "",
        120,
    )
    return {
        "decision": decision,
        "reason": reason or fallback.get("reason") or "需要人工复核",
        "archive_instruction": archive_instruction,
    }


def _merge_review_decision(policy_decision: dict, agent_decision: dict) -> dict:
    final_decision = dict(policy_decision or {})
    if not agent_decision:
        return final_decision

    final_decision["agent_decision"] = agent_decision.get("decision")
    final_decision["agent_reason"] = agent_decision.get("reason")
    final_decision["archive_instruction"] = agent_decision.get("archive_instruction") or ""

    # 最终入库仍然受后端策略保护：高匹配直过、证据不足待人工、不匹配驳回
    if final_decision.get("decision") == agent_decision.get("decision") and agent_decision.get("reason"):
        final_decision["reason"] = agent_decision["reason"]

    return final_decision


def _mark_target_pending_review(target: dict, analysis_text: str, validation: dict, ai_process: dict, fallback_description: str = ""):
    target["status"] = 0
    target["disputeStatus"] = "pending_review"
    target["ai_analysis_result"] = analysis_text
    target["ai_validation"] = validation
    target["ai_confidence"] = validation.get("confidence_score")
    target["ai_process"] = ai_process
    target["voice_prompt"] = validation.get("blind_guidance_summary") or validation.get("blind_audio_desc") or target.get("voice_prompt", "")
    target["blind_guidance_summary"] = validation.get("blind_guidance_summary") or target.get("blind_guidance_summary", "")
    if analysis_text:
        target["description"] = _merge_analysis_into_description(target.get("description", fallback_description), analysis_text)
    if "(待核实)" not in str(target.get("title", "")):
        target["title"] = f"{str(target.get('title', '')).strip()} (待核实)".strip()


def _mark_target_rejected(target: dict, analysis_text: str, validation: dict, ai_process: dict, fallback_description: str = ""):
    target["status"] = 2
    target["disputeStatus"] = None
    target["ai_analysis_result"] = analysis_text
    target["ai_validation"] = validation
    target["ai_confidence"] = validation.get("confidence_score")
    target["ai_process"] = ai_process
    target["voice_prompt"] = validation.get("blind_guidance_summary") or validation.get("blind_audio_desc") or target.get("voice_prompt", "")
    target["blind_guidance_summary"] = validation.get("blind_guidance_summary") or target.get("blind_guidance_summary", "")
    if analysis_text:
        target["description"] = _merge_analysis_into_description(target.get("description", fallback_description), analysis_text)


def _finalize_pending_contribution(
    contribution_id: str,
    analysis_text: str = "",
    validation: Optional[dict] = None,
    ai_process: Optional[dict] = None,
) -> bool:
    global contributions_data, obstacles_data, shops_data

    contribution = next((c for c in contributions_data if str(c.get("id")) == str(contribution_id)), None)
    if not contribution:
        return False

    marker_id = str(contribution.get("markerId") or contribution.get("id"))
    contrib_type = contribution.get("type")
    now_str = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
    validation = validation or {}
    ai_process = ai_process or {"has_real_ai": bool(analysis_text), "flow_steps": []}

    def apply_ai_fields(target: dict, fallback_description: str = ""):
        target["status"] = 1
        target["disputeStatus"] = None
        target["ai_analysis_result"] = analysis_text
        target["ai_validation"] = validation
        target["ai_confidence"] = validation.get("confidence_score")
        target["ai_process"] = ai_process
        target["voice_prompt"] = validation.get("blind_guidance_summary") or validation.get("blind_audio_desc") or target.get("voice_prompt", "")
        target["blind_guidance_summary"] = validation.get("blind_guidance_summary") or target.get("blind_guidance_summary", "")
        target["lastVerifiedAt"] = now_str
        if "(待核实)" in str(target.get("title", "")):
            target["title"] = str(target.get("title", "")).replace("(待核实)", "").strip()
        if analysis_text:
            target["description"] = _merge_analysis_into_description(
                target.get("description", fallback_description),
                analysis_text
            )

    if contrib_type in (0, 4):
        existing_shop = next((s for s in shops_data if str(s.get("id")) == marker_id), None)
        if existing_shop:
            if contribution.get("imageUrl"):
                existing_shop.setdefault("imageUrls", [])
                if contribution["imageUrl"] not in existing_shop["imageUrls"]:
                    existing_shop["imageUrls"].append(contribution["imageUrl"])
            apply_ai_fields(existing_shop, contribution.get("content", ""))
        elif contribution.get("lat") is not None and contribution.get("lng") is not None:
            new_shop = {
                "id": marker_id,
                "lat": contribution.get("lat"),
                "lng": contribution.get("lng"),
                "type": 4,
                "createdAt": contribution.get("createdAt", now_str),
                "status": 1,
                "imageUrls": [contribution["imageUrl"]] if contribution.get("imageUrl") else contribution.get("imageUrls", []),
                "title": str(contribution.get("markerTitle", "未命名店铺")).replace("(待核实)", "").strip(),
                "description": contribution.get("content", ""),
                "comments": contribution.get("comments", []),
            }
            apply_ai_fields(new_shop, contribution.get("content", ""))
            shops_data.append(new_shop)
        save_shops()
    elif contrib_type == 1:
        existing_obstacle = next((o for o in obstacles_data if str(o.get("id")) == marker_id), None)
        if existing_obstacle:
            target_status = contribution.get("proposedStatus")
            if target_status is None:
                target_status = 1
            existing_obstacle["status"] = target_status
            apply_ai_fields(existing_obstacle, contribution.get("content", ""))
            save_obstacles()
    else:
        existing_obstacle = next((o for o in obstacles_data if str(o.get("id")) == marker_id), None)
        if existing_obstacle:
            if contribution.get("imageUrl"):
                existing_obstacle.setdefault("imageUrls", [])
                if contribution["imageUrl"] not in existing_obstacle["imageUrls"]:
                    existing_obstacle["imageUrls"].append(contribution["imageUrl"])
            apply_ai_fields(existing_obstacle, contribution.get("content", ""))
        elif contribution.get("lat") is not None and contribution.get("lng") is not None:
            new_obstacle = {
                "id": marker_id,
                "lat": contribution.get("lat"),
                "lng": contribution.get("lng"),
                "type": 3,
                "createdAt": contribution.get("createdAt", now_str),
                "status": 1,
                "imageUrls": [contribution["imageUrl"]] if contribution.get("imageUrl") else contribution.get("imageUrls", []),
                "title": str(contribution.get("markerTitle", "未命名标注")).replace("(待核实)", "").strip(),
                "description": contribution.get("content", ""),
                "comments": contribution.get("comments", []),
            }
            apply_ai_fields(new_obstacle, contribution.get("content", ""))
            obstacles_data.append(new_obstacle)
        save_obstacles()

    contributions_data = [x for x in contributions_data if str(x.get("id")) != str(contribution_id)]
    save_contributions()
    return True


def _update_pending_contribution_review_state(
    contribution_id: str,
    analysis_text: str = "",
    validation: Optional[dict] = None,
    ai_process: Optional[dict] = None,
    review_status: str = "pending_review",
) -> bool:
    global contributions_data
    validation = validation or {}
    ai_process = ai_process or {"has_real_ai": bool(analysis_text), "flow_steps": []}
    updated = False
    for contribution in contributions_data:
        if str(contribution.get("id")) != str(contribution_id):
            continue
        contribution["ai_analysis_result"] = analysis_text
        contribution["ai_validation"] = validation
        contribution["ai_confidence"] = validation.get("confidence_score")
        contribution["ai_process"] = ai_process
        contribution["voice_prompt"] = validation.get("blind_guidance_summary") or validation.get("blind_audio_desc") or contribution.get("voice_prompt", "")
        contribution["blind_guidance_summary"] = validation.get("blind_guidance_summary") or contribution.get("blind_guidance_summary", "")
        contribution["reviewStatus"] = review_status
        contribution["lastVerifiedAt"] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
        updated = True
        break
    if updated:
        save_contributions()
    return updated


def _normalize_spoken_text(text: str) -> str:
    return re.sub(r"[\s，。！？!?、,.;；:：]+", "", str(text or "")).strip().lower()


def _spoken_contains_any(text: str, keywords: list) -> bool:
    normalized = _normalize_spoken_text(text)
    return any(_normalize_spoken_text(keyword) in normalized for keyword in keywords)


def _strip_spoken_prefix(text: str, prefix: str) -> str:
    source = str(text or "").strip()
    normalized_prefix = _normalize_spoken_text(prefix)
    if not source or not normalized_prefix:
        return source
    for index in range(len(source)):
        candidate = source[index:]
        if _normalize_spoken_text(candidate).startswith(normalized_prefix):
            consumed = len(candidate)
            for inner_index in range(1, len(candidate) + 1):
                chunk = candidate[:inner_index]
                if _normalize_spoken_text(chunk) == normalized_prefix:
                    consumed = inner_index
                    break
            return source[:index] + candidate[consumed:]
    return source


def _voice_event_payload(client_id: str, event: str, state: Optional[dict] = None, text: str = "") -> dict:
    state = state or _get_or_create_device_state(client_id)
    return {
        "type": "voice_state",
        "client_id": client_id,
        "voice_mode": state.get("voice_mode", VOICE_DEFAULT_MODE),
        "voice_awake_until": state.get("voice_awake_until", 0.0),
        "voice_last_event": event,
        "voice_last_text": text,
        "voice_wake_source": state.get("voice_wake_source", ""),
        "timestamp": time.time(),
    }


def _set_voice_mode(client_id: str, mode: str, source: str, text: str = "") -> dict:
    state = _get_or_create_device_state(client_id)
    now = time.time()
    target_mode = "awake" if mode == "awake" else "standby"
    state["voice_mode"] = target_mode
    state["voice_mode_changed_at"] = now
    state["voice_last_event"] = source
    if target_mode == "awake":
        state["voice_awake_until"] = now + VOICE_AWAKE_TIMEOUT_SECONDS
        state["voice_last_wake_at"] = now
        state["voice_wake_source"] = source
    else:
        state["voice_awake_until"] = 0.0
    if text:
        if target_mode == "awake":
            state["voice_last_routed_text"] = text
        else:
            state["voice_last_ignored_text"] = text
    return state


def _refresh_voice_awake_window(client_id: str) -> dict:
    state = _get_or_create_device_state(client_id)
    if state.get("voice_mode") == "awake":
        state["voice_awake_until"] = time.time() + VOICE_AWAKE_TIMEOUT_SECONDS
    return state


def _sync_voice_mode_timeout(client_id: str) -> dict:
    state = _get_or_create_device_state(client_id)
    if state.get("voice_mode") == "awake":
        awake_until = float(state.get("voice_awake_until") or 0.0)
        if awake_until and time.time() > awake_until:
            _set_voice_mode(client_id, "standby", "timeout")
    return state


def _extract_wake_phrase_and_command(text: str) -> tuple[Optional[str], str]:
    stripped_text = str(text or "").strip()
    normalized_text = _normalize_spoken_text(stripped_text)
    for wake_word in VOICE_WAKE_WORDS:
        normalized_wake = _normalize_spoken_text(wake_word)
        if not normalized_wake or not normalized_text:
            continue
        wake_index = normalized_text.find(normalized_wake)
        if wake_index < 0 or wake_index > 3:
            continue
        remainder = _strip_spoken_prefix(stripped_text, wake_word).strip(" ，。！？!?、,.;；:：")
        return wake_word, remainder.strip()
    return None, stripped_text


def _evaluate_voice_gate(client_id: str, text: str) -> dict:
    state = _sync_voice_mode_timeout(client_id)
    stripped_text = str(text or "").strip()
    normalized_text = _normalize_spoken_text(stripped_text)
    if not stripped_text or len(normalized_text) < 2:
        return {"action": "ignore", "reason": "empty", "state": state, "text": ""}

    if _spoken_contains_any(stripped_text, EMERGENCY_ASSIST_KEYWORDS) or _spoken_contains_any(stripped_text, ASSIST_CANCEL_KEYWORDS):
        state["voice_last_routed_text"] = stripped_text
        state["voice_last_event"] = "emergency_bypass"
        return {"action": "route_text", "reason": "emergency_bypass", "state": state, "text": stripped_text}

    # 系统级语音命令在待机态也应直接放行，避免“能查坐标却不能直接导航”。
    if (
        _is_navigation_query(stripped_text)
        or _spoken_contains_any(stripped_text, ["我在哪", "我现在在哪", "当前位置", "当前在哪里", "我现在的位置"])
        or _spoken_contains_any(stripped_text, ["还有多远", "离目的地还有多远", "距离目的地还有多远", "还有多久到", "还有多少米"])
        or _spoken_contains_any(stripped_text, ["再说一遍", "重复一遍", "重复刚才的导航", "再重复一下", "刚才说什么", "上一条指令"])
        or _spoken_contains_any(stripped_text, ["有哪些常用地址", "我的常用地址", "常用地址有哪些", "路线记忆有哪些", "我保存了哪些地址"])
        or _extract_memory_alias_from_speech(stripped_text)
        or _parse_volume_control_intent(stripped_text)
    ):
        state["voice_last_routed_text"] = stripped_text
        state["voice_last_event"] = "system_command_bypass"
        return {"action": "route_text", "reason": "system_command_bypass", "state": state, "text": stripped_text}

    if not VOICE_STANDBY_ENABLED:
        routed_text = stripped_text
        state["voice_last_routed_text"] = routed_text
        return {"action": "route_text", "reason": "standby_disabled", "state": state, "text": routed_text}

    if _spoken_contains_any(stripped_text, VOICE_SLEEP_WORDS):
        state = _set_voice_mode(client_id, "standby", "sleep_command", stripped_text)
        return {"action": "sleep", "reason": "sleep_command", "state": state, "text": ""}

    wake_word, remainder = _extract_wake_phrase_and_command(stripped_text)

    if state.get("voice_mode") == "awake":
        _refresh_voice_awake_window(client_id)
        if wake_word and not remainder:
            return {"action": "wake_only", "reason": "wake_refresh", "state": state, "text": "", "wake_word": wake_word}
        routed_text = remainder if wake_word and remainder else stripped_text
        state["voice_last_routed_text"] = routed_text
        return {"action": "route_text", "reason": "already_awake", "state": state, "text": routed_text}

    if wake_word:
        state = _set_voice_mode(client_id, "awake", "voice", stripped_text)
        if remainder:
            state["voice_last_routed_text"] = remainder
            return {"action": "route_text", "reason": "wake_with_command", "state": state, "text": remainder, "wake_word": wake_word}
        return {"action": "wake_only", "reason": "wake_only", "state": state, "text": "", "wake_word": wake_word}

    state["voice_last_event"] = "ignored_standby"
    state["voice_last_ignored_text"] = stripped_text
    return {"action": "ignore", "reason": "standby_ignored", "state": state, "text": ""}


def _build_voice_state_snapshot(client_id: str) -> dict:
    state = _sync_voice_mode_timeout(client_id)
    now = time.time()
    awake_until = float(state.get("voice_awake_until") or 0.0)
    return {
        "voice_mode": state.get("voice_mode", VOICE_DEFAULT_MODE),
        "voice_last_event": state.get("voice_last_event", "init"),
        "voice_last_ignored_text": state.get("voice_last_ignored_text", ""),
        "voice_last_routed_text": state.get("voice_last_routed_text", ""),
        "voice_wake_source": state.get("voice_wake_source", ""),
        "voice_awake_remaining": max(0.0, round(awake_until - now, 1)) if awake_until else 0.0,
    }


def _parse_volume_control_intent(spoken_text: str) -> Optional[dict]:
    text = str(spoken_text or "").strip()
    normalized = _normalize_spoken_text(text)
    if not text or not normalized:
        return None

    if _spoken_contains_any(text, ["取消静音", "解除静音", "恢复声音", "打开声音", "打开音量"]):
        return {"mode": "unmute", "value": None, "reply": "已尝试恢复音量。"}

    if _spoken_contains_any(text, ["静音", "关闭声音", "关闭音量", "别出声", "没有声音"]):
        return {"mode": "mute", "value": None, "reply": "已尝试静音。"}

    if _spoken_contains_any(text, ["最大音量", "声音最大", "音量最大"]):
        return {"mode": "absolute", "value": 100, "reply": "已将音量调到最大。"}

    if _spoken_contains_any(text, ["最小音量", "声音最小", "音量最小"]):
        return {"mode": "absolute", "value": 0, "reply": "已将音量调到最小。"}

    absolute_match = re.search(r"(?:音量|声音).{0,6}(?:调到|设为|设置为|设置到|开到|变成)?\s*(\d{1,3})\s*[%％]?", text)
    if absolute_match:
        value = max(0, min(100, int(absolute_match.group(1))))
        return {"mode": "absolute", "value": value, "reply": f"已尝试将音量调到 {value}%。"}

    if _spoken_contains_any(text, ["调大音量", "音量大一点", "声音大一点", "增大音量", "提高音量", "把音量调大", "把声音调大"]):
        return {"mode": "relative", "value": 10, "reply": "已尝试调高音量。"}

    if _spoken_contains_any(text, ["调小音量", "音量小一点", "声音小一点", "减小音量", "降低音量", "把音量调小", "把声音调小"]):
        return {"mode": "relative", "value": -10, "reply": "已尝试调低音量。"}

    relative_match = re.search(r"(?:音量|声音).{0,6}(提高|调高|增大|加大|调大|降低|调低|减小|调小).{0,4}(\d{1,3})", text)
    if relative_match:
        delta = max(1, min(100, int(relative_match.group(2))))
        if relative_match.group(1) in {"降低", "调低", "减小", "调小"}:
            delta = -delta
        direction = "调高" if delta > 0 else "调低"
        return {"mode": "relative", "value": delta, "reply": f"已尝试{direction}音量 {abs(delta)}%。"}

    return None


def _find_route_memory_match(client_id: str, spoken_text: str) -> Optional[dict]:
    candidates_to_try = [str(spoken_text or "").strip()]
    extracted = _extract_navigation_destination(spoken_text)
    if extracted and extracted not in candidates_to_try:
        candidates_to_try.append(extracted)

    exact_pool = []
    fallback_pool = []
    for mem in route_memories_data:
        alias = str(mem.get("alias") or "").strip()
        alias_norm = _normalize_spoken_text(alias)
        if not alias_norm:
            continue
        device_keys = {
            str(mem.get("device_key") or ""),
            str(mem.get("device_id") or ""),
            str(mem.get("client_id") or ""),
            str(mem.get("user_id") or ""),
        }
        score = 0
        if client_id in device_keys:
            score += 3
        for text in candidates_to_try:
            normalized = _normalize_spoken_text(text)
            if not normalized:
                continue
            if alias_norm == normalized:
                score = max(score, 7 if client_id in device_keys else 4)
            elif alias_norm in normalized or normalized in alias_norm:
                score = max(score, 5 if client_id in device_keys else 2)
        if score <= 0:
            continue
        candidate = {**mem, "_score": score}
        if client_id in device_keys:
            exact_pool.append(candidate)
        else:
            fallback_pool.append(candidate)

    pool = exact_pool or fallback_pool
    if not pool:
        return None
    pool.sort(key=lambda x: (-x["_score"], -float(x.get("created_at") or 0)))
    return pool[0]


def _get_device_route_memories(client_id: str) -> list:
    result = []
    for mem in route_memories_data:
        keys = {
            str(mem.get("device_key") or ""),
            str(mem.get("device_id") or ""),
            str(mem.get("client_id") or ""),
            str(mem.get("user_id") or ""),
        }
        if client_id in keys:
            result.append(mem)
    result.sort(key=lambda x: -float(x.get("created_at") or 0))
    return result


def _find_local_destination_match(spoken_text: str) -> Optional[dict]:
    candidates_to_try = [str(spoken_text or "").strip()]
    extracted = _extract_navigation_destination(spoken_text)
    if extracted and extracted not in candidates_to_try:
        candidates_to_try.append(extracted)

    candidates = []
    for shop in shops_data:
        title = str(shop.get("title") or "").strip()
        title_norm = _normalize_spoken_text(title)
        lat = shop.get("lat")
        lng = shop.get("lng")
        if not title_norm or lat is None or lng is None:
            continue

        score = 0
        for text in candidates_to_try:
            normalized = _normalize_spoken_text(text)
            if not normalized:
                continue
            if title_norm == normalized:
                score = max(score, 100)
            elif title_norm in normalized:
                score = max(score, 88)
            elif normalized in title_norm:
                score = max(score, 80)
            elif any(token and token in title_norm for token in re.split(r"\s+", normalized) if token):
                score = max(score, 60)
        if score <= 0:
            continue

        candidates.append({
            "name": title,
            "lng": float(lng),
            "lat": float(lat),
            "source": "custom_map_shop",
            "score": score,
            "description": str(shop.get("description") or "").strip(),
            "voice_prompt": str(shop.get("voice_prompt") or "").strip(),
            "blind_guidance_summary": str(shop.get("blind_guidance_summary") or "").strip(),
            "shop_id": str(shop.get("id") or "").strip(),
        })

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item["score"], item["name"]))
    return candidates[0]


def _normalize_route_memory_item(mem: dict) -> dict:
    user_name_map = {}
    try:
        for u in users_data:
            user_name_map[str(u.get("email") or u.get("id") or "")] = u.get("nickname") or u.get("name") or u.get("email") or u.get("id")
    except Exception:
        user_name_map = {}

    device_key = str(mem.get("device_id") or mem.get("client_id") or mem.get("user_id") or "unknown_device")
    return {
        **mem,
        "device_key": device_key,
        "device_label": mem.get("device_name") or user_name_map.get(device_key) or device_key,
    }


def _extract_memory_alias_from_speech(spoken_text: str) -> Optional[str]:
    text = str(spoken_text or "").strip()
    patterns = [
        r"(?:把这里保存为|把当前位置保存为|把现在的位置作为|把当前位置作为|把当前位置记为|把这里记为|记住这里是|保存这里为|保存当前位置为)(.+)",
        r"(?:保存为|记为)(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        alias = match.group(1).strip("。！？!?.,， ")
        alias = re.sub(r"^(常用地址|常用地点)[\s，,]*", "", alias).strip("。！？!?.,， ")
        alias = re.sub(r"^(叫做|叫|是)", "", alias).strip("。！？!?.,， ")
        if alias:
            return alias
    return None


def _save_current_location_as_route_memory(client_id: str, alias: str, lat: float, lng: float) -> dict:
    global route_memories_data
    alias = str(alias or "").strip()
    now_ts = time.time()
    existing = None
    for mem in route_memories_data:
        keys = {
            str(mem.get("device_key") or ""),
            str(mem.get("device_id") or ""),
            str(mem.get("client_id") or ""),
            str(mem.get("user_id") or ""),
        }
        if client_id in keys and _normalize_spoken_text(mem.get("alias", "")) == _normalize_spoken_text(alias):
            existing = mem
            break

    if existing:
        existing["lat"] = float(lat)
        existing["lng"] = float(lng)
        existing["updated_at"] = now_ts
        existing["device_key"] = client_id
        existing["client_id"] = client_id
        save_route_memories()
        return {"created": False, "item": existing}

    item = {
        "id": f"rm_{int(now_ts * 1000)}",
        "user_id": client_id,
        "client_id": client_id,
        "device_key": client_id,
        "alias": alias,
        "lat": float(lat),
        "lng": float(lng),
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    route_memories_data.append(item)
    save_route_memories()
    return {"created": True, "item": item}


def _clean_navigation_destination(raw_text: str) -> str:
    destination = str(raw_text or "").strip("。！？!?.,， ")
    if not destination:
        return ""

    for prefix in ["一下", "一趟", "去一下", "到一下", "前往一下", "去往一下"]:
        if destination.startswith(prefix):
            destination = destination[len(prefix):].strip("。！？!?.,， ")

    for suffix in [
        "怎么走", "怎么去", "怎么过去", "如何去", "如何到", "怎么到", "怎么导航",
        "导航一下", "导航过去", "带我过去", "带路", "路线怎么走", "路线",
        "行吗", "可以吗", "好吗", "可以不", "吧", "呀", "啊", "呢"
    ]:
        if destination.endswith(suffix):
            destination = destination[:-len(suffix)].strip("。！？!?.,， ")
            break

    return destination


def _extract_navigation_destination(spoken_text: str) -> str:
    text = str(spoken_text or "").strip()
    if not text:
        return ""

    text = re.sub(r"^(?:小桥小桥|小乔小乔|你好小桥|你好|请问|那个|那个帮我|帮帮我)[，,\s]*", "", text).strip()

    for prefix in [
        "导航一下到", "导航一下去", "导航到", "导航去", "导航", "带路去", "带我去", "送我去",
        "前往", "去往", "去", "到"
    ]:
        idx = text.find(prefix)
        if idx >= 0:
            destination = _clean_navigation_destination(text[idx + len(prefix):])
            if len(destination) >= 2:
                return destination

    for prefix in ["我想去", "我要去", "想要去", "准备去", "需要去", "我想到", "我要到", "需要到"]:
        idx = text.find(prefix)
        if idx >= 0:
            destination = _clean_navigation_destination(text[idx + len(prefix):])
            if len(destination) >= 2:
                return destination

    for suffix in ["怎么走", "怎么去", "怎么过去", "如何去", "如何到", "怎么到", "怎么导航"]:
        if text.endswith(suffix):
            destination = _clean_navigation_destination(text[:-len(suffix)])
            if len(destination) >= 2:
                return destination
    return ""


def _is_navigation_query(spoken_text: str) -> bool:
    text = str(spoken_text or "").strip()
    if not text:
        return False
    if _extract_navigation_destination(text):
        return True
    return any(keyword in text for keyword in ["导航", "带我去", "前往", "怎么走", "怎么去", "如何去"])


def _build_remaining_distance_reply(client_id: str) -> str:
    session = nav_manager.sessions.get(client_id)
    if not session:
        return "当前没有正在进行的导航。"
    state = manager.device_states.get(client_id, {})
    lat = state.get("lat")
    lng = state.get("lng")
    if lat is None or lng is None or not session.destination_coords:
        return "暂时无法计算剩余距离，请等待定位稳定后再试。"

    distance = session._calc_distance((lng, lat), session.destination_coords)
    if distance >= 1000:
        return f"距离{session.destination_name}还有约 {distance / 1000:.2f} 公里。"
    return f"距离{session.destination_name}还有约 {int(distance)} 米。"


def _build_repeat_instruction_reply(client_id: str) -> str:
    session = nav_manager.sessions.get(client_id)
    if session:
        for candidate in [
            getattr(session, "last_visual_instruction", ""),
            getattr(session, "last_route_instruction", ""),
            getattr(session, "last_macro_message", ""),
        ]:
            if str(candidate or "").strip():
                return str(candidate).strip()

    flow_state = manager.device_states.get(client_id, {})
    last_instruction = str(flow_state.get("last_instruction") or "").strip()
    if last_instruction:
        return last_instruction
    return "当前没有可重复的导航指令。"


def _serialize_route_steps(steps: list) -> list:
    serialized = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        item = dict(step)
        for key in ["polyline", "start_point", "end_point"]:
            value = item.get(key)
            if isinstance(value, tuple):
                item[key] = list(value)
            elif isinstance(value, list):
                normalized = []
                for pt in value:
                    if isinstance(pt, tuple):
                        normalized.append(list(pt))
                    else:
                        normalized.append(pt)
                item[key] = normalized
        serialized.append(item)
    return serialized


def _build_simple_route_steps_from_polyline(route: list) -> list:
    points = [pt for pt in (route or []) if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    if len(points) < 2:
        return []

    def _segment_instruction(prev_pt, pt):
        try:
            dlng = float(pt[0]) - float(prev_pt[0])
            dlat = float(pt[1]) - float(prev_pt[1])
        except Exception:
            return "沿当前路线继续前进"
        if abs(dlng) < 1e-7 and abs(dlat) < 1e-7:
            return "保持当前位置"
            
        angle = math.atan2(dlng, dlat) * 180 / math.pi
        if angle < 0:
            angle += 360
            
        # 注意：这里仅根据全局绝对方向计算，盲人端实际会结合当前设备的真实朝向重新修正播报。
        # 为了大屏展示更直观，先统一使用相对通用的描述。
        if 315 <= angle or angle < 45:
            return "向前直行"
        elif 45 <= angle < 135:
            return "向右转弯"
        elif 135 <= angle < 225:
            return "向后调头"
        else:
            return "向左转弯"

    merged_steps = []
    for idx in range(1, len(points)):
        prev_pt = points[idx - 1]
        pt = points[idx]
        inst = _segment_instruction(prev_pt, pt)
        try:
            distance = round(math.hypot((float(pt[0]) - float(prev_pt[0])) * 95000, (float(pt[1]) - float(prev_pt[1])) * 111000), 1)
        except Exception:
            distance = 0.0

        if not merged_steps:
            merged_steps.append({
                "instruction": inst,
                "assistant_action": inst,
                "distance": distance,
                "duration": max(1, int(distance / 1.0)) if distance else 1,
                "polyline": [list(prev_pt), list(pt)],
            })
        else:
            last_step = merged_steps[-1]
            if last_step["instruction"] == inst:
                last_step["distance"] += distance
                last_step["duration"] += max(1, int(distance / 1.0)) if distance else 1
                last_step["polyline"].append(list(pt))
            else:
                merged_steps.append({
                    "instruction": inst,
                    "assistant_action": inst,
                    "distance": distance,
                    "duration": max(1, int(distance / 1.0)) if distance else 1,
                    "polyline": [list(prev_pt), list(pt)],
                })

    for idx, step in enumerate(merged_steps):
        step["road"] = f"导航路段 {idx + 1}"

    return merged_steps


def _build_route_update_payload(client_id: str, nav_resp: Optional[dict] = None, route_override: Optional[list] = None) -> dict:
    state = _get_or_create_device_state(client_id)
    session = nav_manager.sessions.get(client_id)
    nav_resp = nav_resp or {}

    route = route_override
    if route is None:
        route = nav_resp.get("new_route")
    if route is None and session:
        route = session.route_polyline or []
    if route is None:
        route = state.get("route") or []

    route = [list(pt) if isinstance(pt, tuple) else pt for pt in (route or [])]
    destination_coords = None
    if session and session.destination_coords:
        destination_coords = list(session.destination_coords)
    elif state.get("destination_coords"):
        destination_coords = list(state.get("destination_coords"))

    instruction_text = ""
    for candidate in [
        nav_resp.get("message") if isinstance(nav_resp, dict) else "",
        getattr(session, "last_visual_instruction", "") if session else "",
        getattr(session, "last_route_instruction", "") if session else "",
        getattr(session, "last_macro_message", "") if session else "",
        state.get("last_instruction", ""),
    ]:
        if str(candidate or "").strip():
            instruction_text = str(candidate).strip()
            break

    steps = _serialize_route_steps(getattr(session, "route_steps", []) if session else state.get("route_steps", []))
    mode = str((nav_resp.get("mode") if isinstance(nav_resp, dict) else "") or (session.status if session else state.get("nav_status") or "IDLE")).strip().lower()
    if mode == "init":
        mode = "macro"
    payload = {
        "type": "route_update",
        "client_id": client_id,
        "route": route,
        "destination_name": (session.destination_name if session else state.get("destination_name")) or "",
        "destination_coords": destination_coords,
        "mode": mode or "idle",
        "nav_status": (session.status if session else state.get("nav_status")) or "IDLE",
        "distance_remaining": nav_resp.get("distance") if isinstance(nav_resp, dict) else state.get("distance_remaining"),
        "current_step_index": getattr(session, "current_route_step_index", state.get("current_step_index", 0)) if session else state.get("current_step_index", 0),
        "steps": steps,
        "instruction_text": instruction_text,
        "updated_at": time.time(),
        "coord_system": state.get("coord_system") or "gcj02",
    }

    state["route"] = payload["route"]
    state["route_payload"] = payload
    state["destination_name"] = payload["destination_name"]
    state["destination_coords"] = payload["destination_coords"]
    state["nav_status"] = payload["nav_status"]
    state["route_steps"] = payload["steps"]
    state["current_step_index"] = payload["current_step_index"]
    state["distance_remaining"] = payload["distance_remaining"]
    state["route_updated_at"] = payload["updated_at"]
    return payload


async def _broadcast_route_update(client_id: str, nav_resp: Optional[dict] = None, route_override: Optional[list] = None):
    payload = _build_route_update_payload(client_id, nav_resp=nav_resp, route_override=route_override)
    _record_device_flow_event(client_id, route_updated_at=payload["updated_at"])
    await manager.broadcast(payload)
    await manager.send_to_client_input(client_id, payload)


async def _broadcast_navigation_instruction(client_id: str, text: str, extra_meta: Optional[dict] = None):
    clean_text = str(text or "").strip()
    if not clean_text:
        return
    _record_device_flow_event(client_id, last_instruction=clean_text, last_instruction_at=time.time())
    message = {
        "type": "navigation_instruction",
        "client_id": client_id,
        "text": clean_text,
        "timestamp": time.time(),
    }
    if extra_meta:
        message.update(extra_meta)
    await manager.broadcast(message)
    await manager.send_to_client_input(client_id, message)


async def _broadcast_instruction(client_id: str, text: str):
    _record_device_flow_event(client_id, last_instruction=text, last_instruction_at=time.time())
    message = {
        "type": "assistant_reply",
        "client_id": client_id,
        "text": text,
        "timestamp": time.time()
    }
    await manager.broadcast(message)
    await manager.send_to_client_input(client_id, message)


async def _toggle_voice_assist(client_id: str, enable: bool, reason: str) -> str:
    if enable:
        if model_manager.switch_mode("volunteer_mode"):
            _set_voice_mode(client_id, "standby", "assist_started")
            _record_device_flow_event(client_id, assist_active=True, assist_status="pending", assist_reason=reason, assist_requested_at=time.time())
            control_msg = {"type": "volunteer_request", "status": "pending", "client_id": client_id, "timestamp": time.time()}
            await manager.broadcast(control_msg)
            await manager.broadcast(_voice_event_payload(client_id, "assist_started", text=""))
            delivered = await manager.send_to_client_input(client_id, control_msg)
            if not delivered:
                print(f"⚠️ volunteer_request 未送达树莓派客户端: {client_id}")
            return "已为您呼叫人工协助，请原地稍等。"
        return "人工协助暂时无法接通，请稍后重试。"
    if model_manager.switch_mode("monitor"):
        _set_voice_mode(client_id, "standby", "assist_ended")
        _record_device_flow_event(client_id, assist_active=False, assist_status="idle", assist_reason="", assist_requested_at=0)
        control_msg = {"type": "volunteer_request", "status": "cancelled", "client_id": client_id, "timestamp": time.time()}
        await manager.broadcast(control_msg)
        await manager.broadcast(_voice_event_payload(client_id, "assist_ended", text=""))
        await manager.send_to_client_input(client_id, control_msg)
        return "已为您取消人工协助。"
    return "取消人工协助失败，请稍后再试。"


async def _prime_navigation_after_start(client_id: str):
    current_state = manager.device_states.get(client_id, {})
    cur_lat = current_state.get("lat")
    cur_lng = current_state.get("lng")
    if cur_lat is None or cur_lng is None:
        return
    nav_resp = nav_manager.on_gps_update(client_id, cur_lng, cur_lat, obstacles_data)
    if not nav_resp:
        return
    if nav_resp.get("new_route"):
        await _broadcast_route_update(client_id, nav_resp=nav_resp)
    if nav_resp.get("message"):
        await _broadcast_navigation_instruction(client_id, nav_resp["message"], {"mode": str(nav_resp.get("mode") or "").lower()})


async def _try_start_or_queue_navigation(client_id: str, destination: str) -> str:
    current_state = manager.device_states.get(client_id, {})
    cur_lat = current_state.get("lat")
    cur_lng = current_state.get("lng")

    local_match = _find_local_destination_match(destination)
    if local_match:
        result_msg = nav_manager.start_navigation_to_coords(
            client_id,
            local_match["name"],
            local_match["lng"],
            local_match["lat"],
            context_meta=local_match,
        )
        if local_match.get("voice_prompt") or local_match.get("blind_guidance_summary"):
            result_msg = f"开始导航前往 {local_match['name']}。该地点为社区志愿者添加的标注点。"
    else:
        result_msg = nav_manager.start_navigation(client_id, destination)
        if result_msg.startswith("抱歉，未找到地点"):
            return result_msg

    if cur_lat is None or cur_lng is None:
        target_name = local_match["name"] if local_match else destination
        _record_device_flow_event(
            client_id,
            pending_navigation_destination=target_name,
            pending_navigation_requested_at=time.time(),
            last_instruction=f"已收到导航请求，等待定位稳定后自动开始前往 {target_name}。",
            last_instruction_at=time.time(),
        )
        return f"已收到导航请求，等待定位稳定后自动开始前往 {target_name}。"

    _record_device_flow_event(
        client_id,
        pending_navigation_destination="",
        pending_navigation_requested_at=0.0,
    )
    await _prime_navigation_after_start(client_id)
    return result_msg


async def _resume_pending_navigation_if_ready(client_id: str):
    current_state = manager.device_states.get(client_id, {})
    pending_destination = str(current_state.get("pending_navigation_destination") or "").strip()
    if not pending_destination:
        return
    cur_lat = current_state.get("lat")
    cur_lng = current_state.get("lng")
    if cur_lat is None or cur_lng is None:
        return
    if client_id not in nav_manager.sessions:
        result_msg = nav_manager.start_navigation(client_id, pending_destination)
        if result_msg.startswith("抱歉，未找到地点"):
            await _broadcast_instruction(client_id, result_msg)
            _record_device_flow_event(client_id, pending_navigation_destination="", pending_navigation_requested_at=0.0)
            return
    _record_device_flow_event(client_id, pending_navigation_destination="", pending_navigation_requested_at=0.0)
    await _broadcast_instruction(client_id, f"定位已恢复，开始为您导航到 {pending_destination}。")
    await _prime_navigation_after_start(client_id)


def _safe_created_at_sort_value(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            pass
        try:
            return datetime.datetime.fromisoformat(stripped).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _build_obstacle_annotation_items() -> List[dict]:
    result_list = [o.copy() for o in obstacles_data]
    obs_map = {str(o["id"]): i for i, o in enumerate(result_list)}

    for c in contributions_data:
        marker_id = str(c.get("markerId", ""))
        contrib_type = c.get("type")
        review_status = c.get("reviewStatus")

        if marker_id in obs_map:
            idx = obs_map[marker_id]
            result_list[idx]["disputeStatus"] = "ai_rejected" if review_status == "reject" else "pending_review"
            result_list[idx]["pendingContributionId"] = c.get("id")
            result_list[idx]["pendingContribution"] = c.copy()
            if c.get("ai_analysis_result"):
                result_list[idx]["ai_analysis_result"] = c.get("ai_analysis_result")
            if c.get("ai_validation"):
                result_list[idx]["ai_validation"] = c.get("ai_validation")
            if c.get("ai_process"):
                result_list[idx]["ai_process"] = c.get("ai_process")
            if c.get("blind_guidance_summary"):
                result_list[idx]["blind_guidance_summary"] = c.get("blind_guidance_summary")
            if c.get("voice_prompt"):
                result_list[idx]["voice_prompt"] = c.get("voice_prompt")
            if c.get("ai_confidence") is not None:
                result_list[idx]["ai_confidence"] = c.get("ai_confidence")
            continue

        if contrib_type == 3 and c.get("lat") is not None and c.get("lng") is not None:
            temp_obs = {
                "id": marker_id or c.get("id"),
                "lat": c.get("lat"),
                "lng": c.get("lng"),
                "type": 3,
                "status": 2 if review_status == "reject" else 0,
                "disputeStatus": "ai_rejected" if review_status == "reject" else "new_pending",
                "pendingContributionId": c.get("id"),
                "pendingContribution": c.copy(),
                "title": f"{c.get('markerTitle', '未命名标注')} (待核实)",
                "description": c.get("content", ""),
                "imageUrls": [c["imageUrl"]] if c.get("imageUrl") else c.get("imageUrls", []),
                "createdAt": c.get("createdAt", time.time()),
                "voice_prompt": c.get("voice_prompt"),
                "blind_guidance_summary": c.get("blind_guidance_summary"),
                "ai_analysis_result": c.get("ai_analysis_result"),
                "ai_validation": c.get("ai_validation"),
                "ai_process": c.get("ai_process"),
                "ai_confidence": c.get("ai_confidence"),
                "ai_model": c.get("ai_model"),
            }
            result_list.append(temp_obs)

    return result_list


def _build_shop_annotation_items() -> List[dict]:
    result_list = [s.copy() for s in shops_data]
    shop_map = {str(s["id"]): i for i, s in enumerate(result_list)}

    for c in contributions_data:
        contrib_type = c.get("type")
        if contrib_type not in (0, 4):
            continue

        marker_id = str(c.get("markerId", ""))
        review_status = c.get("reviewStatus")
        if marker_id in shop_map:
            idx = shop_map[marker_id]
            result_list[idx]["disputeStatus"] = "ai_rejected" if review_status == "reject" else "pending_review"
            result_list[idx]["pendingContributionId"] = c.get("id")
            result_list[idx]["pendingContribution"] = c.copy()
            if c.get("ai_analysis_result"):
                result_list[idx]["ai_analysis_result"] = c.get("ai_analysis_result")
            if c.get("ai_validation"):
                result_list[idx]["ai_validation"] = c.get("ai_validation")
            if c.get("ai_process"):
                result_list[idx]["ai_process"] = c.get("ai_process")
            if c.get("blind_guidance_summary"):
                result_list[idx]["blind_guidance_summary"] = c.get("blind_guidance_summary")
            if c.get("voice_prompt"):
                result_list[idx]["voice_prompt"] = c.get("voice_prompt")
            if c.get("ai_confidence") is not None:
                result_list[idx]["ai_confidence"] = c.get("ai_confidence")
            continue

        if c.get("lat") is not None and c.get("lng") is not None:
            temp_shop = {
                "id": marker_id or c.get("id"),
                "lat": c.get("lat"),
                "lng": c.get("lng"),
                "type": 4,
                "status": 2 if review_status == "reject" else 0,
                "disputeStatus": "ai_rejected" if review_status == "reject" else "new_pending",
                "pendingContributionId": c.get("id"),
                "pendingContribution": c.copy(),
                "title": f"{c.get('markerTitle', '未命名店铺')} (待核实)",
                "description": c.get("content", ""),
                "imageUrls": [c["imageUrl"]] if c.get("imageUrl") else c.get("imageUrls", []),
                "createdAt": c.get("createdAt", time.time()),
                "voice_prompt": c.get("voice_prompt"),
                "blind_guidance_summary": c.get("blind_guidance_summary"),
                "ai_analysis_result": c.get("ai_analysis_result"),
                "ai_validation": c.get("ai_validation"),
                "ai_process": c.get("ai_process"),
                "ai_confidence": c.get("ai_confidence"),
                "ai_model": c.get("ai_model"),
            }
            result_list.append(temp_shop)

    return result_list


def _build_unified_annotation_items(marker_type: str = "all") -> List[dict]:
    normalized_type = (marker_type or "all").lower()
    results: List[dict] = []

    if normalized_type in {"all", "obstacle", "obstacles"}:
        results.extend([_to_unified_annotation(o, "obstacle") for o in _build_obstacle_annotation_items()])

    if normalized_type in {"all", "shop", "shops"}:
        results.extend([_to_unified_annotation(s, "shop") for s in _build_shop_annotation_items()])

    results.sort(key=lambda x: _safe_created_at_sort_value(x.get("createdAt")), reverse=True)
    return results


def _to_unified_annotation(item: dict, marker_type: str) -> dict:
    unified = item.copy()
    pending = item.get("pendingContribution") if isinstance(item.get("pendingContribution"), dict) else {}
    unified["marker_type"] = marker_type
    unified["marker_type_label"] = "障碍物" if marker_type == "obstacle" else "店铺"
    unified["source"] = f"{marker_type}_library"
    unified["ai_analysis_result"] = item.get("ai_analysis_result") or pending.get("ai_analysis_result") or ""
    unified["ai_validation"] = item.get("ai_validation") or pending.get("ai_validation") or {}
    unified["voice_prompt"] = item.get("voice_prompt") or pending.get("voice_prompt") or ""
    unified["ai_confidence"] = item.get("ai_confidence") if item.get("ai_confidence") is not None else pending.get("ai_confidence")
    unified["ai_process"] = _normalize_ai_process(item, marker_type)
    unified["blind_guidance_summary"] = (
        item.get("blind_guidance_summary")
        or pending.get("blind_guidance_summary")
        or (item.get("ai_validation") or {}).get("blind_guidance_summary")
        or (pending.get("ai_validation") or {}).get("blind_guidance_summary")
        or item.get("voice_prompt")
        or pending.get("voice_prompt")
        or ""
    )
    
    # Extract comments
    comments_list = []
    if "comments" in item and isinstance(item["comments"], list):
        comments_list.extend(item["comments"])
    pc = item.get("pendingContribution")
    if pc and isinstance(pc, dict) and "comments" in pc and isinstance(pc["comments"], list):
        comments_list.extend(pc["comments"])
    unified["all_comments"] = comments_list
    
    return unified


def _persist_annotation_analysis_result(
    annotation: dict,
    marker_type: str,
    marker_id: str,
    analysis_text: str,
    validation: dict,
    review_decision: dict,
    ai_process: dict,
    workflow_logs: list,
) -> dict:
    global obstacles_data, shops_data

    persisted = False
    target_db = None
    review_state = review_decision["decision"]

    if marker_type in {"obstacle", "all"}:
        for obs in obstacles_data:
            if str(obs.get("id")) != marker_id:
                continue
            original_desc = obs.get("description", "")
            clean_desc = re.sub(r'\[AI检测\]:.*$', '', original_desc).strip()

            if analysis_text:
                obs["description"] = f"{clean_desc}\n[AI检测]: {analysis_text}".strip()
            else:
                obs["description"] = clean_desc

            obs["ai_analysis_result"] = analysis_text
            obs["ai_validation"] = validation
            obs["ai_process"] = ai_process
            obs["voice_prompt"] = validation.get("blind_guidance_summary") or validation.get("blind_audio_desc") or obs.get("voice_prompt", "")
            obs["blind_guidance_summary"] = validation.get("blind_guidance_summary") or obs.get("blind_guidance_summary", "")

            if review_state == "approve":
                obs["status"] = 1
                obs["disputeStatus"] = None
                if "(待核实)" in str(obs.get("title", "")):
                    obs["title"] = obs.get("title", "").replace("(待核实)", "").strip()
            elif review_state == "manual_review":
                _mark_target_pending_review(obs, analysis_text, validation, ai_process, clean_desc)
            else:
                _mark_target_rejected(obs, analysis_text, validation, ai_process, clean_desc)

            save_obstacles()
            persisted = True
            target_db = "obstacles"
            break

    if not persisted and marker_type in {"shop", "all"}:
        for shop in shops_data:
            if str(shop.get("id")) != marker_id:
                continue
            original_desc = shop.get("description", "")
            clean_desc = re.sub(r'\[AI检测\]:.*$', '', original_desc).strip()

            if analysis_text:
                shop["description"] = f"{clean_desc}\n[AI检测]: {analysis_text}".strip()
            else:
                shop["description"] = clean_desc

            shop["ai_analysis_result"] = analysis_text
            shop["ai_validation"] = validation
            shop["ai_process"] = ai_process
            shop["voice_prompt"] = validation.get("blind_guidance_summary") or validation.get("blind_audio_desc") or shop.get("voice_prompt", "")
            shop["blind_guidance_summary"] = validation.get("blind_guidance_summary") or shop.get("blind_guidance_summary", "")

            if review_state == "approve":
                shop["status"] = 1
                shop["disputeStatus"] = None
                if "(待核实)" in str(shop.get("title", "")):
                    shop["title"] = shop.get("title", "").replace("(待核实)", "").strip()
            elif review_state == "manual_review":
                _mark_target_pending_review(shop, analysis_text, validation, ai_process, clean_desc)
            else:
                _mark_target_rejected(shop, analysis_text, validation, ai_process, clean_desc)

            save_shops()
            persisted = True
            target_db = "shops"
            break

    if persisted:
        if review_state == "approve":
            workflow_logs.append("自动审核触发：图文匹配且证据完整，数据状态已更新为“已通过”")
        elif review_state == "manual_review":
            workflow_logs.append("转人工复核：证据不足或图文关系不确定，保留在待审核列表")
        else:
            workflow_logs.append("审核驳回：图文不匹配，已阻断自动通过")

        pending_contribution_id = annotation.get("pendingContributionId")
        if pending_contribution_id:
            if review_state == "approve":
                finalized = _finalize_pending_contribution(
                    pending_contribution_id,
                    analysis_text=analysis_text,
                    validation=validation,
                    ai_process=ai_process,
                )
                if finalized:
                    workflow_logs.append("待审核投稿已同步通过，并已从待审核列表移除")
            else:
                updated = _update_pending_contribution_review_state(
                    pending_contribution_id,
                    analysis_text=analysis_text,
                    validation=validation,
                    ai_process=ai_process,
                    review_status=review_state,
                )
                if updated:
                    workflow_logs.append("待审核投稿已写入 AI 结论，继续保留人工审核状态")

        ai_process["flow_steps"] = _build_agent_flow_steps(workflow_logs, success=True)
        if marker_type in {"obstacle", "all"}:
            for obs in obstacles_data:
                if str(obs.get("id")) == marker_id:
                    obs["ai_process"] = ai_process
                    save_obstacles()
                    break
        if marker_type in {"shop", "all"}:
            for shop in shops_data:
                if str(shop.get("id")) == marker_id:
                    shop["ai_process"] = ai_process
                    save_shops()
                    break
    else:
        pending_contribution_id = annotation.get("pendingContributionId")
        if pending_contribution_id:
            if review_state == "approve":
                finalized = _finalize_pending_contribution(
                    pending_contribution_id,
                    analysis_text=analysis_text,
                    validation=validation,
                    ai_process=ai_process,
                )
                if finalized:
                    persisted = True
                    target_db = "pending_contributions"
                    workflow_logs.append("待审核投稿已转正入库，并已从待审核列表移除")
                    ai_process["flow_steps"] = _build_agent_flow_steps(workflow_logs, success=True)
            else:
                updated = _update_pending_contribution_review_state(
                    pending_contribution_id,
                    analysis_text=analysis_text,
                    validation=validation,
                    ai_process=ai_process,
                    review_status=review_state,
                )
                if updated:
                    persisted = True
                    target_db = "pending_contributions"
                    workflow_logs.append("待审核投稿缺少自动通过条件，继续保留人工审核状态")
                    ai_process["flow_steps"] = _build_agent_flow_steps(workflow_logs, success=True)
        if not persisted:
            workflow_logs.append("警告：未在主库中找到对应记录，保存失败")

    return {
        "persisted": persisted,
        "target_db": target_db,
        "workflow_logs": workflow_logs,
        "ai_process": ai_process,
    }


def _persist_blind_guidance_summary(
    marker_type: str,
    marker_id: str,
    pending_contribution_id: Optional[str],
    validation: dict,
    ai_process: dict,
) -> None:
    global obstacles_data, shops_data, contributions_data

    summary = str(validation.get("blind_guidance_summary") or "").strip()
    if not summary:
        return

    if marker_type in {"obstacle", "all"}:
        for obs in obstacles_data:
            if str(obs.get("id")) != marker_id:
                continue
            obs["blind_guidance_summary"] = summary
            obs["voice_prompt"] = summary
            obs["ai_validation"] = validation
            obs["ai_process"] = ai_process
            save_obstacles()
            break

    if marker_type in {"shop", "all"}:
        for shop in shops_data:
            if str(shop.get("id")) != marker_id:
                continue
            shop["blind_guidance_summary"] = summary
            shop["voice_prompt"] = summary
            shop["ai_validation"] = validation
            shop["ai_process"] = ai_process
            save_shops()
            break

    if pending_contribution_id:
        updated = False
        for contribution in contributions_data:
            if str(contribution.get("id")) != str(pending_contribution_id):
                continue
            contribution["blind_guidance_summary"] = summary
            contribution["voice_prompt"] = summary
            contribution["ai_validation"] = validation
            contribution["ai_process"] = ai_process
            updated = True
            break
        if updated:
            save_contributions()


@app.get("/api/admin/annotations")
async def admin_get_annotations(request: Request, marker_type: str = Query(default="all")):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"success": True, "annotations": _build_unified_annotation_items(marker_type)}


@app.post("/api/admin/annotations/analyze_stream")
async def admin_analyze_annotation_stream(request: Request, data: dict = Body(...)):
    async def event_generator():
        try:
            if not await check_admin(request):
                yield json.dumps({"error": True, "message": "管理员鉴权失败", "workflow_logs": ["管理员鉴权失败"]}) + "\n"
                return

            marker_id = str(data.get("marker_id") or data.get("id") or "").strip()
            marker_type = str(data.get("marker_type") or "all").strip().lower()
            user_prompt = str(data.get("prompt") or "").strip()

            if not marker_id:
                yield json.dumps({"error": True, "message": "Missing marker_id", "workflow_logs": ["缺少标注 ID"]}) + "\n"
                return

            annotations = _build_unified_annotation_items(marker_type)
            annotation = next((item for item in annotations if str(item.get("id")) == marker_id), None)
            if not annotation:
                yield json.dumps({"error": True, "message": "Annotation not found", "workflow_logs": ["统一标注中未找到目标数据"]}) + "\n"
                return

            # --- Step 1: 证据整理智能体 ---
            yield json.dumps({
                "step_update": True,
                "current_agent": "证据整理智能体",
                "detail": f"正在整理 {annotation.get('title', '当前标注')} 的图片、图文说明与评论证据"
            }) + "\n"
            await asyncio.sleep(0.5) # Simulate processing time

            image_urls = annotation.get("imageUrls") or []
            first_image = image_urls[0] if image_urls else None

            if isinstance(first_image, str) and first_image.startswith("/"):
                first_image = str(request.base_url).rstrip("/") + first_image

            analysis_prompt = user_prompt or (
                "请分析这条统一标注，并只返回 JSON格式。"
                "字段必须包含：summary、blind_audio_desc、match_status、match_reason、confidence_score、risk、recommendation、entity_name_match、business_match、manual_review_required、evidence_level。"
                "其中 match_status 只能是 match、mismatch、uncertain；evidence_level 只能是 strong、medium、weak。"
                "要求提供一个专门用于盲人语音播报的字段 blind_audio_desc，控制在 15 字以内。"
                "confidence_score 取 0 到 100 的数字，表示图文匹配可信度。"
                "如果 summary 或 blind_audio_desc 表达的是“标注合理、图文一致、证据充分、可以通过”，则 match_status 不得写 uncertain，必须写 match。"
                "如果表达“图文不匹配、错误标注”，则 match_status 必须写 mismatch。"
                "对于店铺类标注，如果图片招牌文字、标题主实体、经营业态能够明显对上，entity_name_match=true，business_match=true，manual_review_required=false，evidence_level=strong，match_status 必须写 match。"
                "注意：像“疑似井盖”“疑似下水道口”“可能是某类障碍物”这类词，仅表示目标类别存在轻微不确定，不等于图文关系不确定；如果标注本身合理，仍应输出 match。"
                "只有在你明确认为图片和描述对不上、证据不足、无法确认标注关系时，才输出 uncertain。"
                "每个字段尽量简短，summary 控制在 35 个字以内。"
                "请综合判断图片、文字描述以及用户评论是否匹配。若无图片则基于文字和评论判断内容是否属于正常的障碍物或店铺。"
            )
            
            comments_text = ""
            all_comments = annotation.get("all_comments") or []
            if all_comments:
                comments_text = "\n用户评论：\n" + "\n".join([f"- {c.get('content', '')}" for c in all_comments if c.get('content')])

            analysis_prompt += (
                f"\n标注标题：{annotation.get('title', '')}"
                f"\n标注描述：{annotation.get('description', '')}"
                f"\n标注类型：{annotation.get('marker_type_label', '')}"
                f"\n坐标：{annotation.get('lat')}, {annotation.get('lng')}"
                f"{comments_text}"
            )

            workflow_logs = ["读取统一标注数据"]
            content_items = []
            if first_image:
                workflow_logs.append(f"装载图片输入: {first_image}")
                content_items.append(
                    {
                        "type": "input_image",
                        "image_url": first_image
                    }
                )
            else:
                workflow_logs.append("当前标注没有图片，切换为纯文字分析模式")

            content_items.append(
                {
                    "type": "input_text",
                    "text": analysis_prompt
                }
            )

            # --- Step 2: 审核分析智能体 ---
            yield json.dumps({
                "step_update": True,
                "current_agent": "审核分析智能体",
                "detail": f"调用模型 {AUDIT_AGENT_MODEL} 进行图文匹配与风险分析..."
            }) + "\n"

            workflow_logs.append(f"审核分析智能体：调用模型 {AUDIT_AGENT_MODEL}")
            workflow_logs.append("审核分析智能体：等待方舟 Responses 返回结果")

            resp = await audit_agent_client.responses.create(
                model=AUDIT_AGENT_MODEL,
                input=[
                    {
                        "role": "user",
                        "content": content_items
                    }
                ]
            )
            raw_analysis_text = _extract_reply_text(resp)
            if not raw_analysis_text:
                try:
                    raw_analysis_text = _extract_reply_text_from_dump(resp.model_dump())
                except Exception:
                    raw_analysis_text = ""

            parsed_result = _extract_json_block(raw_analysis_text)
            display_text, validation = _format_annotation_analysis_text(parsed_result, raw_analysis_text)

            workflow_logs.append("审核分析智能体：解析模型输出完成")
            workflow_logs.append(f"图文校验：{validation.get('match_reason')}")

            # --- Step 3: 审核决策智能体 ---
            yield json.dumps({
                "step_update": True,
                "current_agent": "审核决策智能体",
                "detail": "基于分析结果进行审核决策推断..."
            }) + "\n"

            policy_decision = _decide_annotation_review(annotation, validation)
            evidence = policy_decision.get("evidence") or {}
            decision_prompt = (
                "你是审核决策智能体。请根据审核分析智能体输出和证据完整性，只返回 JSON。"
                "字段必须包含：decision、reason、archive_instruction。"
                "decision 只能是 approve、manual_review、reject。"
                "必须优先尊重 policy_decision；若 policy_decision 已是 approve，除非存在明确冲突证据，否则不得改成 manual_review。"
                "如果分析结果表达了“匹配合理、图文一致、证据充分”这类结论，且证据完整，可建议 approve。"
                "如果表达了“图文不匹配”，建议 reject。"
                "如果表达了“待确认、存疑、证据不足”，建议 manual_review。"
            )
            decision_input = json.dumps({
                "annotation_id": marker_id,
                "analysis_text": display_text,
                "raw_analysis_text": raw_analysis_text,
                "validation": validation,
                "policy_decision": policy_decision,
                "evidence": evidence,
            }, ensure_ascii=False)
            workflow_logs.append(f"审核决策智能体：调用模型 {DECISION_AGENT_MODEL}")
            decision_resp = await decision_agent_client.responses.create(
                model=DECISION_AGENT_MODEL,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": decision_prompt},
                            {"type": "input_text", "text": decision_input},
                        ]
                    }
                ]
            )
            raw_decision_text = _extract_reply_text(decision_resp)
            if not raw_decision_text:
                try:
                    raw_decision_text = _extract_reply_text_from_dump(decision_resp.model_dump())
                except Exception:
                    raw_decision_text = ""
            parsed_decision = _extract_json_block(raw_decision_text)
            agent_decision = _normalize_agent_decision(parsed_decision, policy_decision)
            review_decision = _merge_review_decision(policy_decision, agent_decision)
            workflow_logs.append(f"审核决策智能体：{review_decision['reason']}")

            # --- Step 4: 归档处置智能体 ---
            yield json.dumps({
                "step_update": True,
                "current_agent": "归档处置智能体",
                "detail": "正在执行归档和状态回写..."
            }) + "\n"

            if review_decision.get("archive_instruction"):
                workflow_logs.append(f"归档处置智能体：{review_decision['archive_instruction']}")
            persisted_ai_process = {
                "has_real_ai": True,
                "agent_structure": "real_multi_agent",
                "audit_agent_model": AUDIT_AGENT_MODEL,
                "decision_agent_model": DECISION_AGENT_MODEL,
                "guidance_agent_model": GUIDANCE_AGENT_MODEL,
                "flow_steps": []
            }
            persist_result = _persist_annotation_analysis_result(
                annotation=annotation,
                marker_type=marker_type,
                marker_id=marker_id,
                analysis_text=display_text,
                validation=validation,
                review_decision=review_decision,
                ai_process=persisted_ai_process,
                workflow_logs=workflow_logs,
            )
            workflow_logs = persist_result["workflow_logs"]
            persisted_ai_process = persist_result["ai_process"]

            # --- Step 5: 导盲摘要智能体 ---
            yield json.dumps({
                "step_update": True,
                "current_agent": "导盲摘要智能体",
                "detail": "正在基于归档结果生成面向盲人的简洁位置提示..."
            }) + "\n"
            blind_guidance_summary = await _generate_blind_guidance_summary(annotation, validation, review_decision)
            validation["blind_guidance_summary"] = blind_guidance_summary
            workflow_logs.append(f"导盲摘要智能体：{blind_guidance_summary}")
            persisted_ai_process["flow_steps"] = _build_agent_flow_steps(workflow_logs, success=True)
            _persist_blind_guidance_summary(
                marker_type=marker_type,
                marker_id=marker_id,
                pending_contribution_id=annotation.get("pendingContributionId"),
                validation=validation,
                ai_process=persisted_ai_process,
            )

            # Final success response
            final_data = {
                "success": True,
                "message": "AI 分析完成",
                "analysis_text": display_text,
                "raw_analysis_text": raw_analysis_text,
                "validation": validation,
                "review_decision": review_decision,
                "blind_guidance_summary": blind_guidance_summary,
                "raw_decision_text": raw_decision_text,
                "workflow_logs": workflow_logs,
                "workflow_flow_steps": _build_agent_flow_steps(workflow_logs, success=True),
                "workflow_mode": "real_multi_agent",
                "agent_models": {
                    "audit_agent": AUDIT_AGENT_MODEL,
                    "decision_agent": DECISION_AGENT_MODEL,
                    "guidance_agent": GUIDANCE_AGENT_MODEL,
                },
                "persisted": persist_result.get("persisted", False),
                "target_db": persist_result.get("target_db"),
            }
            yield json.dumps(final_data) + "\n"
        except Exception as e:
            logger.error(f"Stream Analyze Error: {e}")
            yield json.dumps({"error": True, "message": str(e), "workflow_logs": [f"分析失败: {e}"]}) + "\n"

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


def _normalize_scheduled_time(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not re.fullmatch(r"\d{2}:\d{2}", raw):
        raise ValueError("scheduled_time 必须是 HH:MM 格式")
    hour, minute = raw.split(":")
    hh = int(hour)
    mm = int(minute)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("scheduled_time 超出有效时间范围")
    return f"{hh:02d}:{mm:02d}"


def _load_scheduled_audit_state() -> None:
    global scheduled_audit_state
    if not os.path.exists(SCHEDULED_AUDIT_FILE):
        return
    try:
        with open(SCHEDULED_AUDIT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            scheduled_audit_state.update({
                "enabled": bool(data.get("enabled", False)),
                "scheduled_time": str(data.get("scheduled_time") or "").strip(),
                "last_run_at": str(data.get("last_run_at") or "").strip(),
                "last_run_date": str(data.get("last_run_date") or "").strip(),
                "last_result": data.get("last_result") if isinstance(data.get("last_result"), dict) else {},
            })
    except Exception as e:
        print(f"⚠️ 加载定时全量审核配置失败: {e}")


def _save_scheduled_audit_state() -> None:
    try:
        with open(SCHEDULED_AUDIT_FILE, "w", encoding="utf-8") as f:
            json.dump(scheduled_audit_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存定时全量审核配置失败: {e}")


async def _execute_batch_annotation_analysis(base_url: str = "", trigger: str = "manual") -> dict:
    async with scheduled_audit_lock:
        annotations = _build_unified_annotation_items("all")
        pending_annotations = [item for item in annotations if _annotation_needs_batch_review(item)]
        processed_count = 0
        success_count = 0
        failure_count = 0
        failures = []

        for annotation in pending_annotations:
            processed_count += 1
            marker_type = str(annotation.get("marker_type") or "all").strip().lower()
            marker_id = str(annotation.get("id") or "").strip()
            try:
                result = await _run_annotation_ai_workflow(
                    annotation=annotation,
                    marker_type=marker_type,
                    marker_id=marker_id,
                    base_url=base_url or PUBLIC_BASE_URL,
                    user_prompt="",
                )
                if result.get("success"):
                    success_count += 1
                else:
                    failure_count += 1
                    failures.append({
                        "id": marker_id,
                        "title": annotation.get("title"),
                        "message": result.get("message") or "AI 分析失败",
                    })
            except Exception as item_error:
                failure_count += 1
                failures.append({
                    "id": marker_id,
                    "title": annotation.get("title"),
                    "message": str(item_error),
                })
                print(f"❌ [batch analyze] {marker_type}:{marker_id} -> {item_error}")

        now = datetime.datetime.now()
        result = {
            "success": True,
            "message": f"批量全量审核已完成，共处理 {processed_count} 条记录，成功 {success_count} 条，失败 {failure_count} 条",
            "processed_count": processed_count,
            "success_count": success_count,
            "failure_count": failure_count,
            "failures": failures[:20],
            "trigger": trigger,
            "executed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        scheduled_audit_state["last_run_at"] = result["executed_at"]
        scheduled_audit_state["last_run_date"] = now.strftime("%Y-%m-%d")
        scheduled_audit_state["last_result"] = result
        _save_scheduled_audit_state()
        return result


async def _scheduled_audit_loop():
    while True:
        try:
            if scheduled_audit_state.get("enabled") and scheduled_audit_state.get("scheduled_time"):
                now = datetime.datetime.now()
                current_hm = now.strftime("%H:%M")
                last_run_date = str(scheduled_audit_state.get("last_run_date") or "")
                if current_hm == scheduled_audit_state.get("scheduled_time") and last_run_date != now.strftime("%Y-%m-%d"):
                    print(f"⏰ [定时全量审核] 开始执行，计划时间 {current_hm}")
                    await _execute_batch_annotation_analysis(base_url=PUBLIC_BASE_URL, trigger="scheduled")
        except Exception as e:
            print(f"⚠️ scheduled audit loop error: {e}")
        await asyncio.sleep(20)


def _annotation_needs_batch_review(annotation: dict) -> bool:
    status = annotation.get("status")
    dispute_status = str(annotation.get("disputeStatus") or "").strip()
    title = str(annotation.get("title") or "").strip()
    pending_contribution_id = str(annotation.get("pendingContributionId") or "").strip()
    return (
        status == 0
        or dispute_status in {"new_pending", "pending_review", "ai_rejected"}
        or "(待核实)" in title
        or bool(pending_contribution_id)
    )


async def _run_annotation_ai_workflow(annotation: dict, marker_type: str, marker_id: str, base_url: str = "", user_prompt: str = "") -> dict:
    image_urls = annotation.get("imageUrls") or []
    first_image = image_urls[0] if image_urls else None
    if isinstance(first_image, str) and first_image.startswith("/") and base_url:
        first_image = base_url.rstrip("/") + first_image

    analysis_prompt = user_prompt or (
        "请分析这条统一标注，并只返回 JSON格式。"
        "字段必须包含：summary、blind_audio_desc、match_status、match_reason、confidence_score、risk、recommendation、entity_name_match、business_match、manual_review_required、evidence_level。"
        "其中 match_status 只能是 match、mismatch、uncertain；evidence_level 只能是 strong、medium、weak。"
        "要求提供一个专门用于盲人语音播报的字段 blind_audio_desc，控制在 15 字以内。"
        "confidence_score 取 0 到 100 的数字，表示图文匹配可信度。"
        "如果 summary 或 blind_audio_desc 表达的是“标注合理、图文一致、证据充分、可以通过”，则 match_status 不得写 uncertain，必须写 match。"
        "如果表达“图文不匹配、错误标注”，则 match_status 必须写 mismatch。"
        "对于店铺类标注，如果图片招牌文字、标题主实体、经营业态能够明显对上，entity_name_match=true，business_match=true，manual_review_required=false，evidence_level=strong，match_status 必须写 match。"
        "注意：像“疑似井盖”“疑似下水道口”“可能是某类障碍物”这类词，仅表示目标类别存在轻微不确定，不等于图文关系不确定；如果标注本身合理，仍应输出 match。"
        "只有在你明确认为图片和描述对不上、证据不足、无法确认标注关系时，才输出 uncertain。"
        "每个字段尽量简短，summary 控制在 35 个字以内。"
        "请综合判断图片、文字描述以及用户评论是否匹配。若无图片则基于文字和评论判断内容是否属于正常的障碍物或店铺。"
    )

    comments_text = ""
    all_comments = annotation.get("all_comments") or []
    if all_comments:
        comments_text = "\n用户评论：\n" + "\n".join([f"- {c.get('content', '')}" for c in all_comments if c.get('content')])

    analysis_prompt += (
        f"\n标注标题：{annotation.get('title', '')}"
        f"\n标注描述：{annotation.get('description', '')}"
        f"\n标注类型：{annotation.get('marker_type_label', '')}"
        f"\n坐标：{annotation.get('lat')}, {annotation.get('lng')}"
        f"{comments_text}"
    )

    workflow_logs = ["读取统一标注数据"]
    content_items = []
    if first_image:
        workflow_logs.append(f"装载图片输入: {first_image}")
        content_items.append({"type": "input_image", "image_url": first_image})
    else:
        workflow_logs.append("当前标注没有图片，切换为纯文字分析模式")
    content_items.append({"type": "input_text", "text": analysis_prompt})
    workflow_logs.append(f"审核分析智能体：调用模型 {AUDIT_AGENT_MODEL}")
    workflow_logs.append("审核分析智能体：等待方舟 Responses 返回结果")

    resp = await audit_agent_client.responses.create(
        model=AUDIT_AGENT_MODEL,
        input=[{"role": "user", "content": content_items}]
    )
    raw_analysis_text = _extract_reply_text(resp)
    if not raw_analysis_text:
        try:
            raw_analysis_text = _extract_reply_text_from_dump(resp.model_dump())
        except Exception:
            raw_analysis_text = ""

    parsed_result = _extract_json_block(raw_analysis_text)
    display_text, validation = _format_annotation_analysis_text(parsed_result, raw_analysis_text)
    workflow_logs.append("审核分析智能体：解析模型输出完成")
    workflow_logs.append(f"图文校验：{validation.get('match_reason')}")

    policy_decision = _decide_annotation_review(annotation, validation)
    evidence = policy_decision.get("evidence") or {}
    decision_prompt = (
        "你是审核决策智能体。请根据审核分析智能体输出和证据完整性，只返回 JSON。"
        "字段必须包含：decision、reason、archive_instruction。"
        "decision 只能是 approve、manual_review、reject。"
        "必须优先尊重 policy_decision；若 policy_decision 已是 approve，除非存在明确冲突证据，否则不得改成 manual_review。"
        "如果分析结果表达了“匹配合理、图文一致、证据充分”这类结论，且证据完整，可建议 approve。"
        "如果表达了“图文不匹配”，建议 reject。"
        "如果表达了“待确认、存疑、证据不足”，建议 manual_review。"
    )
    decision_input = json.dumps({
        "annotation_id": marker_id,
        "analysis_text": display_text,
        "raw_analysis_text": raw_analysis_text,
        "validation": validation,
        "policy_decision": policy_decision,
        "evidence": evidence,
    }, ensure_ascii=False)
    workflow_logs.append(f"审核决策智能体：调用模型 {DECISION_AGENT_MODEL}")
    decision_resp = await decision_agent_client.responses.create(
        model=DECISION_AGENT_MODEL,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": decision_prompt},
            {"type": "input_text", "text": decision_input},
        ]}]
    )
    raw_decision_text = _extract_reply_text(decision_resp)
    if not raw_decision_text:
        try:
            raw_decision_text = _extract_reply_text_from_dump(decision_resp.model_dump())
        except Exception:
            raw_decision_text = ""
    parsed_decision = _extract_json_block(raw_decision_text)
    agent_decision = _normalize_agent_decision(parsed_decision, policy_decision)
    review_decision = _merge_review_decision(policy_decision, agent_decision)
    workflow_logs.append(f"审核决策智能体：{review_decision['reason']}")
    if review_decision.get("archive_instruction"):
        workflow_logs.append(f"归档处置智能体：{review_decision['archive_instruction']}")

    persisted_ai_process = {
        "has_real_ai": True,
        "agent_structure": "real_multi_agent",
        "audit_agent_model": AUDIT_AGENT_MODEL,
        "decision_agent_model": DECISION_AGENT_MODEL,
        "guidance_agent_model": GUIDANCE_AGENT_MODEL,
        "flow_steps": _build_agent_flow_steps(workflow_logs, success=True)
    }
    persist_result = _persist_annotation_analysis_result(
        annotation=annotation,
        marker_type=marker_type,
        marker_id=marker_id,
        analysis_text=display_text,
        validation=validation,
        review_decision=review_decision,
        ai_process=persisted_ai_process,
        workflow_logs=workflow_logs,
    )
    workflow_logs = persist_result["workflow_logs"]
    persisted_ai_process = persist_result["ai_process"]

    blind_guidance_summary = await _generate_blind_guidance_summary(annotation, validation, review_decision)
    validation["blind_guidance_summary"] = blind_guidance_summary
    workflow_logs.append(f"导盲摘要智能体：{blind_guidance_summary}")
    persisted_ai_process["flow_steps"] = _build_agent_flow_steps(workflow_logs, success=True)
    _persist_blind_guidance_summary(
        marker_type=marker_type,
        marker_id=marker_id,
        pending_contribution_id=annotation.get("pendingContributionId"),
        validation=validation,
        ai_process=persisted_ai_process,
    )

    return {
        "success": True,
        "message": "AI 分析完成",
        "analysis_text": display_text,
        "raw_analysis_text": raw_analysis_text,
        "validation": validation,
        "review_decision": review_decision,
        "blind_guidance_summary": blind_guidance_summary,
        "raw_decision_text": raw_decision_text,
        "workflow_logs": workflow_logs,
        "workflow_flow_steps": _build_agent_flow_steps(workflow_logs, success=True),
        "workflow_mode": "real_multi_agent",
        "agent_models": {
            "audit_agent": AUDIT_AGENT_MODEL,
            "decision_agent": DECISION_AGENT_MODEL,
            "guidance_agent": GUIDANCE_AGENT_MODEL,
        },
        "persisted": persist_result.get("persisted", False),
        "target_db": persist_result.get("target_db"),
        "ai_process": persisted_ai_process,
    }

@app.post("/api/admin/annotations/analyze")
async def admin_analyze_annotation(request: Request, data: dict = Body(...)):
    try:
        if not await check_admin(request):
            return JSONResponse(
                status_code=403,
                content={"success": False, "message": "Not authorized", "workflow_logs": ["管理员鉴权失败"]}
            )

        marker_id = str(data.get("marker_id") or data.get("id") or "").strip()
        marker_type = str(data.get("marker_type") or "all").strip().lower()
        user_prompt = str(data.get("prompt") or "").strip()

        if not marker_id:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "Missing marker_id", "workflow_logs": ["缺少标注 ID"]}
            )

        annotations = _build_unified_annotation_items(marker_type)
        annotation = next((item for item in annotations if str(item.get("id")) == marker_id), None)
        if not annotation:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "Annotation not found", "workflow_logs": ["统一标注中未找到目标数据"]}
            )
        result = await _run_annotation_ai_workflow(
            annotation=annotation,
            marker_type=marker_type,
            marker_id=marker_id,
            base_url=str(request.base_url).rstrip("/"),
            user_prompt=user_prompt,
        )
        image_urls = annotation.get("imageUrls") or []
        first_image = image_urls[0] if image_urls else None
        if isinstance(first_image, str) and first_image.startswith("/"):
            first_image = str(request.base_url).rstrip("/") + first_image
        result["image_url"] = first_image
        result["analysis_mode"] = "image_text" if first_image else "text_only"
        result["analysis_started"] = True
        result["analysis_finished"] = True
        result["model"] = AUDIT_AGENT_MODEL
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        error_message = str(e)
        if "AuthenticationError" in error_message or "invalid api key" in error_message.lower():
            error_message = f"方舟鉴权失败，请检查 API Key 是否有效：{error_message}"
        elif "image" in error_message.lower():
            error_message = f"图片输入不可访问或格式不符合要求：{error_message}"

        print(f"❌ admin_analyze_annotation error: {e}")
        workflow_logs = locals().get("workflow_logs", ["初始化 AI 分析接口"])
        workflow_logs.append(f"模型调用失败: {error_message}")
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "message": error_message,
                "workflow_logs": workflow_logs,
                "workflow_flow_steps": _build_agent_flow_steps(workflow_logs, success=False),
                "image_url": locals().get("first_image"),
                "workflow_mode": "ai_only",
                "analysis_started": True,
                "analysis_finished": False,
                "model": LLM_MODEL,
            }
        )


@app.post("/api/admin/annotations/batch_analyze")
async def admin_batch_analyze_annotations(request: Request, data: dict = Body(default={})):
    try:
        if not await check_admin(request):
            return JSONResponse(status_code=403, content={"success": False, "message": "Not authorized"})

        scheduled_time = data.get("scheduled_time")
        disable_schedule = bool(data.get("disable_schedule"))
        if disable_schedule:
            scheduled_audit_state["enabled"] = False
            scheduled_audit_state["scheduled_time"] = ""
            _save_scheduled_audit_state()
            return JSONResponse(status_code=200, content={
                "success": True,
                "message": "已关闭定时全量审核任务",
                "scheduled": False,
                "schedule": scheduled_audit_state,
            })

        if scheduled_time is not None:
            normalized_time = _normalize_scheduled_time(scheduled_time)
            scheduled_audit_state["enabled"] = True
            scheduled_audit_state["scheduled_time"] = normalized_time
            _save_scheduled_audit_state()
            print(f"[Admin] Scheduled full audit configured for {normalized_time} daily.")

        result = await _execute_batch_annotation_analysis(
            base_url=str(request.base_url).rstrip("/"),
            trigger="manual",
        )
        result["scheduled"] = bool(scheduled_audit_state.get("enabled"))
        result["schedule"] = {
            "enabled": bool(scheduled_audit_state.get("enabled")),
            "scheduled_time": scheduled_audit_state.get("scheduled_time") or "",
            "last_run_at": scheduled_audit_state.get("last_run_at") or "",
            "last_result": scheduled_audit_state.get("last_result") or {},
        }
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        print(f"❌ admin_batch_analyze error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.get("/api/admin/annotations/batch_analyze/status")
async def admin_batch_analyze_status(request: Request):
    if not await check_admin(request):
        return JSONResponse(status_code=403, content={"success": False, "message": "Not authorized"})
    return JSONResponse(status_code=200, content={
        "success": True,
        "enabled": bool(scheduled_audit_state.get("enabled")),
        "scheduled_time": scheduled_audit_state.get("scheduled_time") or "",
        "last_run_at": scheduled_audit_state.get("last_run_at") or "",
        "last_result": scheduled_audit_state.get("last_result") or {},
    })


@app.get("/api/obstacles")
async def get_obstacles():
    # Obstacle status enum on the app side is:
    # 0 = active, 1 = candidateForRemoval, 2 = removed
    # App-created obstacles are written with status 0, so filtering on == 1 hides new data.
    result_list = [o.copy() for o in obstacles_data if int(o.get("status", 0)) != 2]
    return result_list

@app.post("/api/obstacles")
async def add_obstacle(obstacle: dict = Body(...)):
    if "id" not in obstacle:
         raise HTTPException(status_code=400, detail="Missing ID")
    
    # Check if exists (update)
    for i, o in enumerate(obstacles_data):
        if o["id"] == obstacle["id"]:
            obstacles_data[i] = obstacle
            save_obstacles()
            return {"status": "updated"}
            
    obstacles_data.append(obstacle)
    save_obstacles()
    return {"status": "added"}

@app.delete("/api/obstacles/{obstacle_id}")
async def delete_obstacle(obstacle_id: str):
    global obstacles_data, contributions_data
    initial_len = len(obstacles_data)
    obstacles_data[:] = [o for o in obstacles_data if str(o["id"]) != str(obstacle_id)]
    
    # Also check if it's a pending contribution
    contrib_len = len(contributions_data)
    contributions_data[:] = [c for c in contributions_data if str(c.get("markerId")) != str(obstacle_id)]
    
    if len(obstacles_data) < initial_len or len(contributions_data) < contrib_len:
        save_obstacles()
        if len(contributions_data) < contrib_len:
            save_contributions()
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Obstacle not found")

# --- Shop APIs ---

@app.get("/api/disputes")
async def get_disputes(request: Request):
    _ensure_runtime_obstacle_disputes()
    region_code = (request.query_params.get("regionCode") or "").strip()
    target_type_filter = (request.query_params.get("targetType") or "").strip().lower()

    formatted_disputes = []
    for dispute in disputes_data:
        payload = _format_dispute_payload(dispute)
        if payload.get("status") == "resolved":
            continue
        if target_type_filter and str(payload.get("target_type") or "").lower() != target_type_filter:
            continue
        if region_code and (payload.get("regionCode") or "") != region_code:
            continue
        formatted_disputes.append(payload)

    formatted_disputes.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    region_catalog = _build_dispute_region_catalog(target_type_filter)
    region_catalog.sort(key=lambda item: str(item.get("regionName") or item.get("regionCode") or ""))

    return {
        "success": True,
        "disputes": formatted_disputes,
        "regions": region_catalog,
    }

@app.post("/api/dispute/vote")
async def vote_dispute(data: dict = Body(...)):
    _ensure_runtime_obstacle_disputes()
    dispute_id = str(data.get("disputeId") or "").strip()
    user_id = str(data.get("userId") or "").strip()
    vote_option = str(data.get("voteOption") or "").strip().lower()

    if not all([dispute_id, user_id, vote_option]):
        raise HTTPException(status_code=400, detail="Missing parameters")
    if vote_option not in {"merge", "separate", "reject"}:
        raise HTTPException(status_code=400, detail="Unsupported vote option")

    for dispute in disputes_data:
        if str(dispute.get("id")) != dispute_id:
            continue
        normalized_dispute = _normalize_dispute_record(dispute)
        normalized_dispute["status"] = "pending"
        for option in ["merge", "separate", "reject"]:
            normalized_dispute["votes"].setdefault(option, [])
            normalized_dispute["votes"][option] = [uid for uid in normalized_dispute["votes"][option] if uid != user_id]
        normalized_dispute["votes"][vote_option].append(user_id)
        dispute.clear()
        dispute.update(normalized_dispute)
        save_disputes()
        return {"success": True, "message": "Vote recorded", "dispute": _format_dispute_payload(dispute)}

    raise HTTPException(status_code=404, detail="Dispute not found")

@app.post("/api/admin/dispute/{dispute_id}/resolve")
async def resolve_dispute(dispute_id: str, data: dict = Body(...)):
    _ensure_runtime_obstacle_disputes()
    resolution = data.get("resolution") or data.get("action")
    if not resolution:
        raise HTTPException(status_code=400, detail="Missing resolution")

    for dispute in disputes_data:
        if str(dispute.get("id")) != str(dispute_id):
            continue
        resolved_payload = _resolve_dispute_record(dispute, str(resolution))
        save_disputes()
        return {"success": True, "message": f"Dispute resolved with {resolution}", "dispute": resolved_payload}

    raise HTTPException(status_code=404, detail="Dispute not found")


@app.post("/api/admin/disputes/{dispute_id}/resolve")
async def resolve_dispute_plural(dispute_id: str, data: dict = Body(...)):
    return await resolve_dispute(dispute_id, data)

@app.get("/api/shops")
async def get_shops():
    # Shop records created by the app are stored with status 0 by default.
    # The previous == 1 filter hid newly submitted shops from both app and dashboard views.
    return [s.copy() for s in shops_data if int(s.get("status", 0)) != 2]

@app.put("/api/admin/shops/{shop_id}")
async def admin_update_shop(shop_id: str, data: dict = Body(...)):
    global shops_data
    for i, s in enumerate(shops_data):
        if str(s.get("id")) == str(shop_id):
            if "title" in data:
                shops_data[i]["title"] = data["title"]
            if "description" in data:
                shops_data[i]["description"] = data["description"]
            if "status" in data:
                shops_data[i]["status"] = data["status"]
            save_shops()
            return {"success": True, "shop": shops_data[i]}
    raise HTTPException(status_code=404, detail="Shop not found")

@app.post("/api/shops")
async def add_shop(shop: dict = Body(...)):
    if "id" not in shop:
         raise HTTPException(status_code=400, detail="Missing ID")
    
    # Check if exists (update)
    for i, s in enumerate(shops_data):
        if s["id"] == shop["id"]:
            shops_data[i] = shop
            save_shops()
            return {"status": "updated"}
            
    shops_data.append(shop)
    save_shops()
    return {"status": "added"}

@app.delete("/api/shops/{shop_id}")
async def delete_shop(shop_id: str):
    global shops_data, contributions_data
    initial_len = len(shops_data)
    shops_data[:] = [s for s in shops_data if str(s["id"]) != str(shop_id)]
    
    # Also check if it's a pending contribution
    contrib_len = len(contributions_data)
    contributions_data[:] = [c for c in contributions_data if str(c.get("markerId")) != str(shop_id)]
    
    if len(shops_data) < initial_len or len(contributions_data) < contrib_len:
        save_shops()
        if len(contributions_data) < contrib_len:
            save_contributions()
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Shop not found")

@app.post("/api/shops/{shop_id}/image")
async def upload_shop_image(shop_id: str, request: Request, image: UploadFile = File(...)):
    global shops_data, contributions_data
    # Verify shop exists
    shop = next((s for s in shops_data if str(s.get("id")) == str(shop_id)), None)
    contribution = None
    if not shop:
        # Check if it's a pending contribution
        contribution = next((c for c in contributions_data if str(c.get("markerId")) == str(shop_id)), None)
        if not contribution:
            raise HTTPException(status_code=404, detail="Shop not found")
        
    images_dir = os.path.join(CURRENT_DIR, "static", "images")
    if not os.path.exists(images_dir):
        os.makedirs(images_dir)
    
    file_ext = os.path.splitext(image.filename)[1]
    if not file_ext:
        file_ext = ".jpg"
        
    filename = f"{shop_id}_{int(time.time())}{file_ext}"
    file_path = os.path.join(images_dir, filename)
    
    try:
        with open(file_path, "wb") as buffer:
            content = await image.read()
            buffer.write(content)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"File save error: {e}")
         
    # Construct URL dynamically based on request host instead of hardcoded IP
    host = request.headers.get("host", "127.0.0.1:8000")
    image_url = f"http://{host}/static/images/{filename}"
    
    target = shop if shop else contribution
    
    if "imageUrls" not in target:
        target["imageUrls"] = []
    
    target["imageUrls"].append(image_url)
    
    if shop:
        save_shops()
    else:
        # Also sync imageUrls to imageUrl for contribution backward compatibility
        target["imageUrl"] = image_url
        save_contributions()
    
    return {"status": "success", "imageUrl": image_url}

@app.post("/api/obstacles/{obstacle_id}/image")
async def upload_obstacle_image(obstacle_id: str, request: Request, image: UploadFile = File(...)):
    global obstacles_data, contributions_data
    # Verify obstacle exists
    obstacle = next((o for o in obstacles_data if str(o.get("id")) == str(obstacle_id)), None)
    contribution = None
    if not obstacle:
        # Check if it's a pending contribution
        contribution = next((c for c in contributions_data if str(c.get("markerId")) == str(obstacle_id)), None)
        if not contribution:
            raise HTTPException(status_code=404, detail="Obstacle not found")
        
    images_dir = os.path.join(CURRENT_DIR, "static", "images")
    if not os.path.exists(images_dir):
        os.makedirs(images_dir)
    
    file_ext = os.path.splitext(image.filename)[1]
    if not file_ext:
        file_ext = ".jpg"
        
    filename = f"{obstacle_id}_{int(time.time())}{file_ext}"
    file_path = os.path.join(images_dir, filename)
    
    try:
        with open(file_path, "wb") as buffer:
            content = await image.read()
            buffer.write(content)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"File save error: {e}")
         
    # Construct URL dynamically based on request host instead of hardcoded IP
    host = request.headers.get("host", "127.0.0.1:8000")
    image_url = f"http://{host}/static/images/{filename}"
    
    target = obstacle if obstacle else contribution
    
    if "imageUrls" not in target:
        target["imageUrls"] = []
    
    target["imageUrls"].append(image_url)
    
    if obstacle:
        save_obstacles()
    else:
        # Also sync imageUrls to imageUrl for contribution backward compatibility
        target["imageUrl"] = image_url
        save_contributions()
    
    return {"status": "success", "imageUrl": image_url}

# --- Authentication & Admin ---
from fastapi.security import APIKeyCookie
from fastapi import Depends, Response

ADMIN_USER = "admin"
ADMIN_PASS = "admin888" # Simple hardcoded credentials
ADMIN_SESSIONS = set() # Store valid session tokens
ADMIN_SESSION_SECRET = os.getenv("ADMIN_SESSION_SECRET", f"{ADMIN_USER}:{ADMIN_PASS}:omnivision")
ADMIN_SESSION_MAX_AGE_SECONDS = int(os.getenv("ADMIN_SESSION_MAX_AGE_SECONDS", "604800"))


def _issue_admin_token() -> str:
    exp = int(time.time()) + ADMIN_SESSION_MAX_AGE_SECONDS
    payload = f"{ADMIN_USER}|{exp}"
    payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(
        ADMIN_SESSION_SECRET.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_admin_token(token: str) -> bool:
    try:
        if not token or "." not in token:
            return False
        payload_b64, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(
            ADMIN_SESSION_SECRET.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return False
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        username, exp_str = payload.split("|", 1)
        return username == ADMIN_USER and int(exp_str) >= int(time.time())
    except Exception:
        return False

@app.post("/api/admin/login")
async def admin_login(request: Request):
    # 兼容 JSON 和 FormData 格式
    try:
        # First try parsing as JSON
        body = await request.body()
        if body:
            req = json.loads(body)
            username = req.get("username")
            password = req.get("password")
        else:
            username = None
            password = None
    except:
        # If JSON fails, try form data
        try:
            form = await request.form()
            username = form.get("username")
            password = form.get("password")
        except:
            username = None
            password = None

    print(f"LOGIN ATTEMPT: User={username}, Pass={'*' * len(password) if password else 'None'}")
    
    if username == ADMIN_USER and password == ADMIN_PASS:
        token = _issue_admin_token()
        ADMIN_SESSIONS.add(token)
        response = JSONResponse(content={"success": True})
        response.set_cookie(
            key="admin_token",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=ADMIN_SESSION_MAX_AGE_SECONDS,
            path="/",
        )
        return response
        
    return JSONResponse(content={"success": False, "message": "Invalid credentials"}, status_code=401)

@app.post("/api/admin/logout")
async def admin_logout(request: Request, response: Response):
    token = request.cookies.get("admin_token")
    if token in ADMIN_SESSIONS:
        ADMIN_SESSIONS.remove(token)
    response.delete_cookie("admin_token", path="/")
    return {"success": True}

async def check_admin(request: Request):
    token = request.cookies.get("admin_token")
    if not token:
        return False
    return token in ADMIN_SESSIONS or _verify_admin_token(token)

@app.get("/admin/login")
async def admin_login_page():
    with open(os.path.join(CURRENT_DIR, "static/admin_login.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        })

@app.get("/admin/dashboard")
async def admin_dashboard_page(request: Request):
    if not await check_admin(request):
        return RedirectResponse(url="/admin/login")
    with open(os.path.join(CURRENT_DIR, "static/admin_dashboard.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        })

@app.delete("/api/admin/contribution/{contribution_id}")
async def admin_delete_contribution(contribution_id: str, request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    global contributions_data
    initial_len = len(contributions_data)
    contributions_data = [c for c in contributions_data if c['id'] != contribution_id]
    
    if len(contributions_data) < initial_len:
        save_contributions()
        return {"success": True}
    return {"success": False}

# --- Route Memories Management (AI Semantic Aliases) ---
ROUTE_MEMORIES_FILE = os.path.join(CURRENT_DIR, "route_memories.json")
route_memories_data = []

def load_route_memories():
    global route_memories_data
    route_memories_data = []
    if os.path.exists(ROUTE_MEMORIES_FILE):
        try:
            with open(ROUTE_MEMORIES_FILE, "r", encoding="utf-8") as f:
                route_memories_data = json.load(f)
        except Exception as e:
            print(f"Error loading route memories: {e}")
            route_memories_data = []
    route_memories_data = [item for item in route_memories_data if isinstance(item, dict)]

def save_route_memories():
    try:
        with open(ROUTE_MEMORIES_FILE, "w", encoding="utf-8") as f:
            json.dump(route_memories_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving route memories: {e}")

load_route_memories()

@app.get("/api/admin/route_memories")
async def admin_get_route_memories(request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    normalized = [_normalize_route_memory_item(mem) for mem in route_memories_data]
    normalized.sort(key=lambda x: (str(x.get("device_key")), -(float(x.get("created_at") or 0))))
    return {"success": True, "data": normalized}

@app.post("/api/admin/route_memories/save")
async def admin_save_route_memory(request: Request, data: dict = Body(...)):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")

    client_id = str(data.get("client_id") or data.get("device_key") or data.get("user_id") or "").strip()
    alias = str(data.get("alias") or "").strip()
    lat = data.get("lat")
    lng = data.get("lng")
    if not client_id or not alias or lat is None or lng is None:
        raise HTTPException(status_code=400, detail="Missing client_id, alias, lat or lng")

    try:
        lat = float(lat)
        lng = float(lng)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid lat/lng")

    save_result = _save_current_location_as_route_memory(client_id, alias, lat, lng)
    normalized_item = _normalize_route_memory_item(save_result["item"])
    await manager.broadcast({
        "type": "route_memory_saved",
        "client_id": client_id,
        "memory": normalized_item,
        "created": bool(save_result.get("created")),
        "timestamp": time.time(),
    })
    return {"success": True, "created": bool(save_result.get("created")), "memory": normalized_item}

@app.post("/api/admin/navigation/simulate")
async def admin_simulate_navigation(request: Request, data: dict = Body(...)):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    if not ALLOW_ADMIN_NAV_SIMULATE:
        raise HTTPException(status_code=403, detail="Navigation simulate endpoint disabled in current environment")

    client_id = str(data.get("client_id") or data.get("device_id") or data.get("user_id") or "").strip()
    destination_name = str(data.get("destination_name") or data.get("alias") or "模拟导航终点").strip() or "模拟导航终点"
    start = data.get("start") if isinstance(data.get("start"), dict) else {}
    destination = data.get("destination") if isinstance(data.get("destination"), dict) else {}
    start_lat = start.get("lat")
    start_lng = start.get("lng")
    dest_lat = destination.get("lat")
    dest_lng = destination.get("lng")
    if not client_id or None in {start_lat, start_lng, dest_lat, dest_lng}:
        raise HTTPException(status_code=400, detail="Missing client_id/start/destination coordinates")

    try:
        start_lat = float(start_lat)
        start_lng = float(start_lng)
        dest_lat = float(dest_lat)
        dest_lng = float(dest_lng)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid navigation coordinates")

    state = _get_or_create_device_state(client_id)
    state["lat"] = start_lat
    state["lng"] = start_lng
    state["coord_system"] = "gcj02"
    state["status"] = state.get("status") or "online"
    state["last_active"] = time.time()

    result_msg = nav_manager.start_navigation_to_coords(client_id, destination_name, dest_lng, dest_lat)
    _record_device_flow_event(
        client_id,
        last_instruction=result_msg,
        last_instruction_at=time.time(),
        assist_active=False,
        assist_status="idle",
        assist_reason="",
    )
    await _prime_navigation_after_start(client_id)
    route_payload = _build_route_update_payload(client_id)
    guide_flow = _build_guide_flow_snapshot(client_id)
    if route_payload.get("instruction_text"):
        await _broadcast_navigation_instruction(client_id, route_payload["instruction_text"], {"mode": str(route_payload.get("mode") or "macro")})

    return {
        "success": True,
        "message": result_msg,
        "route": route_payload,
        "guide_flow": guide_flow,
    }

@app.delete("/api/admin/route_memories/{mem_id}")
async def admin_delete_route_memory(mem_id: str, request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    global route_memories_data
    route_memories_data = [m for m in route_memories_data if m['id'] != mem_id]
    save_route_memories()
    return {"success": True}

# --- User Management APIs ---

@app.get("/api/admin/users")
async def admin_get_users(request: Request):
    if not await check_admin(request):
        # Allow reading users list for internal use if needed, but for admin panel we need check
        # For now, let's just return list if authorized, else 403
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Reload from DB to get fresh status
    load_users()
    return {"success": True, "users": users_data}

@app.put("/api/admin/users/{user_id}/status")
async def admin_update_user_status(user_id: str, request: Request, data: dict = Body(...)):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    new_status = data.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="Missing status")
        
    # Update in memory
    found = False
    for u in users_data:
        if u['id'] == user_id:
            u['status'] = new_status
            found = True
            break
            
    if not found:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Update in DB
    save_users() # This saves to JSON and tries to sync to DB
    # Explicitly update DB column to be sure
    update_user_status_in_db(user_id, new_status)
    
    return {"success": True, "status": new_status}

@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    global users_data
    users_data = [u for u in users_data if u['id'] != user_id]
    save_users()
    
    # Also remove from DB
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM users WHERE email=?", (user_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error deleting user from DB: {e}")
            
    return {"success": True}

# 模型路径配置
# 优先使用本地 models 目录
LOCAL_MODEL_DIR = os.path.join(CURRENT_DIR, "models")
MODEL_PATHS = {
    "traffic_light": os.path.join(LOCAL_MODEL_DIR, "trafficlight_det.pt"),
    "blind_path": os.path.join(LOCAL_MODEL_DIR, "blindpath_seg.pt"),
    "crosswalk": os.path.join(LOCAL_MODEL_DIR, "crosswalk_seg.pt")
}

class ModelManager:
    def __init__(self):
        self.models = {}
        self.current_mode = "monitor" # monitor, traffic_light, blind_path, daily_mode
        self.last_results = ["系统: 监控模式运行中"]
        self.navigator = None
        
        if HAS_YOLO:
            print("正在加载 AI 模型...")
            for name, path in MODEL_PATHS.items():
                if os.path.exists(path):
                    try:
                        self.models[name] = YOLO(path)
                        print(f"模型加载成功: {name}")
                    except Exception as e:
                        print(f"模型加载失败 {name}: {e}")
                else:
                    print(f"模型文件不存在: {path}")
            
            # 加载通用模型用于日常模式
            try:
                self.models["daily"] = YOLO("yolov8n.pt")
                print("模型加载成功: daily (yolov8n)")
            except Exception as e:
                print(f"模型加载失败 daily: {e}")

            # 初始化导航器
            if HAS_NAVIGATOR and "blind_path" in self.models:
                try:
                    # 传入盲道模型
                    self.navigator = BlindPathNavigator(yolo_model=self.models["blind_path"])
                    print("BlindPathNavigator 初始化成功")
                except Exception as e:
                    print(f"BlindPathNavigator 初始化失败: {e}")

    def switch_mode(self, mode):
        if mode not in ["monitor", "traffic_light", "blind_path", "crosswalk", "volunteer_mode", "daily_mode"]:
            return False
            
        # 防止重复切换同一模式
        if mode == self.current_mode:
            return True
            
        self.current_mode = mode
        # 切换模式时重置导航器状态
        if mode == "blind_path" and self.navigator:
            self.navigator.reset()
        
        # 记录模式切换事件，以便在流中显示
        mode_names = {
            "monitor": "监控",
            "traffic_light": "红绿灯检测",
            "blind_path": "盲道导航",
            "crosswalk": "斑马线检测",
            "volunteer_mode": "志愿者求助",
            "daily_mode": "日常行走"
        }
        display_name = mode_names.get(mode, mode)
        
        self.last_results = [f"系统: 已切换到 {display_name} 模式"]
        self.system_message_time = time.time() # 记录系统消息产生时间
        self._last_pushed_text = "" # 强制推送更新
        self._last_pushed_time = 0
        
        # 语音播报切换状态
        if HAS_AUDIO:
            try:
                voice_text = f"切换到{display_name}模式。"
                play_voice_text(voice_text)
            except Exception as e:
                print(f"语音播报失败: {e}")
        
        return True

    def _process_daily_mode(self, frame):
        """日常行走模式核心逻辑"""
        # 1. 基础物体检测
        if "daily" not in self.models:
            return frame, ["模型未加载"]
            
        results = self.models["daily"](frame, verbose=False, classes=[0, 1, 2, 3, 5, 7, 9, 11, 13]) 
        
        annotated_frame = results[0].plot()
        detected_objects = []
        guidance = []
        
        # 提取检测到的物体
        boxes = results[0].boxes
        img_h, img_w = frame.shape[:2]
        
        # 简单的避障逻辑
        left_obstacles = 0
        right_obstacles = 0
        center_obstacles = 0
        
        for box in boxes:
            cls_id = int(box.cls[0])
            label = results[0].names[cls_id]
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            obj_center = (x1 + x2) / 2
            
            # 距离估算 (越靠下越近)
            is_close = y2 > img_h * 0.8
            
            if is_close:
                if obj_center < img_w * 0.4:
                    left_obstacles += 1
                elif obj_center > img_w * 0.6:
                    right_obstacles += 1
                else:
                    center_obstacles += 1
                    
            if label == "traffic light":
                detected_objects.append("前方有红绿灯")
            elif label in ["car", "truck", "bus"] and is_close:
                detected_objects.append("注意前方车辆")
        
        # 导航建议
        if center_obstacles > 0:
            if left_obstacles < right_obstacles:
                guidance.append("前方有障碍，请向左偏")
            else:
                guidance.append("前方有障碍，请向右偏")
        else:
            if left_obstacles > 2: 
                guidance.append("左侧有墙或障碍，请保持距离")
            elif right_obstacles > 2:
                guidance.append("右侧有墙或障碍，请保持距离")
                
        final_msgs = list(set(detected_objects + guidance))
        return annotated_frame, final_msgs

    def process(self, frame):
        if self.current_mode == "monitor" or not HAS_YOLO:
            return frame, self.last_results
            
        if self.current_mode == "volunteer_mode":
            overlay = frame.copy()
            cv2.rectangle(overlay, (0, 0), (frame.shape[1], 100), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
            cv2.putText(frame, "CALLING VOLUNTEER...", (50, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            if not self.last_results or not self.last_results[0].startswith("系统:"):
                 self.last_results = ["系统: 正在呼叫志愿者..."]
            return frame, self.last_results

        if self.current_mode == "blind_path" and self.navigator:
            try:
                result = self.navigator.process_frame(frame)
                annotated_frame = result.annotated_image if result.annotated_image is not None else frame
                if result.guidance_text:
                    self.last_results = [f"导航指令: {result.guidance_text}"]
                return annotated_frame, self.last_results
            except Exception as e:
                print(f"导航器处理出错: {e}")
                return frame, ["导航器错误"]
        
        if self.current_mode == "daily_mode":
            try:
                annotated_frame, msgs = self._process_daily_mode(frame)
                if msgs:
                    self.last_results = msgs
                return annotated_frame, self.last_results
            except Exception as e:
                print(f"日常模式出错: {e}")
                return frame, ["检测错误"]

        model_name = self.current_mode
        if model_name not in self.models:
            return frame, None

        try:
            results = self.models[model_name](frame, verbose=False)
            annotated_frame = results[0].plot()
            if annotated_frame is None: annotated_frame = frame
            
            text_results = []
            for r in results:
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    cls_name = self.models[model_name].names[cls_id]
                    text_results.append(f"识别到: {cls_name} ({conf:.2f})")
            
            if text_results:
                self.last_results = text_results
            else:
                is_recent_system_msg = False
                if self.last_results and self.last_results[0].startswith("系统:"):
                    if time.time() - getattr(self, 'system_message_time', 0) < 3.0:
                        is_recent_system_msg = True
                if not is_recent_system_msg:
                    self.last_results = []
                
            return annotated_frame, self.last_results
        except Exception as e:
            print(f"推理出错: {e}")
            return frame, None

    def get_latest_recognition(self):
        # 1. 优先处理系统消息（强制推送）
        if self.last_results:
            current_text = ", ".join(sorted(self.last_results))
            
            # 初始化状态变量
            if not hasattr(self, '_last_pushed_text'):
                self._last_pushed_text = ""
            if not hasattr(self, '_last_pushed_time'):
                self._last_pushed_time = 0
            
            now = time.time()
            
            # 逻辑修正：
            # 1. 如果是系统消息，且与上次推送的不同（或者是刚切换），推送并更新状态
            # 2. 如果是普通识别结果，且内容变化或超过冷却时间，推送
            
            should_push = False
            
            if current_text.startswith("系统:"):
                # 系统消息：只有当内容真正改变时才推送，或者这是该模式下的第一次推送
                # 我们在 switch_mode 里重置了 _last_pushed_text，所以第一次肯定会推
                # 关键：如果内容一样，绝对不推！防止刷屏
                if current_text != self._last_pushed_text:
                    should_push = True
            else:
                # 普通识别消息：内容变化 OR 超过冷却时间
                if current_text != self._last_pushed_text or (now - self._last_pushed_time > 3.0):
                    should_push = True
            
            if should_push:
                self._last_pushed_text = current_text
                self._last_pushed_time = now
                return self.last_results[0]
                
        return None

# --- Move API outside class ---
@app.post("/api/volunteer/call")
async def call_volunteer(req: dict):
    action = req.get("action", "start")
    client_id = req.get("client_id")
    if action == "start":
        if model_manager.switch_mode("volunteer_mode"):
            target_ids = [client_id] if client_id else [cid for cid, st in manager.device_states.items() if st.get("status") == "online"]
            for cid in target_ids:
                _record_device_flow_event(cid, assist_active=True, assist_status="pending", assist_reason="盲人主动求助或测试触发", assist_requested_at=time.time())
            msg = {"type": "volunteer_request", "status": "pending", "client_id": client_id, "timestamp": time.time()}
            await manager.broadcast(msg)
            for cid in target_ids:
                await manager.send_to_client_input(cid, {**msg, "client_id": cid})
            return {"status": "success", "mode": "volunteer_mode"}
    elif action == "stop":
        if model_manager.switch_mode("monitor"):
            target_ids = [client_id] if client_id else list(manager.device_states.keys())
            for cid in target_ids:
                _record_device_flow_event(cid, assist_active=False, assist_status="idle", assist_reason="", assist_requested_at=0)
            msg = {"type": "volunteer_request", "status": "cancelled", "client_id": client_id, "timestamp": time.time()}
            await manager.broadcast(msg)
            for cid in target_ids:
                await manager.send_to_client_input(cid, {**msg, "client_id": cid})
            return {"status": "success", "mode": "monitor"}
    return {"status": "error", "message": "Failed to switch mode"}

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.client_input_connections: Dict[str, WebSocket] = {}
        self.device_states: Dict[str, dict] = {}
        self.last_recognition_text: str = ""
        self.last_recognition_at: float = 0.0

    async def push_recognition_update(self, client_id: str = None):
        if not self.active_connections:
            return
        recognition_text = simulator.get_recognition_result()
        if not recognition_text:
            return
        if recognition_text == self.last_recognition_text and (time.time() - self.last_recognition_at) < 1.0:
            return
        self.last_recognition_text = recognition_text
        self.last_recognition_at = time.time()
        await self.broadcast({
            "type": "recognition_update",
            "client_id": client_id,
            "text": recognition_text,
            "recognition_ts": self.last_recognition_at,
        })

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

        # 连接成功后，如果有已缓存的设备路线数据，立即下发给新连接的前端
        for client_id, state in self.device_states.items():
            route_payload = state.get("route_payload")
            if route_payload and route_payload.get("route"):
                try:
                    await websocket.send_text(json.dumps(route_payload))
                except Exception as e:
                    print(f"Failed to sync initial route to new connection: {e}")
                    continue
            elif state.get("route"):
                try:
                    await websocket.send_text(json.dumps(_build_route_update_payload(client_id)))
                except Exception as e:
                    print(f"Failed to sync initial route to new connection: {e}")

        # 连接成功后补发最近一次识别结果，保证 recognition 通道有独立初始状态
        if self.last_recognition_text:
            try:
                await websocket.send_text(json.dumps({
                    "type": "recognition_update",
                    "client_id": None,
                    "text": self.last_recognition_text,
                    "recognition_ts": self.last_recognition_at or time.time(),
                }))
            except Exception as e:
                print(f"Failed to sync initial recognition to new connection: {e}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

        stale_ids = [cid for cid, ws in self.client_input_connections.items() if ws == websocket]
        for cid in stale_ids:
            del self.client_input_connections[cid]

    def register_client_input(self, client_id: str, websocket: WebSocket):
        self.client_input_connections[client_id] = websocket

    def unregister_client_input(self, client_id: str, websocket: WebSocket = None):
        current = self.client_input_connections.get(client_id)
        if current and (websocket is None or current == websocket):
            del self.client_input_connections[client_id]

    async def send_to_client_input(self, client_id: str, message: dict) -> bool:
        websocket = self.client_input_connections.get(client_id)
        if not websocket:
            return False
        try:
            await websocket.send_text(json.dumps(message))
            return True
        except Exception as e:
            print(f"Failed to send control message to client {client_id}: {e}")
            self.unregister_client_input(client_id, websocket)
            return False

    def update_device_state(self, client_id: str, status: str = None, lat: float = None, lng: float = None, battery: int = None, angle: float = None,
                            speed: float = None, sats: int = None, hdop: float = None, alt: float = None,
                            camera_state: str = None, audio_output_state: str = None, safety_prompt_state: str = None,
                            last_heartbeat_sent_at: float = None, last_camera_frame_at: float = None, last_audio_output_at: float = None,
                            last_camera_error: str = None, last_audio_error: str = None, last_safety_prompt: str = None):
        if client_id not in self.device_states:
            self.device_states[client_id] = {
                "speed": 0.0, "sats": 0, "hdop": 99.9, "alt": 0.0,
                "coord_system": "gcj02", "route": [], "route_payload": None
            }

        state = self.device_states[client_id]
        state["last_active"] = time.time()

        if status:
            state["status"] = status
        if lat is not None:
            state["lat"] = lat
            state["coord_system"] = "gcj02"
        if lng is not None:
            state["lng"] = lng
            state["coord_system"] = "gcj02"
        if battery is not None:
            state["battery"] = battery
        if angle is not None:
            state["angle"] = angle

        if speed is not None:
            state["speed"] = speed
        if sats is not None:
            state["sats"] = sats
        if hdop is not None:
            state["hdop"] = hdop
        if alt is not None:
            state["alt"] = alt
        if camera_state is not None:
            state["camera_state"] = camera_state
        if audio_output_state is not None:
            state["audio_output_state"] = audio_output_state
        if safety_prompt_state is not None:
            state["safety_prompt_state"] = safety_prompt_state
        if last_heartbeat_sent_at is not None:
            state["last_heartbeat_sent_at"] = last_heartbeat_sent_at
        if last_camera_frame_at is not None:
            state["last_camera_frame_at"] = last_camera_frame_at
        if last_audio_output_at is not None:
            state["last_audio_output_at"] = last_audio_output_at
        if last_camera_error is not None:
            state["last_camera_error"] = last_camera_error
        if last_audio_error is not None:
            state["last_audio_error"] = last_audio_error
        if last_safety_prompt is not None:
            state["last_safety_prompt"] = last_safety_prompt

    async def broadcast(self, message: dict, exclude: WebSocket = None):
        # 优化：并行发送
        if not self.active_connections:
            return

        recognition_text = simulator.get_recognition_result()
        if recognition_text:
            self.last_recognition_text = recognition_text
            self.last_recognition_at = time.time()

        # 补充字段
        message["signal"] = simulator.get_signal_strength()
        message["timestamp"] = time.time()
        
        # 序列化一次
        json_str = json.dumps(message)
        
        # 群发 - 使用 fire-and-forget 避免阻塞
        # 注意：如果 active_connections 很大，这可能产生大量 task
        # 但对于 demo 场景 (1-2 connections) 是安全的
        
        dead_connections = []
        for connection in list(self.active_connections):
            if exclude is not None and connection is exclude:
                continue
            try:
                await connection.send_text(json_str)
            except:
                dead_connections.append(connection)
        for conn in dead_connections:
            self.disconnect(conn)

manager = ConnectionManager()

# 初始化模型管理器
model_manager = ModelManager()


def _get_or_create_device_state(client_id: str) -> dict:
    if client_id not in manager.device_states:
        manager.device_states[client_id] = {
            "speed": 0.0,
            "sats": 0,
            "hdop": 99.9,
            "alt": 0.0,
            "coord_system": "gcj02",
            "route": [],
            "route_payload": None,
            "voice_mode": VOICE_DEFAULT_MODE,
            "voice_mode_changed_at": 0.0,
            "voice_awake_until": 0.0,
            "voice_last_wake_at": 0.0,
            "voice_wake_source": "init",
            "voice_last_event": "init",
            "voice_last_ignored_text": "",
            "voice_last_routed_text": "",
        }
    else:
        state = manager.device_states[client_id]
        state.setdefault("voice_mode", VOICE_DEFAULT_MODE)
        state.setdefault("voice_mode_changed_at", 0.0)
        state.setdefault("voice_awake_until", 0.0)
        state.setdefault("voice_last_wake_at", 0.0)
        state.setdefault("voice_wake_source", "init")
        state.setdefault("voice_last_event", "init")
        state.setdefault("voice_last_ignored_text", "")
        state.setdefault("voice_last_routed_text", "")
    return manager.device_states[client_id]


def _record_device_flow_event(client_id: str, **kwargs):
    state = _get_or_create_device_state(client_id)
    state["last_active"] = time.time()
    for key, value in kwargs.items():
        if value is not None:
            state[key] = value


def _build_guide_flow_snapshot(client_id: str, state: Optional[dict] = None) -> dict:
    state = state or manager.device_states.get(client_id, {})
    session = nav_manager.sessions.get(client_id)
    route_payload = state.get("route_payload") if isinstance(state.get("route_payload"), dict) else {}
    now_ts = time.time()

    lat = state.get("lat")
    lng = state.get("lng")
    angle = state.get("angle", 0)
    last_instruction = state.get("last_instruction", "")
    last_instruction_at = float(state.get("last_instruction_at", 0) or 0)
    last_video_frame_at = float(
        state.get("last_camera_frame_at", state.get("last_video_frame_at", 0)) or 0
    )
    last_heartbeat_sent_at = float(state.get("last_heartbeat_sent_at", 0) or 0)
    last_audio_output_at = float(state.get("last_audio_output_at", 0) or 0)
    assist_active = bool(state.get("assist_active"))
    assist_reason = state.get("assist_reason") or ""
    assist_status = state.get("assist_status") or "idle"
    camera_state = state.get("camera_state") or "unknown"
    audio_output_state = state.get("audio_output_state") or "idle"
    safety_prompt_state = state.get("safety_prompt_state") or "idle"
    voice_mode = state.get("voice_mode") or VOICE_DEFAULT_MODE
    voice_last_event = state.get("voice_last_event") or "init"
    voice_last_routed_text = state.get("voice_last_routed_text") or ""
    voice_last_ignored_text = state.get("voice_last_ignored_text") or ""

    distance = None
    route_points = 0
    destination_name = "未开始导航"
    nav_status = "IDLE"
    route_generated = False
    last_macro_message = ""
    last_obstacle_warning = ""
    last_route_instruction = ""
    last_visual_instruction = ""
    last_visual_analysis_at = 0.0
    last_mode_switch_at = 0.0

    if session:
        destination_name = session.destination_name
        nav_status = session.status
        route_points = len(session.route_polyline or [])
        route_generated = bool(session.route_polyline)
        last_macro_message = session.last_macro_message or ""
        last_obstacle_warning = session.last_obstacle_warning or ""
        last_route_instruction = session.last_route_instruction or ""
        last_visual_instruction = session.last_visual_instruction or ""
        last_visual_analysis_at = float(session.last_visual_analysis_at or 0)
        last_mode_switch_at = float(session.last_mode_switch_at or 0)
        if lat is not None and lng is not None and session.destination_coords:
            distance = round(session._calc_distance((lng, lat), session.destination_coords), 1)
    elif route_payload.get("route"):
        destination_name = str(route_payload.get("destination_name") or state.get("destination_name") or "导航目标").strip() or "导航目标"
        route_points = len(route_payload.get("route") or [])
        route_generated = route_points > 0
        nav_status = str(route_payload.get("nav_status") or "MACRO").strip().upper()
        if nav_status in {"IDLE", ""}:
            nav_status = "MACRO"
        last_macro_message = str(route_payload.get("instruction_text") or state.get("last_instruction") or "").strip()
        last_route_instruction = last_macro_message
        destination_coords = route_payload.get("destination_coords") or state.get("destination_coords")
        if lat is not None and lng is not None and isinstance(destination_coords, (list, tuple)) and len(destination_coords) >= 2:
            try:
                distance = round(math.hypot((float(destination_coords[0]) - float(lng)) * 95000, (float(destination_coords[1]) - float(lat)) * 111000), 1)
            except Exception:
                distance = None

    route_steps = _serialize_route_steps(getattr(session, "route_steps", []) if session else state.get("route_steps", []))
    current_step_index = int(getattr(session, "current_route_step_index", state.get("current_step_index", 0)) if session else state.get("current_step_index", 0) or 0)
    current_step = route_steps[current_step_index] if 0 <= current_step_index < len(route_steps) else None
    current_step_instruction = ""
    if isinstance(current_step, dict):
        current_step_instruction = str(
            current_step.get("instruction")
            or current_step.get("assistant_action")
            or ""
        ).strip()
    next_step = route_steps[current_step_index + 1] if 0 <= current_step_index + 1 < len(route_steps) else None
    next_step_instruction = ""
    if isinstance(next_step, dict):
        next_step_instruction = str(
            next_step.get("instruction")
            or next_step.get("assistant_action")
            or ""
        ).strip()
    instruction_text = (
        last_visual_instruction
        or last_route_instruction
        or last_instruction
        or last_macro_message
        or last_obstacle_warning
        or current_step_instruction
        or ""
    )
    distance_remaining = state.get("distance_remaining")
    if distance_remaining is None:
        distance_remaining = distance

    recent_video = (now_ts - last_video_frame_at) < 12 if last_video_frame_at else False
    recent_visual = (now_ts - last_visual_analysis_at) < 18 if last_visual_analysis_at else False
    recent_instruction = (now_ts - last_instruction_at) < 18 if last_instruction_at else False
    is_macro_active = nav_status in {"MACRO", "SIMULATED"}
    is_micro_active = nav_status == "MICRO"

    if assist_active:
        active_mode = "assist"
    elif is_micro_active and (recent_visual or recent_video or last_visual_instruction or recent_instruction):
        active_mode = "micro"
    elif is_micro_active:
        active_mode = "doubao"
    elif is_macro_active:
        active_mode = "macro"
    else:
        active_mode = "macro"

    def status_payload(active: bool, idle_text: str, active_text: str, alert_text: str = ""):
        if active and alert_text:
            return alert_text, "text-rose-600 bg-rose-50"
        if active:
            return active_text, "text-emerald-600 bg-emerald-50"
        return idle_text, "text-slate-500 bg-slate-100"

    macro_status_text, macro_status_class = status_payload(
        is_macro_active,
        "未启动",
        "运行中"
    )
    doubao_active = is_micro_active and (recent_video or last_visual_analysis_at > 0 or last_visual_instruction)
    doubao_status_text = "分析中" if recent_video and not recent_visual else ("已返回" if recent_visual or last_visual_instruction else "未触发")
    doubao_status_class = "text-violet-600 bg-violet-50" if doubao_active else "text-slate-500 bg-slate-100"
    micro_status_text = "主动决策" if is_micro_active and (recent_instruction or last_visual_instruction) else ("等待决策" if is_micro_active else "未触发")
    micro_status_class = "text-amber-600 bg-amber-50" if is_micro_active else "text-slate-500 bg-slate-100"
    assist_status_text = "兜底在线" if assist_active else "未触发"
    assist_status_class = "text-rose-600 bg-rose-50" if assist_active else "text-slate-500 bg-slate-100"

    macro_steps = ["位置定位", "路线规划", "路口决策", "语音播报"]
    doubao_steps = ["宏观收束", "姿态上送", "视频采样", "多模态理解"]
    micro_steps = ["视频上送", "场景判断", "方向决策", "即时播报"]
    assist_steps = ["低置信升级", "摘要打包", "人工判断", "双向通话"]

    macro_active_index = -1
    if session:
        macro_active_index = 0
        if route_generated:
            macro_active_index = 1
        if distance is not None and distance < 120:
            macro_active_index = 2
        if last_macro_message:
            macro_active_index = 3

    doubao_active_index = -1
    if is_micro_active:
        doubao_active_index = 1 if recent_video else 0
        if last_visual_analysis_at:
            doubao_active_index = 2
        if last_visual_instruction:
            doubao_active_index = 3

    micro_active_index = -1
    if is_micro_active:
        micro_active_index = 0 if recent_video else -1
        if last_visual_analysis_at:
            micro_active_index = 1
        if last_visual_instruction:
            micro_active_index = 2
        if recent_instruction:
            micro_active_index = 3

    assist_active_index = 3 if assist_active else -1

    return {
        "active_mode": active_mode,
        "client_id": client_id,
        "updated_at": now_ts,
        "destination_name": destination_name,
        "nav_status": nav_status,
        "distance_remaining": distance_remaining,
        "route_points": route_points,
        "route_generated": route_generated,
        "route_steps": route_steps,
        "current_step_index": current_step_index,
        "current_step": current_step,
        "instruction_text": instruction_text,
        "last_instruction": last_instruction,
        "coord_system": state.get("coord_system") or "gcj02",
        "route_updated_at": float(state.get("route_updated_at", 0) or 0),
        "destination_coords": list(session.destination_coords) if session and session.destination_coords else state.get("destination_coords"),
        "modules": {
            "macro": {
                "badge": "宏观规划",
                "stage": "高德地图宏观路线规划",
                "summary": "真实 GPS、目的地与步行路线共同决定全局导航路径。",
                "statusText": macro_status_text,
                "statusClass": macro_status_class,
                "title": "宏观导航负责全局路径决策",
                "desc": current_step_instruction or last_route_instruction or last_macro_message or f"当前目标：{destination_name}。系统基于真实 GPS 与高德步行路径进行宏观导航。",
                "steps": macro_steps,
                "activeIndex": macro_active_index,
                "metrics": [
                    {"label": "当前模式", "value": "GPS + 高德" if session else ("GPS + 路线" if route_generated else "未导航"), "tone": "text-blue-700"},
                    {"label": "剩余距离", "value": f"{distance:.0f}m" if distance is not None and distance < 1000 else (f"{distance/1000:.2f}km" if distance is not None else "--"), "tone": "text-indigo-700"},
                    {"label": "路线节点", "value": str(route_points or 0), "tone": "text-emerald-700"},
                ],
                "signals": [
                    ["实时定位", f"{lat:.6f}, {lng:.6f}" if lat is not None and lng is not None else "暂无 GPS 定位"],
                    ["目标地点", destination_name],
                    ["当前该怎么走", last_macro_message or current_step_instruction or last_route_instruction or "当前无新增导航指令"],
                    ["下一步动作", state.get("next_instruction_text") or next_step_instruction or "等待后续路线指令"],
                ],
            },
            "doubao": {
                "badge": "豆包交互",
                "stage": "豆包多模态视频交互",
                "summary": "真实视频帧、朝向与位置送入多模态理解链路。",
                "statusText": doubao_status_text,
                "statusClass": doubao_status_class,
                "title": "豆包多模态 AI 负责近场环境理解",
                "desc": "系统在微观导航阶段读取真实视频帧，并结合朝向、位置与目标信息做多模态分析。",
                "steps": doubao_steps,
                "activeIndex": doubao_active_index,
                "metrics": [
                    {"label": "输入组合", "value": "GPS + 朝向 + 视频帧" if recent_video else "等待视频帧", "tone": "text-violet-700"},
                    {"label": "朝向角", "value": f"{float(angle or 0):.0f}°", "tone": "text-blue-700"},
                    {"label": "最近分析", "value": f"{int(now_ts - last_visual_analysis_at)}s 前" if last_visual_analysis_at else "--", "tone": "text-amber-700"},
                ],
                "signals": [
                    ["视频帧状态", "正在上传并分析" if recent_video else f"当前无新视频帧（相机:{camera_state}）"],
                    ["豆包返回", last_visual_instruction or "尚未返回多模态结果"],
                    ["切换依据", f"导航状态：{nav_status}"],
                ],
            },
            "micro": {
                "badge": "微观导航",
                "stage": "服务器视频帧微观导航决策",
                "summary": "基于真实视频分析结果输出最后几米的可执行动作。",
                "statusText": micro_status_text,
                "statusClass": micro_status_class,
                "title": "微观导航负责生成实时动作级指令",
                "desc": "当导航进入目标附近，服务器会基于真实视频帧分析结果生成转向与避障语音指令。",
                "steps": micro_steps,
                "activeIndex": micro_active_index,
                "metrics": [
                    {"label": "导航阶段", "value": nav_status, "tone": "text-amber-700"},
                    {"label": "最近播报", "value": f"{int(now_ts - last_instruction_at)}s 前" if last_instruction_at else "--", "tone": "text-blue-700"},
                    {"label": "指令状态", "value": "已播报" if recent_instruction else ("待播报" if is_micro_active else "未启动"), "tone": "text-emerald-700"},
                ],
                "signals": [
                    ["视频输入", "有实时视频帧" if recent_video else "暂未收到新视频帧"],
                    ["导航指令", last_visual_instruction or last_instruction or current_step_instruction or "暂无微观导航指令"],
                    ["工作状态", "正在微观导盲" if is_micro_active else "尚未进入微观导盲"],
                ],
            },
            "assist": {
                "badge": "人工接力",
                "stage": "按需触发的人工求助闭环",
                "summary": "仅在盲人主动求助或 AI 置信不足时，才切换人工接力。",
                "statusText": assist_status_text,
                "statusClass": assist_status_class,
                "title": "人工接力是按需触发的安全兜底",
                "desc": "人工接力不常驻运行，只有真实求助或测试触发后才会进入在线接力状态。",
                "steps": assist_steps,
                "activeIndex": assist_active_index,
                "metrics": [
                    {"label": "触发条件", "value": assist_reason or "未触发", "tone": "text-rose-700"},
                    {"label": "接力状态", "value": state.get("assist_status", "idle"), "tone": "text-blue-700"},
                    {"label": "目标", "value": "安全通过", "tone": "text-emerald-700"},
                ],
                "signals": [
                    ["是否接力", "已触发人工接力" if assist_active else "当前无需人工接力"],
                    ["触发原因", assist_reason or "未触发或未上报"],
                    ["设备保活", f"最近心跳 {int(now_ts - last_heartbeat_sent_at)}s 前" if last_heartbeat_sent_at else "尚未上报心跳"],
                    ["音频输出", f"{audio_output_state} / 安全播报:{safety_prompt_state}"],
                ],
            },
        }
    }

# 全局变量存储 ESP32 帧
esp32_frame_buffer = None
esp32_frame_lock = threading.Lock()

# 启动后台分析线程
def analysis_loop():
    global esp32_frame_buffer
    print("AI Analysis Loop Started")
    while True:
        try:
            frame_data = None
            with esp32_frame_lock:
                if esp32_frame_buffer:
                    frame_data = esp32_frame_buffer
            
            if frame_data:
                # 解码并推理
                nparr = np.frombuffer(frame_data, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if frame is not None:
                     # 只需要跑 process，它会更新 model_manager.last_results
                     # 我们不需要它的返回值（带框的图），因为我们只显示原始图
                     model_manager.process(frame)
            
            # 控制推理频率，比如 10 FPS，避免抢占 CPU
            time.sleep(0.1)
        except Exception as e:
            print(f"Analysis error: {e}")
            time.sleep(1)

analysis_thread = threading.Thread(target=analysis_loop, daemon=True)
analysis_thread.start()

class SerialReader:
    def __init__(self, baud_rate=921600):
        self.baud_rate = baud_rate
        self.running = False
        self.thread = None
        self.ser = None
        
    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        self.running = False
        if self.ser:
            self.ser.close()
            
    def _find_port(self):
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            # 简单的自动发现策略：找描述里带 USB 的，或者 CP210x/CH340
            if "USB" in p.description or "Serial" in p.description:
                return p.device
        if ports:
            return ports[0].device
        return None

    def _run_loop(self):
        print("启动串口读取线程...")
        while self.running:
            try:
                if not self.ser or not self.ser.is_open:
                    port = self._find_port()
                    if port:
                        print(f"尝试连接串口: {port} @ {self.baud_rate}")
                        self.ser = serial.Serial(port, self.baud_rate, timeout=1)
                        self.ser.dtr = False
                        self.ser.rts = False
                        print(f"串口已连接: {port}")
                    else:
                        time.sleep(2)
                        continue
                        
                # 读取数据
                if self.ser.in_waiting > 0:
                    line = self.ser.readline()
                    # Debug: 打印收到的原始数据的前20个字节
                    # print(f"Raw: {line[:20]}") 
                    
                    if b"--FRAME_START--" in line:
                        # print("收到帧头")
                        len_line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                        if len_line.startswith("LEN:"):
                            try:
                                frame_len = int(len_line.split(":")[1])
                                # print(f"帧长度: {frame_len}")
                                # 读取数据体
                                data = b""
                                while len(data) < frame_len:
                                    chunk = self.ser.read(frame_len - len(data))
                                    if not chunk:
                                        break
                                    data += chunk
                                
                                if len(data) == frame_len:
                                    # 读取 footer
                                    footer = self.ser.readline()
                                    # 可能会有多余的换行符
                                    if b"--FRAME_END--" in footer or b"--FRAME_END--" in self.ser.readline():
                                        # 校验 JPEG 结尾 (FF D9)
                                        if len(data) >= 2 and data[-2:] == b'\xff\xd9':
                                            # 更新全局缓冲区
                                            global esp32_frame_buffer
                                            with esp32_frame_lock:
                                                esp32_frame_buffer = data
                                        else:
                                            print("JPEG EOI 校验失败，丢弃帧")
                                    else:
                                        print("未找到帧尾")
                                else:
                                    print(f"数据不完整: {len(data)}/{frame_len}")
                            except ValueError:
                                print(f"长度解析错误: {len_line}")
                else:
                    time.sleep(0.001)
            except Exception as e:
                print(f"串口错误: {e}")
                if self.ser:
                    self.ser.close()
                    self.ser = None
                time.sleep(2)

# 初始化并启动串口读取器
serial_reader = SerialReader()
# serial_reader.start() # 暂时停用，避免串口占用报错

class ImageEnhancer:
    def __init__(self):
        # CLAHE (Contrast Limited Adaptive Histogram Equalization)
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    
    def enhance(self, frame):
        if frame is None:
            return None
            
        # 1. CLAHE 增强对比度 (YUV 颜色空间)
        try:
            img_yuv = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV)
            img_yuv[:,:,0] = self.clahe.apply(img_yuv[:,:,0])
            frame_clahe = cv2.cvtColor(img_yuv, cv2.COLOR_YUV2BGR)
        except Exception:
            frame_clahe = frame
            
        # 2. 锐化 (Unsharp Masking)
        try:
            gaussian = cv2.GaussianBlur(frame_clahe, (0, 0), 3.0)
            frame_sharp = cv2.addWeighted(frame_clahe, 1.5, gaussian, -0.5, 0)
        except Exception:
            frame_sharp = frame_clahe
            
        return frame_sharp

class RealSystemMonitor:
    def __init__(self):
        # 匹配客户端 800x600 分辨率
        self.width = 800
        self.height = 600
        self.source = "esp32" # local, esp32 (默认开启数据线连接)
        self.last_valid_frame = None # 缓存上一帧成功解码的画面，防止闪烁
        self.enhancer = ImageEnhancer()
        
        # 初始化摄像头
        print("正在尝试打开摄像头...")
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            print("警告: 无法打开摄像头，将使用模拟画面")
            self.use_mock = True
        else:
            print("摄像头打开成功")
            self.use_mock = False
            # 设置分辨率
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            
        # 启动 WiFi 信号监测线程
        self.wifi_signal = 0
        self.stop_signal_thread = False
        self.signal_thread = threading.Thread(target=self._update_wifi_loop, daemon=True)
        self.signal_thread.start()

    def _update_wifi_loop(self):
        while not self.stop_signal_thread:
            try:
                signal = self.get_real_wifi_signal()
                if signal > 0:
                     self.wifi_signal = signal
            except Exception as e:
                print(f"WiFi monitor error: {e}")
            time.sleep(2) # 每2秒更新一次

    def get_real_wifi_signal(self):
        """获取真实的 WiFi 信号强度 (0-100)"""
        try:
            if platform.system() == "Windows":
                # Windows 使用 netsh 命令
                # 尝试不同的编码以兼容中英文系统
                for enc in ["gbk", "utf-8", "cp437"]:
                    try:
                        result = subprocess.check_output(["netsh", "wlan", "show", "interfaces"], encoding=enc, errors="ignore")
                        # 匹配 "Signal : 99%" 或 "信号 : 99%"
                        match = re.search(r"(?:Signal|信号)\s*:\s*(\d+)%", result, re.IGNORECASE)
                        if match:
                            return int(match.group(1))
                    except subprocess.CalledProcessError:
                        continue
                        
            elif platform.system() == "Linux":
                # Linux 使用 nmcli 或 /proc/net/wireless
                with open("/proc/net/wireless", "r") as f:
                    content = f.read()
                    lines = content.splitlines()
                    if len(lines) > 2:
                        link = float(lines[2].split()[2].replace('.', ''))
                        return int(link)
        except Exception as e:
            # print(f"Get signal error: {e}")
            pass
        return 0

    def generate_frame(self):
        frame = None
        
        if self.source == "esp32":
            global esp32_frame_buffer
            with esp32_frame_lock:
                if esp32_frame_buffer is not None:
                    # 解码 ESP32 发来的 JPEG 数据
                    try:
                        nparr = np.frombuffer(esp32_frame_buffer, np.uint8)
                        decoded_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        if decoded_frame is not None:
                            frame = cv2.resize(decoded_frame, (self.width, self.height))
                            self.last_valid_frame = frame.copy() # 更新缓存
                    except Exception as e:
                        print(f"ESP32 frame decode error: {e}")
            
            # 如果解码失败或没有新帧，使用缓存帧
            if frame is None and self.last_valid_frame is not None:
                frame = self.last_valid_frame.copy()
        
        if frame is None and not self.use_mock:
            # 如果是本地模式，或者 ESP32 模式但没数据，尝试读取本地摄像头
            if self.source == "local":
                ret, cam_frame = self.cap.read()
                if ret:
                    frame = cv2.resize(cam_frame, (self.width, self.height))
                else:
                    print("读取摄像头帧失败")

        if frame is not None:
            # 极低延迟模式：关闭耗时增强，直接传输高质量原图
            # try:
            #     gaussian = cv2.GaussianBlur(frame, (0, 0), 3.0)
            #     frame = cv2.addWeighted(frame, 1.5, gaussian, -0.5, 0)
            # except:
            #     pass

            # === AI 模型处理 ===
            # 在这里调用 model_manager 处理帧
            processed_frame, _ = model_manager.process(frame)
            
            # 添加水印
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            mode_text = f"MODE: {model_manager.current_mode.upper()}"
            source_text = f"SOURCE: {self.source.upper()}"
            
            cv2.putText(processed_frame, f"REAL CAM {timestamp}", (20, 40), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.putText(processed_frame, mode_text, (20, 70), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.putText(processed_frame, source_text, (20, 100), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
            
            # 编码为 JPEG
            _, buffer = cv2.imencode('.jpg', processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            img_b64 = base64.b64encode(buffer).decode('utf-8')
            return img_b64

        # === 模拟画面回退逻辑 ===
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        t = time.time()
        msg = "NO CAMERA" if self.source == "local" else "WAITING FOR SERIAL..."
        cv2.putText(img, msg, (100, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        _, buffer = cv2.imencode('.jpg', img)
        return base64.b64encode(buffer).decode('utf-8')

    def get_signal_strength(self):
        return self.wifi_signal

    def get_recognition_result(self):
        # 优先获取真实模型的识别结果
        res = model_manager.get_latest_recognition()
        if res:
            return res

        # 严格真实链路：没有真实识别结果时不再返回 mock 文本
        if hasattr(self, '_monitor_msg_shown') and model_manager.current_mode != "monitor":
            delattr(self, '_monitor_msg_shown')
        return None
    
    def release(self):
        if self.cap and self.cap.isOpened():
            self.cap.release()

simulator = RealSystemMonitor()

class ModeRequest(BaseModel):
    mode: str

@app.post("/api/set_mode")
async def set_mode(req: ModeRequest):
    if model_manager.switch_mode(req.mode):
        return {"status": "success", "mode": req.mode}
    raise HTTPException(status_code=400, detail="Invalid mode")

@app.post("/api/set_source")
async def set_source(req: dict):
    source = req.get("source")
    if source in ["local", "esp32"]:
        simulator.source = source
        return {"status": "success", "source": source}
    raise HTTPException(status_code=400, detail="Invalid source")

@app.post("/api/geocode")
async def geocode_address(req: dict):
    address = req.get("address")
    if not address:
        raise HTTPException(status_code=400, detail="Address is required")

    amap_key = getattr(config_secrets, "AMAP_KEY", None) or os.getenv("AMAP_KEY")
    if not amap_key:
        raise HTTPException(status_code=503, detail="AMAP_KEY is not configured")

    try:
        encoded_name = urllib.parse.quote(address)
        geo_url = f"https://restapi.amap.com/v3/geocode/geo?address={encoded_name}&key={amap_key}"

        with urllib.request.urlopen(geo_url, timeout=5) as url:
            data = json.loads(url.read().decode())
            if data['status'] == '1' and data['geocodes']:
                loc_str = data['geocodes'][0]['location']
                dlon, dlat = map(float, loc_str.split(','))
                return {"status": "success", "lat": dlat, "lon": dlon, "name": address}
            else:
                return {"status": "error", "message": "Address not found"}
    except Exception as e:
        print(f"Geocode error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/monitor")
async def monitor_page():
    with open(os.path.join(CURRENT_DIR, "static/monitor.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.websocket("/ws/stream")
async def stream_endpoint(websocket: WebSocket, client_id: str = "monitor"):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, maybe handle control messages from monitor
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.websocket("/ws/video/{client_id}")
async def video_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket)
    print(f"Video WebSocket connected: {client_id}")
    manager.update_device_state(client_id, status="online")
    
    try:
        while True:
            # 接收数据
            # 协议格式：
            # 1. 纯视频帧 (Bytes)
            # 2. JSON指令 (Text)
            
            message = await websocket.receive()
            
            # 更新活跃时间
            manager.update_device_state(client_id)

            if "bytes" in message:
                data = message["bytes"]
                # 解码图片
                nparr = np.frombuffer(data, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if frame is None:
                    continue
                _record_device_flow_event(client_id, last_video_frame_at=time.time())
                
                # --- 1. 调用 NavigationManager 处理视觉逻辑 (微观导航) ---
                instruction = nav_manager.on_video_frame(client_id, frame)
                if instruction:
                    _record_device_flow_event(client_id, last_instruction=instruction, last_instruction_at=time.time())
                    await _broadcast_navigation_instruction(client_id, instruction, {"mode": "micro"})
                    print(f"Sent nav instruction to {client_id}: {instruction}")

                # --- 2. 原有的处理逻辑 (可选保留) ---
                # 将帧存入全局 buffer 供 ModelManager 分析
                global esp32_frame_buffer
                with esp32_frame_lock:
                    esp32_frame_buffer = data # 保持原样，假设 ModelManager 能处理原始 bytes 或 frame
                await manager.push_recognition_update(client_id)

            elif "text" in message:
                text_data = message["text"]
                try:
                    msg_obj = json.loads(text_data)
                    msg_type = msg_obj.get("type")

                    if msg_type == "gps_update":
                        # 处理 GPS 更新 (宏观导航)
                        lng = msg_obj.get("lng")
                        lat = msg_obj.get("lat")

                        manager.update_device_state(client_id, lat=lat, lng=lng)

                        nav_resp = nav_manager.on_gps_update(client_id, lng, lat, obstacles_data)
                        if nav_resp:
                            if nav_resp.get("new_route"):
                                await _broadcast_route_update(client_id, nav_resp=nav_resp)
                            if nav_resp.get("message"):
                                await _broadcast_navigation_instruction(client_id, nav_resp["message"], {"mode": str(nav_resp.get("mode") or "").lower()})

                    elif msg_type == "start_nav":
                        # 开始导航
                        dest = msg_obj.get("destination")
                        result_msg = await _try_start_or_queue_navigation(client_id, dest)
                        _record_device_flow_event(client_id, last_instruction=result_msg, last_instruction_at=time.time(), assist_active=False, assist_status="idle", assist_reason="")
                        await _broadcast_navigation_instruction(client_id, result_msg, {"mode": "macro"})

                    elif msg_type == "stop_nav":
                         result_msg = nav_manager.stop_navigation(client_id)
                         _record_device_flow_event(client_id, last_instruction=result_msg, last_instruction_at=time.time())
                         if client_id in manager.device_states:
                             manager.device_states[client_id]["route"] = []
                             manager.device_states[client_id]["route_payload"] = None
                         await _broadcast_route_update(client_id, route_override=[])
                         await _broadcast_navigation_instruction(client_id, result_msg, {"mode": "idle"})
                    
                    # --- 原有心跳逻辑等 ---
                    # if msg_type == "ping": ...
                        
                except Exception as e:
                    print(f"WS Text Error: {e}")

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        manager.update_device_state(client_id, status="offline")
        print(f"Video WebSocket disconnected: {client_id}")
    except Exception as e:
        manager.disconnect(websocket)
        manager.update_device_state(client_id, status="offline")
        print(f"Video WebSocket Error: {e}")

@app.websocket("/ws/client_input")
async def client_input_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    # Extract client_id from query params
    client_id = websocket.query_params.get("client_id", "unknown_client")
    manager.register_client_input(client_id, websocket)
    print(f"Python Client Connected: {client_id}")
    
    # Register device state
    manager.update_device_state(client_id, status="online")
    
    global esp32_frame_buffer
    
    # Reset push state on new connection
    model_manager._last_pushed_text = ""
    
    try:
        while True:
            data_str = await websocket.receive_text()
            
            # Update heartbeat AND set online explicitly
            manager.update_device_state(client_id, status="online")
            
            try:
                data = json.loads(data_str)

                # Construct payload
                status_fields = {
                    "camera_state": data.get("camera_state"),
                    "audio_output_state": data.get("audio_output_state"),
                    "safety_prompt_state": data.get("safety_prompt_state"),
                    "last_heartbeat_sent_at": data.get("last_heartbeat_sent_at"),
                    "last_camera_frame_at": data.get("last_camera_frame_at"),
                    "last_audio_output_at": data.get("last_audio_output_at"),
                    "last_camera_error": data.get("last_camera_error"),
                    "last_audio_error": data.get("last_audio_error"),
                    "last_safety_prompt": data.get("last_safety_prompt"),
                }
                manager.update_device_state(client_id, **status_fields)

                has_gps = "lat" in data and "lng" in data
                if has_gps:
                    manager.update_device_state(
                        client_id, 
                        lat=float(data["lat"]), 
                        lng=float(data["lng"]), 
                        angle=data.get("angle") if "angle" in data else None,
                        speed=data.get("speed") if "speed" in data else None,
                        sats=data.get("sats") if "sats" in data else None,
                        hdop=data.get("hdop") if "hdop" in data else None,
                        alt=data.get("alt") if "alt" in data else None
                    )
                if data.get("type") == "heartbeat":
                    manager.update_device_state(
                        client_id,
                        angle=data.get("angle", 0),
                        speed=data.get("speed", 0.0),
                        sats=data.get("sats", 0),
                        hdop=data.get("hdop", 99.9),
                        alt=data.get("alt", 0.0)
                    )
                    # 心跳包既要写入定位，也要驱动导航状态推进。
                    if has_gps:
                        cur_lat = float(data["lat"])
                        cur_lng = float(data["lng"])
                        nav_resp = nav_manager.on_gps_update(client_id, cur_lng, cur_lat, obstacles_data)
                        if nav_resp:
                            if nav_resp.get("new_route"):
                                await _broadcast_route_update(client_id, nav_resp=nav_resp)
                            if nav_resp.get("message"):
                                await _broadcast_navigation_instruction(client_id, nav_resp["message"], {"mode": str(nav_resp.get("mode") or "").lower()})
                        await _resume_pending_navigation_if_ready(client_id)
                elif data.get("type") == "mock_route_update":
                    if not ALLOW_MOCK_ROUTE_UPDATE:
                        print(f"⚠️ mock_route_update disabled in current environment: {client_id}")
                        continue
                    mock_route = data.get("route") or []
                    if not isinstance(mock_route, list):
                        mock_route = []
                    state = _get_or_create_device_state(client_id)
                    destination_name = str(data.get("destination_name") or state.get("destination_name") or "").strip()
                    instruction_text = str(data.get("instruction_text") or state.get("last_instruction") or "已生成步行路线，请沿当前路线前进").strip()
                    nav_status = str(data.get("nav_status") or "SIMULATED").strip().upper()
                    state["destination_name"] = destination_name
                    state["nav_status"] = nav_status
                    if "next_instruction_text" in data:
                        state["next_instruction_text"] = data["next_instruction_text"]
                    if "current_step_index" in data:
                        state["current_step_index"] = int(data["current_step_index"])
                    else:
                        state["current_step_index"] = 0
                    state["route_steps"] = _build_simple_route_steps_from_polyline(mock_route)
                    if mock_route:
                        last_pt = mock_route[-1]
                        if isinstance(last_pt, (list, tuple)) and len(last_pt) >= 2:
                            state["destination_coords"] = [float(last_pt[0]), float(last_pt[1])]
                    if has_gps and state.get("destination_coords"):
                        try:
                            cur_lat = float(data["lat"])
                            cur_lng = float(data["lng"])
                            dest_lng, dest_lat = state["destination_coords"][0], state["destination_coords"][1]
                            state["distance_remaining"] = round(math.hypot((float(dest_lng) - cur_lng) * 95000, (float(dest_lat) - cur_lat) * 111000), 1)
                        except Exception:
                            pass
                    if instruction_text:
                        state["last_instruction"] = instruction_text
                        state["last_instruction_at"] = time.time()
                    await _broadcast_route_update(client_id, route_override=mock_route)
                
                if "image" in data:
                    # 1. 立即广播给前端 (极低延迟路径)
                    # 重命名 image -> frame 以匹配前端协议
                    frame_b64 = data.pop("image")
                    data["frame"] = frame_b64

                    # 注入辅助信息
                    data["signal"] = simulator.get_signal_strength()

                    await manager.broadcast(data, exclude=websocket)
                    await manager.push_recognition_update(client_id)
                    image_queue = latest_images_by_client.get(client_id)
                    if image_queue is None:
                        image_queue = deque(maxlen=2)
                        latest_images_by_client[client_id] = image_queue
                    image_queue.append(frame_b64)
                    _record_device_flow_event(client_id, last_video_frame_at=time.time())

                    # 2. 存入 buffer 供后台 AI 分析 (异步路径)
                    img_bytes = base64.b64decode(data["frame"])
                    with esp32_frame_lock:
                        esp32_frame_buffer = img_bytes

                    # --- 3. 检查 GPS 心跳 ---
                    if "lat" in data and "lng" in data:
                        nav_resp = nav_manager.on_gps_update(client_id, data["lng"], data["lat"], obstacles_data)
                        if nav_resp:
                            if nav_resp.get("new_route"):
                                await _broadcast_route_update(client_id, nav_resp=nav_resp)
                            if nav_resp.get("message"):
                                await _broadcast_navigation_instruction(client_id, nav_resp["message"], {"mode": str(nav_resp.get("mode") or "").lower()})

                    # --- 4. 如果处于微观导航模式，触发 NavigationManager ---
                    session = nav_manager.sessions.get(client_id)
                    if session:
                         nparr = np.frombuffer(img_bytes, np.uint8)
                         frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                         if frame is not None:
                             instruction = nav_manager.on_video_frame(client_id, frame)
                             if instruction:
                                 _record_device_flow_event(client_id, last_instruction=instruction, last_instruction_at=time.time(), last_video_frame_at=time.time())
                                 await _broadcast_navigation_instruction(client_id, instruction, {"mode": "micro"})


                elif data.get("type") == "start_nav":
                    destination = data.get("destination")
                    result_msg = await _try_start_or_queue_navigation(client_id, destination)
                    _record_device_flow_event(
                        client_id,
                        last_instruction=result_msg,
                        last_instruction_at=time.time(),
                        assist_active=False,
                        assist_status="idle",
                        assist_reason=""
                    )
                    await _broadcast_instruction(client_id, result_msg)

                    current_state = manager.device_states.get(client_id, {})
                    cur_lat = current_state.get("lat")
                    cur_lng = current_state.get("lng")
                    if cur_lat is not None and cur_lng is not None:
                        nav_resp = nav_manager.on_gps_update(client_id, cur_lng, cur_lat, obstacles_data)
                        if nav_resp:
                            if nav_resp.get("new_route"):
                                await _broadcast_route_update(client_id, nav_resp=nav_resp)
                            if nav_resp.get("message"):
                                await _broadcast_navigation_instruction(client_id, nav_resp["message"], {"mode": str(nav_resp.get("mode") or "").lower()})
                        await _resume_pending_navigation_if_ready(client_id)

                elif data.get("type") == "stop_nav":
                    result_msg = nav_manager.stop_navigation(client_id)
                    _record_device_flow_event(client_id, last_instruction=result_msg, last_instruction_at=time.time())
                    if client_id in manager.device_states:
                        manager.device_states[client_id]["route"] = []
                        manager.device_states[client_id]["route_payload"] = None
                    await _broadcast_instruction(client_id, result_msg)
                    await _broadcast_route_update(client_id, route_override=[])
                            
                # Audio handling can be added here
            except Exception as e:
                print(f"Client input parse error: {e}")
    except WebSocketDisconnect:
        manager.unregister_client_input(client_id, websocket)
        print(f"Python Client Disconnected: {client_id}")
        manager.update_device_state(client_id, status="offline")
    except Exception as e:
        print(f"Client input connection error: {e}")
    finally:
        manager.unregister_client_input(client_id, websocket)
        manager.update_device_state(client_id, status="offline")

@app.websocket("/ws/esp32_cam")
async def esp32_cam_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("ESP32 Camera Connected")
    try:
        global esp32_frame_buffer
        while True:
            # 接收二进制数据 (JPEG)
            data = await websocket.receive_bytes()
            with esp32_frame_lock:
                esp32_frame_buffer = data
    except WebSocketDisconnect:
        print("ESP32 Camera Disconnected")
        with esp32_frame_lock:
            esp32_frame_buffer = None
    except Exception as e:
        print(f"ESP32 WS Error: {e}")
        with esp32_frame_lock:
            esp32_frame_buffer = None

# Removed WebSocket Endpoint


@app.get("/")
async def get():
    # Redirect root to admin dashboard
    return RedirectResponse(url="/admin/login")



@app.get("/nav_map")
async def get_nav_map():
    with open(os.path.join(CURRENT_DIR, "static/amap_nav.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

class ContributionRequest(BaseModel):
    markerId: str
    markerTitle: str
    type: int
    userId: str
    userNickname: str
    userAvatar: Optional[str] = None
    content: str
    imageUrl: Optional[str] = None
    zones: List[str] = []
    proposedStatus: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    regionCode: Optional[str] = None
    regionName: Optional[str] = None
    cityCode: Optional[str] = None
    cityName: Optional[str] = None
    forumKey: Optional[str] = None
    forumName: Optional[str] = None
    targetType: Optional[str] = None

class ASRUpdateRequest(BaseModel):
    client_id: str = "asr_client"
    asr_text: str
    intent: Optional[str] = None

class VoteRequest(BaseModel):
    contributionId: str
    userId: str
    voteType: str # "up" or "down"

@app.post("/api/asr/update")
async def asr_update(req: ASRUpdateRequest):
    client_id = req.client_id or "asr_client"
    routed_text = str(req.asr_text or "").strip()
    voice_state = _build_voice_state_snapshot(client_id)
    msg = {
        "type": "asr_update",
        "asr_text": routed_text,
        "client_id": client_id,
        "intent": req.intent,
        "timestamp": time.time(),
        **voice_state,
    }
    await manager.broadcast(msg)
    manager.last_recognition_text = routed_text
    manager.last_recognition_at = time.time()
    await manager.broadcast({
        "type": "recognition_update",
        "client_id": client_id,
        "text": routed_text,
        "recognition_ts": manager.last_recognition_at,
        **voice_state,
    })
    asr_pending_text_by_client[client_id] = routed_text
    old_task = asr_debounce_task_by_client.get(client_id)
    if old_task and not old_task.done():
        old_task.cancel()

    immediate_system_command = (
        _is_navigation_query(routed_text)
        or _spoken_contains_any(routed_text, ["我在哪", "我现在在哪", "当前位置", "当前在哪里", "我现在的位置"])
        or _spoken_contains_any(routed_text, ["还有多远", "离目的地还有多远", "距离目的地还有多远", "还有多久到", "还有多少米"])
        or _spoken_contains_any(routed_text, ["再说一遍", "重复一遍", "重复刚才的导航", "再重复一下", "刚才说什么", "上一条指令"])
        or _spoken_contains_any(routed_text, ["有哪些常用地址", "我的常用地址", "常用地址有哪些", "路线记忆有哪些", "我保存了哪些地址"])
        or bool(_extract_memory_alias_from_speech(routed_text))
        or bool(_parse_volume_control_intent(routed_text))
    )

    if immediate_system_command:
        print(f"⚡ [ASR] 立即处理系统指令: {routed_text}")
        await process_llm_response(client_id, routed_text)
    else:
        asr_debounce_task_by_client[client_id] = asyncio.create_task(_debounce_process_llm(client_id))

    return {"status": "ok"}

@app.post("/api/asr/preview")
async def asr_preview(req: ASRUpdateRequest):
    client_id = req.client_id or "asr_client"
    voice_state = _build_voice_state_snapshot(client_id)
    msg = {
        "type": "asr_update",
        "asr_text": req.asr_text,
        "client_id": client_id,
        "intent": req.intent,
        "preview": True,
        "timestamp": time.time(),
        **voice_state,
    }
    await manager.broadcast(msg)
    manager.last_recognition_text = req.asr_text
    manager.last_recognition_at = time.time()
    await manager.broadcast({
        "type": "recognition_update",
        "client_id": client_id,
        "text": req.asr_text,
        "recognition_ts": manager.last_recognition_at,
        **voice_state,
    })
    return {"status": "ok"}

def _normalize_asr_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).strip()

def _extract_reply_text(response) -> str:
    reply_text = ""
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    if isinstance(output_text, list):
        joined = "".join([str(x) for x in output_text if isinstance(x, str)])
        if joined.strip():
            return joined.strip()
    for out in getattr(response, "output", []) or []:
        out_type = getattr(out, "type", None) if not isinstance(out, dict) else out.get("type")
        if out_type != "message":
            continue
        out_content = getattr(out, "content", []) if not isinstance(out, dict) else out.get("content", [])
        for content in out_content or []:
            content_type = getattr(content, "type", None) if not isinstance(content, dict) else content.get("type")
            if content_type in ("output_text", "text"):
                text_val = getattr(content, "text", None) if not isinstance(content, dict) else content.get("text")
                if text_val is not None:
                    reply_text += str(text_val)
    return reply_text.strip()

def _extract_reply_text_from_dump(resp_dump: dict) -> str:
    try:
        outputs = resp_dump.get("output", []) if isinstance(resp_dump, dict) else []
        chunks = []
        for out in outputs or []:
            if not isinstance(out, dict):
                continue
            if out.get("type") != "message":
                continue
            for content in out.get("content", []) or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") in ("output_text", "text"):
                    text_val = content.get("text")
                    if text_val is not None:
                        chunks.append(str(text_val))
        return "".join(chunks).strip()
    except Exception:
        return ""


def _extract_json_block(raw_text: str) -> dict:
    text = (raw_text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _clean_short_text(text: str, limit: int = 80) -> str:
    cleaned = re.sub(r"[#*_`>\-]+", " ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \n\r\t:;，。")
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "..."
    return cleaned


def _clean_blind_guidance_summary_text(text: str, fallback: str = "") -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return fallback
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    cleaned = re.sub(r"^(?:导盲摘要|摘要|播报摘要|播报|场景描述|盲人提示|提示)[:：]\s*", "", cleaned)
    cleaned = re.sub(r"(?:位置参考|位置参照)[:：]\s*", "", cleaned)
    cleaned = re.sub(r"(?:可作为|作为)?位置参(?:考|照)", "能帮你判断方位", cleaned)
    cleaned = re.sub(r"^(?:该位置|这里|此处)", "前方", cleaned)
    cleaned = re.sub(r"(?:建议视障用户|建议盲人|建议行人|建议用户)", "建议你", cleaned)
    cleaned = re.sub(r"(?:视障用户|盲人用户|用户)可", "你可以", cleaned)
    cleaned = re.sub(r"(?:可帮助|帮助)判断方位", "能帮你判断方位", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \n\r\t:;，。")
    if len(cleaned) > 40:
        cleaned = cleaned[:40].rstrip() + "..."
    if cleaned and not re.search(r"[。！？!?]$", cleaned):
        cleaned += "。"
    return cleaned or fallback


def _normalize_confidence_score(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            value = value.strip().replace("%", "")
        score = float(value)
    except Exception:
        return None
    if score <= 1:
        score *= 100.0
    return max(0.0, min(100.0, score))


def _infer_match_status_from_text(text: str) -> Optional[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return None

    mismatch_keywords = ["不匹配", "不一致", "错误标注", "明显错误", "无关", "驳回", "不合理"]
    relation_uncertain_keywords = [
        "图文待确认",
        "图文关系待确认",
        "图文关系不确定",
        "描述待确认",
        "需要人工复核",
        "需人工复核",
        "人工复核",
        "待核实",
        "待确认",
        "存疑",
    ]
    match_keywords = ["标注合理", "匹配合理", "图文一致", "内容一致", "证据充分", "可以通过", "属实", "审核通过", "合理"]

    if any(keyword in normalized for keyword in mismatch_keywords):
        return "mismatch"
    if any(keyword in normalized for keyword in match_keywords):
        return "match"
    if any(keyword in normalized for keyword in relation_uncertain_keywords):
        return "uncertain"
    return None


def _format_annotation_analysis_text(parsed: dict, fallback_text: str) -> tuple[str, dict]:
    blind_desc = _clean_short_text(parsed.get("blind_audio_desc") or parsed.get("summary") or parsed.get("result") or fallback_text, 35)
    summary = _clean_short_text(parsed.get("summary") or parsed.get("result") or fallback_text, 90)
    risk = _clean_short_text(parsed.get("risk") or parsed.get("risk_level") or "", 40)
    recommendation = _clean_short_text(parsed.get("recommendation") or parsed.get("action") or "", 50)
    match_status = str(parsed.get("match_status") or parsed.get("consistency") or "uncertain").strip().lower()
    match_reason = _clean_short_text(parsed.get("match_reason") or parsed.get("validation_reason") or "", 50)
    confidence_score = _normalize_confidence_score(
        parsed.get("confidence_score")
        or parsed.get("match_confidence")
        or parsed.get("confidence")
        or parsed.get("similarity")
        or parsed.get("score")
    )
    entity_name_match = _normalize_bool_flag(
        parsed.get("entity_name_match")
        or parsed.get("name_match")
        or parsed.get("shop_name_match")
    )
    business_match = _normalize_bool_flag(
        parsed.get("business_match")
        or parsed.get("category_match")
        or parsed.get("business_type_match")
    )
    manual_review_required = _normalize_bool_flag(
        parsed.get("manual_review_required")
        or parsed.get("needs_manual_review")
    )
    evidence_level = str(parsed.get("evidence_level") or parsed.get("evidence_strength") or "").strip().lower()

    if match_status not in {"match", "mismatch", "uncertain"}:
        match_status = "uncertain"

    inferred_status = _infer_match_status_from_text(
        " ".join([str(summary or ""), str(blind_desc or ""), str(match_reason or ""), str(fallback_text or "")])
    )
    if inferred_status and (match_status == "uncertain" or not parsed.get("match_status")):
        match_status = inferred_status

    if match_status == "match" and confidence_score is None:
        confidence_score = 92.0
    elif match_status == "mismatch" and confidence_score is None:
        confidence_score = 20.0

    if not match_reason:
        if match_status == "match":
            match_reason = "图文一致，标注合理"
        elif match_status == "mismatch":
            match_reason = "图文不一致，疑似错误标注"
        else:
            match_reason = "需要人工复核"

    parts = []
    if blind_desc:
        parts.append(blind_desc)
    if match_status == "mismatch":
        parts.append("(图文疑似不匹配已拦截)")
    elif match_status == "match":
        parts.append("(已通过校验)")
    else:
        parts.append("(图文匹配度待确认)")

    display_text = " ".join([p for p in parts if p]).strip()
    if not display_text:
        display_text = _clean_short_text(fallback_text or "已完成分析", 90)

    validation = {
        "match_status": match_status,
        "match_reason": match_reason or ("图文一致" if match_status == "match" else "需要人工复核"),
        "risk": risk,
        "summary": summary,
        "blind_audio_desc": blind_desc,
        "recommendation": recommendation,
        "confidence_score": confidence_score,
        "entity_name_match": entity_name_match,
        "business_match": business_match,
        "manual_review_required": manual_review_required,
        "evidence_level": evidence_level,
    }
    return display_text, validation


def _build_blind_guidance_fallback(annotation: dict, validation: dict, review_decision: dict) -> str:
    title = str(annotation.get("title") or annotation.get("markerTitle") or "该位置").strip()
    scene_hint = _clean_short_text(annotation.get("description") or "", 18)
    marker_type = str(annotation.get("marker_type") or "").lower()
    risk = str(validation.get("risk") or "").strip()
    recommendation = str(validation.get("recommendation") or "").strip()
    blind_desc = str(validation.get("blind_audio_desc") or "").strip()
    review_state = str(review_decision.get("decision") or "").strip().lower()

    if blind_desc:
        return blind_desc
    if review_state == "reject":
        return "该标注未通过审核，请忽略。"
    if marker_type == "shop":
        if scene_hint:
            return f"{title}附近{scene_hint}，能帮你判断方位。"
        return f"前方附近有{title}，能帮你判断方位。"
    if marker_type == "obstacle":
        if risk:
            if scene_hint:
                return f"前方{title}附近{scene_hint}，存在{risk}，请提前避让。"
            return f"前方有{title}，存在{risk}，请提前避让。"
        if recommendation:
            if scene_hint:
                return f"前方{title}附近{scene_hint}，建议你减速绕开。"
            return f"前方有{title}，建议你减速绕开。"
        if scene_hint:
            return f"前方{title}附近{scene_hint}，请注意通行。"
        return f"前方有{title}，请注意通行。"
    if risk:
        if scene_hint:
            return f"前方{title}附近{scene_hint}，风险{risk}，请注意避让。"
        return f"前方有{title}，风险{risk}，请注意避让。"
    if recommendation:
        if scene_hint:
            return f"{title}附近{scene_hint}，建议{recommendation}。"
        return f"前方有{title}，建议{recommendation}。"
    if scene_hint:
        return f"{title}附近{scene_hint}，请注意通行。"
    return f"前方有{title}，请注意通行。"


async def _generate_blind_guidance_summary(annotation: dict, validation: dict, review_decision: dict) -> str:
    fallback = _build_blind_guidance_fallback(annotation, validation, review_decision)
    marker_type = str(annotation.get("marker_type") or "").lower()
    tone_instruction = (
        "当前是店铺或建筑锚点，请用帮助辨认方向、确认门头或判断拐点的口吻。"
        if marker_type == "shop"
        else "当前是障碍物，请用前方风险提醒、减速、绕开、避让的口吻。"
        if marker_type == "obstacle"
        else "请根据标注类型选择更贴近通行提醒的口吻。"
    )
    prompt = (
        "你是导盲摘要智能体。请基于已归档的位置标注，为视障用户生成一句简洁、自然、可直接播报的话。"
        "要求只返回 JSON，字段必须包含 blind_guidance_summary。"
        "字数控制在 16 到 36 个中文字符。"
        "语气要像语音助手当面提醒用户，不要写成说明文或报告。"
        "避免术语化表达，优先说明标注场景是什么、位于什么环境中、是否影响通行、盲人该注意什么。"
        "如果是店铺或建筑锚点，要带出门头、路口、楼下、拐角、台阶、通道等场景线索，不要只说名称。"
        "不要输出“位置参考”“位置参照”“摘要”等标签词，不要分点，不要解释流程。"
        "尽量使用“前方”“附近”“请注意”“能帮你判断方位”这类直接口语。"
        "如果是障碍物，要强调其所在场景和避让方式；如果是店铺或建筑锚点，要强调它如何帮助判断方位。"
        + tone_instruction
    )
    payload = json.dumps({
        "title": annotation.get("title"),
        "description": annotation.get("description"),
        "marker_type": annotation.get("marker_type"),
        "marker_type_label": annotation.get("marker_type_label"),
        "lat": annotation.get("lat"),
        "lng": annotation.get("lng"),
        "validation": validation,
        "review_decision": review_decision,
    }, ensure_ascii=False)

    try:
        resp = await guidance_agent_client.responses.create(
            model=GUIDANCE_AGENT_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_text", "text": payload},
                    ]
                }
            ]
        )
        raw_text = _extract_reply_text(resp)
        if not raw_text:
            try:
                raw_text = _extract_reply_text_from_dump(resp.model_dump())
            except Exception:
                raw_text = ""
        parsed = _extract_json_block(raw_text)
        summary = _clean_short_text(
            parsed.get("blind_guidance_summary")
            or parsed.get("summary")
            or parsed.get("blind_summary")
            or raw_text,
            40,
        )
        return _clean_blind_guidance_summary_text(summary, fallback) or fallback
    except Exception as e:
        print(f"⚠️ [导盲摘要智能体失败]: {e}")
        return fallback

def _extract_chat_completion_text(resp) -> str:
    try:
        choices = getattr(resp, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        if message is None:
            return ""
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str):
                        parts.append(txt)
                else:
                    txt = getattr(item, "text", None)
                    if isinstance(txt, str):
                        parts.append(txt)
            return "".join(parts).strip()
        return ""
    except Exception:
        return ""

async def _debounce_process_llm(client_id: str):
    try:
        pending_text = (asr_pending_text_by_client.get(client_id) or "").strip()
        debounce_seconds = ASR_LLM_DEBOUNCE_SECONDS
        if _spoken_contains_any(pending_text, FAST_COMMAND_KEYWORDS):
            debounce_seconds = min(0.05, ASR_LLM_DEBOUNCE_SECONDS)
        if debounce_seconds > 0:
            await asyncio.sleep(debounce_seconds)
        user_text = (asr_pending_text_by_client.get(client_id) or "").strip()
        if len(user_text) < 2:
            return
        gate = _evaluate_voice_gate(client_id, user_text)
        gate_action = gate.get("action")
        state = gate.get("state") or _get_or_create_device_state(client_id)
        if gate_action == "ignore":
            await manager.broadcast(_voice_event_payload(client_id, gate.get("reason", "ignore"), state=state, text=user_text))
            return
        if gate_action == "wake_only":
            await manager.broadcast(_voice_event_payload(client_id, gate.get("reason", "wake_only"), state=state, text=user_text))
            if VOICE_WAKE_REPLY:
                await _broadcast_instruction(client_id, VOICE_WAKE_REPLY)
            return
        if gate_action == "sleep":
            await manager.broadcast(_voice_event_payload(client_id, gate.get("reason", "sleep"), state=state, text=user_text))
            if VOICE_SLEEP_REPLY:
                await _broadcast_instruction(client_id, VOICE_SLEEP_REPLY)
            return
        user_text = (gate.get("text") or "").strip()
        if len(user_text) < 2:
            return
        await manager.broadcast(_voice_event_payload(client_id, gate.get("reason", "route_text"), state=state, text=user_text))
        norm_text = _normalize_asr_text(user_text)
        last_norm = asr_last_sent_norm_by_client.get(client_id, "")
        last_ts = asr_last_sent_ts_by_client.get(client_id, 0.0)
        now = time.time()
        has_sentence_end = bool(re.search(r"[。！？!?\.]$", user_text))
        if last_norm and norm_text.startswith(last_norm) and not has_sentence_end and (now - last_ts) < 3.0:
            return
        if norm_text and norm_text == last_norm and (now - last_ts) < ASR_LLM_DUPLICATE_TTL_SECONDS:
            return
        asr_last_sent_norm_by_client[client_id] = norm_text
        asr_last_sent_ts_by_client[client_id] = now
        await process_llm_response(client_id, user_text)
    except asyncio.CancelledError:
        return
    finally:
        current_task = asr_debounce_task_by_client.get(client_id)
        if current_task is asyncio.current_task():
            asr_debounce_task_by_client.pop(client_id, None)

async def process_llm_response(client_id: str, user_text: str):
    """
    Sends the user's ASR text to Doubao LLM and broadcasts the response.
    Injects context like GPS location.
    """
    if not user_text or len(user_text.strip()) < 2:
        return

    # --- 1. 意图拦截：检查是否是导航指令 ---
    normalized_text = user_text.strip()

    stop_nav_patterns = [
        r"^(停止导航|结束导航|取消导航|退出导航|关闭导航)\s*$",
        r"^(先停一下导航|先暂停导航|暂停导航|暂时停止导航|临时结束导航)\s*$",
    ]
    for pattern in stop_nav_patterns:
        if re.match(pattern, normalized_text):
            print(f"🛑 [Intent] 识别到停止导航意图: {normalized_text}")
            result_msg = nav_manager.stop_navigation(client_id)
            _record_device_flow_event(
                client_id,
                last_instruction=result_msg,
                last_instruction_at=time.time(),
                assist_active=False,
                assist_status="idle",
                assist_reason="stop_navigation_voice"
            )
            await _broadcast_instruction(client_id, result_msg)
            await _broadcast_route_update(client_id, route_override=[])
            return

    if _spoken_contains_any(normalized_text, EMERGENCY_ASSIST_KEYWORDS):
        if not _spoken_contains_any(normalized_text, ["取消", "不用", "关闭", "结束"]):
            # 检查是否已经在呼叫中，避免重复触发或识别历史重播
            state = _get_or_create_device_state(client_id)
            if state.get("assist_active") and state.get("assist_status") == "pending":
                print(f"🆘 [Intent] 已经在呼叫中，忽略重复的人工求助意图: {normalized_text}")
                return
            print(f"🆘 [Intent] 识别到人工求助意图: {normalized_text}")
            result_msg = await _toggle_voice_assist(client_id, True, "voice_help_request")
            await _broadcast_instruction(client_id, result_msg)
            return

    if _spoken_contains_any(normalized_text, ASSIST_CANCEL_KEYWORDS):
        state = _get_or_create_device_state(client_id)
        if not state.get("assist_active"):
             print(f"✅ [Intent] 当前未在呼叫中，忽略取消人工求助意图: {normalized_text}")
             return
        print(f"✅ [Intent] 识别到取消人工求助意图: {normalized_text}")
        result_msg = await _toggle_voice_assist(client_id, False, "voice_help_cancel")
        await _broadcast_instruction(client_id, result_msg)
        return

    volume_intent = _parse_volume_control_intent(normalized_text)
    if volume_intent:
        print(f"🔊 [Intent] 识别到音量控制意图: {volume_intent}")
        control_msg = {
            "type": "device_control",
            "action": "set_volume",
            "client_id": client_id,
            "mode": volume_intent["mode"],
            "value": volume_intent.get("value"),
            "timestamp": time.time(),
        }
        delivered = await manager.send_to_client_input(client_id, control_msg)
        if delivered:
            await _broadcast_instruction(client_id, volume_intent["reply"])
        else:
            await _broadcast_instruction(client_id, "设备当前不在线，暂时无法调节音量。")
        return

    save_alias = _extract_memory_alias_from_speech(normalized_text)
    if save_alias and _spoken_contains_any(normalized_text, ["保存", "记为", "记住", "作为"]):
        state = manager.device_states.get(client_id, {})
        lat = state.get("lat")
        lng = state.get("lng")
        if lat is None or lng is None:
            await _broadcast_instruction(client_id, "当前定位还不稳定，暂时无法保存这个地点。")
        else:
            save_result = _save_current_location_as_route_memory(client_id, save_alias, lat, lng)
            normalized_item = _normalize_route_memory_item(save_result["item"])
            await manager.broadcast({
                "type": "route_memory_saved",
                "client_id": client_id,
                "memory": normalized_item,
                "created": bool(save_result.get("created")),
                "timestamp": time.time(),
            })
            if save_result.get("created"):
                await _broadcast_instruction(client_id, f"已为您保存常用地址：{save_alias}。")
            else:
                await _broadcast_instruction(client_id, f"已更新常用地址：{save_alias}。")
        return

    if _spoken_contains_any(normalized_text, ["还有多远", "离目的地还有多远", "距离目的地还有多远", "还有多久到", "还有多少米"]):
        await _broadcast_instruction(client_id, _build_remaining_distance_reply(client_id))
        return

    if _spoken_contains_any(normalized_text, ["再说一遍", "重复一遍", "重复刚才的导航", "再重复一下", "刚才说什么", "上一条指令"]):
        await _broadcast_instruction(client_id, _build_repeat_instruction_reply(client_id))
        return

    if _spoken_contains_any(normalized_text, ["我在哪", "我现在在哪", "当前位置", "当前在哪里", "我现在的位置"]):
        state = manager.device_states.get(client_id, {})
        lat = state.get("lat")
        lng = state.get("lng")
        if lat is None or lng is None:
            await _broadcast_instruction(client_id, "暂时还没有定位成功，请稍等片刻。")
        else:
            await _broadcast_instruction(client_id, f"您当前的位置坐标是，经度 {lng:.6f}，纬度 {lat:.6f}。")
        return

    if _spoken_contains_any(normalized_text, ["有哪些常用地址", "我的常用地址", "常用地址有哪些", "路线记忆有哪些", "我保存了哪些地址"]):
        memories = _get_device_route_memories(client_id)
        if not memories:
            await _broadcast_instruction(client_id, "当前还没有为您保存常用地址。")
        else:
            alias_text = "，".join([str(m.get("alias")) for m in memories[:5] if m.get("alias")])
            await _broadcast_instruction(client_id, f"您当前保存的常用地址有：{alias_text}。")
        return

    memory_match = _find_route_memory_match(client_id, normalized_text)
    if memory_match and _is_navigation_query(normalized_text):
        alias = memory_match.get("alias") or "常用地址"
        print(f"🏠 [Intent] 识别到常用地址导航意图: {alias}")
        result_msg = nav_manager.start_navigation_to_coords(client_id, alias, memory_match["lng"], memory_match["lat"])
        _record_device_flow_event(
            client_id,
            last_instruction=result_msg,
            last_instruction_at=time.time(),
            assist_active=False,
            assist_status="idle",
            assist_reason=""
        )
        await _broadcast_navigation_instruction(client_id, result_msg, {"mode": "macro"})
        await _prime_navigation_after_start(client_id)
        return

    destination = _extract_navigation_destination(normalized_text)
    if destination:
        print(f"📍 [Intent] 识别到导航意图，目的地: {destination}")
        result_msg = await _try_start_or_queue_navigation(client_id, destination)
        _record_device_flow_event(
            client_id,
            last_instruction=result_msg,
            last_instruction_at=time.time(),
            assist_active=False,
            assist_status="idle",
            assist_reason=""
        )
        await _broadcast_navigation_instruction(client_id, result_msg, {"mode": "macro"})
        await _prime_navigation_after_start(client_id)
        return

    if _is_navigation_query(normalized_text):
        await _broadcast_instruction(client_id, "我听到您想导航了，请再说一次具体目的地名称。")
        return

    print(f"🧠 [LLM Request] Sending to Doubao: {user_text}")
    t0 = time.time()
    
    # 1. Gather Context (e.g. latest GPS)
    image_queue = latest_images_by_client.get(client_id)
    has_visual_context = bool(image_queue)
    active_nav_session = nav_manager.sessions.get(client_id)
    has_nav_context = bool(active_nav_session)

    current_state = manager.device_states.get(client_id, {})
    cur_lat = current_state.get("lat")
    cur_lng = current_state.get("lng")
    location_hint = ""
    if cur_lat is not None and cur_lng is not None:
        location_hint = f"当前设备 GPS 坐标为：经度 {float(cur_lng):.6f}，纬度 {float(cur_lat):.6f}。"

    if has_visual_context or has_nav_context:
        context_str = (
            "你是专为全盲或重度视障人士设计的智能导航助手，你的回答将通过语音直接播报给用户听。"
            "【核心规则】："
            "1. 只有在确实存在视觉画面、导航路线或明确环境上下文时，才输出物理动作指令。"
            "2. 如果缺少足够环境上下文，不要编造左转、右转、前方多少米等导航信息。"
            "3. 当可以导航时，必须具体、具象地指导物理动作（例如：向左横跨半步、向右后方转身、原地停住用盲杖向正前方探等），绝不能使用“绕开”“注意避让”等笼统词汇。"
            "4. 给出明确的距离和方位感（用钟表方向或前后左右）。"
            "5. 不要输出 markdown、列表序号或客套话，直接口语化回答。"
            "6. 回答必须简练，控制在30字以内。"
        )
        if location_hint:
            context_str += location_hint
    else:
        context_str = (
            "你是视障出行系统的语音助手。"
            "如果用户的话不是已命中的系统指令，请直接、简短地复述你的理解或提示用户换一种更明确的说法。"
            "禁止编造任何左转、右转、前方多少米等导航动作。"
            "禁止虚构环境信息。"
            "回答控制在25字以内，直接输出可播报口语。"
        )
        if location_hint:
            context_str += f" 当前已知设备位置：{location_hint}"
    
    # Optional: If you have global GPS variables in backend.py, inject them here
    # e.g., context_str += f"\nUser is currently at Longitude: {current_lng}, Latitude: {current_lat}"

    try:
        max_tokens = 60  # 优化点3：极致压缩 max_tokens，盲人导航 30 字以内足够，设为 60 防止截断
        temperature = 0.01 # 优化点4：降低 temperature 减少模型采样计算时间
        user_content = [{"type": "text", "text": user_text}]
        if image_queue:
            for img_b64 in list(image_queue)[-2:]:
                user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
        
        chat_resp_stream = await asyncio.wait_for(
            llm_client.chat.completions.create(
                model=LLM_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                messages=[
                    {"role": "system", "content": context_str},
                    {"role": "user", "content": user_content}
                ]
            ),
            timeout=18.0
        )
        
        print(f"⏱️ [LLM API Request Elapsed]: {int((time.time() - t0) * 1000)}ms")
        
        buffer = ""
        full_reply_text = ""
        first_chunk_sent = False
        
        async for chunk in chat_resp_stream:
            content = chunk.choices[0].delta.content
            if content:
                buffer += content
                full_reply_text += content
                # Send chunks at punctuation to avoid choppy TTS
                if re.search(r"[。！？,!?，]$", buffer):
                    send_text = buffer.strip()
                    if send_text:
                        print(f"🤖 [LLM Chunk]: {send_text}")
                        await manager.broadcast({
                            "type": "assistant_reply",
                            "client_id": client_id,
                            "text": send_text,
                            "is_chunk": True,
                            "is_first_chunk": not first_chunk_sent,
                            "timestamp": time.time()
                        })
                        first_chunk_sent = True
                    buffer = ""

        # Send any remaining buffer
        if buffer.strip():
            print(f"🤖 [LLM Final Chunk]: {buffer.strip()}")
            await manager.broadcast({
                "type": "assistant_reply",
                "client_id": client_id,
                "text": buffer.strip(),
                "is_chunk": True,
                "is_first_chunk": not first_chunk_sent,
                "timestamp": time.time()
            })
            
        if not full_reply_text.strip():
            full_reply_text = "我在，请再说一遍问题。"
            await manager.broadcast({
                "type": "assistant_reply",
                "client_id": client_id,
                "text": full_reply_text,
                "timestamp": time.time()
            })
        print(f"🤖 [LLM Full Response]: {full_reply_text}")
    except asyncio.TimeoutError:
        print("❌ [LLM Error]: timeout(18s)")
        await manager.broadcast({
            "type": "assistant_reply",
            "client_id": client_id,
            "text": "我在，请再说一遍问题。",
            "timestamp": time.time()
        })
    except Exception as e:
        print(f"❌ [LLM Error]: {e}")

def process_audit():
    """Checks for expired contributions and promotes/rejects them."""
    global contributions_data, obstacles_data
    now = time.time()
    # 10 days = 864000 seconds
    AUDIT_PERIOD = 864000
    
    # Identify expired contributions
    # Allow custom audit period if set, otherwise default 10 days
    expired = []
    active = []
    
    for c in contributions_data:
        period = c.get('auditPeriod', AUDIT_PERIOD)
        if now - c['createdAt'] > period:
            expired.append(c)
        else:
            active.append(c)
    
    if not expired:
        return

    changes_made = False
    
    for c in expired:
        upvotes = len(c.get('upvotes', []))
        downvotes = len(c.get('downvotes', []))
        
        # Simple Logic: If upvotes >= downvotes, Approve. Else Reject.
        if upvotes >= downvotes:
            # Approved
            print(f"Approving contribution {c['id']}")
            
            # Check if it's an update to existing obstacle or new one
            marker_id = c['markerId']
            existing_obstacle = next((o for o in obstacles_data if o['id'] == marker_id), None)
            
            if existing_obstacle:
                # Update existing
                if c['type'] == 1: # obstacleStatus
                     # VerificationStatus: active=0, candidate=1, removed=2
                     # ObstacleStatus: active=0, removed=1
                     existing_obstacle['status'] = 0 if c.get('proposedStatus') == 0 else 2
                     existing_obstacle['lastVerifiedAt'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
                     if c.get('imageUrl'):
                         if 'imageUrls' not in existing_obstacle:
                             existing_obstacle['imageUrls'] = []
                         existing_obstacle['imageUrls'].append(c['imageUrl'])
                     changes_made = True
                
                elif c['type'] == 0: # storeLayout (Shop Info Update)
                     # For shops, we just add the image if verified
                     if c.get('imageUrl'):
                         if 'imageUrls' not in existing_obstacle:
                             existing_obstacle['imageUrls'] = []
                         existing_obstacle['imageUrls'].append(c['imageUrl'])
                         existing_obstacle['lastVerifiedAt'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
                         changes_made = True
            else:
                # New Obstacle (Create)
                # Requires lat/lng
                lat = c.get('lat')
                lng = c.get('lng')
                
                if lat is not None and lng is not None:
                    new_obstacle = {
                        "id": marker_id, 
                        "lat": lat,
                        "lng": lng,
                        "type": 3, # Default to other
                        "radius": 20.0,
                        "createdAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                        "status": 0,
                        "clearCount": 0,
                        "lastVerifiedAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                        "imageUrls": [c['imageUrl']] if c.get('imageUrl') else [],
                        "title": c['markerTitle'],
                        "description": c['content'],
                        "comments": []
                    }
                    
                    if c['type'] == 0: # storeLayout -> Shop
                        new_obstacle['type'] = 4
                    else:
                        new_obstacle['type'] = 3
                        
                    obstacles_data.append(new_obstacle)
                    changes_made = True
                else:
                    print(f"Cannot promote contribution {c['id']} - missing lat/lng")
        else:
            print(f"Rejecting contribution {c['id']}")
            
    if changes_made:
        save_obstacles()
        
    # Update contributions list (remove expired)
    contributions_data = active
    save_contributions()

@app.post("/api/contribution/add")
async def add_contribution(req: ContributionRequest):
    global contributions_data, obstacles_data
    new_contribution = _normalize_region_fields(req.dict())
    if not new_contribution.get("forumKey"):
        new_contribution["forumKey"] = new_contribution.get("forumName") or "默认论坛"
    if not new_contribution.get("forumName"):
        new_contribution["forumName"] = new_contribution.get("forumKey") or "默认论坛"
    if not new_contribution.get("targetType"):
        new_contribution["targetType"] = "shop" if req.type in (0, 4) else "obstacle"
    new_contribution['id'] = str(uuid.uuid4())
    new_contribution['createdAt'] = time.time()
    new_contribution['upvotes'] = []
    new_contribution['downvotes'] = []

    contributions_data.insert(0, new_contribution)
    save_contributions()

    if req.type == 3 and req.imageUrl and req.lat and req.lng:
        is_duplicate = False
        for obs in obstacles_data:
            if abs(obs['lat'] - req.lat) < 0.0001 and abs(obs['lng'] - req.lng) < 0.0001:
                is_duplicate = True
                break

        if not is_duplicate:
            new_obstacle = _normalize_region_fields({
                "id": str(uuid.uuid4()),
                "lat": req.lat,
                "lng": req.lng,
                "type": 3,
                "radius": 20.0,
                "createdAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                "status": 0,
                "clearCount": 0,
                "lastVerifiedAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                "imageUrls": [req.imageUrl],
                "title": req.markerTitle,
                "description": req.content,
                "comments": [],
                "regionCode": req.regionCode,
                "regionName": req.regionName,
                "cityCode": req.cityCode,
                "cityName": req.cityName,
                "targetType": "obstacle",
                "forumKey": req.forumKey or "障碍论坛",
                "forumName": req.forumName or "障碍论坛",
            }, fallback_forum="障碍论坛")
            obstacles_data.append(new_obstacle)
            save_obstacles()
            print(f"Auto-created obstacle from contribution: {new_obstacle['id']}")

    return {"success": True, "contribution_id": new_contribution['id']}
        
    contrib_id = dispute['candidateB'] # The new one
    marker_id = dispute['candidateA'] # The existing one
    
    contrib = next((c for c in contributions_data if c['id'] == contrib_id), None)
    
    if resolution == "merge":
        # Merge C into A
        # Update A with images from C
        existing_obs = next((o for o in obstacles_data if o['id'] == marker_id), None)
        existing_shop = next((s for s in shops_data if s['id'] == marker_id), None)
        
        target = existing_obs if existing_obs else existing_shop
        
        if target and contrib:
            if contrib.get('imageUrl'):
                 if 'imageUrls' not in target:
                     target['imageUrls'] = []
                 if contrib['imageUrl'] not in target['imageUrls']:
                     target['imageUrls'].append(contrib['imageUrl'])
            target['lastVerifiedAt'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
            
            save_obstacles()
            save_shops()
            
        # Delete Contribution C
        contributions_data = [c for c in contributions_data if c['id'] != contrib_id]
        save_contributions()
        
    elif resolution == "separate":
        # Approve C as independent
        # Just remove the "dispute_voting" status? Or promote it directly?
        # Let's promote it directly using the standard logic (by calling admin_approve internally or replicating logic)
        # For simplicity, we just clear the dispute status and let it remain as a contribution pending approval?
        # Or better: "Separate" means "Approved as new".
        if contrib:
             # Promote to Obstacle/Shop
             if contrib['type'] == 0 or contrib['type'] == 4:
                 # Create Shop
                 new_shop = {
                    "id": contrib['markerId'], 
                    "lat": contrib['lat'],
                    "lng": contrib['lng'],
                    "type": 4,
                    "createdAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                    "status": 0,
                    "imageUrls": [contrib['imageUrl']] if contrib.get('imageUrl') else [],
                    "title": contrib['markerTitle'],
                    "description": contrib['content'],
                    "comments": []
                 }
                 shops_data.append(new_shop)
                 save_shops()
             else:
                 # Create Obstacle
                 new_obs = {
                    "id": contrib['markerId'], 
                    "lat": contrib['lat'],
                    "lng": contrib['lng'],
                    "type": 3,
                    "radius": 20.0,
                    "createdAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                    "status": 0,
                    "clearCount": 0,
                    "lastVerifiedAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f"),
                    "imageUrls": [contrib['imageUrl']] if contrib.get('imageUrl') else [],
                    "title": contrib['markerTitle'],
                    "description": contrib['content'],
                    "comments": []
                 }
                 obstacles_data.append(new_obs)
                 save_obstacles()
                 
             # Remove contribution
             contributions_data = [c for c in contributions_data if c['id'] != contrib_id]
             save_contributions()
             
    elif resolution == "reject":
        # Delete Contribution C
        contributions_data = [c for c in contributions_data if c['id'] != contrib_id]
        save_contributions()
        
    # Mark dispute as resolved
    dispute['status'] = "resolved"
    dispute['resolution'] = resolution
    save_disputes()
    
    return {"success": True, "resolution": resolution}

@app.get("/api/contribution/list")
async def list_contributions(request: Request):
    region_code = (request.query_params.get("regionCode") or "").strip()
    forum_key = (request.query_params.get("forumKey") or "").strip()
    target_type = (request.query_params.get("targetType") or "").strip().lower()

    enriched_posts = []
    for c in contributions_data:
        c_copy = _normalize_region_fields(c.copy())
        if target_type and str(c_copy.get("targetType") or "").lower() != target_type:
            continue
        if region_code and (c_copy.get("regionCode") or "") != region_code:
            continue
        if forum_key and (c_copy.get("forumKey") or "") != forum_key:
            continue
        c_copy['userTrustScore'] = get_user_trust_score(c['userId'])
        enriched_posts.append(c_copy)

    def _created_at_sort_key(post: dict) -> float:
        created_at = post.get("createdAt")
        if isinstance(created_at, (int, float)):
            return float(created_at)
        if isinstance(created_at, str):
            try:
                return float(created_at)
            except Exception:
                return 0.0
        return 0.0

    enriched_posts.sort(key=_created_at_sort_key, reverse=True)

    return {
        "success": True,
        "posts": enriched_posts,
        "forums": _get_forum_catalog([p for p in contributions_data if str(_normalize_region_fields(dict(p)).get("targetType") or "").lower() == (target_type or str(_normalize_region_fields(dict(p)).get("targetType") or "").lower())]),
        "regions": _get_region_catalog(enriched_posts),
    }

@app.post("/api/contribution/vote")
async def vote_contribution(req: VoteRequest):
    for c in contributions_data:
        if c['id'] == req.contributionId:
            # Initialize new fields if missing
            if 'votes_detail' not in c:
                c['votes_detail'] = {}
                # Backfill
                for uid in c.get('upvotes', []):
                    c['votes_detail'][uid] = {"type": "up", "weight": 1.0}
                for uid in c.get('downvotes', []):
                    c['votes_detail'][uid] = {"type": "down", "weight": 1.0}
            
            # Get User Trust Score
            trust_score = get_user_trust_score(req.userId)
            weight = 1.0
            if trust_score >= 500:
                weight = 3.0
            elif trust_score >= 200:
                weight = 1.5
            elif trust_score < 50:
                weight = 0.5
            
            # Update Vote Detail
            # Check if user already voted
            prev_vote = c['votes_detail'].get(req.userId)
            
            # Remove from simple lists first
            if req.userId in c['upvotes']:
                c['upvotes'].remove(req.userId)
            if req.userId in c['downvotes']:
                c['downvotes'].remove(req.userId)
                
            # Update detail
            c['votes_detail'][req.userId] = {
                "type": req.voteType,
                "weight": weight,
                "timestamp": time.time()
            }
            
            # Add to simple lists
            if req.voteType == 'up':
                c['upvotes'].append(req.userId)
            elif req.voteType == 'down':
                c['downvotes'].append(req.userId)
                
            # Recalculate Score
            total_score = 0.0
            for v in c['votes_detail'].values():
                w = v.get('weight', 1.0)
                if v['type'] == 'up':
                    total_score += w
                else:
                    total_score -= w
            c['score'] = total_score
            
            save_contributions()
            return {
                "success": True, 
                "upvotes": c['upvotes'], 
                "downvotes": c['downvotes'],
                "score": total_score,
                "user_weight": weight
            }
    return {"success": False, "message": "Contribution not found"}

class CommentRequest(BaseModel):
    userId: str
    userNickname: str
    content: str

@app.post("/api/contribution/{contribution_id}/comment")
async def add_contribution_comment(contribution_id: str, req: CommentRequest):
    for c in contributions_data:
        if c['id'] == contribution_id:
            if 'comments' not in c:
                c['comments'] = []
            
            new_comment = {
                "id": str(uuid.uuid4()),
                "userId": req.userId,
                "userNickname": req.userNickname,
                "content": req.content,
                "createdAt": time.time()
            }
            c['comments'].append(new_comment)
            save_contributions()
            return {"success": True, "comments": c['comments']}
            
    return HTTPException(status_code=404, detail="Contribution not found")

@app.post("/api/admin/contribution/{contribution_id}/approve")
async def admin_approve_contribution(contribution_id: str, request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    global contributions_data, obstacles_data, shops_data
    contribution = next((c for c in contributions_data if c['id'] == contribution_id), None)
    
    if not contribution:
        raise HTTPException(status_code=404, detail="Contribution not found")
        
    # --- LOGGING FOR ROLLBACK ---
    # Record state BEFORE change
    prev_state = None
    target_id = contribution['markerId']
    action_type = "unknown"
    
    # Check if target exists
    if contribution['type'] == 0 or contribution['type'] == 4:
        existing = next((s for s in shops_data if s['id'] == target_id), None)
        action_type = "shop_update" if existing else "shop_create"
        if existing:
            prev_state = existing.copy()
    else:
        existing = next((o for o in obstacles_data if o['id'] == target_id), None)
        action_type = "obstacle_update" if existing else "obstacle_create"
        if existing:
            prev_state = existing.copy()
            
    log_entry = {
        "id": str(uuid.uuid4()),
        "contribution_id": contribution_id,
        "action_type": action_type,
        "target_id": target_id,
        "prev_state": prev_state,
        "timestamp": time.time(),
        "contribution_data": contribution.copy() # Save full contribution to know who voted
    }
    action_logs.append(log_entry)
    save_action_logs()
    # ----------------------------
        
    changes_made = _finalize_pending_contribution(contribution_id)
    return {"success": changes_made}

@app.delete("/api/contribution/{contribution_id}/comment/{comment_id}")
async def delete_contribution_comment(contribution_id: str, comment_id: str, userId: str):
    for c in contributions_data:
        if c['id'] == contribution_id:
            if 'comments' in c:
                original_len = len(c['comments'])
                # Allow comment author OR contribution author to delete
                # But for now let's just check comment author
                
                # Filter out the comment if userId matches
                new_comments = []
                deleted = False
                for comment in c['comments']:
                    if comment['id'] == comment_id:
                        if comment['userId'] == userId:
                            deleted = True
                            continue # Skip this comment (delete)
                        else:
                            raise HTTPException(status_code=403, detail="Not authorized")
                    new_comments.append(comment)
                
                if deleted:
                    c['comments'] = new_comments
                    save_contributions()
                    return {"success": True}
                else:
                     raise HTTPException(status_code=404, detail="Comment not found")
            else:
                raise HTTPException(status_code=404, detail="Comment not found")
                
    raise HTTPException(status_code=404, detail="Contribution not found")

@app.delete("/api/contribution/{contribution_id}")
async def delete_contribution(contribution_id: str, userId: str):
    global contributions_data
    # Check if contribution exists and user is owner
    contribution = next((c for c in contributions_data if c['id'] == contribution_id), None)
    if not contribution:
        raise HTTPException(status_code=404, detail="Contribution not found")
        
    if contribution['userId'] != userId:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    initial_len = len(contributions_data)
    contributions_data = [c for c in contributions_data if c['id'] != contribution_id]
    
    if len(contributions_data) < initial_len:
        save_contributions()
        return {"success": True}
    return {"success": False}

class TrajectoryRequest(BaseModel):
    userId: str
    path: List[List[float]] # [[lat, lng], [lat, lng], ...]

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371e3 # meters
    phi1 = lat1 * math.pi / 180
    phi2 = lat2 * math.pi / 180
    dphi = (lat2 - lat1) * math.pi / 180
    dlambda = (lon2 - lon1) * math.pi / 180
    
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

@app.post("/api/verify/trajectory")
async def verify_trajectory(req: TrajectoryRequest):
    """
    Geofence Automatic Verification:
    If a user passes through a location that has a pending 'Removal' request,
    System automatically votes 'UP' for removal.
    """
    verified_count = 0
    
    # 1. Find pending removal requests
    # Type 1 (Status Change) where proposedStatus is 1 or 2 (Removed)
    pending_removals = [c for c in contributions_data if c['type'] == 1 and c.get('proposedStatus') in [1, 2]]
    
    if not pending_removals:
        return {"status": "no_pending_removals"}
        
    # 2. Check trajectory against these locations
    for c in pending_removals:
        # Get target obstacle location
        # Need to look up the obstacle in obstacles_data
        target_obs = next((o for o in obstacles_data if o['id'] == c['markerId']), None)
        if not target_obs:
            continue
            
        obs_lat = target_obs['lat']
        obs_lng = target_obs['lng']
        
        # Check if any point in path is close enough (e.g., < 10 meters)
        is_close = False
        for point in req.path:
            if len(point) >= 2:
                dist = calculate_distance(point[0], point[1], obs_lat, obs_lng)
                if dist < 10.0: # 10 meters
                    is_close = True
                    break
        
        if is_close:
            # Trigger System Vote
            # Create a mock VoteRequest
            vote_req = VoteRequest(
                contributionId=c['id'],
                userId="SYSTEM_GEOFENCE",
                voteType="up" # Support removal
            )
            # Call vote logic directly
            # Note: We need to adapt vote_contribution logic since it's an async function
            # and we are inside an async function, so we can await it.
            # However, vote_contribution expects a request object.
            
            # Let's manually invoke the logic to avoid recursion issues or just call the function
            await vote_contribution(vote_req)
            verified_count += 1
            print(f"✅ System Geofence Verification: Voted UP for {c['id']}")

    return {"status": "success", "verified_count": verified_count}

@app.get("/api/admin/action_logs")
async def get_action_logs(request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    # Sort by timestamp desc
    sorted_logs = sorted(action_logs, key=lambda x: x['timestamp'], reverse=True)
    return {"success": True, "logs": sorted_logs}

@app.post("/api/admin/rollback")
async def rollback_action(req: dict, request: Request):
    """
    Admin One-Click Rollback:
    req: { "logId": "..." }
    """
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    log_id = req.get("logId")
    log_entry = next((l for l in action_logs if l.get('id') == log_id), None)
    
    if not log_entry:
        raise HTTPException(status_code=404, detail="Log entry not found")
        
    action_type = log_entry['action_type']
    target_id = log_entry['target_id']
    prev_state = log_entry['prev_state']
    
    global shops_data, obstacles_data
    
    # 1. Revert Data
    if action_type == "shop_update":
        if prev_state:
            # Restore previous state
            for i, s in enumerate(shops_data):
                if s['id'] == target_id:
                    shops_data[i] = prev_state
                    save_shops()
                    break
    elif action_type == "shop_create":
        # Delete created shop
        shops_data = [s for s in shops_data if s['id'] != target_id]
        save_shops()
        
    elif action_type == "obstacle_update":
        if prev_state:
            for i, o in enumerate(obstacles_data):
                if o['id'] == target_id:
                    obstacles_data[i] = prev_state
                    save_obstacles()
                    break
    elif action_type == "obstacle_create":
        obstacles_data = [o for o in obstacles_data if o['id'] != target_id]
        save_obstacles()
        
    # 2. Penalize Users (Simple implementation)
    # Reduce trust score of users who voted UP for the erroneous contribution
    contrib_data = log_entry.get('contribution_data')
    if contrib_data:
        upvoters = contrib_data.get('upvotes', [])
        for uid in upvoters:
            user = next((u for u in users_data if u['id'] == uid), None)
            if user:
                current_score = user.get('trust_score', 100)
                user['trust_score'] = max(0, current_score - 20) # Heavy penalty
        save_users()

    # 3. Mark log as rolled back (optional, or just delete it?)
    # For now, let's remove it from logs to prevent double rollback
    action_logs.remove(log_entry)
    save_action_logs()
    
    return {"success": True, "message": "Rollback successful"}

# --- Admin Extended APIs ---

class ObstacleUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    imageUrls: Optional[List[str]] = None
    status: Optional[int] = None

@app.put("/api/admin/obstacles/{obstacle_id}")
async def admin_update_obstacle(obstacle_id: str, req: ObstacleUpdateRequest, request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    global obstacles_data
    obstacle = next((o for o in obstacles_data if o['id'] == obstacle_id), None)
    
    if not obstacle:
        raise HTTPException(status_code=404, detail="Obstacle not found")
        
    if req.title is not None:
        obstacle['title'] = req.title
    if req.description is not None:
        obstacle['description'] = req.description
    if req.imageUrls is not None:
        obstacle['imageUrls'] = req.imageUrls
    if req.status is not None:
        obstacle['status'] = req.status
        
    save_obstacles()
    return {"success": True, "obstacle": obstacle}

class ContributionAuditPeriodUpdateRequest(BaseModel):
    auditPeriodDays: float

@app.put("/api/admin/contribution/{contribution_id}/audit_period")
async def admin_update_contribution_audit_period(contribution_id: str, req: ContributionAuditPeriodUpdateRequest, request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    global contributions_data
    contribution = next((c for c in contributions_data if c['id'] == contribution_id), None)
    
    if not contribution:
        raise HTTPException(status_code=404, detail="Contribution not found")
        
    # Convert days to seconds
    contribution['auditPeriod'] = req.auditPeriodDays * 24 * 3600
    save_contributions()
    return {"success": True, "contribution": contribution}

@app.delete("/api/admin/contribution/{contribution_id}/comment/{comment_id}")
async def admin_delete_contribution_comment(contribution_id: str, comment_id: str, request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    global contributions_data
    for c in contributions_data:
        if c['id'] == contribution_id:
            if 'comments' in c:
                original_len = len(c['comments'])
                new_comments = [comment for comment in c['comments'] if comment['id'] != comment_id]
                
                if len(new_comments) < original_len:
                    c['comments'] = new_comments
                    save_contributions()
                    return {"success": True}
                else:
                     raise HTTPException(status_code=404, detail="Comment not found")
            else:
                raise HTTPException(status_code=404, detail="Comment not found")
                
    raise HTTPException(status_code=404, detail="Contribution not found")

@app.post("/api/contribution/upload_image")
async def upload_contribution_image(request: Request, file: UploadFile = File(...)):
    try:
        file_ext = file.filename.split(".")[-1]
        file_name = f"{uuid.uuid4()}.{file_ext}"
        file_path = os.path.join(CURRENT_DIR, "static/images", file_name)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        host = request.headers.get("host", "127.0.0.1:8000")
        full_url = f"http://{host}/static/images/{file_name}"
            
        return {"success": True, "url": full_url}
    except Exception as e:
        print(f"Upload error: {e}")
        return {"success": False, "message": str(e)}

# --- Admin User Management APIs ---

@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Reload from DB to ensure latest data
    # Force reload by resetting cache
    global _users_cache
    _users_cache["last_loaded"] = 0
    load_users()
    
    # Sync first to ensure we catch new posters
    sync_users_from_contributions()
    return {"success": True, "users": users_data}

class UserUpdateRequest(BaseModel):
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    status: Optional[str] = None

@app.post("/api/admin/upload/avatar")
async def admin_upload_avatar(request: Request, file: UploadFile = File(...), user_id: str = Body(default=None)):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    try:
        if user_id:
            # Create user-specific directory: uploads/users/{user_id}/
            user_dir = os.path.join(UPLOAD_DIR, "users", user_id)
            if not os.path.exists(user_dir):
                os.makedirs(user_dir)
            
            # Clean up old files in this directory
            for f in os.listdir(user_dir):
                try:
                    os.remove(os.path.join(user_dir, f))
                except Exception as e:
                    print(f"Error removing old avatar: {e}")

            # Save new file
            ext = file.filename.split('.')[-1] if '.' in file.filename else 'jpg'
            filename = f"avatar_{int(time.time())}.{ext}"
            filepath = os.path.join(user_dir, filename)
            
            with open(filepath, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # Return URL: uploads/users/{user_id}/{filename}
            # Note: The client expects a relative path that can be appended to base URL
            return {"success": True, "url": f"uploads/users/{user_id}/{filename}"}
        else:
            # Fallback to old behavior if no user_id provided
            if not os.path.exists(UPLOAD_DIR):
                os.makedirs(UPLOAD_DIR)
                
            ext = file.filename.split('.')[-1] if '.' in file.filename else 'jpg'
            filename = f"avatar_{int(time.time())}_{''.join(random.choices('abcdefghijklmnopqrstuvwxyz', k=4))}.{ext}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            
            with open(filepath, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
                
            return {"success": True, "url": f"uploads/{filename}"}

    except Exception as e:
        print(f"Upload error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})

@app.put("/api/admin/users/{user_id}")
async def admin_update_user(user_id: str, req: UserUpdateRequest, request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    global users_data
    user = next((u for u in users_data if u['id'] == user_id), None)
    
    if user:
        if req.nickname is not None:
            user['nickname'] = req.nickname
        if req.avatar is not None:
            user['avatar'] = req.avatar
        if req.status is not None:
            if req.status in ["active", "banned"]:
                user['status'] = req.status
                
        save_users()
        return {"success": True, "user": user}
        
    return HTTPException(status_code=404, detail="User not found")



@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    # Delete user from users_data
    global users_data
    users_data = [u for u in users_data if u['id'] != user_id]
    save_users()
    
    # Delete from DB
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM users WHERE email=?", (user_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error deleting user from DB: {e}")
    
    # Optionally: Delete all contributions by this user?
    # For now, let's keep contributions but maybe mark them?
    # Or just delete them to be clean. Let's delete them.
    global contributions_data
    contributions_data = [c for c in contributions_data if c['userId'] != user_id]
    save_contributions()
    
    return {"success": True}



@app.get("/api/admin/devices")
async def admin_list_devices(request: Request):
    if not await check_admin(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Reload from DB to ensure latest data
    load_users()

    def _safe_float(v, default=0.0):
        try:
            if v is None:
                return float(default)
            return float(v)
        except Exception:
            return float(default)
    
    devices = []
    processed_uids = set()
    
    for user in users_data:
        uid = user['id']
        processed_uids.add(uid)
        # 默认离线
        status = "offline"
        last_active = 0
        lat = None
        lng = None
        battery = None
        angle = 0
        
        # 检查实时状态
        if uid in manager.device_states:
            state = manager.device_states[uid]
            status = state.get("status", "offline")
            last_active = _safe_float(state.get("last_active", 0), 0.0)
            lat = state.get("lat")
            lng = state.get("lng")
            battery = state.get("battery")
            angle = state.get("angle", 0)
            
            # 如果超过 60 秒没有活动，视为离线 (Socket.IO 可能还没断开)
            if (time.time() - last_active) > 60 and status == "online":
                status = "offline" # 或者 "inactive"

            recent_frame_ts = state.get("last_camera_frame_at", state.get("last_video_frame_at", 0))
            video_state = state.get("camera_state") or ("streaming" if recent_frame_ts else "unknown")
            audio_state = state.get("audio_output_state") or "idle"
            last_heartbeat_sent_at = float(state.get("last_heartbeat_sent_at", 0) or 0)

            device = {
                "id": uid,
                "name": user.get('nickname', uid),
                "avatar": user.get('avatar'),
                "status": status,
                "lastActive": last_active,
                "lastHeartbeatSentAt": last_heartbeat_sent_at,
                "videoState": video_state,
                "audioState": audio_state,
                "lat": lat,
                "lng": lng,
                "battery": battery,
                "angle": angle,
                "speed": _safe_float(state.get("speed", 0.0), 0.0),
                "sats": int(_safe_float(state.get("sats", 0), 0)),
                "hdop": _safe_float(state.get("hdop", 99.9), 99.9),
                "alt": _safe_float(state.get("alt", 0.0), 0.0),
                "guide_flow": _build_guide_flow_snapshot(uid, state),
                "route_payload": state.get("route_payload"),
                "type": "blind_client" # 默认为盲人端
            }
            devices.append(device)
        
    # Check for other connected devices not in DB
    for client_id, state in manager.device_states.items():
        if client_id not in processed_uids:
            # Check if active recently
            status = state.get("status", "offline")
            last_active = _safe_float(state.get("last_active", 0), 0.0)
            
            if (time.time() - last_active) > 60 and status == "online":
                status = "offline"
            
            # Only show if recently active or online
            if status == "online" or (time.time() - last_active < 3600):
                recent_frame_ts = state.get("last_camera_frame_at", state.get("last_video_frame_at", 0))
                video_state = state.get("camera_state") or ("streaming" if recent_frame_ts else "unknown")
                audio_state = state.get("audio_output_state") or "idle"
                device = {
                    "id": client_id,
                    "name": f"Unknown Device ({client_id[:8]})",
                    "avatar": None,
                    "status": status,
                    "lastActive": last_active,
                    "lastHeartbeatSentAt": float(state.get("last_heartbeat_sent_at", 0) or 0),
                    "videoState": video_state,
                    "audioState": audio_state,
                    "lat": state.get("lat"),
                    "lng": state.get("lng"),
                    "battery": state.get("battery"),
                    "angle": state.get("angle", 0),
                    "speed": _safe_float(state.get("speed", 0.0), 0.0),
                    "sats": int(_safe_float(state.get("sats", 0), 0)),
                    "hdop": _safe_float(state.get("hdop", 99.9), 99.9),
                    "alt": _safe_float(state.get("alt", 0.0), 0.0),
                    "guide_flow": _build_guide_flow_snapshot(client_id, state),
                    "route_payload": state.get("route_payload"),
                    "type": "blind_client"
                }
                devices.append(device)
    
    # Sort devices: Online first, then by Last Active desc
    devices.sort(key=lambda d: (0 if d.get("status") == "online" else 1, -_safe_float(d.get("lastActive", 0), 0.0)))
        
    return {"success": True, "devices": devices}

# --- Volunteer Authentication System ---

def init_auth_db():
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Ensure password_hash column exists
            try:
                c.execute("SELECT password_hash FROM users LIMIT 1")
            except:
                print("Adding 'password_hash' column to users table")
                c.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Auth DB Init Error: {e}")

init_auth_db()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(stored_hash, password):
    return stored_hash == hashlib.sha256(password.encode()).hexdigest()

# Verification Code Store: email -> {code, timestamp}
VERIFY_CODES = {}

def send_email(to_email, subject, body):
    # Mock Mode
    if not config_secrets.SMTP_USER or "your_qq_email" in config_secrets.SMTP_USER:
        print(f"MOCK EMAIL TO {to_email}: {body}")
        # Return True so app shows success, user can see code in server logs
        return True
        
    try:
        msg = MIMEMultipart()
        msg['From'] = config_secrets.SMTP_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP_SSL(config_secrets.SMTP_SERVER, config_secrets.SMTP_PORT)
        server.login(config_secrets.SMTP_USER, config_secrets.SMTP_PASS)
        server.sendmail(config_secrets.SMTP_USER, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"SMTP Error: {e}")
        return False

class AuthRequest(BaseModel):
    email: str
    password: Optional[str] = None
    code: Optional[str] = None
    nickname: Optional[str] = None

@app.post("/api/auth/register")
async def auth_register(req: AuthRequest):
    if not req.email or not req.password:
        return {"success": False, "message": "Email and password required"}
    
    # Check if exists
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT email FROM users WHERE email=?", (req.email,))
    if c.fetchone():
        conn.close()
        return {"success": False, "message": "User already exists"}
    
    # Create
    p_hash = hash_password(req.password)
    nickname = req.nickname or req.email.split("@")[0]
    
    c.execute("INSERT INTO users (email, nickname, password_hash, status, trust_score, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (req.email, nickname, p_hash, "active", 100, time.time()))
    conn.commit()
    conn.close()
    
    # Update memory cache
    load_users()
    
    return {"success": True, "message": "Registered successfully"}

@app.post("/api/auth/login")
async def auth_login(req: AuthRequest):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT password_hash, nickname, status FROM users WHERE email=?", (req.email,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return {"success": False, "message": "User not found"}
        
    p_hash, nickname, status = row
    
    if not p_hash:
        return {"success": False, "message": "Account exists but no password set (OAuth?)"}
        
    if verify_password(p_hash, req.password):
        if status == "banned":
             return {"success": False, "message": "Account is banned"}
        return {"success": True, "user": {"email": req.email, "nickname": nickname}}
    else:
        return {"success": False, "message": "Invalid password"}

@app.post("/api/auth/send-code")
async def auth_send_code(req: AuthRequest):
    code = str(random.randint(100000, 999999))
    VERIFY_CODES[req.email] = {"code": code, "time": time.time()}
    
    # Send email
    subject = "OmniVision Verification Code"
    body = f"Your verification code is: {code}\nValid for 5 minutes."
    
    if send_email(req.email, subject, body):
        return {"success": True, "message": "Code sent (Check logs if no email)"}
    else:
        return {"success": False, "message": "Failed to send email"}

@app.post("/api/auth/reset-password")
async def auth_reset_password(req: AuthRequest):
    if not req.code or not req.password:
        return {"success": False, "message": "Code and new password required"}
        
    cached = VERIFY_CODES.get(req.email)
    if not cached:
        return {"success": False, "message": "No code requested"}
        
    if time.time() - cached["time"] > 300:
        return {"success": False, "message": "Code expired"}
        
    if cached["code"] != req.code:
        return {"success": False, "message": "Invalid code"}
        
    # Reset
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    p_hash = hash_password(req.password)
    
    # Check if user exists
    c.execute("SELECT email FROM users WHERE email=?", (req.email,))
    if c.fetchone():
        c.execute("UPDATE users SET password_hash=? WHERE email=?", (p_hash, req.email))
    else:
        conn.close()
        return {"success": False, "message": "User does not exist"}
        
    conn.commit()
    conn.close()
    
    del VERIFY_CODES[req.email]
    return {"success": True, "message": "Password reset successfully"}

if __name__ == "__main__":
    import uvicorn
    # 启动服务器
    print("启动 WiFi 视频流模拟系统...")
    port = int(os.environ.get("PORT", 8000))
    print(f"访问地址: http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
