"""CreditService 测试套件。

单线程用例覆盖 6 个方法的正确性 + 幂等 + 边界。
并发用例（命门）证明 FOR UPDATE 真的防住超扣/重复加钱。

并发实现：threading.Barrier 让线程同刻释放最大化竞争；每线程独立 app context
（Flask-SQLAlchemy 的 session 不能跨线程共享）；真实 commit 落库。
"""
import threading
from decimal import Decimal

import pytest

from exts import db
from models import CreditTransactionModel
from services import (
    CreditService,
    InvalidAmountError,
    AccountNotFoundError,
    InsufficientCreditError,
)
from conftest import balance_of, tx_count


# ═══════════════════════════ 单线程：只读 ═══════════════════════════
def test_get_balance_empty_for_new_user(app, make_user):
    uid = make_user()
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("0")
    assert bal["frozen"] == Decimal("0")
    assert bal["total"] == Decimal("0")


# ═══════════════════════════ 单线程：grant ═══════════════════════════
def test_grant_increases_balance_and_records_snapshot(app, make_user):
    uid = make_user()
    with app.app_context():
        res = CreditService.grant(uid, 100, source="admin_grant", reason="初始发放")
    assert res["replayed"] is False
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("100")
    # 流水快照：before 0 -> after 100
    with app.app_context():
        txn = CreditTransactionModel.query.filter_by(user_id=uid).one()
        assert txn.type == "grant"
        assert txn.amount == Decimal("100")
        assert txn.before_available == Decimal("0")
        assert txn.after_available == Decimal("100")
        assert txn.before_frozen == Decimal("0")
        assert txn.after_frozen == Decimal("0")
        assert txn.source == "admin_grant"


def test_grant_idempotent_same_key(app, make_user):
    uid = make_user()
    key = "grant:camp-reward:42"
    with app.app_context():
        r1 = CreditService.grant(uid, 50, source="training_camp",
                                  reason="训练营奖励", idempotency_key=key)
        r2 = CreditService.grant(uid, 50, source="training_camp",
                                  reason="训练营奖励", idempotency_key=key)
    assert r1["replayed"] is False
    assert r2["replayed"] is True
    assert r1["transaction_id"] == r2["transaction_id"]
    # 只加了一次
    assert balance_of(app, uid)["available"] == Decimal("50")
    assert tx_count(app, uid) == 1


def test_grant_invalid_amount(app, make_user):
    uid = make_user()
    for bad in (0, -10, "abc", None):
        with app.app_context():
            with pytest.raises(InvalidAmountError):
                CreditService.grant(uid, bad)


# ═══════════════════════════ 单线程：freeze ═══════════════════════════
def test_freeze_moves_credit(app, make_user, make_order):
    uid = make_user(credit=100)
    oid = make_order(uid)
    with app.app_context():
        CreditService.freeze(uid, oid, 30, quote_version=1)
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("70")
    assert bal["frozen"] == Decimal("30")


def test_freeze_insufficient(app, make_user, make_order):
    uid = make_user(credit=20)
    oid = make_order(uid)
    with app.app_context():
        with pytest.raises(InsufficientCreditError):
            CreditService.freeze(uid, oid, 50, quote_version=1)
    # 零变动
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("20")
    assert bal["frozen"] == Decimal("0")
    assert tx_count(app, uid) == 1  # 只有 test_setup 的 grant


def test_freeze_idempotent(app, make_user, make_order):
    uid = make_user(credit=100)
    oid = make_order(uid)
    with app.app_context():
        r1 = CreditService.freeze(uid, oid, 30, quote_version=1)
        r2 = CreditService.freeze(uid, oid, 30, quote_version=1)
    assert r1["replayed"] is False
    assert r2["replayed"] is True
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("70")
    assert bal["frozen"] == Decimal("30")
    assert tx_count(app, uid, type_filter="freeze") == 1


