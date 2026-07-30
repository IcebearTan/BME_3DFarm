"""Poller：定时同步活跃订单状态（Celery Beat 每 30s 触发）。

查 PRINTING + 已绑 BambuddyJob 的订单 → 调 adapter 读 printer_status → 更新进度 + 检测完成/失败。
是 Webhook 的兜底：Webhook 丢了/延迟，Poller 还能补；Bambuddy 不可达不崩。

同步逻辑抽成 _do_sync() 纯函数，Celery 入口（sync_active_orders）只做包装，测试可绕过 celery 直调。
gcode_state → 事件映射以 Bambuddy 实测为准（保守，本轮只更新进度 + 明确的 failed/finish）。
"""
from datetime import datetime

from celery_app import celery
from exts import db
from models import PrintOrderModel, BambuddyJobModel, PrinterModel
from bambuddy_adapter import BambuddyAdapter, BambuddyError
from services.bambuddy_sync import apply_event


@celery.task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
    retry_backoff_max=60,
)
def sync_active_orders():
    """Celery 入口（beat 每 30s 触发）。"""
    return _do_sync()


def _do_sync():
    """同步所有 PRINTING + 已绑 BambuddyJob 的订单。返回 {synced, total}。"""
    jobs = (
        db.session.query(BambuddyJobModel)
        .join(PrintOrderModel, PrintOrderModel.id == BambuddyJobModel.order_id)
        .filter(PrintOrderModel.status.in_([
            PrintOrderModel.STATUS_PRINTING,
            PrintOrderModel.STATUS_READY_TO_PRINT,
        ]))
        .all()
    )
    if not jobs:
        return {"synced": 0, "total": 0}

    adapter = BambuddyAdapter()
    synced = 0
    for job in jobs:
        if not job.bambuddy_printer_id:
            continue
        try:
            status = adapter.get_printer_status(job.bambuddy_printer_id)
            order = db.session.get(PrintOrderModel, job.order_id)
            if order:
                _apply_printer_status(order, status, job)
                synced += 1
        except BambuddyError:
            # 单台/单订单失败不拖垮整轮
            db.session.rollback()
            continue
    return {"synced": synced, "total": len(jobs)}


def _apply_printer_status(order, status, job=None):
    """根据 Bambuddy printer_status 更新订单进度 + 推进状态机。

    Bambuddy printer_status 典型字段：gcode_state / mc_percent / mc_remaining_time。
    gcode_state → 事件映射：running 类→print_started（兜底 READY_TO_PRINT→PRINTING，
    因 Bambuddy 不推送 print_started 事件）、finish/succeeded→complete、failed→failed。
    非法转换（如 READY_TO_PRINT→PRINT_COMPLETED 跳级）由 OrderStateMachine 拦截。
    job 起止时间在此回填（apply_event 只转订单状态、不动 BambuddyJob）。
    """
    if not isinstance(status, dict):
        return

    gcode_state = (status.get("gcode_state") or status.get("state") or "").lower()
    mc_percent = status.get("mc_percent") or status.get("progress")
    mc_remaining = status.get("mc_remaining_time") or status.get("mc_remaining") or status.get("remaining_time")

    # 进度/剩余时间只在 PRINTING 时有意义（READY_TO_PRINT 尚未开打）
    if order.status == PrintOrderModel.STATUS_PRINTING:
        if mc_percent is not None:
            try:
                order.public_progress = max(0, min(100, int(float(mc_percent))))
            except (ValueError, TypeError):
                pass
        if mc_remaining is not None:
            try:
                # mc_remaining 来自 Bambuddy remaining_time，单位是分钟；字段名是 seconds → 换算
                order.remaining_seconds = int(float(mc_remaining) * 60)
            except (ValueError, TypeError):
                pass

    state_event = {
        "running": "print_started",
        "busy": "print_started",
        "preparing": "print_started",
        "pause": "print_started",
        "paused": "print_started",
        "finish": "complete",
        "finished": "complete",
        "succeeded": "complete",
        "failed": "failed",
    }.get(gcode_state)
    if state_event:
        applied = apply_event(order, state_event)
        # 回填 job 起止时间（apply_event 不动 BambuddyJob）
        if applied and job is not None:
            now = datetime.now()
            if order.status == PrintOrderModel.STATUS_PRINTING and not job.started_at:
                job.started_at = now
            elif order.status == PrintOrderModel.STATUS_PRINT_COMPLETED and not job.completed_at:
                job.completed_at = now

    db.session.commit()


# ─────────────────────────────────────────────────────────────────
# 打印机状态同步（Phase 4.5）—— 真机 + Virtual Printer → printer 表
# ─────────────────────────────────────────────────────────────────
@celery.task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
    retry_backoff_max=60,
)
def sync_printers():
    """Celery 入口（beat 每 30s 触发）。"""
    return _do_sync_printers()


