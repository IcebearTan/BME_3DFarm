"""通知蓝图 — 站内通知中心 API（公告 + 订单提醒）。

照搬 BME_platform_flask/blueprints/notification.py（系统 A）：
  - 广播 = 写时扇出（每用户一行），无 broadcast 关联表
  - 已读 = is_read 布尔挂行
  - 业务点（admin 订单操作等）直接 import create_notification() 扇出

与 BME 的差异：3DFarm 复用本仓库更强的 _current_user（自动建用户）、
角色两级用 require_admin、不集成 flask_mail（邮件走 services/mailer.py 现状）。
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from exts import db
from models import UserModel, NotificationModel
from . import _current_user, require_admin

bp = Blueprint("notifications", __name__, url_prefix="/notifications")


def _to_dict(n):
    return {
        "id": n.id,
        "user_id": n.user_id,
        "title": n.title,
        "content": n.content,
        "category": n.category,
        "source_type": n.source_type,
        "source_id": n.source_id,
        "is_read": n.is_read,
        "created_at": n.created_at.strftime("%Y-%m-%d %H:%M:%S") if n.created_at else None,
    }


# ─────────── 业务点调用的扇出函数（只 add，由调用方 commit） ───────────
def create_notification(user_id, title, content=None, category=NotificationModel.CAT_ORDER,
                        source_type=None, source_id=None):
    """点对点通知（业务点调用，如订单失败/完成）。只 add 不 commit。"""
    n = NotificationModel(
        user_id=user_id, title=title, content=content, category=category,
        source_type=source_type, source_id=source_id,
    )
    db.session.add(n)
    return n


def batch_create_notifications(user_ids, title, content=None,
                               category=NotificationModel.CAT_SYSTEM,
                               source_type=NotificationModel.SRC_ANNOUNCEMENT):
    """扇出：给每个 user_id 插一行（公告用）。只 add 不 commit。"""
    return [create_notification(uid, title, content, category=category, source_type=source_type)
            for uid in user_ids]


# ─────────── 用户侧 ───────────
@bp.route("/list", methods=["GET"])
@jwt_required()
def list_notifications():
    """我的通知列表（带 unread_count）。query: category / is_read / page / per_page。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 401, "message": "用户未认证"}), 401
    category = request.args.get("category")
    is_read = request.args.get("is_read")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    query = NotificationModel.query.filter_by(user_id=user.id)
    unread_q = NotificationModel.query.filter_by(user_id=user.id, is_read=False)
    if category:
        query = query.filter_by(category=category)
        unread_q = unread_q.filter_by(category=category)
    if is_read is not None:
        query = query.filter_by(is_read=(is_read.lower() == "true"))

    pagination = query.order_by(NotificationModel.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    return jsonify({"code": 200, "data": {
        "items": [_to_dict(n) for n in pagination.items],
        "total": pagination.total,
        "unread_count": unread_q.count(),
        "page": page, "per_page": per_page,
    }})


@bp.route("/unread_count", methods=["GET"])
@jwt_required()
def unread_count():
    """未读数（轻量，供铃铛轮询）。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 401, "message": "用户未认证"}), 401
    count = NotificationModel.query.filter_by(user_id=user.id, is_read=False).count()
    return jsonify({"code": 200, "data": {"unread_count": count}})


@bp.route("/mark_read", methods=["POST"])
@jwt_required()
def mark_read():
    """单条已读。body {id}，校验 id+user_id 双匹配。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 401, "message": "用户未认证"}), 401
    nid = (request.get_json(silent=True) or {}).get("id")
    if not nid:
        return jsonify({"code": 400, "message": "id 不能为空"}), 400
    n = NotificationModel.query.filter_by(id=nid, user_id=user.id).first()
    if not n:
        return jsonify({"code": 404, "message": "通知不存在"}), 404
    n.is_read = True
    db.session.commit()
    return jsonify({"code": 200, "message": "已标记已读"})


@bp.route("/mark_all_read", methods=["POST"])
@jwt_required()
def mark_all_read():
    """全部已读。body {category?}。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 401, "message": "用户未认证"}), 401
    data = request.get_json(silent=True) or {}
    query = NotificationModel.query.filter_by(user_id=user.id, is_read=False)
    if data.get("category"):
        query = query.filter_by(category=data["category"])
    count = query.update({"is_read": True})
    db.session.commit()
    return jsonify({"code": 200, "message": f"已标记 {count} 条已读",
                    "data": {"marked_count": count}})


@bp.route("/delete", methods=["DELETE"])
@jwt_required()
def delete_notifications():
    """删除（限自己的已读）。body {ids:[]}。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 401, "message": "用户未认证"}), 401
    ids = (request.get_json(silent=True) or {}).get("ids", [])
    if not ids:
        return jsonify({"code": 400, "message": "ids 不能为空"}), 400
    count = NotificationModel.query.filter(
        NotificationModel.id.in_(ids),
        NotificationModel.user_id == user.id,
        NotificationModel.is_read == True,  # noqa: E712
    ).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"code": 200, "message": "删除成功", "data": {"deleted_count": count}})


# ─────────── 管理员侧：发公告 ───────────
@bp.route("/announce", methods=["POST"])
@jwt_required()
@require_admin
def announce():
    """发公告。body {title, content, user_ids?}。user_ids 空=全员 customer。"""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    user_ids = data.get("user_ids") or []
    if not title:
        return jsonify({"code": 400, "message": "title 不能为空"}), 400

    if user_ids:
        user_ids = list(dict.fromkeys(uid for uid in user_ids if uid is not None))
    else:
        # 全员 customer（不含 admin / disabled）
        user_ids = [u.id for u in
                    UserModel.query.filter_by(role="customer", status="active").all()]
        if not user_ids:
            return jsonify({"code": 200, "message": "没有可发送的客户",
                            "data": {"created_count": 0}})

    created = batch_create_notifications(
        user_ids, title, content,
        category=NotificationModel.CAT_SYSTEM,
        source_type=NotificationModel.SRC_ANNOUNCEMENT,
    )
    db.session.commit()
    return jsonify({"code": 200, "message": f"已向 {len(created)} 名用户发送公告",
                    "data": {"created_count": len(created)}})
