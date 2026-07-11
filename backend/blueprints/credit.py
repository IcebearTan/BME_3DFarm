"""客户额度 API。占位路由，Phase 1 实现。"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

bp = Blueprint("credit", __name__, url_prefix="/credit")


@bp.route("/me", methods=["GET"])
@jwt_required()
def my_credit():
    """我的额度余额（available / frozen）。"""
    # TODO Phase 1: 查 CreditAccountModel
    return jsonify({"code": 200, "data": {"available": 0, "frozen": 0}, "message": "TODO: Phase 1"})


@bp.route("/me/transactions", methods=["GET"])
@jwt_required()
def my_transactions():
    """我的额度流水。"""
    # TODO Phase 1: 查 CreditTransactionModel，分页
    return jsonify({"code": 200, "data": [], "message": "TODO: Phase 1"})
