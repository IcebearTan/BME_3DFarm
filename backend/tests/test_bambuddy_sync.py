"""Phase 2 同步链测试（mock 驱动，不依赖 Bambuddy/打印机）。

覆盖：Webhook 完成实扣 / 幂等 / 失败释放 / 无匹配 / 错 secret / admin 绑定 / Poller 进度+完成检测。
"""
import uuid
from decimal import Decimal

import pytest

from exts import db
from models import (
    UserModel, PrintOrderModel, BambuddyJobModel, PrintEventModel, CreditAccountModel,
)
from services import CreditService, OrderStateMachine


# ── helper：建一个走到 PRINTING + 绑 job 的订单 ──
def _setup_printing_order(app, uid, frozen=30, archive_id=999):
    with app.app_context():
        CreditService.grant(uid, 100, source="test")
        order = PrintOrderModel(
            order_no=f"PO{uuid.uuid4().hex[:8]}", user_id=uid,
            status=PrintOrderModel.STATUS_DRAFT, material="PLA", quantity=1,
        )
        db.session.add(order)
        db.session.commit()
        order.estimated_credit = frozen
        # 走主线到 WAITING_CONFIRM，再 freeze + CREDIT_RESERVED → 审核 → PRINTING
        for s in (PrintOrderModel.STATUS_FILE_UPLOADED, PrintOrderModel.STATUS_QUOTING,
                  PrintOrderModel.STATUS_WAITING_CONFIRM):
            OrderStateMachine.transition(order, s)
        CreditService.freeze(uid, order.id, frozen, quote_version=1)
        OrderStateMachine.transition(order, PrintOrderModel.STATUS_CREDIT_RESERVED)
        order.frozen_credit = frozen
        for s in (PrintOrderModel.STATUS_REVIEWING, PrintOrderModel.STATUS_APPROVED,
                  PrintOrderModel.STATUS_READY_TO_PRINT, PrintOrderModel.STATUS_PRINTING):
            OrderStateMachine.transition(order, s)
        db.session.commit()
        job = BambuddyJobModel(
            order_id=order.id, order_no=order.order_no,
            bambuddy_printer_id=1, bambuddy_archive_id=archive_id,
            filename=f"{order.order_no}.gcode.3mf", mapping_confidence="manual",
        )
        db.session.add(job)
        db.session.commit()
        return order.id


def _balance(app, uid):
    with app.app_context():
        a = CreditAccountModel.query.filter_by(user_id=uid).first()
        return {"available": a.available_credit, "frozen": a.frozen_credit}


def _webhook(client, app, payload):
    secret = app.config["WEBHOOK_SECRET"]
    return client.post(f"/internal/webhooks/bambuddy/{secret}", json=payload)


# ═══════════════════════════ Webhook ═══════════════════════════
def test_webhook_complete_capture(app, make_user):
    """complete 事件 → 订单 PRINT_COMPLETED + capture（frozen 转 0）。"""
    uid = make_user()
    oid = _setup_printing_order(app, uid, frozen=30)
    client = app.test_client()

    r = _webhook(client, app, {"event_type": "complete", "archive_id": 999})
    assert r.get_json()["code"] == 200
    assert r.get_json()["result"]["status"] == "applied"

    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        assert order.status == PrintOrderModel.STATUS_PRINT_COMPLETED
        assert order.frozen_credit == 0
        assert order.actual_credit == Decimal("30")
        bal = _balance(app, uid)
        assert bal["available"] == Decimal("70")  # 100-30 freeze，capture actual=frozen 不退不补
        assert bal["frozen"] == Decimal("0")
        assert PrintEventModel.query.filter_by(order_id=oid, event_type="complete").count() == 1


def test_webhook_idempotent(app, make_user):
    """同事件重复 → replay，订单/余额不变，print_event 仍 1 条。"""
    uid = make_user()
    oid = _setup_printing_order(app, uid, frozen=30)
    client = app.test_client()

    _webhook(client, app, {"event_type": "complete", "archive_id": 999, "timestamp": "t1"})
    r2 = _webhook(client, app, {"event_type": "complete", "archive_id": 999, "timestamp": "t1"})
    assert r2.get_json()["result"]["status"] == "replayed"
    with app.app_context():
        assert PrintEventModel.query.filter_by(order_id=oid, event_type="complete").count() == 1


def test_webhook_failed_releases(app, make_user):
    """failed 事件 → PRINT_FAILED + release（frozen 回 available）。"""
    uid = make_user()
    oid = _setup_printing_order(app, uid, frozen=30)
    client = app.test_client()

    r = _webhook(client, app, {"event_type": "failed", "archive_id": 999})
    assert r.get_json()["result"]["status"] == "applied"
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        assert order.status == PrintOrderModel.STATUS_PRINT_FAILED
        bal = _balance(app, uid)
        assert bal["available"] == Decimal("100")  # release 退回 30
        assert bal["frozen"] == Decimal("0")


