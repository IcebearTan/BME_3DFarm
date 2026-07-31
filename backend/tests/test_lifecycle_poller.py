"""Poller 序列驱动 —— 真实 HTTP 路径跑 _do_sync/_do_sync_printers，秒级走完生命周期。

replay 代理喂序列 fixture，每次 poller 轮询推进一档状态（10 分钟打印压缩成 4 次轮询）：
  seq_print/          —— GET printers/{id}/status 的 .000~.003（running 5%→42%→85%→finish），
                        4 次 _do_sync() 把订单从 READY_TO_PRINT 走到 PRINT_COMPLETED + credit capture
  seq_printers_sync/  —— 真机+虚拟打印机行、AMS 克数富化（inventory 真值替代硬件 -1）
故障注入：faults 让 printers/{id}/status 返回 500 → _do_sync 不崩、synced=0、回滚。

跑法：python -m pytest tests/test_lifecycle_poller.py -v
"""
import os
from decimal import Decimal

from exts import db
from models import PrintOrderModel, PrinterModel, BambuddyJobModel
from conftest import FIXTURES_DIR, balance_of, tx_count, point_bambuddy
from tasks import poller


def _order(app, oid):
    return db.session.get(PrintOrderModel, oid)


def _run_sync(app):
    with app.app_context():
        return poller._do_sync()


def test_poller_sequence_drives_order_to_completion(app, make_user, make_ready_order, replay_proxy):
    """seq_print 序列：4 次 _do_sync 走完 READY_TO_PRINT → PRINT_COMPLETED + capture。"""
    uid = make_user(credit=100)
    oid = make_ready_order(uid, frozen=30, archive_id=999, printer_id=2)
    p = replay_proxy(os.path.join(FIXTURES_DIR, "seq_print"))
    point_bambuddy(app, p["base_url"])

    # 第 1 轮 running 5% → 状态翻转 PRINTING；progress 仍 0（poller quirk：转换在进度块之后）
    _run_sync(app)
    with app.app_context():
        order = _order(app, oid)
        assert order.status == PrintOrderModel.STATUS_PRINTING
        assert order.public_progress == 0
        assert BambuddyJobModel.query.filter_by(order_id=oid).first().started_at is not None

    # 第 2 轮 running 42% / 剩 3min → progress 42、remaining_seconds 180
    _run_sync(app)
    with app.app_context():
        order = _order(app, oid)
        assert order.status == PrintOrderModel.STATUS_PRINTING
        assert order.public_progress == 42
        assert order.remaining_seconds == 180

    # 第 3 轮 running 85% / 剩 1min → progress 85
    _run_sync(app)
    with app.app_context():
        assert _order(app, oid).public_progress == 85

    # 第 4 轮 finish 100% → PRINT_COMPLETED + capture（frozen 0 / available 70 / 3 条流水）
    _run_sync(app)
    with app.app_context():
        order = _order(app, oid)
        assert order.status == PrintOrderModel.STATUS_PRINT_COMPLETED
        assert order.public_progress == 100
        assert order.frozen_credit == 0
        assert order.actual_credit == Decimal("30")
        assert BambuddyJobModel.query.filter_by(order_id=oid).first().completed_at is not None
        bal = balance_of(app, uid)
        assert bal["available"] == Decimal("70")
        assert tx_count(app, uid) == 3  # grant + freeze + capture

    # 越界重放（重复 finish）→ 幂等安全：不报错、不重复计费
    _run_sync(app)
    with app.app_context():
        assert _order(app, oid).status == PrintOrderModel.STATUS_PRINT_COMPLETED
        assert tx_count(app, uid) == 3


def test_poller_printers_sync_enriches_ams(app, replay_proxy):
    """seq_printers_sync：真机+虚拟打印机行、AMS 克数富化、虚拟 idle。"""
    p = replay_proxy(os.path.join(FIXTURES_DIR, "seq_printers_sync"))
    point_bambuddy(app, p["base_url"])

    with app.app_context():
        result = poller._do_sync_printers()
        assert result["printers_synced"] == 2

        real = PrinterModel.query.filter_by(source="real", bambuddy_printer_id=10).first()
        assert real is not None
        assert real.status == PrinterModel.STATUS_PRINTING
        tray = real.status_detail["ams"][0]["tray"][0]
        assert tray["remain_g"] == 850.5
        assert tray["label_weight_g"] == 1000
        assert tray["remain"] == 85  # round(850.5/1000*100)，覆盖硬件恒 -1

        vp = PrinterModel.query.filter_by(source="virtual", bambuddy_printer_id=1).first()
        assert vp is not None
        assert vp.status == PrinterModel.STATUS_IDLE  # 序列 .001 idle
        assert vp.enabled is True


def test_poller_survives_fault_injection(app, make_user, make_ready_order, replay_proxy):
    """faults 让 printers/{id}/status 返回 500 → _do_sync 不崩、synced=0、订单回滚未动。"""
    uid = make_user(credit=100)
    oid = make_ready_order(uid, frozen=30, archive_id=999, printer_id=2)
    p = replay_proxy(os.path.join(FIXTURES_DIR, "live"),
                     faults={"printers/{id}/status": {"status": 500}})
    point_bambuddy(app, p["base_url"])

    with app.app_context():
        result = poller._do_sync()
        assert result["synced"] == 0
        assert result["total"] >= 1
        order = _order(app, oid)
        assert order.status == PrintOrderModel.STATUS_READY_TO_PRINT  # 未变
        assert order.frozen_credit == Decimal("30")  # 未被 capture/release