def test_freeze_account_not_found(app):
    # 没建账户的用户直接 freeze → AccountNotFoundError（订单操作前应已建账）
    with app.app_context():
        with pytest.raises(AccountNotFoundError):
            CreditService.freeze(999999, 1, 10, quote_version=1)


# ═══════════════════════════ 单线程：capture（多退少补） ═══════════════════════════
def _freeze_for_capture(app, uid, oid, amount):
    with app.app_context():
        CreditService.freeze(uid, oid, amount, quote_version=1)


def test_capture_by_frozen_amount(app, make_user, make_order):
    uid = make_user(credit=100)
    oid = make_order(uid)
    _freeze_for_capture(app, uid, oid, 30)
    with app.app_context():
        CreditService.capture(uid, oid, frozen_amount=30)  # actual=None → 按 frozen
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("70")
    assert bal["frozen"] == Decimal("0")


def test_capture_actual_less_refunds_diff(app, make_user, make_order):
    uid = make_user(credit=100)
    oid = make_order(uid)
    _freeze_for_capture(app, uid, oid, 30)
    with app.app_context():
        CreditService.capture(uid, oid, frozen_amount=30, actual_credit=20)
    # 冻结 30，实际 20 → 退 10 到 available
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("80")  # 70 + 10
    assert bal["frozen"] == Decimal("0")
    # 流水 amount 记 settle（实际用量）
    with app.app_context():
        txn = CreditTransactionModel.query.filter_by(
            user_id=uid, type="capture").one()
        assert txn.amount == Decimal("20")


def test_capture_actual_more_supplements(app, make_user, make_order):
    uid = make_user(credit=100)
    oid = make_order(uid)
    _freeze_for_capture(app, uid, oid, 30)
    with app.app_context():
        CreditService.capture(uid, oid, frozen_amount=30, actual_credit=50)
    # 冻结 30，实际 50 → 从 available 补扣 20
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("50")  # 70 - 20
    assert bal["frozen"] == Decimal("0")


def test_capture_actual_more_insufficient(app, make_user, make_order):
    uid = make_user(credit=40)  # grant 40
    oid = make_order(uid)
    _freeze_for_capture(app, uid, oid, 30)  # available=10, frozen=30
    with app.app_context():
        with pytest.raises(InsufficientCreditError):
            # 实际 50 → 补扣 20，但 available 只有 10
            CreditService.capture(uid, oid, frozen_amount=30, actual_credit=50)
    # 零变动
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("10")
    assert bal["frozen"] == Decimal("30")


# ═══════════════════════════ 单线程：release / refund ═══════════════════════════
def test_release_unfreezes(app, make_user, make_order):
    uid = make_user(credit=100)
    oid = make_order(uid)
    _freeze_for_capture(app, uid, oid, 30)
    with app.app_context():
        CreditService.release(uid, oid, amount=30, reason="取消订单", version=1)
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("100")
    assert bal["frozen"] == Decimal("0")


def test_refund_increases_available(app, make_user, make_order):
    uid = make_user(credit=100)
    oid = make_order(uid)
    _freeze_for_capture(app, uid, oid, 30)
    with app.app_context():
        CreditService.capture(uid, oid, frozen_amount=30)  # available=70
        CreditService.refund(uid, oid, amount=15, reason_code="print_defect",
                              version=1)
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("85")  # 70 + 15


# ═══════════════════════════ 单线程：adjust ═══════════════════════════
def test_adjust_up_and_down(app, make_user):
    uid = make_user(credit=100)
    with app.app_context():
        CreditService.adjust(uid, 50, reason="补发", operator_id=uid)
    assert balance_of(app, uid)["available"] == Decimal("150")
    with app.app_context():
        CreditService.adjust(uid, -30, reason="纠错扣减", operator_id=uid)
    assert balance_of(app, uid)["available"] == Decimal("120")


