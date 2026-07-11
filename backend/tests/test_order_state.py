"""OrderStateMachine 测试套件。

覆盖：主线转换链、非法转换拒绝、脱敏映射、重打循环、终态、
状态变更日志、CANCELLED 可达性、打印中不可取消。
"""
import pytest

from exts import db
from models import PrintOrderModel, PrintEventModel
from services import OrderStateMachine, InvalidTransitionError

S = PrintOrderModel  # 状态常量别名

MAIN_LINE = [
    S.STATUS_FILE_UPLOADED,
    S.STATUS_QUOTING,
    S.STATUS_WAITING_CONFIRM,
    S.STATUS_CREDIT_RESERVED,
    S.STATUS_REVIEWING,
    S.STATUS_APPROVED,
    S.STATUS_READY_TO_PRINT,
    S.STATUS_PRINTING,
    S.STATUS_PRINT_COMPLETED,
    S.STATUS_QC_PENDING,
    S.STATUS_CLOSED,
]


# ═══════════════════════════ 主线 ═══════════════════════════
def test_main_line_full_chain(app, make_user, make_order):
    """DRAFT → ... → CLOSED 主线全通，每步 public_status 正确脱敏。"""
    uid = make_user()
    oid = make_order(uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        assert order.status == S.STATUS_DRAFT

        for nxt in MAIN_LINE:
            OrderStateMachine.transition(order, nxt)
            assert order.status == nxt
            assert order.public_status == OrderStateMachine.public_status_of(nxt)

        # CLOSED 是终态
        assert OrderStateMachine.is_terminal(order.status)


def test_invalid_transition_rejected(app, make_user, make_order):
    """DRAFT → PRINTING（跳级）非法：抛错 + order 零变动。"""
    uid = make_user()
    oid = make_order(uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        with pytest.raises(InvalidTransitionError) as ei:
            OrderStateMachine.transition(order, S.STATUS_PRINTING)
        assert ei.value.from_status == S.STATUS_DRAFT
        assert ei.value.to_status == S.STATUS_PRINTING
        # order 未变
        assert order.status == S.STATUS_DRAFT
        # 无日志写入
        assert PrintEventModel.query.filter_by(order_id=oid).count() == 0


def test_same_status_is_noop(app, make_user, make_order):
    """同状态 transition 幂等：无操作、无日志。"""
    uid = make_user()
    oid = make_order(uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        OrderStateMachine.transition(order, S.STATUS_DRAFT)  # 同状态
        assert order.status == S.STATUS_DRAFT
        assert PrintEventModel.query.filter_by(order_id=oid).count() == 0


# ═══════════════════════════ 脱敏 / 查询 ═══════════════════════════
def test_public_status_mapping_complete(app):
    """所有 17 个状态都有脱敏映射（无 '未知'）。"""
    all_statuses = {
        S.STATUS_DRAFT, S.STATUS_FILE_UPLOADED, S.STATUS_QUOTING,
        S.STATUS_WAITING_CONFIRM, S.STATUS_CREDIT_RESERVED, S.STATUS_REVIEWING,
        S.STATUS_APPROVED, S.STATUS_READY_TO_PRINT, S.STATUS_PRINTING,
        S.STATUS_PRINT_COMPLETED, S.STATUS_QC_PENDING, S.STATUS_CLOSED,
        S.STATUS_REJECTED, S.STATUS_REFUNDED, S.STATUS_PRINT_FAILED,
        S.STATUS_NEED_REVIEW, S.STATUS_CANCELLED,
    }
    for st in all_statuses:
        mapped = OrderStateMachine.public_status_of(st)
        assert mapped != "未知", f"状态 {st} 未配置脱敏映射"
    # 几个关键脱敏值
    assert OrderStateMachine.public_status_of(S.STATUS_DRAFT) == "报价中"
    assert OrderStateMachine.public_status_of(S.STATUS_PRINTING) == "打印中"
    assert OrderStateMachine.public_status_of(S.STATUS_CLOSED) == "已完成"


def test_next_statuses(app):
    """后继状态查询正确。"""
    nxt = OrderStateMachine.next_statuses(S.STATUS_REVIEWING)
    assert nxt == {S.STATUS_APPROVED, S.STATUS_REJECTED, S.STATUS_CANCELLED}
    # 终态无后继
    assert OrderStateMachine.next_statuses(S.STATUS_CLOSED) == set()


# ═══════════════════════════ 异常分支 ═══════════════════════════
def test_rejected_branch(app, make_user, make_order):
    """REVIEWING → REJECTED → REFUNDED → CLOSED。"""
    uid = make_user()
    oid = make_order(uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        for st in [S.STATUS_FILE_UPLOADED, S.STATUS_QUOTING,
                   S.STATUS_WAITING_CONFIRM, S.STATUS_CREDIT_RESERVED,
                   S.STATUS_REVIEWING]:
            OrderStateMachine.transition(order, st)
        OrderStateMachine.transition(order, S.STATUS_REJECTED)
        assert order.public_status == "无法打印"
        OrderStateMachine.transition(order, S.STATUS_REFUNDED)
        assert order.public_status == "已退款"
        OrderStateMachine.transition(order, S.STATUS_CLOSED)
        assert order.public_status == "已完成"


def test_reprint_loop(app, make_user, make_order):
    """失败重打循环：PRINTING→PRINT_FAILED→NEED_REVIEW→READY_TO_PRINT
    →PRINTING→PRINT_FAILED→NEED_REVIEW→REFUNDED。"""
    uid = make_user()
    oid = make_order(uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        for st in MAIN_LINE[:8]:  # 推进到 PRINTING
            OrderStateMachine.transition(order, st)
        assert order.status == S.STATUS_PRINTING

        # 第一次失败 → 重打
        OrderStateMachine.transition(order, S.STATUS_PRINT_FAILED)
        OrderStateMachine.transition(order, S.STATUS_NEED_REVIEW)
        OrderStateMachine.transition(order, S.STATUS_READY_TO_PRINT)
        OrderStateMachine.transition(order, S.STATUS_PRINTING)
        # 第二次失败 → 退款
        OrderStateMachine.transition(order, S.STATUS_PRINT_FAILED)
        OrderStateMachine.transition(order, S.STATUS_NEED_REVIEW)
        OrderStateMachine.transition(order, S.STATUS_REFUNDED)
        assert order.status == S.STATUS_REFUNDED


def test_terminal_closed_blocks_all(app, make_user, make_order):
    """CLOSED 终态：转到任何状态都抛错。"""
    uid = make_user()
    oid = make_order(uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        for st in MAIN_LINE:
            OrderStateMachine.transition(order, st)
        for target in [S.STATUS_DRAFT, S.STATUS_PRINTING, S.STATUS_REFUNDED]:
            with pytest.raises(InvalidTransitionError):
                OrderStateMachine.transition(order, target)


# ═══════════════════════════ CANCELLED 可达性 ═══════════════════════════
@pytest.mark.parametrize("pre_status", [
    S.STATUS_DRAFT,
    S.STATUS_FILE_UPLOADED,
    S.STATUS_QUOTING,
    S.STATUS_WAITING_CONFIRM,
    S.STATUS_CREDIT_RESERVED,
    S.STATUS_REVIEWING,
    S.STATUS_APPROVED,
    S.STATUS_READY_TO_PRINT,
])
def test_cancel_from_pre_print(app, make_user, make_order, pre_status):
    """任意未打印状态可直接 → CANCELLED。"""
    uid = make_user()
    oid = make_order(uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        if pre_status != S.STATUS_DRAFT:
            # 沿主线推进到 pre_status（含），MAIN_LINE 已按合法顺序排好
            for st in MAIN_LINE:
                OrderStateMachine.transition(order, st)
                if st == pre_status:
                    break
        assert order.status == pre_status
        OrderStateMachine.transition(order, S.STATUS_CANCELLED)
        assert order.status == S.STATUS_CANCELLED
        assert order.public_status == "已取消"


def test_cancel_during_print_rejected(app, make_user, make_order):
    """打印中（PRINTING）不可直接取消，须走失败流程。"""
    uid = make_user()
    oid = make_order(uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        for st in MAIN_LINE[:8]:  # 推到 PRINTING
            OrderStateMachine.transition(order, st)
        with pytest.raises(InvalidTransitionError):
            OrderStateMachine.transition(order, S.STATUS_CANCELLED)


# ═══════════════════════════ 变更日志 ═══════════════════════════
def test_transition_writes_event_log(app, make_user, make_order):
    """每次转换写一条 PrintEventModel 日志，from/to/source 正确。"""
    uid = make_user()
    oid = make_order(uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        OrderStateMachine.transition(order, S.STATUS_FILE_UPLOADED,
                                     actor_id=uid, note="客户上传文件")
        events = PrintEventModel.query.filter_by(order_id=oid).all()
        assert len(events) == 1
        ev = events[0]
        assert ev.source == "admin"  # 有 actor_id
        assert ev.event_type == "status_change"
        assert ev.normalized_payload["from"] == S.STATUS_DRAFT
        assert ev.normalized_payload["to"] == S.STATUS_FILE_UPLOADED
        assert ev.normalized_payload["note"] == "客户上传文件"

        # 再转一次 → 第二条日志
        OrderStateMachine.transition(order, S.STATUS_QUOTING)  # 无 actor → system
        events = PrintEventModel.query.filter_by(order_id=oid).all()
        assert len(events) == 2
        assert events[1].source == "system"