def test_webhook_unmatched_records_event(app, make_user):
    """archive_id 不匹配 → unmatched，订单不变但事件落库（order_id=None）。"""
    uid = make_user()
    oid = _setup_printing_order(app, uid, frozen=30, archive_id=999)
    client = app.test_client()

    r = _webhook(client, app, {"event_type": "complete", "archive_id": 88888})
    assert r.get_json()["result"]["status"] == "unmatched"
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        assert order.status == PrintOrderModel.STATUS_PRINTING  # 未变
        assert PrintEventModel.query.filter_by(order_id=None).count() == 1


def test_webhook_wrong_secret_404(app):
    client = app.test_client()
    r = client.post("/internal/webhooks/bambuddy/wrong-secret",
                    json={"event_type": "complete"})
    assert r.status_code == 404


# ═══════════════════════════ admin 绑定 ═══════════════════════════
def _register(client, app, email, role="customer"):
    r = client.post("/auth/register", json={"email": email, "password": "123", "username": email})
    token = r.get_json()["token"]
    with app.app_context():
        user = UserModel.query.filter_by(email=email).first()
        uid = user.id
        if role == "admin":
            user.role = "admin"
            db.session.commit()
    return uid, token


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_admin_bind_bambuddy(app):
    client = app.test_client()
    cuid, _ = _register(client, app, "cust@x.com")
    _, atoken = _register(client, app, "admin@x.com", role="admin")

    # 建 order（DB）
    with app.app_context():
        import uuid as _uuid
        order = PrintOrderModel(
            order_no=f"PO{_uuid.uuid4().hex[:8]}", user_id=cuid,
            status=PrintOrderModel.STATUS_DRAFT, material="PLA", quantity=1,
        )
        db.session.add(order); db.session.commit()
        oid = order.id

    # bind
    r = client.post(f"/admin/orders/{oid}/bind-bambuddy", headers=_h(atoken),
                    json={"bambuddy_printer_id": 3, "bambuddy_archive_id": 555,
                          "filename": "PO_x.gcode.3mf"})
    assert r.get_json()["code"] == 200
    assert r.get_json()["data"]["bambuddy_printer_id"] == 3
    assert r.get_json()["data"]["mapping_confidence"] == "manual"

    # 查
    r = client.get(f"/admin/orders/{oid}/bambuddy-job", headers=_h(atoken))
    assert r.get_json()["data"]["bambuddy_archive_id"] == 555

    # 再 bind（更新）
    r = client.post(f"/admin/orders/{oid}/bind-bambuddy", headers=_h(atoken),
                    json={"bambuddy_printer_id": 4})
    with app.app_context():
        assert BambuddyJobModel.query.filter_by(order_id=oid).count() == 1
        assert BambuddyJobModel.query.filter_by(order_id=oid).first().bambuddy_printer_id == 4


# ═══════════════════════════ Poller（mock adapter） ═══════════════════════════
def test_poller_updates_progress_and_completes(app, make_user, monkeypatch):
    """Poller sync_active_orders：mock adapter 返回 finish → 订单完成 + 进度 100。"""
    uid = make_user()
    oid = _setup_printing_order(app, uid, frozen=30)

    from tasks import poller

    def fake_status(self, printer_id):
        return {"gcode_state": "finish", "mc_percent": 100, "mc_remaining_time": 0}

    monkeypatch.setattr(poller.BambuddyAdapter, "get_printer_status", fake_status)

    with app.app_context():
        poller._do_sync()
        order = db.session.get(PrintOrderModel, oid)
        assert order.status == PrintOrderModel.STATUS_PRINT_COMPLETED
        assert order.public_progress == 100
        acct = CreditAccountModel.query.filter_by(user_id=uid).first()
        assert acct.frozen_credit == Decimal("0")  # capture 了


def test_poller_bambuddy_unreachable_no_crash(app, make_user, monkeypatch):
    """Bambuddy 不可达 → 单订单失败不崩，其他正常。"""
    uid = make_user()
    oid = _setup_printing_order(app, uid, frozen=30)

    from tasks import poller
    from bambuddy_adapter import BambuddyError

    def boom(self, printer_id):
        raise BambuddyError("unreachable")

    monkeypatch.setattr(poller.BambuddyAdapter, "get_printer_status", boom)

    with app.app_context():
        result = poller._do_sync()
        assert result["total"] >= 1
        order = db.session.get(PrintOrderModel, oid)
        assert order.status == PrintOrderModel.STATUS_PRINTING  # 未变
