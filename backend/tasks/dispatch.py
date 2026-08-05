"""Phase 3 半自动调度：管理员一键下发 Bambuddy（upload archive + add_to_queue）。

dispatch 只管下发 + 建/更新 BambuddyJob，不改订单状态——print_started 由 Phase 2 Webhook/Poller
处理 READY_TO_PRINT → PRINTING，避免状态机冲突。
_do_dispatch / _do_cancel 纯函数，测试绕 celery（同 Poller 模式）；admin 接口直接调纯函数（同步）。
路径/字段以 Bambuddy 实测为准（本轮假设 POST /archives/ + POST /queue/ + DELETE /queue/{id}）。
"""
from datetime import datetime

from celery_app import celery
from exts import db
from models import PrintOrderModel, OrderFileModel, BambuddyJobModel, PrinterModel
from bambuddy_adapter import BambuddyAdapter, BambuddyError
from services.storage import storage


@celery.task(
    autoretry_for=(Exception,), retry_backoff=True, max_retries=3, retry_backoff_max=60
)
def dispatch_order(order_id, printer_id, ams_mapping=None):
    """Celery 入口（生产 apply_async 异步；admin/测试可直调 _do_dispatch）。"""
    return _do_dispatch(order_id, printer_id, ams_mapping=ams_mapping)


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


def _queue_item_reusable(adapter, queue_id):
    """旧 Bambuddy 队列项是否已无效（可重新下发）。

    终态(failed/completed/cancelled)或已不存在(404) → True；
    仍在(pending/printing)或查询出错 → False（保守不重发，避免重复打印）。
    """
    try:
        qi = adapter.get_queue_item(queue_id)
    except BambuddyError as e:
        return "404" in str(e)   # 队列项已不存在 → 可重发；其他错误保守拦截
    if not isinstance(qi, dict):
        return False
    return qi.get("status") in ("failed", "completed", "cancelled", "canceled")


def _to_bambuddy_mapping(ams_mapping, printer_id=None):
    """内部 1-based tray_id → Bambuddy 取料值（off-by-one：v → v−1，转 0-based）。

    Bambuddy/Bambu 的 ams_mapping 用 0-based tray id（0..N-1）；仓库内部 tray_id 是数组
    下标+1（1-based）。实测证据：4 槽 AMS 上发值 4 越界报 0500-4004，而历史任务
    ams_mapping=[3]、[2,1] 均 completed（值 1/2/3 正常）→ 取值范围 0-3。故下发前每个值 −1。
    job.ams_mapping 仍存内部 1-based（监控页"使用中"高亮按内部 tray_id 匹配，不能改）。
    """
    if not isinstance(ams_mapping, list) or not ams_mapping:
        return ams_mapping
    return [int(v) - 1 for v in ams_mapping]


def _do_dispatch(order_id, printer_id, ams_mapping=None):
    """下发：MinIO 拉文件 → upload archive（复用已有 archive_id）→ add_to_queue → 建/更新 job。

    ams_mapping 透传给 add_to_queue（admin 手选或自动匹配的 AMS 料盘映射），并持久化到 job。
    """
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
    elif job.bambuddy_queue_id:
        # 幂等：已下发过（queue_id 已设）就不重复 add_to_queue，避免重复打印。
        # 但若 Bambuddy 那边旧队列项已无效（failed/completed/cancelled 或已不存在），
        # 视为可重发——清掉 queue_id 走下面的正常下发（archive 复用，不重传），
        # 避免一次"启动前失败"（如 SD 卡问题）把订单永久卡死、再下发只能 already_dispatched。
        if _queue_item_reusable(adapter, job.bambuddy_queue_id):
            job.bambuddy_queue_id = None
            db.session.commit()
        else:
            return {"status": "already_dispatched",
                    "queue_id": job.bambuddy_queue_id,
                    "archive_id": job.bambuddy_archive_id}

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

    # ams_mapping 内部 1-based → Bambuddy 0-based（off-by-one，v→v−1）。
    # job.ams_mapping 仍存内部 1-based（监控页"使用中"高亮按内部 tray_id 匹配，不能动）。
    bambuddy_mapping = _to_bambuddy_mapping(ams_mapping, printer_id)
    queue_resp = adapter.add_to_queue(archive_id, printer_id, ams_mapping=bambuddy_mapping)
    queue_id = _extract_id(queue_resp, "id", "queue_id", "uid")

    job.bambuddy_printer_id = printer_id
    job.bambuddy_archive_id = archive_id
    job.bambuddy_queue_id = queue_id
    job.ams_mapping = ams_mapping
    job.filename = f.original_filename
    job.mapping_confidence = "exact"
    job.dispatched_at = datetime.now()
    db.session.commit()
    return {"status": "dispatched", "archive_id": archive_id,
            "queue_id": queue_id, "ams_mapping": ams_mapping}


def _do_cancel(order_id):
    """取消下发：优先 POST /queue/{id}/cancel（妥善停止，不易制造 FAILED），失败回退 DELETE。"""
    job = BambuddyJobModel.query.filter_by(order_id=order_id).first()
    if not job or not job.bambuddy_queue_id:
        return {"status": "no_active_dispatch"}
    adapter = BambuddyAdapter()
    try:
        adapter.cancel_queue_item(job.bambuddy_queue_id)
    except BambuddyError:
        # cancel 端点不可用就回退到硬删除
        try:
            adapter.remove_queue_item(job.bambuddy_queue_id)
        except BambuddyError:
            pass  # queue 项已不在也视为已取消
    job.bambuddy_queue_id = None
    job.completed_at = datetime.now()  # 取消也算结束：监控页用 completed_at IS NULL 判活跃，填上后立即不再当"当前任务"
    db.session.commit()
    return {"status": "cancelled"}
