"""Bambuddy 打印事件 → 订单状态同步（Webhook + Poller 共用核心）。

事件流：幂等落 print_event → 匹配 BambuddyJob → 映射订单状态 → transition + credit 联动。
Webhook（Bambuddy 被动推）和 Poller（主动拉）都调 handle_print_event，逻辑统一、不旁路。
credit + transition 在单事务（复用 _commit=False 模式），任一步失败全回滚。
"""
from exts import db
from models import PrintEventModel, BambuddyJobModel, PrintOrderModel
from services import (
    OrderStateMachine, CreditService,
    InsufficientCreditError, InvalidTransitionError,
)

# 事件 → 订单目标状态。不在此表的事件（progress 等）忽略状态转换，由调用方更新进度。
EVENT_STATE_MAP = {
    "print_started": PrintOrderModel.STATUS_PRINTING,
    "started": PrintOrderModel.STATUS_PRINTING,
    "complete": PrintOrderModel.STATUS_PRINT_COMPLETED,
    "completed": PrintOrderModel.STATUS_PRINT_COMPLETED,
    "print_complete": PrintOrderModel.STATUS_PRINT_COMPLETED,
    "failed": PrintOrderModel.STATUS_PRINT_FAILED,
    "print_failed": PrintOrderModel.STATUS_PRINT_FAILED,
}


def match_job(printer_id=None, archive_id=None, filename=None, order_no=None):
    """匹配 BambuddyJob：archive_id 精确优先 → order_no → filename 模糊兜底 → 同打印机未完成 job。"""
    if archive_id:
        j = BambuddyJobModel.query.filter_by(bambuddy_archive_id=archive_id).first()
        if j:
            return j
    if order_no:
        j = BambuddyJobModel.query.filter_by(order_no=order_no).first()
        if j:
            return j
    if filename:
        j = BambuddyJobModel.query.filter(
            BambuddyJobModel.filename.ilike(f"%{filename}%")
        ).first()
        if j:
            return j
    if printer_id:
        # 低置信度兜底：同打印机 + 未开始的 job
        j = BambuddyJobModel.query.filter_by(
            bambuddy_printer_id=printer_id, bambuddy_status=None
        ).first()
        if j:
            return j
    return None


def apply_event(order, event_type, actor_id=None):
    """根据事件 transition 订单 + credit 联动。返回 True（已应用）/ False（无需或失败）。"""
    target = EVENT_STATE_MAP.get(event_type)
    if target is None:
        return False  # 非状态事件（progress）
    if order.status == target:
        return False  # 已在目标状态

    try:
        if target == PrintOrderModel.STATUS_PRINT_COMPLETED:
            frozen = order.frozen_credit or 0
            if frozen and frozen > 0:
                CreditService.capture(order.user_id, order.id, frozen, _commit=False)
            OrderStateMachine.transition(
                order, target, actor_id=actor_id, source="webhook", _commit=False
            )
            order.actual_credit = frozen
            order.frozen_credit = 0
            db.session.commit()
        elif target == PrintOrderModel.STATUS_PRINT_FAILED:
            if order.frozen_credit and order.frozen_credit > 0:
                CreditService.release(
                    order.user_id, order.id, order.frozen_credit, _commit=False
                )
            OrderStateMachine.transition(
                order, target, actor_id=actor_id, source="webhook", _commit=False
            )
            order.frozen_credit = 0
            db.session.commit()
        else:  # print_started → PRINTING（无 credit）
            OrderStateMachine.transition(
                order, target, actor_id=actor_id, source="webhook"
            )
        return True
    except (InsufficientCreditError, InvalidTransitionError):
        db.session.rollback()
        return False


def handle_print_event(*, source, event_type, printer_id=None, archive_id=None,
                       filename=None, order_no=None, timestamp=None, payload=None):
    """处理一个打印事件：幂等落 print_event → 匹配 job → 更新订单。

    返回 {status: replayed|applied|unmatched|no_order|transition_failed|ignored, ...}
    """
    idempotency_key = "|".join(
        str(x) for x in (source, event_type, printer_id, archive_id, filename, timestamp)
    )

    # 幂等：同 key 已存在 → replay
    if PrintEventModel.query.filter_by(idempotency_key=idempotency_key).first():
        return {"status": "replayed"}

    job = match_job(printer_id, archive_id, filename, order_no)
    order = db.session.get(PrintOrderModel, job.order_id) if job else None

    applied = False
    fail_reason = None
    if order:
        try:
            applied = apply_event(order, event_type)
        except Exception as e:  # 兜底，不让异常冒到 Webhook 500
            fail_reason = str(e)

    # 落 print_event（无论匹配与否；无匹配的留待人工绑定后回溯）
    db.session.add(PrintEventModel(
        order_id=order.id if order else None,
        bambuddy_job_id=job.id if job else None,
        source=source,
        event_type=event_type,
        raw_payload=payload if isinstance(payload, dict) else (
            {"raw": str(payload)} if payload else None
        ),
        normalized_payload={
            "printer_id": printer_id, "archive_id": archive_id,
            "filename": filename, "order_no": order_no, "timestamp": timestamp,
        },
        idempotency_key=idempotency_key,
    ))
    db.session.commit()

    if not job:
        return {"status": "unmatched"}
    if not order:
        return {"status": "no_order"}
    if fail_reason:
        return {"status": "transition_failed", "reason": fail_reason}
    # 完成通知（局部 import 避免循环；SMTP 未配自动跳过，失败不阻塞）
    if applied and order.status == PrintOrderModel.STATUS_PRINT_COMPLETED:
        try:
            from services.mailer import send_completion_notice
            send_completion_notice(order)
        except Exception:
            pass
    return {"status": "applied" if applied else "ignored", "order_status": order.status}