def test_adjust_down_insufficient(app, make_user):
    uid = make_user(credit=20)
    with app.app_context():
        with pytest.raises(InsufficientCreditError):
            CreditService.adjust(uid, -50, reason="扣太多", operator_id=uid)
    assert balance_of(app, uid)["available"] == Decimal("20")


def test_adjust_zero_rejected(app, make_user):
    uid = make_user()
    with app.app_context():
        with pytest.raises(InvalidAmountError):
            CreditService.adjust(uid, 0, reason="零调整", operator_id=uid)


# ═══════════════════════════ 并发命门 ═══════════════════════════
def test_concurrent_freeze_no_overspend(app, make_user, make_order):
    """100 credit，10 线程各 freeze 20（不同 order）→ 恰好 5 成功 5 失败，
    最终 available=0 / frozen=100，freeze 流水恰好 5 笔。
    若 FOR UPDATE 失效会超扣（frozen>100 或成功数>5）。"""
    uid = make_user(credit=100)
    order_ids = [make_order(uid) for _ in range(10)]

    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def worker(order_id):
        with app.app_context():
            barrier.wait()  # 同刻释放，最大化竞争
            try:
                CreditService.freeze(uid, order_id, 20, quote_version=1)
                status = "ok"
            except InsufficientCreditError:
                status = "fail"
            with lock:
                results.append(status)

    threads = [threading.Thread(target=worker, args=(oid,)) for oid in order_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok_count = results.count("ok")
    fail_count = results.count("fail")
    assert ok_count == 5, f"期望 5 成功，实际 {ok_count}"
    assert fail_count == 5, f"期望 5 失败，实际 {fail_count}"

    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("0"), f"available 应为 0，实际 {bal['available']}"
    assert bal["frozen"] == Decimal("100"), f"frozen 应为 100，实际 {bal['frozen']}"
    assert tx_count(app, uid, type_filter="freeze") == 5


def test_concurrent_grant_idempotent(app, make_user):
    """同 idempotency_key 并发 grant 10 次 → 余额只 +一次，流水恰好 1 笔。
    靠 UNIQUE(idempotency_key) + IntegrityError replay 兜底。"""
    uid = make_user()
    key = "grant:concurrent:test"
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def worker():
        with app.app_context():
            barrier.wait()
            res = CreditService.grant(uid, 50, source="test",
                                       reason="并发发放", idempotency_key=key)
            with lock:
                results.append(res["replayed"])

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    created = results.count(False)
    replayed = results.count(True)
    assert created == 1, f"期望 1 次真正创建，实际 {created}"
    assert replayed == 9, f"期望 9 次 replay，实际 {replayed}"
    assert balance_of(app, uid)["available"] == Decimal("50")
    assert tx_count(app, uid) == 1


def test_concurrent_capture_no_double_spend(app, make_user, make_order):
    """同订单并发 capture → 只扣款一次（不重复扣）。

    其余线程要么 replay（快速路径查到已存在），要么 InsufficientCreditError
    （锁内发现 frozen 已被前一个线程扣完）——两种都算"正确未重复扣款"。
    核心断言：真正扣款恰好 1 次，frozen 只减一次，流水 1 笔。
    """
    uid = make_user(credit=100)
    oid = make_order(uid)
    _freeze_for_capture(app, uid, oid, 30)  # available=70, frozen=30

    created, replayed, errored = [], [], []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def worker():
        with app.app_context():
            barrier.wait()
            try:
                res = CreditService.capture(uid, oid, frozen_amount=30)
                with lock:
                    (replayed if res["replayed"] else created).append(1)
            except InsufficientCreditError:
                with lock:
                    errored.append(1)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 核心：真正扣款恰好 1 次，无重复扣
    assert len(created) == 1, f"应只 1 次真正扣款，实际 {len(created)}"
    assert len(created) + len(replayed) + len(errored) == 10
    bal = balance_of(app, uid)
    assert bal["available"] == Decimal("70")
    assert bal["frozen"] == Decimal("0")
    assert tx_count(app, uid, type_filter="capture") == 1
