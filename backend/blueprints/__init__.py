"""BME_3DFarm 蓝图工具 + 注册。

仿 BME_platform_flask/blueprints/__init__.py，但 MVP 角色两级（admin/customer），
RBAC 简化为 require_admin（无细粒度 ACL 表）。未来需要模块级权限时，
可参考 BME 的 Permission + UserPermission 表 + check_permission 装饰器扩展。
"""
import json
import secrets
import urllib.request
from datetime import datetime
from functools import wraps

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity
from sqlalchemy.exc import IntegrityError

from exts import db
from models import UserModel, AuditLog
from services import CreditService


def _current_user():
    """从 JWT 取当前用户；本地无该 email 则自动建 customer（方案 A：无条件接纳 BME 账号）。

    BME 是唯一 IdP：token 只由 BME login 签发（identity=email），3dfarm 只验不签。
    本地无该 email 时自动建号，并与 register 对齐地建空 credit 账户、best-effort 回填
    username。admin 由库内 role 提升，不在此自动给——fresh customer 撞 admin 端点会被
    require_admin 挡 403。
    """
    email = get_jwt_identity()
    if not email:
        return None
    user = UserModel.query.filter_by(email=email).first()
    if user is not None:
        return user

    # 首次接入：自动建 customer。username/password 均 NOT NULL——username 先用 email
    # 本地部分占位（随后 best-effort 回填真实名），密码随机不可登录（SSO 用户只能经 BME 登）。
    user = UserModel(
        email=email,
        username=email.split("@", 1)[0] or email,
        role="customer",
        sso_subject="bme",
    )
    user.set_password(secrets.token_urlsafe(32))
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        # 并发同 email 首次接入兜底（仿 CreditService.ensure_account）：回滚后重查命中即返回
        db.session.rollback()
        return UserModel.query.filter_by(email=email).first()

    # 建号即建空 credit 账户（与 register 对齐：订单操作前账户必然存在）
    CreditService.ensure_account(user.id)
    _enrich_username_from_bme(user)
    return user


def _enrich_username_from_bme(user):
    """best-effort：拿当前请求的 Bearer token 回 BME /user/user_index 取真实用户名回填。

    仅在首次建号时调用一次。超时/失败/非 200 一律静默，保留占位 username，不影响主流程。
    """
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.split("Bearer ", 1)[1].strip() if auth_header.startswith("Bearer ") else ""
    if not token:
        return
    url = current_app.config["BME_BASE_URL"].rstrip("/") + "/user/user_index"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status != 200:
                return
            data = json.loads(resp.read().decode("utf-8", "replace"))
        user_name = (data.get("User_Name") or "").strip()
        if user_name and user_name != user.username:
            user.username = user_name
            db.session.commit()
    except Exception:
        # 回填是锦上添花，任何异常都不影响主流程
        db.session.rollback()


def require_admin(func):
    """要求当前用户是 admin（打印农场后台操作员）。"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if not user:
            return jsonify({"code": 401, "message": "用户未认证"}), 401
        if not user.is_admin_like():
            return jsonify({"code": 403, "message": "需要管理员权限"}), 403
        return func(*args, **kwargs)
    return wrapper


def audit_log(operation=None):
    """安全审计装饰器（简化版）：记录用户操作到 audit_log 表。

    业务异常不影响审计记录；审计写入失败不影响主流程。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = datetime.utcnow()
            result = "失败"
            try:
                response = func(*args, **kwargs)
                status_code = response[1] if isinstance(response, tuple) and len(response) == 2 else 200
                result = "成功" if status_code < 400 else "失败"
                return response
            except Exception:
                result = "失败"
                raise
            finally:
                try:
                    user = _current_user()
                    if user:
                        real_ip = (
                            request.headers.get("X-Real-IP")
                            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                        )
                        db.session.add(AuditLog(
                            user_id=user.id,
                            username=user.username,
                            ip_address=real_ip or request.remote_addr,
                            user_agent=request.headers.get("User-Agent", ""),
                            operation=operation or func.__name__,
                            operation_url=request.url,
                            result=result,
                            timestamp=start_time,
                        ))
                        db.session.commit()
                except Exception:
                    db.session.rollback()

        return wrapper

    return decorator


# 蓝图导入（确保所有 bp 被注册）
from .auth import bp as auth_bp
from .orders import bp as orders_bp
from .credit import bp as credit_bp
from .admin import bp as admin_bp
from .webhook_bambuddy import bp as webhook_bp
from .internal import bp as internal_bp
from .printers import bp as printers_bp

__all__ = ["auth_bp", "orders_bp", "credit_bp", "admin_bp", "webhook_bp", "internal_bp", "printers_bp"]
