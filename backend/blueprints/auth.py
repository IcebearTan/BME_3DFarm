"""认证：注册/登录/当前用户。仿 BME_platform_flask/blueprints/auth.py。

简化点（内网场景）：
  - 不接邮箱验证码（BME 用 Redis 存 captcha，这里先省，如需要可补）
  - 不用 WTForms，直接 request.get_json 校验
  - 注册默认角色 customer，admin 由现有管理员/数据库提升
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required

from exts import db
from models import UserModel
from . import _current_user, audit_log

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/register", methods=["POST"])
@audit_log(operation="用户注册")
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password")
    username = (data.get("username") or "").strip()
    if not email or not password or not username:
        return jsonify({"code": 400, "message": "email/password/username 必填"}), 400

    if UserModel.query.filter_by(email=email).first():
        return jsonify({"code": 409, "message": "邮箱已存在"}), 409

    user = UserModel(email=email, username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    token = create_access_token(identity=email)
    return jsonify({
        "code": 200, "message": "注册成功",
        "token": token, "role": user.role, "role_rank": user.role_rank,
    }), 200


@bp.route("/login", methods=["POST"])
@audit_log(operation="用户登录")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password")
    if not email or not password:
        return jsonify({"code": 400, "message": "email/password 必填"}), 400

    user = UserModel.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"code": 402, "message": "邮箱或密码错误"}), 402

    # 历史明文密码登录成功后自动升级为加盐哈希
    if not user.password_is_hashed:
        user.set_password(password)
        db.session.commit()

    token = create_access_token(identity=email)
    return jsonify({
        "code": 200, "message": "登录成功",
        "token": token,
        "role": user.role, "role_rank": user.role_rank,
        "User_Name": user.username, "User_Email": user.email,
    }), 200


@bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    return jsonify({"code": 200, "data": {
        "id": user.id, "email": user.email, "username": user.username,
        "role": user.role, "role_rank": user.role_rank,
    }})
