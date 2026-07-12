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
import hashlib
import io
import os
import tempfile
import uuid
from decimal import Decimal, InvalidOperation

from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required

from exts import db
from models import (
    PrintOrderModel, UserModel, CreditAccountModel, PricingConfigModel,
    BambuddyJobModel, OrderFileModel, PrinterModel,
)
from services import (
    CreditService,
    OrderStateMachine,
    InsufficientCreditError,
    InvalidTransitionError,
    InvalidAmountError,
    AccountNotFoundError,
)
from services.storage import storage
from services.gcode_parser import parse_gcode_3mf
from services.pricing import PricingService
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


# ─────────── Bambuddy 任务绑定（Phase 2） ───────────
def _job_to_dict(job):
    if not job:
        return None
    return {
        "id": job.id, "order_id": job.order_id, "order_no": job.order_no,
        "bambuddy_printer_id": job.bambuddy_printer_id,
        "bambuddy_queue_id": job.bambuddy_queue_id,
        "bambuddy_archive_id": job.bambuddy_archive_id,
        "filename": job.filename, "job_token": job.job_token,
        "bambuddy_status": job.bambuddy_status,
        "mapping_confidence": job.mapping_confidence,
        "dispatched_at": job.dispatched_at.isoformat() if job.dispatched_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "last_sync_at": job.last_sync_at.isoformat() if job.last_sync_at else None,
    }


@bp.route("/orders/<int:order_id>/bambuddy-job", methods=["GET"])
@jwt_required()
@require_admin
def get_bambuddy_job(order_id):
    """查订单绑定的 Bambuddy 任务。"""
    if not _get_order(order_id):
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    job = BambuddyJobModel.query.filter_by(order_id=order_id).first()
    return jsonify({"code": 200, "data": _job_to_dict(job)})


@bp.route("/orders/<int:order_id>/bind-bambuddy", methods=["POST"])
@jwt_required()
@require_admin
def bind_bambuddy(order_id):
    """手动绑定订单 ↔ Bambuddy 任务（mapping_confidence=manual）。

    body: {bambuddy_printer_id(必填), bambuddy_queue_id?, bambuddy_archive_id?, filename?, job_token?}
    """
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    data = request.get_json(silent=True) or {}
    printer_id = data.get("bambuddy_printer_id")
    if printer_id is None:
        return jsonify({"code": 400, "message": "bambuddy_printer_id 必填"}), 400

    job = BambuddyJobModel.query.filter_by(order_id=order_id).first()
    if not job:
        job = BambuddyJobModel(order_id=order_id, order_no=order.order_no)
        db.session.add(job)
    job.bambuddy_printer_id = printer_id
    if data.get("bambuddy_queue_id") is not None:
        job.bambuddy_queue_id = data["bambuddy_queue_id"]
    if data.get("bambuddy_archive_id") is not None:
        job.bambuddy_archive_id = data["bambuddy_archive_id"]
    if data.get("filename") is not None:
        job.filename = data["filename"]
    if data.get("job_token") is not None:
        job.job_token = data["job_token"]
    job.mapping_confidence = "manual"
    db.session.commit()
    return jsonify({"code": 200, "message": "已绑定", "data": _job_to_dict(job)})


# ─────────── 下发 Bambuddy（Phase 3 半自动调度） ───────────
@bp.route("/orders/<int:order_id>/dispatch", methods=["POST"])
@jwt_required()
@require_admin
def dispatch_order(order_id):
    """下发 Bambuddy 打印。body: {bambuddy_printer_id}。

    订单须 READY_TO_PRINT + 有 .gcode.3mf 文件。同步调 _do_dispatch（开发期），
    生产可改 dispatch_order.apply_async 异步。
    """
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    if order.status != PrintOrderModel.STATUS_READY_TO_PRINT:
        return jsonify({"code": 409, "message": "订单必须在 READY_TO_PRINT 才能下发"}), 409
    data = request.get_json(silent=True) or {}
    printer_id = data.get("bambuddy_printer_id")
    if printer_id is None:
        return jsonify({"code": 400, "message": "bambuddy_printer_id 必填"}), 400
    sliced = OrderFileModel.query.filter_by(
        order_id=order_id, file_type=OrderFileModel.FILE_SLICED
    ).first()
    if not sliced:
        return jsonify({"code": 409, "message": "订单无 .gcode.3mf 文件，无法自动下发"}), 409

    from bambuddy_adapter import BambuddyError
    from tasks.dispatch import _do_dispatch
    try:
        result = _do_dispatch(order_id, printer_id)
    except BambuddyError as e:
        return jsonify({"code": 502, "message": f"Bambuddy 下发失败: {e}"}), 502
    code = 200 if result.get("status") == "dispatched" else 409
    return jsonify({"code": code, "data": result, "message": result.get("status")}), code


