import socketio
from aiohttp import web
import time
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
import hashlib
import random
import string
import json
import asyncio
import os
import shutil
from concurrent.futures import ThreadPoolExecutor

# --- 配置信息 ---
import config_secrets

SMTP_SERVER = config_secrets.SMTP_SERVER
SMTP_PORT = config_secrets.SMTP_PORT
SENDER_EMAIL = config_secrets.SMTP_USER
SENDER_PASSWORD = config_secrets.SMTP_PASS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "users.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

# 线程池用于执行阻塞操作 (DB, Email)
executor = ThreadPoolExecutor(max_workers=5)

# --- 数据库初始化 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 用户表
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  email TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  created_at REAL)''')
    # 尝试添加新字段 (如果不存在)
    try:
        c.execute("ALTER TABLE users ADD COLUMN nickname TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
    except sqlite3.OperationalError:
        pass
    # 验证码表 (尝试添加 timestamp 字段如果缺失)
    c.execute('''CREATE TABLE IF NOT EXISTS verification_codes
                 (email TEXT PRIMARY KEY,
                  code TEXT NOT NULL,
                  timestamp REAL NOT NULL)''')
    try:
        c.execute("ALTER TABLE verification_codes ADD COLUMN timestamp REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    
    # 帖子表
    c.execute('''CREATE TABLE IF NOT EXISTS posts
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_email TEXT NOT NULL,
                  content TEXT NOT NULL,
                  image_path TEXT,
                  correct_count INTEGER DEFAULT 0,
                  incorrect_count INTEGER DEFAULT 0,
                  created_at REAL,
                  latitude REAL,
                  longitude REAL)''')
    
    # 投票表
    c.execute('''CREATE TABLE IF NOT EXISTS votes
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  post_id INTEGER NOT NULL,
                  user_email TEXT NOT NULL,
                  vote_type TEXT NOT NULL,
                  timestamp REAL)''')

    # 评论表
    c.execute('''CREATE TABLE IF NOT EXISTS comments
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  post_id INTEGER NOT NULL,
                  user_email TEXT NOT NULL,
                  content TEXT NOT NULL,
                  created_at REAL)''')

    conn.commit()
    conn.close()

init_db()

# --- 辅助函数 ---
async def run_blocking(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, func, *args)

def _send_email_sync(to_email, code):
    try:
        msg = MIMEText(f"您的验证码是：{code}，有效期5分钟。", 'plain', 'utf-8')
        msg['From'] = formataddr(["OmniVision", SENDER_EMAIL])
        msg['To'] = formataddr(["User", to_email])
        msg['Subject'] = "OmniVision 验证码"

        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, [to_email], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def _save_code_sync(email, code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO verification_codes (email, code, timestamp) VALUES (?, ?, ?)",
              (email, code, time.time()))
    conn.commit()
    conn.close()

def _verify_code_sync(email, code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT code, timestamp FROM verification_codes WHERE email=?", (email,))
    row = c.fetchone()
    conn.close()
    
    if row:
        db_code, timestamp = row
        if db_code == code and time.time() - timestamp < 300: # 5分钟有效期
            return True
    return False

def _register_user_sync(email, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    try:
        # Default nickname and avatar
        nickname = email.split('@')[0]
        avatar_path = None
        c.execute("INSERT INTO users (email, password, nickname, avatar_path, created_at) VALUES (?, ?, ?, ?, ?)",
                  (email, pwd_hash, nickname, avatar_path, time.time()))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def _login_user_sync(email, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    c.execute("SELECT id FROM users WHERE email=? AND password=?", (email, pwd_hash))
    row = c.fetchone()
    conn.close()
    return row is not None

def _reset_password_sync(email, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    c.execute("UPDATE users SET password=? WHERE email=?", (pwd_hash, email))
    updated = c.rowcount > 0
    conn.commit()
    conn.close()
    return updated

def _create_post_sync(user_email, content, image_path, latitude=None, longitude=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO posts (user_email, content, image_path, created_at, latitude, longitude) VALUES (?, ?, ?, ?, ?, ?)",
              (user_email, content, image_path, time.time(), latitude, longitude))
    conn.commit()
    conn.close()
    return True

def _get_posts_sync():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    query = '''
        SELECT p.id, p.user_email, p.content, p.image_path, p.correct_count, p.incorrect_count, p.created_at,
               u.nickname, u.avatar_path, p.latitude, p.longitude
        FROM posts p
        LEFT JOIN users u ON p.user_email = u.email
        ORDER BY p.created_at DESC
    '''
    try:
        c.execute(query)
    except sqlite3.OperationalError:
         c.execute("SELECT id, user_email, content, image_path, correct_count, incorrect_count, created_at, NULL, NULL, latitude, longitude FROM posts ORDER BY created_at DESC")

    rows = c.fetchall()

    posts = []
    for r in rows:
        post_id = r[0]
        nickname = r[7] if len(r) > 7 else r[1].split('@')[0]
        avatar_path = r[8] if len(r) > 8 else None
        latitude = r[9] if len(r) > 9 else None
        longitude = r[10] if len(r) > 10 else None

        # 获取评论数
        c.execute("SELECT COUNT(*) FROM comments WHERE post_id=?", (post_id,))
        comment_count = c.fetchone()[0]
        
        posts.append({
            'id': post_id,
            'email': r[1],
            'content': r[2],
            'image': r[3],
            'correct_count': r[4] or 0,
            'incorrect_count': r[5] or 0,
            'comment_count': comment_count,
            'time': r[6],
            'nickname': nickname,
            'avatar_path': avatar_path,
            'latitude': latitude,
            'longitude': longitude
        })
    conn.close()
    return posts

def _vote_post_sync(post_id, user_email, vote_type):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        # 检查是否已投过票
        c.execute("SELECT vote_type FROM votes WHERE post_id=? AND user_email=?", (post_id, user_email))
        existing_vote = c.fetchone()

        if existing_vote:
            if existing_vote[0] == vote_type:
                return False, "您已经投过票了"
            else:
                # 更改投票 (例如从 correct 改为 incorrect)
                c.execute("UPDATE votes SET vote_type=? WHERE post_id=? AND user_email=?", (vote_type, post_id, user_email))
                if vote_type == 'correct':
                    c.execute("UPDATE posts SET correct_count=correct_count+1, incorrect_count=incorrect_count-1 WHERE id=?", (post_id,))
                else:
                    c.execute("UPDATE posts SET incorrect_count=incorrect_count+1, correct_count=correct_count-1 WHERE id=?", (post_id,))
        else:
            # 新投票
            c.execute("INSERT INTO votes (post_id, user_email, vote_type) VALUES (?, ?, ?)", (post_id, user_email, vote_type))
            if vote_type == 'correct':
                c.execute("UPDATE posts SET correct_count=correct_count+1 WHERE id=?", (post_id,))
            else:
                c.execute("UPDATE posts SET incorrect_count=incorrect_count+1 WHERE id=?", (post_id,))
        
        conn.commit()
        return True, "投票成功"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def _add_comment_sync(post_id, user_email, content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO comments (post_id, user_email, content, created_at) VALUES (?, ?, ?, ?)",
              (post_id, user_email, content, time.time()))
    conn.commit()
    conn.close()

def _get_comments_sync(post_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    query = '''
        SELECT c.id, c.user_email, c.content, c.created_at, u.nickname, u.avatar_path
        FROM comments c
        LEFT JOIN users u ON c.user_email = u.email
        WHERE c.post_id = ?
        ORDER BY c.created_at ASC
    '''
    try:
        c.execute(query, (post_id,))
    except sqlite3.OperationalError:
        c.execute("SELECT id, user_email, content, created_at FROM comments WHERE post_id=? ORDER BY created_at ASC", (post_id,))

    rows = c.fetchall()
    conn.close()

    results = []
    for r in rows:
        nickname = r[4] if len(r) > 4 else None
        avatar_path = r[5] if len(r) > 5 else None
        results.append({
            'id': r[0], 'email': r[1], 'content': r[2], 'time': r[3],
            'nickname': nickname, 'avatar_path': avatar_path
        })
    return results

def _delete_comment_sync(comment_id, user_email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_email FROM comments WHERE id=?", (comment_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "评论不存在"
    if row[0] != user_email:
        conn.close()
        return False, "无权删除"

    c.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    conn.commit()
    conn.close()
    return True, "删除成功"

def _get_user_comments_sync(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = '''
        SELECT c.id, c.content, c.created_at, p.content, p.image_path, c.post_id
        FROM comments c
        LEFT JOIN posts p ON c.post_id = p.id
        WHERE c.user_email = ?
        ORDER BY c.created_at DESC
    '''
    c.execute(query, (email,))
    rows = c.fetchall()
    conn.close()

    comments = []
    for r in rows:
        comments.append({
            'id': r[0],
            'content': r[1],
            'time': r[2],
            'post_content': r[3],
            'post_image': r[4],
            'post_id': r[5]
        })
    return comments

def _get_user_posts_sync(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = '''
        SELECT p.id, p.content, p.image_path, p.correct_count, p.incorrect_count, p.created_at,
               (SELECT COUNT(*) FROM comments WHERE post_id=p.id) as comment_count,
               u.nickname, u.avatar_path, p.latitude, p.longitude
        FROM posts p
        LEFT JOIN users u ON p.user_email = u.email
        WHERE p.user_email = ?
        ORDER BY p.created_at DESC
    '''
    c.execute(query, (email,))
    rows = c.fetchall()

    posts = []
    for r in rows:
        posts.append({
            'id': r[0],
            'email': email,
            'content': r[1],
            'image': r[2],
            'correct_count': r[3] or 0,
            'incorrect_count': r[4] or 0,
            'comment_count': r[6],
            'time': r[5],
            'nickname': r[7],
            'avatar_path': r[8],
            'latitude': r[9],
            'longitude': r[10]
        })
    conn.close()
    return posts

def _delete_post_sync(post_id, user_email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_email FROM posts WHERE id=?", (post_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "帖子不存在"
    if row[0] != user_email:
        conn.close()
        return False, "无权删除"

    c.execute("DELETE FROM posts WHERE id=?", (post_id,))
    c.execute("DELETE FROM comments WHERE post_id=?", (post_id,))
    c.execute("DELETE FROM votes WHERE post_id=?", (post_id,))
    conn.commit()
    conn.close()
    return True, "删除成功"

def _update_user_profile_sync(email, nickname, avatar_path):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Check if user exists
    c.execute("SELECT id FROM users WHERE email=?", (email,))
    if c.fetchone():
        if nickname:
            c.execute("UPDATE users SET nickname=? WHERE email=?", (nickname, email))
        if avatar_path:
            c.execute("UPDATE users SET avatar_path=? WHERE email=?", (avatar_path, email))
    else:
        # Insert if missing (restore account)
        dummy_hash = "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92"
        c.execute("INSERT INTO users (email, password, nickname, avatar_path, created_at) VALUES (?, ?, ?, ?, ?)",
                  (email, dummy_hash, nickname, avatar_path, time.time()))

    conn.commit()
    conn.close()

def _get_user_profile_sync(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT nickname, avatar_path FROM users WHERE email=?", (email,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'nickname': row[0], 'avatar_path': row[1]}
    return {}

# --- API Handlers ---

async def handle_send_code(request):
    try:
        data = await request.json()
        email = data.get('email')
        if not email:
            return web.json_response({'success': False, 'message': '邮箱不能为空'})

        # 生成验证码
        code = ''.join(random.choices(string.digits, k=6))

        # 保存并发送
        await run_blocking(_save_code_sync, email, code)
        success = await run_blocking(_send_email_sync, email, code)

        if success:
            return web.json_response({'success': True, 'message': '验证码已发送'})
        else:
            return web.json_response({'success': False, 'message': '邮件发送失败，请检查邮箱是否正确'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_register(request):
    try:
        data = await request.json()
        email = data.get('email')
        password = data.get('password')
        code = data.get('code')

        if not (email and password and code):
            return web.json_response({'success': False, 'message': '参数不完整'})

        # 验证码校验
        valid_code = await run_blocking(_verify_code_sync, email, code)
        if not valid_code:
            return web.json_response({'success': False, 'message': '验证码错误或已过期'})

        # 注册
        success = await run_blocking(_register_user_sync, email, password)
        if success:
            return web.json_response({'success': True, 'message': '注册成功'})
        else:
            return web.json_response({'success': False, 'message': '该邮箱已被注册'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_login(request):
    try:
        data = await request.json()
        email = data.get('email')
        password = data.get('password')

        if not (email and password):
            return web.json_response({'success': False, 'message': '参数不完整'})

        success = await run_blocking(_login_user_sync, email, password)
        if success:
            return web.json_response({'success': True, 'username': email})
        else:
            return web.json_response({'success': False, 'message': '邮箱或密码错误'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_reset_password(request):
    try:
        data = await request.json()
        email = data.get('email')
        password = data.get('password') # 新密码
        code = data.get('code')

        if not (email and password and code):
            return web.json_response({'success': False, 'message': '参数不完整'})

        valid_code = await run_blocking(_verify_code_sync, email, code)
        if not valid_code:
            return web.json_response({'success': False, 'message': '验证码错误或已过期'})

        success = await run_blocking(_reset_password_sync, email, password)
        if success:
            return web.json_response({'success': True, 'message': '密码重置成功'})
        else:
            return web.json_response({'success': False, 'message': '用户不存在'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_create_post(request):
    try:
        reader = await request.multipart()
        field = await reader.next()

        email = None
        content = None
        image_path = None
        latitude = None
        longitude = None

        while field:
            if field.name == 'email':
                email = await field.read(decode=True)
                email = email.decode('utf-8')
            elif field.name == 'content':
                content = await field.read(decode=True)
                content = content.decode('utf-8')
            elif field.name == 'latitude':
                lat_str = await field.read(decode=True)
                if lat_str:
                    try:
                        latitude = float(lat_str.decode('utf-8'))
                    except:
                        pass
            elif field.name == 'longitude':
                lng_str = await field.read(decode=True)
                if lng_str:
                    try:
                        longitude = float(lng_str.decode('utf-8'))
                    except:
                        pass
            elif field.name == 'image':
                filename = field.filename
                if filename:
                    # Create community specific dir
                    community_dir = os.path.join(UPLOAD_DIR, "community")
                    if not os.path.exists(community_dir):
                        os.makedirs(community_dir)

                    # Generate unique filename
                    ext = filename.split('.')[-1] if '.' in filename else 'jpg' 
                    new_name = f"{int(time.time())}_{''.join(random.choices(string.ascii_letters, k=4))}.{ext}"

                    # Absolute path for saving
                    save_path = os.path.join(community_dir, new_name)

                    with open(save_path, 'wb') as f:
                        while True:
                            chunk = await field.read_chunk()
                            if not chunk: break
                            f.write(chunk)

                    # Relative path for DB (URL access)
                    image_path = f"uploads/community/{new_name}"

            field = await reader.next()

        if not email:
             return web.json_response({'success': False, 'message': '未登录'})

        await run_blocking(_create_post_sync, email, content, image_path, latitude, longitude)
        return web.json_response({'success': True, 'message': '发布成功'})

    except Exception as e:
        print(f"Post error: {e}")
        return web.json_response({'success': False, 'message': str(e)})

async def handle_get_posts(request):
    try:
        posts = await run_blocking(_get_posts_sync)
        return web.json_response({'success': True, 'posts': posts})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_vote_post(request):
    try:
        data = await request.json()
        post_id = data.get('post_id')
        email = data.get('email')
        vote_type = data.get('vote_type')

        if not (post_id and email and vote_type in ['correct', 'incorrect']):
            return web.json_response({'success': False, 'message': '参数错误'})

        success, msg = await run_blocking(_vote_post_sync, post_id, email, vote_type)
        return web.json_response({'success': success, 'message': msg})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_add_comment(request):
    try:
        data = await request.json()
        post_id = data.get('post_id')
        email = data.get('email')
        content = data.get('content')

        if not (post_id and email and content):
            return web.json_response({'success': False, 'message': '参数错误'})

        await run_blocking(_add_comment_sync, post_id, email, content)
        return web.json_response({'success': True, 'message': '评论成功'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_get_comments(request):
    try:
        post_id = request.query.get('post_id')
        if not post_id:
             return web.json_response({'success': False, 'message': '缺少 post_id'})

        comments = await run_blocking(_get_comments_sync, post_id)
        return web.json_response({'success': True, 'comments': comments})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_delete_comment(request):
    try:
        data = await request.json()
        comment_id = data.get('comment_id')
        email = data.get('email')

        if not (comment_id and email):
             return web.json_response({'success': False, 'message': '参数不完整'})

        success, message = await run_blocking(_delete_comment_sync, comment_id, email)
        return web.json_response({'success': success, 'message': message})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_get_user_comments(request):
    try:
        email = request.query.get('email')
        if not email:
             return web.json_response({'success': False, 'message': '缺少 email'})

        comments = await run_blocking(_get_user_comments_sync, email)
        return web.json_response({'success': True, 'comments': comments})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_get_user_posts(request):
    try:
        email = request.query.get('email')
        if not email:
             return web.json_response({'success': False, 'message': '缺少 email'})

        posts = await run_blocking(_get_user_posts_sync, email)
        return web.json_response({'success': True, 'posts': posts})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_delete_post(request):
    try:
        data = await request.json()
        post_id = data.get('post_id')
        email = data.get('email')

        if not (post_id and email):
             return web.json_response({'success': False, 'message': '参数不完整'})

        success, message = await run_blocking(_delete_post_sync, post_id, email)
        return web.json_response({'success': success, 'message': message})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_update_profile(request):
    try:
        reader = await request.multipart()
        field = await reader.next()
        email = None
        nickname = None
        avatar_path = None

        while field:
            if field.name == 'email':
                email = await field.read(decode=True)
                email = email.decode('utf-8')
            elif field.name == 'nickname':
                nickname = await field.read(decode=True)
                nickname = nickname.decode('utf-8')
            elif field.name == 'avatar':
                filename = field.filename
                if filename and email:
                    # --- NEW LOGIC START ---
                    # Create user specific dir
                    user_dir = os.path.join(UPLOAD_DIR, "users", email)
                    if not os.path.exists(user_dir):
                        os.makedirs(user_dir)
                    
                    # Clean up old files
                    for f in os.listdir(user_dir):
                        try:
                            os.remove(os.path.join(user_dir, f))
                        except Exception as e:
                            print(f"Cleanup error: {e}")

                    ext = filename.split('.')[-1] if '.' in filename else 'jpg'
                    new_name = f"avatar_{int(time.time())}.{ext}"
                    save_path = os.path.join(user_dir, new_name)
                    
                    with open(save_path, 'wb') as f:
                        while True:
                            chunk = await field.read_chunk()
                            if not chunk: break
                            f.write(chunk)
                    
                    # Store relative path for DB
                    avatar_path = f"uploads/users/{email}/{new_name}"
                    # --- NEW LOGIC END ---
                    
            field = await reader.next()

        if not email:
            return web.json_response({'success': False, 'message': 'Email required'})

        await run_blocking(_update_user_profile_sync, email, nickname, avatar_path)
        return web.json_response({'success': True, 'avatar_path': avatar_path})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

async def handle_get_profile(request):
    try:
        email = request.query.get('email')
        if not email:
            return web.json_response({'success': False, 'message': 'Email required'})
        profile = await run_blocking(_get_user_profile_sync, email)
        return web.json_response({'success': True, 'profile': profile})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})

# 创建 Socket.IO 服务器
async def cors_middleware(app, handler):
    async def middleware(request):
        if request.method == 'OPTIONS':
            response = web.Response()
        else:
            try:
                response = await handler(request)
            except web.HTTPException as e:
                # Allow 404/etc to have CORS headers
                response = e
            except Exception as e:
                response = web.json_response({'success': False, 'message': str(e)}, status=500)

        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS, PUT, DELETE'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response
    return middleware

sio = socketio.AsyncServer(async_mode='aiohttp', cors_allowed_origins='*')
app = web.Application(middlewares=[cors_middleware])
sio.attach(app)

# 注册路由
app.router.add_post('/api/auth/send-code', handle_send_code)
app.router.add_post('/api/auth/register', handle_register)
app.router.add_post('/api/auth/login', handle_login)
app.router.add_post('/api/auth/reset-password', handle_reset_password)
app.router.add_post('/api/community/upload', handle_create_post)
app.router.add_get('/api/community/list', handle_get_posts)
app.router.add_post('/api/community/vote', handle_vote_post)
app.router.add_post('/api/community/comment', handle_add_comment)
app.router.add_post('/api/community/comment/delete', handle_delete_comment)
app.router.add_get('/api/community/comments', handle_get_comments)
app.router.add_get('/api/community/user/comments', handle_get_user_comments)
app.router.add_get('/api/community/user/posts', handle_get_user_posts)
app.router.add_post('/api/community/post/delete', handle_delete_post)
app.router.add_post('/api/user/profile/update', handle_update_profile)
app.router.add_get('/api/user/profile', handle_get_profile)
app.router.add_static('/uploads', UPLOAD_DIR) # 静态文件服务

# 存储等待中的呼叫请求 {caller_sid: {"timestamp": float, "current_batch": set(), "tried_volunteers": set(), "timer_task": Task}}
pending_calls = {}

# 存储志愿者状态 {sid: {"status": "idle"|"busy", "userId": str, "last_active": float}}
volunteers = {}

@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")
    # 清理呼叫
    if sid in pending_calls:
        # 如果是呼叫者断开，取消所有正在响铃的志愿者
        batch = pending_calls[sid].get("current_batch", set())
        for v_sid in batch:
            await sio.emit('cancel_call', {'caller_sid': sid}, to=v_sid)
            
        task = pending_calls[sid].get("timer_task")
        if task: task.cancel()
        del pending_calls[sid]
        print(f"Removed pending call from {sid}")

    # 清理志愿者
    if sid in volunteers:
        del volunteers[sid]
        print(f"Volunteer {sid} disconnected")

@sio.event
async def join(sid, data):
    room = data.get('room', 'stream_room')
    role = data.get('role', 'unknown')
    await sio.enter_room(sid, room)
    print(f"{sid} joined room {room} as {role}")

    if role == 'volunteer':
        volunteers[sid] = {"status": "idle", "userId": data.get('userId'), "last_active": time.time()}

CALL_BATCH_SIZE = 20
CALL_BATCH_TIMEOUT_SECONDS = 30


async def dispatch_call_batch(caller_sid):
    """批量分发呼叫请求"""
    if caller_sid not in pending_calls:
        return

    call_info = pending_calls[caller_sid]
    tried = call_info["tried_volunteers"]

    available_volunteers = [
        v_sid for v_sid, v_info in volunteers.items()
        if v_info["status"] == "idle" and v_sid not in tried
    ]

    if not available_volunteers:
        print(f"No more available volunteers for {caller_sid}")
        await sio.emit('no_volunteers', {}, to=caller_sid)
        if caller_sid in pending_calls:
            del pending_calls[caller_sid]
        return

    batch_size = min(CALL_BATCH_SIZE, len(available_volunteers))
    next_batch = available_volunteers[:batch_size]

    call_info["current_batch"] = set(next_batch)
    call_info["tried_volunteers"].update(next_batch)

    print(
        f"Dispatching call from {caller_sid} to batch(size={len(next_batch)}, timeout={CALL_BATCH_TIMEOUT_SECONDS}s): {next_batch}"
    )

    for v_sid in next_batch:
        await sio.emit('incoming_call', {'caller_sid': caller_sid}, to=v_sid)

    async def batch_timeout():
        await asyncio.sleep(CALL_BATCH_TIMEOUT_SECONDS)
        if caller_sid not in pending_calls:
            return

        current_batch = pending_calls[caller_sid]["current_batch"]
        if not current_batch:
            return

        print(f"Batch timeout for {caller_sid}, cancelling batch {current_batch}")
        for v_sid in list(current_batch):
            await sio.emit('cancel_call', {'caller_sid': caller_sid}, to=v_sid)

        pending_calls[caller_sid]["current_batch"] = set()
        await dispatch_call_batch(caller_sid)

    task = asyncio.create_task(batch_timeout())
    call_info["timer_task"] = task

@sio.event
async def call_request(sid, data):
    # 盲人端请求帮助
    print(f"Call request from {sid}")

    if sid in pending_calls:
        return # 已经在请求中

    pending_calls[sid] = {
        "timestamp": time.time(),
        "tried_volunteers": set(),
        "current_batch": set(),
        "timer_task": None
    }

    await dispatch_call_batch(sid)

@sio.event
async def cancel_request(sid, data):
    # 取消呼叫
    if sid in pending_calls:
        # 取消当前批次的呼叫
        batch = pending_calls[sid].get("current_batch", set())
        for v_sid in batch:
            await sio.emit('cancel_call', {'caller_sid': sid}, to=v_sid)
            
        task = pending_calls[sid].get("timer_task")
        if task: task.cancel()
        del pending_calls[sid]
        print(f"Cancelled call request from {sid}")

@sio.event
async def accept_call(sid, data): # 统一事件名为 accept_call，兼容旧的 accept_help
    # 志愿者接单 (抢单逻辑)
    caller_sid = data.get('caller_sid')
    print(f"Volunteer {sid} attempting to accept call from {caller_sid}")

    if caller_sid not in pending_calls:
        # 呼叫已不存在（已被接听或取消）
        await sio.emit('call_ended', {'reason': 'taken'}, to=sid)
        return

    call_info = pending_calls[caller_sid]
    
    # 验证该志愿者是否在当前允许的批次中
    # (虽然不在批次中也可以允许接听，但为了严谨性可以检查)
    
    # --- 抢单成功 ---
    # 1. 取消超时计时器
    task = call_info.get("timer_task")
    if task: task.cancel()
    
    # 2. 通知同批次其他志愿者取消呼叫
    batch = call_info.get("current_batch", set())
    for v_sid in batch:
        if v_sid != sid: # 不通知自己
            await sio.emit('call_taken', {'caller_sid': caller_sid, 'taker_sid': sid}, to=v_sid)
            # 或者复用 cancel_call
            await sio.emit('cancel_call', {'caller_sid': caller_sid}, to=v_sid)

    # 3. 清理呼叫状态
    del pending_calls[caller_sid]

    # 4. 更新志愿者状态
    if sid in volunteers:
        volunteers[sid]["status"] = "busy"

    # 5. 通知盲人端，志愿者已就绪
    print(f"Volunteer {sid} successfully took call from {caller_sid}")
    await sio.emit('volunteer_accepted', {'volunteer_sid': sid}, to=caller_sid)

# 兼容旧事件名
@sio.event
async def accept_help(sid, data):
    await accept_call(sid, data)



@sio.event
async def offer(sid, data):
    target = data.get('target')
    if target:
        print(f"Forwarding offer from {sid} to {target}")
        await sio.emit('offer', {
            'sdp': data['sdp'],
            'type': data['type'],
            'caller_sid': sid
        }, to=target)

@sio.event
async def answer(sid, data):
    target = data.get('target')
    if target:
        print(f"Forwarding answer from {sid} to {target}")
        await sio.emit('answer', {
            'sdp': data['sdp'],
            'type': data['type'],
            'responder_sid': sid
        }, to=target)

@sio.event
async def candidate(sid, data):
    target = data.get('target')
    if target:
        print(f"Forwarding candidate from {sid} to {target}")
        await sio.emit('candidate', {'candidate': data['candidate']}, to=target)

@sio.event
async def bye(sid, data):
    target = data.get('target')
    if target:
        print(f"Forwarding bye from {sid} to {target}")
        await sio.emit('bye', {}, to=target)

        # 1. 如果 target 是志愿者 (盲人挂断)，重置志愿者状态
        if target in volunteers:
            print(f"Volunteer {target} status reset to idle (by caller)")
            volunteers[target]["status"] = "idle"

    # 2. 如果发送者是志愿者 (志愿者挂断)，重置其状态
    if sid in volunteers:
        print(f"Volunteer {sid} status reset to idle (self)")
        volunteers[sid]["status"] = "idle"

if __name__ == '__main__':
    print("Starting Signaling Server on port 6000...")
    web.run_app(app, host='0.0.0.0', port=6000)
