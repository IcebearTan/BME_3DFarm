"""内部 API（给训练营奖励系统预留）。

POST /internal/credit/grant —— 未来训练营奖励系统调它给学员充 credit。
鉴权：INTERNAL_API_KEY（请求头 X-Internal-Key）。

复用 CreditService.grant（source 默认 training_camp）。request_id 作为幂等键：
训练营侧每次奖励发放带稳定 request_id，重复回调只生效一次。
"""
from flask import Blueprint, request, jsonify, current_app

from models import UserModel
from services import CreditService, InvalidAmountError

bp = Blueprint("internal", __name__, url_prefix="/internal")


@bp.route("/credit/grant", methods=["POST"])
def grant_credit():
    """给指定用户发放 credit（训练营奖励等外部来源）。

    请求体: {user_id, amount, reason, source?, request_id?}
      - source: 默认 training_camp
      - request_id: 外部系统幂等标识，防重复回调多充
    """
    key = request.headers.get("X-Internal-Key", "")
    if key != current_app.config.get("INTERNAL_API_KEY", ""):
        return jsonify({"code": 401, "message": "无效的内部 API Key"}), 401

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    amount = data.get("amount")
    reason = data.get("reason")
    source = data.get("source") or "training_camp"
    request_id = data.get("request_id")
    idempotency_key = f"internal:{request_id}" if request_id else None

    if not user_id or amount is None:
        return jsonify({"code": 400, "message": "user_id/amount 必填"}), 400

    if not UserModel.query.filter_by(id=user_id).first():
        return jsonify({"code": 404, "message": f"用户 {user_id} 不存在"}), 404

    try:
        result = CreditService.grant(
            user_id,
            amount,
            source=source,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    except InvalidAmountError as e:
        return jsonify({"code": 400, "message": str(e)}), 400

    return jsonify({
        "code": 200,
        "message": "发放成功" if not result["replayed"] else "已发放（幂等重放）",
        "data": result,
    })
