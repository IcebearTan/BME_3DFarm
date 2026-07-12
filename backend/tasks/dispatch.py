"""Phase 3 半自动调度：管理员一键下发 Bambuddy（upload archive + add_to_queue）。

dispatch 只管下发 + 建/更新 BambuddyJob，不改订单状态——print_started 由 Phase 2 Webhook/Poller
处理 READY_TO_PRINT → PRINTING，避免状态机冲突。
_do_dispatch / _do_cancel 纯函数，测试绕 celery（同 Poller 模式）；admin 接口直接调纯函数（同步）。
路径/字段以 Bambuddy 实测为准（本轮假设 POST /archives/ + POST /queue/ + DELETE /queue/{id}）。
"""
from datetime import datetime

from celery_app import celery
from exts import db
from models import PrintOrderModel, OrderFileModel, BambuddyJobModel
from bambuddy_adapter import BambuddyAdapter, BambuddyError
from services.storage import storage


@celery.task(
    autoretry_for=(Exception,), retry_backoff=True, max_retries=3, retry_backoff_max=60
)
def dispatch_order(order_id, printer_id):
    """Celery 入口（生产 apply_async 异步；admin/测试可直调 _do_dispatch）。"""
    return _do_dispatch(order_id, printer_id)


@celery.task
def cancel_dispatch(order_id):
    return _do_cancel(order_id)


def _extract_id(resp, *keys):
    """从 Bambuddy 响应提取 id（字段名实测后定，兼容几种命名）。"""
    if not isinstance(resp, dict):
        return None
    for k in keys:
        if resp.get(k) is not None:
            return resp[k]
    return None


def _do_dispatch(order_id, printer_id):
    """下发：MinIO 拉文件 → upload archive（复用已有 archive_id）→ add_to_queue → 建/更新 job。"""
    order = db.session.get(PrintOrderModel, order_id)
    if not order:
        return {"status": "no_order"}
    if order.status != PrintOrderModel.STATUS_READY_TO_PRINT:
        return {"status": "wrong_status", "order_status": order.status}

    f = (
        OrderFileModel.query
        .filter_by(order_id=order_id, file_type=OrderFileModel.FILE_SLICED)
        .first()
    )
    if not f:
        return {"status": "no_sliced_file"}

    adapter = BambuddyAdapter()
    job = BambuddyJobModel.query.filter_by(order_id=order_id).first()
    if not job:
        job = BambuddyJobModel(order_id=order_id, order_no=order.order_no)
        db.session.add(job)

    # archive：复用 job.bambuddy_archive_id，否则从 MinIO 拉 → upload
    if job.bambuddy_archive_id:
        archive_id = job.bambuddy_archive_id
    else:
        resp = storage.get_object(f.storage_key)
        try:
            data = resp.read()
        finally:
            resp.close()
        upload_resp = adapter.upload_archive(
            data, f.original_filename, f.content_type or "application/octet-stream"
        )
        archive_id = _extract_id(upload_resp, "id", "archive_id", "uid")
        if not archive_id:
            raise BambuddyError(f"upload_archive 无 id: {upload_resp}")

    queue_resp = adapter.add_to_queue(archive_id, printer_id)
    queue_id = _extract_id(queue_resp, "id", "queue_id", "uid")

    job.bambuddy_printer_id = printer_id
    job.bambuddy_archive_id = archive_id
    job.bambuddy_queue_id = queue_id
    job.filename = f.original_filename
    job.mapping_confidence = "exact"
    job.dispatched_at = datetime.now()
    db.session.commit()
    return {"status": "dispatched", "archive_id": archive_id, "queue_id": queue_id}


def _do_cancel(order_id):
    """取消下发：remove queue item + 清 job.queue_id。"""
    job = BambuddyJobModel.query.filter_by(order_id=order_id).first()
    if not job or not job.bambuddy_queue_id:
        return {"status": "no_active_dispatch"}
    adapter = BambuddyAdapter()
    adapter.remove_queue_item(job.bambuddy_queue_id)
    job.bambuddy_queue_id = None
    db.session.commit()
    return {"status": "cancelled"}
