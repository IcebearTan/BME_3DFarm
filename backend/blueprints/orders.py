"""客户订单 API。占位路由，Phase 1 实现完整逻辑。"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from . import _current_user

bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.route("/", methods=["GET"])
@jwt_required()
def my_orders():
    """我的订单列表。"""
    # TODO Phase 1: 按 user_id 查 PrintOrderModel，分页
    return jsonify({"code": 200, "data": [], "message": "TODO: Phase 1"})


@bp.route("/<int:order_id>", methods=["GET"])
@jwt_required()
def order_detail(order_id):
    """订单详情（脱敏）。"""
    # TODO Phase 1
    return jsonify({"code": 501, "message": "TODO: Phase 1"}), 501


@bp.route("/", methods=["POST"])
@jwt_required()
def create_order():
    """创建订单草稿 + 上传文件。"""
    # TODO Phase 1: 建 PrintOrderModel + OrderFileModel（MinIO 存文件）
    return jsonify({"code": 501, "message": "TODO: Phase 1"}), 501


@bp.route("/<int:order_id>/cancel", methods=["POST"])
@jwt_required()
def cancel_order(order_id):
    """取消未打印的订单（释放冻结 credit）。"""
    # TODO Phase 1
    return jsonify({"code": 501, "message": "TODO: Phase 1"}), 501
