"""后台管理 API（仅 admin）。订单生命周期管理 + credit 发放。

订单操作的 credit 联动（事务原子，_commit=False + 统一 commit）：
  quote    填 estimated_credit + QUOTING→WAITING_CONFIRM（无 credit）
  approve  CREDIT_RESERVED→REVIEWING→APPROVED→READY_TO_PRINT（无 credit，纯审核）
  start    READY_TO_PRINT→PRINTING（无 credit）
  complete PRINTING→PRINT_COMPLETED + capture（实扣，actual_credit 多退少补）
  fail     PRINTING→PRINT_FAILED + release（释放冻结）
  refund   →REFUNDED + refund（实扣退回）
  cancel   →CANCELLED + release（未打印释放）
"""
from decimal import Decimal, InvalidOperation

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from exts import db
from models import PrintOrderModel, UserModel, CreditAccountModel, PricingConfigModel
from services import (
    CreditService,
    OrderStateMachine,
    InsufficientCreditError,
    InvalidTransitionError,
    InvalidAmountError,
    AccountNotFoundError,
)
from . import require_admin, _current_user

bp = Blueprint("admin", __name__, url_prefix="/admin")

S = PrintOrderModel


def _admin_order_to_dict(order):
    """管理员视角订单（完整字段，含 admin_note/actual_credit/预估等）。"""
    return {
        "id": order.id,
        "order_no": order.order_no,
        "user_id": order.user_id,
        "status": order.status,
        "public_status": order.public_status,
        "material": order.material,
        "color": order.color,
        "quantity": order.quantity,
        "layer_height": str(order.layer_height) if order.layer_height is not None else None,
        "nozzle_size": str(order.nozzle_size) if order.nozzle_size is not None else None,
        "estimate_weight_g": str(order.estimate_weight_g) if order.estimate_weight_g is not None else None,
        "estimate_print_seconds": order.estimate_print_seconds,
        "estimated_credit": str(order.estimated_credit) if order.estimated_credit is not None else None,
        "frozen_credit": str(order.frozen_credit),
        "actual_credit": str(order.actual_credit) if order.actual_credit is not None else None,
        "priority": order.priority,
        "due_at": order.due_at.isoformat() if order.due_at else None,
        "customer_note": order.customer_note,
        "admin_note": order.admin_note,
        "public_progress": order.public_progress,
        "remaining_seconds": order.remaining_seconds,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        "files": [
            {
                "id": f.id, "file_type": f.file_type,
                "original_filename": f.original_filename,
                "storage_key": f.storage_key,
                "size_bytes": f.size_bytes, "sha256": f.sha256, "version": f.version,
            }
            for f in order.files
        ],
    }


