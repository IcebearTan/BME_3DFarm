"""内部 API（给训练营奖励系统预留）。

POST /internal/credit/grant —— 未来训练营奖励系统调它给学员充 credit。
鉴权：INTERNAL_API_KEY（请求头 X-Internal-Key）。

现在只放骨架；Phase 1 实现 CreditService.grant 后，这里复用同一 service。
"""
from flask import Blueprint, request, jsonify, current_app

bp = Blueprint("internal", __name__, url_prefix="/internal")


@bp.route("/credit/grant", methods=["POST"])
def grant_credit():
    """给指定用户发放 credit（训练营奖励等外部来源）。

    请求体: {user_id, amount, reason, source}
    """
    key = request.headers.get("X-Internal-Key", "")
    if key != current_app.config.get("INTERNAL_API_KEY", ""):
        return jsonify({"code": 401, "message": "无效的内部 API Key"}), 401

    # TODO Phase 1: 复用 CreditService.grant(user_id, amount, source='training_camp', reason=...)
    return jsonify({"code": 501, "message": "TODO: Phase 1 实现 CreditService.grant"}), 501
