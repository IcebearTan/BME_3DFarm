"""BME_3DFarm 蓝图工具 + 注册。

仿 BME_platform_flask/blueprints/__init__.py，但 MVP 角色两级（admin/customer），
RBAC 简化为 require_admin（无细粒度 ACL 表）。未来需要模块级权限时，
可参考 BME 的 Permission + UserPermission 表 + check_permission 装饰器扩展。
"""
from datetime import datetime
from functools import wraps

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity

from exts import db
from models import UserModel, AuditLog


def _current_user():
    """从 JWT 取当前用户，无则返回 None。"""
    email = get_jwt_identity()
    if not email:
        return None
    return UserModel.query.filter_by(email=email).first()


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
