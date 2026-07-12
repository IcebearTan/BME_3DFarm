"""客户侧打印机状态（脱敏：状态 + 队列长度，不暴露温度/任务详情）。

customer 看农场打印机忙闲（下单前了解排队），不看具体任务/温度。
admin 看全量用 /admin/printers。
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from models import PrinterModel
from . import _current_user

bp = Blueprint("printers", __name__, url_prefix="/printers")


def _public_dict(p):
    return {
        "id": p.id,
        "public_name": p.public_name,
        "model": p.model,
        "status": p.status,
        "source": p.source,
        "queue_count": p.queue_count,
    }


@bp.route("/", methods=["GET"])
@jwt_required()
def list_printers():
    """农场打印机状态（脱敏）。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    printers = PrinterModel.query.order_by(PrinterModel.id).all()
    return jsonify({"code": 200, "data": {"items": [_public_dict(p) for p in printers]}})
