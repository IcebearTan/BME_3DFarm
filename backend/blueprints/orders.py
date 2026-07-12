"""客户订单 API。组合 CreditService + OrderStateMachine + storage 跑通订单闭环。

所有接口带 user_id 过滤，防多租户越权（§8）。
事务原子性：confirm/cancel 把 credit 操作与状态转换组合在单一事务（_commit=False
+ 统一 commit），任一步失败全回滚。
"""
import hashlib
import os
import tempfile
import uuid
from datetime import datetime

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from exts import db
from models import PrintOrderModel, OrderFileModel
from services import (
    CreditService,
    OrderStateMachine,
    InsufficientCreditError,
    InvalidTransitionError,
)
from services.storage import storage
from services.gcode_parser import parse_gcode_3mf
from services.pricing import PricingService
from . import _current_user

bp = Blueprint("orders", __name__, url_prefix="/orders")

ALLOWED_FILE_EXT = (".3mf", ".gcode.3mf")
MAX_FILE_BYTES = 200 * 1024 * 1024  # 200MB


def _order_to_dict(order):
    """订单序列化（脱敏：客户看 public_status，不看 admin_note/actual_credit）。"""
    return {
        "id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "public_status": order.public_status,
        "material": order.material,
        "color": order.color,
        "quantity": order.quantity,
        "layer_height": str(order.layer_height) if order.layer_height is not None else None,
        "nozzle_size": str(order.nozzle_size) if order.nozzle_size is not None else None,
        "estimated_credit": str(order.estimated_credit) if order.estimated_credit is not None else None,
        "frozen_credit": str(order.frozen_credit),
        "customer_note": order.customer_note,
        "public_progress": order.public_progress,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "files": [
            {
                "id": f.id,
                "file_type": f.file_type,
                "original_filename": f.original_filename,
                "size_bytes": f.size_bytes,
                "version": f.version,
            }
            for f in order.files
        ],
    }


def _owned_order_or_404(order_id, user_id):
    """查订单并校验属主（防越权）。不存在或不属于该用户都返回 None。"""
    order = db.session.get(PrintOrderModel, order_id)
    if order is None or order.user_id != user_id:
        return None
    return order


@bp.route("/", methods=["POST"])
@jwt_required()
def create_order():
    """创建订单（status=QUOTING，等管理员报价）。multipart：表单参数 + 可选 file。

    表单：material(必填)/color/quantity/layer_height/nozzle_size/customer_note
    文件：file（.3mf 或 .gcode.3mf，<=200MB，可选）
    """
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404

    material = request.form.get("material")
    if not material:
        return jsonify({"code": 400, "message": "material 必填"}), 400

    color = request.form.get("color")
    quantity = request.form.get("quantity", 1, type=int) or 1
    layer_height = request.form.get("layer_height", type=float)
    nozzle_size = request.form.get("nozzle_size", type=float)
    customer_note = request.form.get("customer_note")

    # 先处理文件：校验 + 流式算 sha256 + 存临时（不爆内存）；失败直接返回不建 order
    file_meta = None
    file = request.files.get("file")
    if file and file.filename:
        filename = file.filename.lower()
        if not filename.endswith(ALLOWED_FILE_EXT):
            return jsonify({"code": 400, "message": f"文件类型必须为 {ALLOWED_FILE_EXT}"}), 400

        h = hashlib.sha256()
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            size = 0
            while True:
                chunk = file.stream.read(8192)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    os.unlink(tmp_path)
                    return jsonify({"code": 413, "message": f"文件过大（上限 {MAX_FILE_BYTES // 1024 // 1024}MB）"}), 413
                h.update(chunk)
                tmp.write(chunk)
        file_meta = {
            "tmp_path": tmp_path,
            "sha256": h.hexdigest(),
            "size": size,
            "original_filename": file.filename,
            "content_type": file.content_type or "application/octet-stream",
            "is_sliced": filename.endswith(".gcode.3mf"),
        }

    # .gcode.3mf 自动报价：解析 gcode 拿克重/时长 → PricingService 算 credit
    estimated_credit = None
    estimate_weight = None
    estimate_seconds = None
    if file_meta and file_meta.get("is_sliced"):
        parsed = parse_gcode_3mf(file_meta["tmp_path"])
        if parsed:
            estimate_weight = parsed.get("filament_used_g")
            estimate_seconds = parsed.get("print_time_s")
            if estimate_weight is not None or estimate_seconds is not None:
                estimated_credit = PricingService.calc(
                    estimate_weight or 0, estimate_seconds or 0, material
                )

    # 建 order（commit 拿 id，用于 storage key）
    order_no = f"PO{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
    status = (PrintOrderModel.STATUS_WAITING_CONFIRM if estimated_credit is not None
              else PrintOrderModel.STATUS_QUOTING)
    order = PrintOrderModel(
        order_no=order_no,
        user_id=user.id,
        status=status,
        public_status=OrderStateMachine.public_status_of(status),
        material=material, color=color, quantity=quantity,
        layer_height=layer_height, nozzle_size=nozzle_size,
        customer_note=customer_note,
        estimated_credit=estimated_credit,
        estimate_weight_g=estimate_weight,
        estimate_print_seconds=int(estimate_seconds) if estimate_seconds is not None else None,
    )
    db.session.add(order)
    db.session.commit()

    # 上传文件到 MinIO + 写 order_file
    if file_meta:
        try:
            ext = ".gcode.3mf" if file_meta["is_sliced"] else ".3mf"
            key = f"orders/{order.id}/{uuid.uuid4().hex}{ext}"
            with open(file_meta["tmp_path"], "rb") as f:
                storage.put_object(key, f, file_meta["size"],
                                   content_type=file_meta["content_type"])
            db.session.add(OrderFileModel(
                order_id=order.id,
                file_type=(OrderFileModel.FILE_SLICED if file_meta["is_sliced"]
                           else OrderFileModel.FILE_SOURCE_MODEL),
                original_filename=file_meta["original_filename"],
                storage_key=key,
                content_type=file_meta["content_type"],
                size_bytes=file_meta["size"],
                sha256=file_meta["sha256"],
            ))
            db.session.commit()
        finally:
            if os.path.exists(file_meta["tmp_path"]):
                try:
                    os.unlink(file_meta["tmp_path"])
                except OSError:
                    pass

    msg = (f"订单已创建，自动报价 {estimated_credit} credit，待确认"
           if estimated_credit is not None
           else "订单已创建，等待管理员报价")
    return jsonify({"code": 200, "message": msg,
                    "data": _order_to_dict(order)}), 200


