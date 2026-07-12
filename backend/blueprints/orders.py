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
from services.gcode_parser import parse_gcode_3mf, main_material
from services.pricing import PricingService, quote_to_jsonable
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
        "is_manual_slice_path": bool(order.is_manual_slice_path),
        "parsed_filaments": order.parsed_filaments,
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
    """创建订单。multipart：表单参数 + 可选 file。

    gcode 是唯一真相：material 不再手填。
      .gcode.3mf → 解析取主材料 → 自动报价 → WAITING_CONFIRM（存 parsed_filaments/nozzles）
      .3mf       → 不解析 → QUOTING + is_manual_slice_path=True（等 admin 切片）
      解析失败   → 降级 QUOTING（人工报价），不阻塞下单
    表单：quantity/customer_note（color/layer_height/nozzle_size 可选，保留兼容）
    文件：file（.3mf 或 .gcode.3mf，<=200MB，可选）
    """
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404

    # gcode 是唯一真相：material 不再必填，由 .gcode.3mf 解析推导（.3mf 路径留空等切片）
    form_material = request.form.get("material")
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

    # 分支：.gcode.3mf 自动报价（gcode 推主材料）；.3mf 手动切片路径（无报价）
    estimated_credit = None
    estimate_weight = None
    estimate_seconds = None
    parsed_filaments = None
    parsed_nozzles = None
    material = form_material  # 默认兜底；.gcode.3mf 路径会被主材料覆盖
    is_manual_slice_path = False

    if file_meta and file_meta.get("is_sliced"):
        parsed = parse_gcode_3mf(file_meta["tmp_path"])
        if parsed:
            parsed_filaments = parsed.get("filaments") or None
            parsed_nozzles = parsed.get("nozzles") or None
            estimate_weight = parsed.get("filament_used_g")
            estimate_seconds = parsed.get("print_time_s")
            material = main_material(parsed) or form_material
            if estimate_weight is not None or estimate_seconds is not None:
                estimated_credit = PricingService.calc(
                    estimate_weight or 0, estimate_seconds or 0, material, surcharge=False,
                )
        # 解析失败：material 维持 form_material，降级 QUOTING（人工报价），不阻塞下单
    elif file_meta and not file_meta.get("is_sliced"):
        # .3mf 模型 → admin 手动切片路径
        is_manual_slice_path = True

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
        parsed_filaments=parsed_filaments,
        parsed_nozzles=parsed_nozzles,
        is_manual_slice_path=is_manual_slice_path,
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

    if estimated_credit is not None:
        msg = f"订单已创建，自动报价 {estimated_credit} credit，待确认"
    elif is_manual_slice_path:
        msg = "订单已创建，需管理员切片后报价"
    else:
        msg = "订单已创建，等待管理员报价"
    return jsonify({"code": 200, "message": msg,
                    "data": _order_to_dict(order)}), 200


def _stream_to_temp(file, max_bytes):
    """流式把 file 落临时文件 + 算 sha256/size。超限返回 (None, None, None, None, error_response)。

    返回 (tmp_path, sha256, size, content_type, None) 或 (None, None, None, None, (resp, code))。
    preview/create 共用：避免一次性 read() 撑爆内存。
    """
    h = hashlib.sha256()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        size = 0
        while True:
            chunk = file.stream.read(8192)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                os.unlink(tmp_path)
                return None, None, None, None, (
                    jsonify({"code": 413,
                             "message": f"文件过大（上限 {max_bytes // 1024 // 1024}MB）"}),
                    413,
                )
            h.update(chunk)
            tmp.write(chunk)
    return tmp_path, h.hexdigest(), size, file.content_type or "application/octet-stream", None


@bp.route("/preview", methods=["POST"])
@jwt_required()
def preview_order():
    """预览 .gcode.3mf：解析返回材料/多色/克重/时长/报价，不落库不冻 credit，临时文件即删。

    返回 {filaments, nozzles, plate_index, material, filament_used_g, print_time_s, quote}。
    解析失败 → 422（提示用户检查文件）。
    """
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"code": 400, "message": "file 必填"}), 400
    if not file.filename.lower().endswith(".gcode.3mf"):
        return jsonify({"code": 400, "message": "预览仅支持 .gcode.3mf（已切片）"}), 400

    tmp_path, _, _, _, err = _stream_to_temp(file, MAX_FILE_BYTES)
    if err:
        return err
    try:
        parsed = parse_gcode_3mf(tmp_path)
        if not parsed:
            return jsonify({
                "code": 422,
                "message": "无法解析 gcode（slice_info.config 与 gcode 注释均无有效信息）",
            }), 422
        material = main_material(parsed)
        quote = PricingService.describe(
            parsed.get("filament_used_g") or 0,
            parsed.get("print_time_s") or 0,
            material, surcharge=False,
        )
        return jsonify({"code": 200, "data": {
            "filaments": parsed.get("filaments"),
            "nozzles": parsed.get("nozzles"),
            "plate_index": parsed.get("plate_index"),
            "material": material,
            "filament_used_g": parsed.get("filament_used_g"),
            "print_time_s": parsed.get("print_time_s"),
            "quote": quote_to_jsonable(quote),
        }})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


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