def _map_printer_status(status_data):
    """Bambuddy status → PrinterModel status 枚举（表达「当前可用性」）。

    Bambu 的 gcode_state 是历史粘性状态：FAILED/FINISH 反映「上次结果」、不会自动回 IDLE。
    故以 HMS 报警区分真故障：FAILED 且有 HMS→ERROR（停用）；FAILED 无 HMS→IDLE（取消/失败
    残留，机器物理可用，下一个真实下单下发时打印机会自动从 FAILED 恢复）。
    上次结果/温度/HMS 等细节另存 status_detail，不混进 status。
    """
    if isinstance(status_data, dict):
        gs = (status_data.get("gcode_state") or status_data.get("state") or "").lower()
        if gs in ("running", "busy", "preparing", "pause", "paused"):
            return PrinterModel.STATUS_PRINTING
        if gs in ("idle", "finish", "finished", "standby"):
            return PrinterModel.STATUS_IDLE
        if gs in ("failed", "error", "fault"):
            # 有 HMS = 真硬件故障→停用；无 HMS = 取消/失败残留→可用（新单顶掉即恢复）
            if status_data.get("hms_errors"):
                return PrinterModel.STATUS_ERROR
            return PrinterModel.STATUS_IDLE
        if status_data.get("running") is True:
            return PrinterModel.STATUS_PRINTING
    return PrinterModel.STATUS_OFFLINE


def _enrich_ams_weights(status, bambuddy_pid, assign_map, adapter):
    """把 Bambuddy inventory 的真实剩余克数/标重写进 status.ams[*].tray[*]。

    Bambu 硬件 tray.remain 恒为 -1（不上报），前端拿不到重量。Bambuddy 的 inventory
    系统才有真实值：inventory-remain 给每槽剩余克数，assignments 给料盘标重(label_weight)。
    每个 tray 填：remain_g（剩余克数）、label_weight_g（标重）、remain（百分比，供进度条）。
    inventory 不可达/无数据时静默跳过，tray 维持原值。
    """
    if not isinstance(status, dict):
        return
    ams = status.get("ams")
    if not isinstance(ams, list) or not ams:
        return
    try:
        remain_map = adapter.get_inventory_remain(bambuddy_pid)
    except BambuddyError:
        remain_map = {}
    for unit in ams:
        if not isinstance(unit, dict):
            continue
        for tray in (unit.get("tray") or []):
            if not isinstance(tray, dict):
                continue
            slot = tray.get("id")
            if slot is None:
                continue
            try:
                slot_i = int(slot)
            except (TypeError, ValueError):
                continue
            remain_g = remain_map.get(slot_i) if remain_map else None
            if remain_g is None:
                continue
            spool = assign_map.get((bambuddy_pid, slot_i)) or {}
            label_w = spool.get("label_weight")
            base = label_w if (isinstance(label_w, (int, float)) and label_w > 0) else 1000
            tray["remain_g"] = round(remain_g, 1)
            tray["label_weight_g"] = int(base)
            # remain 改用 inventory 算的百分比（覆盖硬件的 -1），供前端进度条
            tray["remain"] = max(0, min(100, round(remain_g / base * 100)))


def _do_sync_printers():
    """同步 Bambuddy 真机 + Virtual Printer → printer 表（status + status_detail）。"""
    adapter = BambuddyAdapter()
    items = []
    # 真机（/printers/）
    try:
        for p in (adapter.list_printers() or []):
            items.append(("real", p))
    except BambuddyError:
        pass
    # Virtual Printer（/virtual-printers）
    try:
        vp = adapter._request("GET", "/virtual-printers")
        for p in (vp.get("printers", []) if isinstance(vp, dict) else []):
            items.append(("virtual", p))
    except BambuddyError:
        pass

    # AMS 料盘分配索引（一次性拉全量）：{(bambuddy_printer_id, tray_id): spool}
    # 给各 tray 补标重(label_weight)/颜色名；失败则空，标重降级默认 1000g。
    assign_map = {}
    try:
        for a in adapter.list_inventory_assignments():
            pid_a, tid = a.get("printer_id"), a.get("tray_id")
            if pid_a is not None and tid is not None:
                assign_map[(pid_a, tid)] = a.get("spool") or {}
    except BambuddyError:
        pass

    for source, p in items:
        pid = p.get("id")
        if pid is None:
            continue
        row = PrinterModel.query.filter_by(
            bambuddy_printer_id=pid, source=source
        ).first()
        if not row:
            row = PrinterModel(
                public_name=p.get("name") or f"Printer-{pid}",
                bambuddy_printer_id=pid,
                source=source,
                model=p.get("model_name") or p.get("model") or "P1S",
            )
            db.session.add(row)
        # /printers/ 列表只含元数据、不含实时 status，须单独调 /printers/{id}/status
        try:
            st = adapter.get_printer_status(pid) or {}
        except BambuddyError:
            st = {}
        # 给 AMS 各 tray 补真实剩余克数/标重/百分比（Bambu 硬件 remain 恒 -1）
        _enrich_ams_weights(st, pid, assign_map, adapter)
        row.status = _map_printer_status(st)
        row.status_detail = st or None
        row.last_seen_at = datetime.now()
        if source == "virtual":
            row.enabled = bool(p.get("enabled", True))
    db.session.commit()
    return {"printers_synced": len(items)}
