"""Bambuddy Webhook 接收器。骨架，Phase 2 实现。

端点：/internal/webhooks/bambuddy/{secret}
安全：难猜 URL + 仅内网可达 + （Bambuddy 不支持签名时）IP 白名单补偿。
职责（Phase 2）：
  1. 校验 secret
  2. 落 print_event（幂等键去重）
  3. 匹配订单（archive_id 优先，filename 兜底）
  4. 触发状态更新（不在请求内做重业务，必要时丢 Celery task）
"""
from flask import Blueprint, request, jsonify, current_app

bp = Blueprint("webhook_bambuddy", __name__, url_prefix="/internal/webhooks/bambuddy")


@bp.route("/<secret>", methods=["POST"])
def receive(secret):
    if secret != current_app.config.get("WEBHOOK_SECRET", ""):
        return jsonify({"code": 404}), 404  # 不存在的路径，不暴露端点存在性

    payload = request.get_json(silent=True) or {}
    # TODO Phase 2: 幂等键 = source + event + printer_id + archive_id + filename + timestamp
    # TODO Phase 2: 落 PrintEventModel + 匹配 BambuddyJob + 更新 PrintOrderModel 状态
    return jsonify({"code": 200, "message": "received", "todo": "Phase 2"}), 200
