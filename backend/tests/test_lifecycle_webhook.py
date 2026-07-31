"""Webhook 合成事件驱动 —— 秒级走完真实订单状态机（happy/fail/cancel + credit + 幂等）。

走真实事件核心：POST /internal/webhooks/bambuddy/<secret> → handle_print_event →
apply_event（状态转换 + credit 联动），全程无 Bambuddy / 无打印。

关键约束：idempotency_key 含 timestamp，每步事件必须用唯一 timestamp，
否则同 key 重放会返回 replayed 而不推进状态。

跑法：python -m pytest tests/test_lifecycle_webhook.py -v
"""
from decimal import Decimal

import pytest

from exts import db
from models import PrintOrderModel, PrintEventModel, CreditTransactionModel
from services import CreditService, OrderStateMachine
from conftest import balance_of, tx_count


def _webhook(client, app, payload):
    secret = app.config["WEBHOOK_SECRET"]
    return client.post(f"/internal/webhooks/bambuddy/{secret}", json=payload)


def _order(app, oid):
    return db.session.get(PrintOrderModel, oid)


def _print_events(app, oid, event_type=None):
    q = PrintEventModel.query.filter_by(order_id=oid)
    if event_type:
        q = q.filter_by(event_type=event_type)
    return q.count()


# ═══════════════════════════ Happy path ═══════════════════════════
def test_happy_path_webhook_drives_full_state_machine(app, make_user, make_ready_order):
    """print_started → progress(ignored) → print_complete → QC_PENDING → CLOSED + credit capture。"""
    uid = make_user(credit=100)
    oid = make_ready_order(uid, frozen=30, archive_id=999, printer_id=2)
    client = app.test_client()

    # 1) print_started → READY_TO_PRINT → PRINTING（无 credit 变动）
    r = _webhook(client, app, {"event_type": "print_started", "printer_id": 2,
                               "archive_id": 999, "timestamp": "T0"})
    assert r.get_json()["result"]["status"] == "applied"
    with app.app_context():
        assert _order(app, oid).status == PrintOrderModel.STATUS_PRINTING

    # 2) print_progress 无状态事件 → ignored，订单保持 PRINTING，进度不动（文档化边界）
    r = _webhook(client, app, {"event_type": "print_progress", "archive_id": 999,
                               "timestamp": "T1"})
    assert r.get_json()["result"]["status"] == "ignored"
    with app.app_context():
        order = _order(app, oid)
        assert order.status == PrintOrderModel.STATUS_PRINTING
        assert order.public_progress == 0

    # 3) print_complete → PRINT_COMPLETED + capture（frozen 30 实扣，available 保持 70）
    r = _webhook(client, app, {"event_type": "print_complete", "archive_id": 999,
                               "timestamp": "T2"})
    assert r.get_json()["result"]["status"] == "applied"
    with app.app_context():
        order = _order(app, oid)
        assert order.status == PrintOrderModel.STATUS_PRINT_COMPLETED
        assert order.frozen_credit == 0
        assert order.actual_credit == Decimal("30")
        bal = balance_of(app, uid)
        assert bal["available"] == Decimal("70")
        assert bal["frozen"] == Decimal("0")
        assert tx_count(app, uid) == 3  # grant + freeze + capture
        assert tx_count(app, uid, "capture") == 1
        assert _print_events(app, oid, "print_complete") == 1

    # 4) 幂等：同 timestamp 重发 → replayed，流水/事件不变
    r = _webhook(client, app, {"event_type": "print_complete", "archive_id": 999,
                               "timestamp": "T2"})
    assert r.get_json()["result"]["status"] == "replayed"
    with app.app_context():
        assert tx_count(app, uid) == 3
        assert _print_events(app, oid, "print_complete") == 1

    # 5) 质检收尾（无 webhook 事件，走 admin 状态机原语）→ CLOSED
    with app.app_context():
        order = _order(app, oid)
        OrderStateMachine.transition(order, PrintOrderModel.STATUS_QC_PENDING)
        OrderStateMachine.transition(order, PrintOrderModel.STATUS_CLOSED)
        assert order.public_status == "已完成"
        assert OrderStateMachine.is_terminal(order.status)


