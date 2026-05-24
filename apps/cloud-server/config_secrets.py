import os

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
AMAP_KEY = os.getenv("AMAP_KEY", "")
