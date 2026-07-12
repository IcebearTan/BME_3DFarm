"""Bambuddy Webhook 接收器。

端点：POST /internal/webhooks/bambuddy/{secret}
安全：难猜 URL secret + 仅内网可达（Bambuddy 不支持签名时 IP 白名单补偿）。
职责：校验 secret → 调 services.bambuddy_sync.handle_print_event（幂等落事件 + 匹配 + 状态/credit）。
  状态/credit 在事务内同步处理（DB 操作，足够快）；后续若加重业务（通知等）再丢 Celery task。
"""
from flask import Blueprint, request, jsonify, current_app

from services.bambuddy_sync import handle_print_event

bp = Blueprint("webhook_bambuddy", __name__, url_prefix="/internal/webhooks/bambuddy")


@bp.route("/<secret>", methods=["POST"])
def receive(secret):
    if secret != current_app.config.get("WEBHOOK_SECRET", ""):
        return jsonify({"code": 404}), 404  # 当作路径不存在，不暴露端点

    payload = request.get_json(silent=True) or {}
    # Bambuddy payload 字段名以实测为准，兼容几种常见命名
    result = handle_print_event(
        source="webhook",
        event_type=(
            payload.get("event_type")
            or payload.get("event")
            or payload.get("type")
        ),
        printer_id=payload.get("printer_id") or payload.get("printer"),
        archive_id=payload.get("archive_id") or payload.get("archive"),
        filename=payload.get("filename") or payload.get("file"),
        order_no=payload.get("order_no"),
        timestamp=payload.get("timestamp") or payload.get("ts"),
        payload=payload,
    )
    return jsonify({"code": 200, "message": "received", "result": result}), 200