def test_webhook_unmatched_records_event_and_keeps_order(app, make_user, make_ready_order):
    """archive_id 不匹配 → unmatched，订单不变但事件落库（order_id=None）——安全网行为。"""
    uid = make_user(credit=100)
    oid = make_ready_order(uid, frozen=30, archive_id=999, printer_id=2)
    client = app.test_client()

    r = _webhook(client, app, {"event_type": "print_complete", "archive_id": 88888,
                               "timestamp": "T3"})
    assert r.get_json()["result"]["status"] == "unmatched"
    with app.app_context():
        order = _order(app, oid)
        assert order.status == PrintOrderModel.STATUS_READY_TO_PRINT  # 未动
        assert PrintEventModel.query.filter_by(order_id=None).count() == 1
        assert order.frozen_credit == Decimal("30")  # 未 capture


# ═══════════════════════════ Failure branch ═══════════════════════════
def test_failure_path_webhook_releases_then_refund(app, make_user, make_ready_order):
    """print_failed → PRINT_FAILED + release；经 NEED_REVIEW → REFUNDED → CLOSED 验证退款。"""
    uid = make_user(credit=100)
    oid = make_ready_order(uid, frozen=30, archive_id=999, printer_id=2)
    client = app.test_client()

    # 先 print_started 推进到 PRINTING（READY_TO_PRINT 不可直接转 PRINT_FAILED），再 failed
    r = _webhook(client, app, {"event_type": "print_started", "archive_id": 999,
                               "timestamp": "F-1"})
    assert r.get_json()["result"]["status"] == "applied"
    r = _webhook(client, app, {"event_type": "print_failed", "archive_id": 999,
                               "timestamp": "F0"})
    assert r.get_json()["result"]["status"] == "applied"
    with app.app_context():
        order = _order(app, oid)
        assert order.status == PrintOrderModel.STATUS_PRINT_FAILED
        assert order.frozen_credit == 0
        bal = balance_of(app, uid)
        assert bal["available"] == Decimal("100")  # release 退回 30
        assert bal["frozen"] == Decimal("0")
        assert tx_count(app, uid) == 3  # grant + freeze + release

    # 异常处理 → 退款 → 关闭（PRINT_FAILED 不能直接 REFUNDED，必须经 NEED_REVIEW）
    with app.app_context():
        order = _order(app, oid)
        OrderStateMachine.transition(order, PrintOrderModel.STATUS_NEED_REVIEW)
        CreditService.refund(uid, oid, 30, reason_code="dev_refund")
        OrderStateMachine.transition(order, PrintOrderModel.STATUS_REFUNDED)
        OrderStateMachine.transition(order, PrintOrderModel.STATUS_CLOSED)
        bal = balance_of(app, uid)
        assert bal["available"] == Decimal("130")  # 100 + refund 30
        assert tx_count(app, uid) == 4  # grant + freeze + release + refund
        assert tx_count(app, uid, "refund") == 1


# ═══════════════════════════ Cancel branch ═══════════════════════════
def test_cancel_branch_release_and_close(app, make_user, make_ready_order):
    """webhook 无取消事件 → 用客户/管理员取消同一组原语（release + CANCELLED）。"""
    uid = make_user(credit=100)
    oid = make_ready_order(uid, frozen=30, archive_id=999, printer_id=2)

    with app.app_context():
        order = _order(app, oid)
        CreditService.release(uid, oid, 30, version=1)
        OrderStateMachine.transition(order, PrintOrderModel.STATUS_CANCELLED)
        bal = balance_of(app, uid)
        assert bal["available"] == Decimal("100")  # release 退回 30
        assert bal["frozen"] == Decimal("0")
        assert order.public_status == "已取消"
        assert tx_count(app, uid) == 3  # grant + freeze + release
        assert tx_count(app, uid, "release") == 1

        OrderStateMachine.transition(order, PrintOrderModel.STATUS_CLOSED)
        assert OrderStateMachine.is_terminal(order.status)
