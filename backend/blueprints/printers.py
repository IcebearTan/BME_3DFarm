"""客户侧打印机状态（脱敏：状态 + 队列长度，不暴露温度/任务详情）。

customer 看农场打印机忙闲（下单前了解排队），不看具体任务/温度。
admin 看全量用 /admin/printers。
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from models import PrinterModel
from . import _current_user

bp = Blueprint("printers", __name__, url_prefix="/printers")


def _ams_trays(p):
    """脱敏 AMS 料槽：只暴露颜色/类型/余量，供客户下单前了解可用耗材。

    与 admin 的全量 status_detail 区分——不含温度/进度/任务等敏感细节。
    取主 AMS 单元（ams[0]），P1S 单 AMS 即 4 槽；离线/未同步时返回空列表。
    """
    units = (p.status_detail or {}).get("ams") or []
    if not units or not isinstance(units[0], dict):
        return []
    trays = []
    for tray in (units[0].get("tray") or []):
        if not isinstance(tray, dict):
            continue
        remain = tray.get("remain")
        trays.append({
            "slot": tray.get("id"),
            "color": tray.get("tray_color") or None,
            "type": tray.get("tray_type") or None,
            "brand": tray.get("tray_brand") or None,
            "subtype": tray.get("tray_sub_brands") or None,
            # remain < 0（空/未知）统一归一为 None，前端按空槽处理
            "remain": remain if (isinstance(remain, (int, float)) and remain >= 0) else None,
        })
    return trays


def _public_dict(p):
    return {
        "id": p.id,
        "public_name": p.public_name,
        "model": p.model,
        "status": p.status,
        "source": p.source,
        "queue_count": p.queue_count,
        "has_ams": bool(p.has_ams),
        "ams": _ams_trays(p),
    }


@bp.route("/", methods=["GET"])
@jwt_required()
def list_printers():
    """农场打印机状态（脱敏）。"""
    user = _current_user()
    if not user:
        return jsonify({"code": 404, "message": "用户不存在"}), 404
    printers = PrinterModel.query.order_by(PrinterModel.public_name).all()
    return jsonify({"code": 200, "data": {"items": [_public_dict(p) for p in printers]}})
