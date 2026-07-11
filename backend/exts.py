"""BME_3DFarm 扩展单例。仿 BME_platform_flask/exts.py。"""
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_redis import FlaskRedis

db = SQLAlchemy()
mail = Mail()
redis_client = FlaskRedis()

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="redis://localhost:6379/0",
    storage_options={"socket_connect_timeout": 30},
    strategy="fixed-window",
)
