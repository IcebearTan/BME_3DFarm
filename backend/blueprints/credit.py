"""客户额度 API。CreditService 的直接消费者（只读档）。"""
from flask import Blueprint, request, jsonify

from flask_jwt_extended import jwt_required

from services import CreditService
from . import _current_user

bp = Blueprint("credit", __name__, url_prefix="/credit")


@bp.route("/me", methods=["GET"])
@jwt_required()
def my_credit():
    """我的额度余额（available 可用 / frozen 冻结 / total 合计）。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    return jsonify({"code": 200, "data": CreditService.get_balance(user.id)})


@bp.route("/me/transactions", methods=["GET"])
@jwt_required()
def my_transactions():
    """我的额度流水（分页 + 类型过滤）。 newest first。

    query: page (默认 1) / per_page (默认 20) / type (grant/freeze/capture/...)
    """
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    type_filter = request.args.get("type") or None
    data = CreditService.list_transactions(
        user.id, page=page, per_page=per_page, type_filter=type_filter
    )
    return jsonify({"code": 200, "data": data})
