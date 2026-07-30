"""BME_3DFarm Flask 入口。仿 BME_platform_flask/app.py。

启动：python app.py（默认监听 0.0.0.0:5002）
"""
from flask import Flask, jsonify

import config
from exts import db, mail, limiter, redis_client
from services.storage import storage
from flask_migrate import Migrate
from flask_cors import CORS
from flask_jwt_extended import JWTManager

from blueprints import (
    auth_bp,
    orders_bp,
    credit_bp,
    admin_bp,
    webhook_bp,
    internal_bp,
    printers_bp,
    notifications_bp,
)

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.config.from_object(config)

# 扩展初始化
db.init_app(app)
mail.init_app(app)
limiter.init_app(app)
redis_client.init_app(app)
storage.init_app(app)
migrate = Migrate(app, db)
jwt = JWTManager(app)

# 蓝图注册
app.register_blueprint(auth_bp)
app.register_blueprint(orders_bp)
app.register_blueprint(credit_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(webhook_bp)
app.register_blueprint(internal_bp)
app.register_blueprint(printers_bp)
app.register_blueprint(notifications_bp)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "bme-3dfarm"})


@app.route("/")
def index():
    return jsonify({"service": "bme-3dfarm", "docs": "/health"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002)