@bp.route("/orders/<int:order_id>/cancel-dispatch", methods=["POST"])
@jwt_required()
@require_admin
def cancel_dispatch(order_id):
    """取消 Bambuddy 队列项（remove queue + 清 job.queue_id）。"""
    if not _get_order(order_id):
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    from bambuddy_adapter import BambuddyError
    from tasks.dispatch import _do_cancel
    try:
        result = _do_cancel(order_id)
    except BambuddyError as e:
        return jsonify({"code": 502, "message": f"Bambuddy 取消失败: {e}"}), 502
    code = 200 if result.get("status") == "cancelled" else 409
    return jsonify({"code": code, "data": result, "message": result.get("status")}), code


# ─────────── 文件下载 + 切片产物上传（Phase 4 路径 B） ───────────
@bp.route("/orders/<int:order_id>/files/<int:file_id>/download", methods=["GET"])
@jwt_required()
@require_admin
def download_order_file(order_id, file_id):
    """下载订单文件（管理员拉 .3mf 去本地 Bambu Studio 切片）。"""
    f = db.session.get(OrderFileModel, file_id)
    if not f or f.order_id != order_id:
        return jsonify({"code": 404, "message": "文件不存在"}), 404
    resp = storage.get_object(f.storage_key)
    try:
        data = resp.read()
    finally:
        resp.close()
    return Response(
        data,
        mimetype=f.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{f.original_filename}"'},
    )


@bp.route("/orders/<int:order_id>/upload-sliced", methods=["POST"])
@jwt_required()
@require_admin
def upload_sliced(order_id):
    """管理员上传切片产物 .gcode.3mf → 解析 gcode + 自动报价（复用 Phase 1.5）。

    订单在 QUOTING：报价成功后转 WAITING_CONFIRM。
    """
    admin = _current_user()
    order = _get_order(order_id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"code": 400, "message": "file 必填"}), 400
    if not file.filename.lower().endswith(".gcode.3mf"):
        return jsonify({"code": 400, "message": "仅支持 .gcode.3mf（已切片）"}), 400

    file_bytes = file.read()
    size = len(file_bytes)
    sha = hashlib.sha256(file_bytes).hexdigest()
    key = f"orders/{order_id}/{uuid.uuid4().hex}.gcode.3mf"
    storage.put_object(
        key, io.BytesIO(file_bytes), size,
        content_type=file.content_type or "application/octet-stream",
    )
    db.session.add(OrderFileModel(
        order_id=order_id, file_type=OrderFileModel.FILE_SLICED,
        original_filename=file.filename, storage_key=key,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size, sha256=sha,
    ))

    # 解析 gcode + 自动报价
    parsed = None
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        parsed = parse_gcode_3mf(tmp_path)
    except Exception:
        parsed = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    estimated = None
    if parsed and (parsed.get("filament_used_g") or parsed.get("print_time_s")):
        estimated = PricingService.calc(
            parsed.get("filament_used_g") or 0,
            parsed.get("print_time_s") or 0,
            order.material,
        )
        order.estimated_credit = estimated
        order.estimate_weight_g = parsed.get("filament_used_g")
        order.estimate_print_seconds = (
            int(parsed["print_time_s"]) if parsed.get("print_time_s") else None
        )
        if order.status == PrintOrderModel.STATUS_QUOTING:
            OrderStateMachine.transition(
                order, PrintOrderModel.STATUS_WAITING_CONFIRM,
                actor_id=admin.id, note=f"切片产物上传，自动报价 {estimated}", _commit=False,
            )
    db.session.commit()

    return jsonify({
        "code": 200,
        "message": (
            f"已上传并自动报价 {estimated} credit" if estimated
            else "已上传（未能解析 gcode，需手动报价）"
        ),
        "data": {
            "estimated_credit": str(estimated) if estimated else None,
            "order_status": order.status,
        },
    })


# ─────────── 打印机（Phase 2） ───────────
@bp.route("/printers", methods=["GET"])
@jwt_required()
@require_admin
def list_printers():
    """打印机状态总览（全量：含 status_detail 温度/进度，Poller 每 30s 同步）。"""
    printers = PrinterModel.query.order_by(PrinterModel.id).all()
    items = [
        {
            "id": p.id, "public_name": p.public_name, "internal_name": p.internal_name,
            "bambuddy_printer_id": p.bambuddy_printer_id, "model": p.model,
            "has_ams": p.has_ams, "status": p.status, "source": p.source,
            "status_detail": p.status_detail, "queue_count": p.queue_count,
            "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
            "enabled": p.enabled,
        }
        for p in printers
    ]
    return jsonify({"code": 200, "data": {"items": items}})