@bp.route("/", methods=["GET"])
@jwt_required()
def my_orders():
    """我的订单列表（分页，按 user_id 过滤）。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    status = request.args.get("status") or None
    q = PrintOrderModel.query.filter_by(user_id=user.id)
    if status:
        q = q.filter_by(status=status)
    q = q.order_by(PrintOrderModel.created_at.desc())
    pag = q.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({"code": 200, "data": {
        "items": [_order_to_dict(o) for o in pag.items],
        "total": pag.total, "page": pag.page,
        "per_page": pag.per_page, "pages": pag.pages,
    }})


@bp.route("/<int:order_id>", methods=["GET"])
@jwt_required()
def order_detail(order_id):
    """订单详情（user_id 过滤，脱敏）。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    order = _owned_order_or_404(order_id, user.id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    return jsonify({"code": 200, "data": _order_to_dict(order)})


@bp.route("/<int:order_id>/confirm", methods=["POST"])
@jwt_required()
def confirm_order(order_id):
    """确认下单：WAITING_CONFIRM → CREDIT_RESERVED，冻结 estimated_credit。

    事务原子：freeze + transition 同提交，余额不足或状态非法全回滚。
    """
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    order = _owned_order_or_404(order_id, user.id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404
    if order.estimated_credit is None:
        return jsonify({"code": 409, "message": "订单尚未报价"}), 409

    try:
        CreditService.freeze(user.id, order.id, order.estimated_credit,
                             quote_version=1, _commit=False)
        OrderStateMachine.transition(
            order, PrintOrderModel.STATUS_CREDIT_RESERVED,
            actor_id=user.id, note="客户确认下单", _commit=False,
        )
        order.frozen_credit = order.estimated_credit
        db.session.commit()
    except InsufficientCreditError as e:
        db.session.rollback()
        return jsonify({"code": 402,
                        "message": f"可用额度不足: 需要 {e.need}, 当前 {e.available}"}), 402
    except InvalidTransitionError as e:
        db.session.rollback()
        return jsonify({"code": 409, "message": str(e)}), 409

    return jsonify({"code": 200, "message": "已确认，额度已冻结",
                    "data": _order_to_dict(order)})


@bp.route("/<int:order_id>/cancel", methods=["POST"])
@jwt_required()
def cancel_order(order_id):
    """取消未打印订单：→ CANCELLED，释放冻结 credit。事务原子。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    order = _owned_order_or_404(order_id, user.id)
    if not order:
        return jsonify({"code": 404, "message": "订单不存在"}), 404

    try:
        if order.frozen_credit and order.frozen_credit > 0:
            CreditService.release(user.id, order.id, order.frozen_credit,
                                  reason="客户取消订单", version=1, _commit=False)
        OrderStateMachine.transition(
            order, PrintOrderModel.STATUS_CANCELLED,
            actor_id=user.id, note="客户取消", _commit=False,
        )
        order.frozen_credit = 0
        db.session.commit()
    except (InsufficientCreditError, InvalidTransitionError) as e:
        db.session.rollback()
        return jsonify({"code": 409, "message": str(e)}), 409

    return jsonify({"code": 200, "message": "订单已取消，冻结额度已释放",
                    "data": _order_to_dict(order)})