def _to_dec(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _get_order(order_id):
    return db.session.get(PrintOrderModel, order_id)


# ─────────── 订单列表 / 详情 ───────────
@bp.route("/orders", methods=["GET"])
@jwt_required()
@require_admin
def list_orders():
    """全部订单列表（可筛 status / user_id）。"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    status = request.args.get("status") or None
    user_id = request.args.get("user_id", type=int)
    q = PrintOrderModel.query
    if status:
        q = q.filter_by(status=status)
    if user_id:
        q = q.filter_by(user_id=user_id)
    q = q.order_by(PrintOrderModel.created_at.desc())
    pag = q.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({"code": 200, "data": {
        "items": [_admin_order_to_dict(o) for o in pag.items],
        "total": pag.total, "page": pag.page,
        "per_page": pag.per_page, "pages": pag.pages,
    }})


@bp.route("/orders/<int:order_id>", methods=["GET"])
@jwt_required()
@require_admin
def order_detail(order_id):
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    return jsonify({"code": 200, "data": _admin_order_to_dict(order)})


# ─────────── 报价 / 审核 / 开打 ───────────
@bp.route("/orders/<int:order_id>/quote", methods=["POST"])
@jwt_required()
@require_admin
def quote_order(order_id):
    """人工报价：填 estimated_credit + QUOTING→WAITING_CONFIRM。"""
    admin = _current_user()
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    data = request.get_json(silent=True) or {}
    estimated = _to_dec(data.get("estimated_credit"))
    if estimated is None or estimated <= 0:
        return jsonify({"code": 400, "message": "estimated_credit 必须为正数"}), 400
    try:
        order.estimated_credit = estimated
        if data.get("admin_note"):
            order.admin_note = data["admin_note"]
        OrderStateMachine.transition(
            order, S.STATUS_WAITING_CONFIRM, actor_id=admin.id,
            note=f"报价 {estimated}", _commit=False,
        )
        db.session.commit()
    except InvalidTransitionError as e:
        db.session.rollback()
        return jsonify({"code": 409, "message": str(e)}), 409
    return jsonify({"code": 200, "message": "报价完成，待客户确认",
                    "data": _admin_order_to_dict(order)})


@bp.route("/orders/<int:order_id>/approve", methods=["POST"])
@jwt_required()
@require_admin
def approve_order(order_id):
    """审核通过 + 排队：CREDIT_RESERVED→REVIEWING→APPROVED→READY_TO_PRINT（连走 3 步）。"""
    admin = _current_user()
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    try:
        OrderStateMachine.transition(order, S.STATUS_REVIEWING, actor_id=admin.id, _commit=False)
        OrderStateMachine.transition(order, S.STATUS_APPROVED, actor_id=admin.id, _commit=False)
        OrderStateMachine.transition(order, S.STATUS_READY_TO_PRINT, actor_id=admin.id, _commit=False)
        db.session.commit()
    except InvalidTransitionError as e:
        db.session.rollback()
        return jsonify({"code": 409, "message": str(e)}), 409
    return jsonify({"code": 200, "message": "审核通过，已进入打印队列",
                    "data": _admin_order_to_dict(order)})


@bp.route("/orders/<int:order_id>/start", methods=["POST"])
@jwt_required()
@require_admin
def start_order(order_id):
    """开始打印：READY_TO_PRINT→PRINTING。"""
    admin = _current_user()
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    try:
        OrderStateMachine.transition(order, S.STATUS_PRINTING, actor_id=admin.id, _commit=False)
        db.session.commit()
    except InvalidTransitionError as e:
        db.session.rollback()
        return jsonify({"code": 409, "message": str(e)}), 409
    return jsonify({"code": 200, "message": "已开始打印", "data": _admin_order_to_dict(order)})


# ─────────── 完成 / 失败 / 退款 / 取消（credit 联动） ───────────
@bp.route("/orders/<int:order_id>/complete", methods=["POST"])
@jwt_required()
@require_admin
def complete_order(order_id):
    """标记完成：PRINTING→PRINT_COMPLETED + capture（实扣，多退少补）。

    body: {actual_credit?: number} 不传则按 frozen_credit 实扣。
    """
    admin = _current_user()
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    data = request.get_json(silent=True) or {}
    frozen = order.frozen_credit
    if not frozen or frozen <= 0:
        return jsonify({"code": 409, "message": "订单无冻结额度，无法实扣"}), 409
    actual = _to_dec(data.get("actual_credit"))  # None 或 Decimal

    try:
        CreditService.capture(order.user_id, order.id, frozen,
                              actual_credit=actual, _commit=False)
        OrderStateMachine.transition(
            order, S.STATUS_PRINT_COMPLETED, actor_id=admin.id,
            note="打印完成", _commit=False,
        )
        order.actual_credit = actual if actual is not None else frozen
        order.frozen_credit = 0
        db.session.commit()
    except InsufficientCreditError as e:
        db.session.rollback()
        return jsonify({"code": 402, "message": str(e)}), 402
    except InvalidTransitionError as e:
        db.session.rollback()
        return jsonify({"code": 409, "message": str(e)}), 409
    return jsonify({"code": 200, "message": "已完成实扣",
                    "data": _admin_order_to_dict(order)})


@bp.route("/orders/<int:order_id>/fail", methods=["POST"])
@jwt_required()
@require_admin
def fail_order(order_id):
    """标记失败：PRINTING→PRINT_FAILED + release（释放冻结，进人工）。"""
    admin = _current_user()
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    try:
        if order.frozen_credit and order.frozen_credit > 0:
            CreditService.release(order.user_id, order.id, order.frozen_credit,
                                  reason="打印失败释放", version=1, _commit=False)
        OrderStateMachine.transition(
            order, S.STATUS_PRINT_FAILED, actor_id=admin.id,
            note="打印失败", _commit=False,
        )
        order.frozen_credit = 0
        db.session.commit()
    except (InsufficientCreditError, InvalidTransitionError) as e:
        db.session.rollback()
        return jsonify({"code": 409, "message": str(e)}), 409
    return jsonify({"code": 200, "message": "已标记失败，冻结额度已释放",
                    "data": _admin_order_to_dict(order)})


@bp.route("/orders/<int:order_id>/refund", methods=["POST"])
@jwt_required()
@require_admin
def refund_order(order_id):
    """退款：→REFUNDED + refund（实扣退回）。body: {amount, reason?}。"""
    admin = _current_user()
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    data = request.get_json(silent=True) or {}
    amount = _to_dec(data.get("amount"))
    if amount is None or amount <= 0:
        return jsonify({"code": 400, "message": "amount 必须为正数"}), 400
    reason = data.get("reason") or "admin_refund"
    try:
        CreditService.refund(order.user_id, order.id, amount,
                             reason_code=reason, version=1, _commit=False)
        OrderStateMachine.transition(
            order, S.STATUS_REFUNDED, actor_id=admin.id,
            note=f"退款 {amount}", _commit=False,
        )
        db.session.commit()
    except (InsufficientCreditError, InvalidTransitionError) as e:
        db.session.rollback()
        return jsonify({"code": 409, "message": str(e)}), 409
    return jsonify({"code": 200, "message": "已退款", "data": _admin_order_to_dict(order)})


@bp.route("/orders/<int:order_id>/cancel", methods=["POST"])
@jwt_required()
@require_admin
def cancel_order(order_id):
    """管理员取消未打印订单：→CANCELLED + release。"""
    admin = _current_user()
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    try:
        if order.frozen_credit and order.frozen_credit > 0:
            CreditService.release(order.user_id, order.id, order.frozen_credit,
                                  reason="管理员取消", version=1, _commit=False)
        OrderStateMachine.transition(
            order, S.STATUS_CANCELLED, actor_id=admin.id,
            note="管理员取消", _commit=False,
        )
        order.frozen_credit = 0
        db.session.commit()
    except (InsufficientCreditError, InvalidTransitionError) as e:
        db.session.rollback()
        return jsonify({"code": 409, "message": str(e)}), 409
    return jsonify({"code": 200, "message": "订单已取消", "data": _admin_order_to_dict(order)})


# ─────────── credit 发放 ───────────
@bp.route("/credit/grant", methods=["POST"])
@jwt_required()
@require_admin
def grant_credit():
    """管理员发放 credit。body: {user_id, amount, reason?}。"""
    admin = _current_user()
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    amount = data.get("amount")
    reason = data.get("reason") or "管理员发放"
    if not user_id or amount is None:
        return jsonify({"code": 400, "message": "user_id/amount 必填"}), 400
    if not UserModel.query.filter_by(id=user_id).first():
        return jsonify({"code": 404, "message": f"用户 {user_id} 不存在"}), 404
    try:
        result = CreditService.grant(user_id, amount, source="admin_grant",
                                     reason=reason, operator_id=admin.id)
    except InvalidAmountError as e:
        return jsonify({"code": 400, "message": str(e)}), 400
    except AccountNotFoundError:
        return jsonify({"code": 404, "message": "用户账户不存在"}), 404
    return jsonify({
        "code": 200,
        "message": "发放成功" if not result["replayed"] else "已发放（幂等重放）",
        "data": result,
    })


# ─────────── 用户搜索（发 credit 选用户用，Phase 1.5） ───────────
@bp.route("/users", methods=["GET"])
@jwt_required()
@require_admin
def list_users():
    """搜索用户（email/username 模糊，limit 20）。返回 id/email/username/role + 余额。"""
    q = (request.args.get("q") or "").strip()
    query = UserModel.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(UserModel.email.ilike(like), UserModel.username.ilike(like))
        )
    users = query.order_by(UserModel.id.desc()).limit(20).all()
    items = []
    for u in users:
        acct = CreditAccountModel.query.filter_by(user_id=u.id).first()
        items.append({
            "id": u.id, "email": u.email, "username": u.username, "role": u.role,
            "available": str(acct.available_credit) if acct else "0.00",
            "frozen": str(acct.frozen_credit) if acct else "0.00",
        })
    return jsonify({"code": 200, "data": {"items": items, "total": len(items)}})


# ─────────── 费率配置（自动报价，Phase 1.5） ───────────
def _pricing_to_dict(p):
    return {
        "id": p.id, "key": p.key, "value": str(p.value),
        "category": p.category, "label": p.label, "unit": p.unit,
    }


@bp.route("/pricing", methods=["GET"])
@jwt_required()
@require_admin
def list_pricing():
    items = PricingConfigModel.query.order_by(
        PricingConfigModel.category, PricingConfigModel.id
    ).all()
    return jsonify(
        {"code": 200, "data": {"items": [_pricing_to_dict(p) for p in items]}}
    )


@bp.route("/pricing", methods=["POST"])
@jwt_required()
@require_admin
def add_pricing():
    """加新材料费率。body: {key, value, label?, unit?}，key 形如 material:NYLON。"""
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    value = data.get("value")
    if not key or value is None:
        return jsonify({"code": 400, "message": "key/value 必填"}), 400
    if PricingConfigModel.query.filter_by(key=key).first():
        return jsonify({"code": 409, "message": f"key {key} 已存在"}), 409
    try:
        val = Decimal(str(value))
    except InvalidOperation:
        return jsonify({"code": 400, "message": "value 必须为数字"}), 400
    p = PricingConfigModel(
        key=key, value=val, category=PricingConfigModel.CAT_MATERIAL,
        label=data.get("label") or key.split(":")[-1], unit=data.get("unit") or "g",
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"code": 200, "message": "已添加", "data": _pricing_to_dict(p)})


@bp.route("/pricing/<int:pid>", methods=["PUT"])
@jwt_required()
@require_admin
def update_pricing(pid):
    """改单项 value（也可改 label）。body: {value?, label?}。"""
    p = db.session.get(PricingConfigModel, pid)
    if not p:
        return jsonify({"code": 404, "message": "配置不存在"}), 404
    data = request.get_json(silent=True) or {}
    if data.get("value") is not None:
        try:
            p.value = Decimal(str(data["value"]))
        except InvalidOperation:
            return jsonify({"code": 400, "message": "value 必须为数字"}), 400
    if data.get("label") is not None:
        p.label = data["label"]
    db.session.commit()
    return jsonify({"code": 200, "message": "已更新", "data": _pricing_to_dict(p)})


# ─────────── 打印机（Phase 2） ───────────
@bp.route("/printers", methods=["GET"])
@jwt_required()
@require_admin
def list_printers():
    """打印机状态总览。Phase 0 录入 + Phase 2 从 Bambuddy 同步。"""
    return jsonify({"code": 200, "data": [], "message": "TODO: Phase 0 录入 + Phase 2 同步"})
