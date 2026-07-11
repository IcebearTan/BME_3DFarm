"""后台管理 API（仅 admin）。占位路由，Phase 0/1/2 实现。"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from . import require_admin

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/orders", methods=["GET"])
@jwt_required()
@require_admin
def list_orders():
    """订单列表（可筛选状态）。"""
    # TODO Phase 1
    return jsonify({"code": 200, "data": [], "message": "TODO: Phase 1"})


@bp.route("/orders/<int:order_id>/quote", methods=["POST"])
@jwt_required()
@require_admin
def quote_order(order_id):
    """人工报价（Phase 1）或自动报价（Phase 4）。"""
    # TODO Phase 1: 管理员填 estimated_credit
    return jsonify({"code": 501, "message": "TODO: Phase 1"}), 501


@bp.route("/orders/<int:order_id>/mark-completed", methods=["POST"])
@jwt_required()
@require_admin
def mark_completed(order_id):
    """人工打印后标记完成（Phase 1：触发实扣 credit）。"""
    # TODO Phase 1
    return jsonify({"code": 501, "message": "TODO: Phase 1"}), 501


@bp.route("/credit/grant", methods=["POST"])
@jwt_required()
@require_admin
def grant_credit():
    """管理员发放 credit（管理员后台手动发放）。"""
    # TODO Phase 1: 复用 CreditService.grant(source='admin_grant')
    return jsonify({"code": 501, "message": "TODO: Phase 1"}), 501


@bp.route("/printers", methods=["GET"])
@jwt_required()
@require_admin
def list_printers():
    """打印机状态总览。"""
    # TODO Phase 0: 录入 7 台 P1S（含 has_ams）；Phase 2: 从 Bambuddy 同步状态
    return jsonify({"code": 200, "data": [], "message": "TODO: Phase 0 录入 + Phase 2 同步"})
