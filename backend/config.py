"""BME_3DFarm 配置。

从 .env 读取，仿 BME_platform_flask/config.py 模式。
所有大写变量会被 Flask 当作 app.config。
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

# ── MySQL ──
HOSTNAME = os.getenv("DB_HOST", "127.0.0.1")
PORT = os.getenv("DB_PORT", "3306")
DATABASE = os.getenv("DB_NAME", "bme_3dfarm")
USERNAME = os.getenv("DB_USERNAME", "root")
PASSWORD = os.getenv("DB_PASSWORD", "")
SQLALCHEMY_DATABASE_URI = (
    f"mysql+pymysql://{USERNAME}:{PASSWORD}@{HOSTNAME}:{PORT}/{DATABASE}?charset=utf8mb4"
)
SQLALCHEMY_TRACK_MODIFICATIONS = False

# ── Redis（Celery broker + 限流 + 缓存）──
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── JWT ──
JWT_SECRET_KEY = os.getenv("JWT_SECRET", "change-me-in-prod")
JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)

# ── BME 后端（同机 flask_Zero :5000；SSO 首次建号时 best-effort 回填 username）──
BME_BASE_URL = os.getenv("BME_BASE_URL", "http://127.0.0.1:5000")

# ── 邮箱（订单状态通知）──
MAIL_SERVER = os.getenv("EMAIL_SERVER")
MAIL_USE_SSL = True
MAIL_PORT = int(os.getenv("EMAIL_PORT", "465"))
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_DEFAULT_SENDER = os.getenv("MAIL_USERNAME")

# ── Bambuddy（打印机底座，仅内网）──
BAMBUDDY_BASE_URL = os.getenv("BAMBUDDY_BASE_URL", "http://127.0.0.1:8000/api/v1")
BAMBUDDY_API_KEY = os.getenv("BAMBUDDY_API_KEY", "")

# ── 内部 API（给训练营预留的 credit 发放接口鉴权）──
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "change-me")

# ── Webhook（Bambuddy 事件回调鉴权，Phase 2）──
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "dev-webhook-secret")

# ── MinIO（3D 模型文件存储）──
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "bme-3dfarm-models")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# ── Celery ──
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
