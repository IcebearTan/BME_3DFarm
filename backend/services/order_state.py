"""订单状态机 —— 见实施方案 §5.3（简化 15 状态）+ §5.4（脱敏映射）。

职责（本轮边界）：
  - 校验状态转换合法性（按转换表）
  - 更新 order.status + 自动算 public_status（脱敏）
  - 写状态变更日志到 PrintEventModel
  - 查询当前状态可转的后继（给前端显示可操作按钮）

不含（下一 part 订单蓝图做）：
  - credit 联动（freeze/capture/release/refund）—— 由订单蓝图在调 transition
    前后组合 CreditService；事务协调（credit + 状态原子）也在蓝图层
  - 文件上传、报价字段处理

所有转换经 transition() 统一入口，保证状态机不旁路、日志不漏。
"""
import uuid

from exts import db
from models import PrintOrderModel, PrintEventModel


class InvalidTransitionError(Exception):
    """非法状态转换（from→to 不在转换表）。"""

    def __init__(self, from_status, to_status):
        super().__init__(f"非法状态转换: {from_status} → {to_status}")
        self.from_status = from_status
        self.to_status = to_status


class OrderStateMachine:
    """订单状态机。静态方法、无状态。"""

    S = PrintOrderModel  # 状态常量别名，缩短转换表写法

    # ── 合法转换表（§5.3 主线 + 异常分支）──
    # 任意"未打印"状态（DRAFT..READY_TO_PRINT）可 → CANCELLED；
    # 进入 PRINTING 后不可直接取消，须走 PRINT_FAILED/退款流程。
    TRANSITIONS = {
        S.STATUS_DRAFT:            {S.STATUS_FILE_UPLOADED, S.STATUS_CANCELLED},
        S.STATUS_FILE_UPLOADED:    {S.STATUS_QUOTING, S.STATUS_CANCELLED},
        S.STATUS_QUOTING:          {S.STATUS_WAITING_CONFIRM, S.STATUS_CANCELLED},
        S.STATUS_WAITING_CONFIRM:  {S.STATUS_CREDIT_RESERVED, S.STATUS_CANCELLED},
        S.STATUS_CREDIT_RESERVED:  {S.STATUS_REVIEWING, S.STATUS_CANCELLED},
        S.STATUS_REVIEWING:        {S.STATUS_APPROVED, S.STATUS_REJECTED,
                                    S.STATUS_CANCELLED},
        S.STATUS_APPROVED:         {S.STATUS_READY_TO_PRINT, S.STATUS_CANCELLED},
        S.STATUS_READY_TO_PRINT:   {S.STATUS_PRINTING, S.STATUS_CANCELLED},
        S.STATUS_PRINTING:         {S.STATUS_PRINT_COMPLETED, S.STATUS_PRINT_FAILED},
        S.STATUS_PRINT_COMPLETED:  {S.STATUS_QC_PENDING, S.STATUS_REFUNDED},
        S.STATUS_QC_PENDING:       {S.STATUS_CLOSED, S.STATUS_REFUNDED},
        S.STATUS_PRINT_FAILED:     {S.STATUS_NEED_REVIEW},
        S.STATUS_NEED_REVIEW:      {S.STATUS_READY_TO_PRINT,  # 重打
                                    S.STATUS_REFUNDED, S.STATUS_CANCELLED},
        S.STATUS_REJECTED:         {S.STATUS_REFUNDED},
        S.STATUS_REFUNDED:         {S.STATUS_CLOSED},
        S.STATUS_CANCELLED:        {S.STATUS_CLOSED},
        S.STATUS_CLOSED:           set(),  # 终态
    }

    # ── 脱敏映射（§5.4）：内部 status → 客户可见 public_status ──
    PUBLIC_STATUS_MAP = {
        S.STATUS_DRAFT:            "报价中",
        S.STATUS_FILE_UPLOADED:    "报价中",
        S.STATUS_QUOTING:          "报价中",
        S.STATUS_WAITING_CONFIRM:  "待确认",
        S.STATUS_CREDIT_RESERVED:  "准备打印",
        S.STATUS_REVIEWING:        "准备打印",
        S.STATUS_APPROVED:         "准备打印",
        S.STATUS_READY_TO_PRINT:   "排队中",
        S.STATUS_PRINTING:         "打印中",
        S.STATUS_PRINT_COMPLETED:  "已打印，质检中",
        S.STATUS_QC_PENDING:       "已打印，质检中",
        S.STATUS_PRINT_FAILED:     "异常处理中",
        S.STATUS_NEED_REVIEW:      "异常处理中",
        S.STATUS_REJECTED:         "无法打印",
        S.STATUS_REFUNDED:         "已退款",
        S.STATUS_CANCELLED:        "已取消",
        S.STATUS_CLOSED:           "已完成",
    }

    # 终态（不可再转出）
    TERMINAL_STATUSES = {S.STATUS_CLOSED}
    # 已进入打印阶段（不可直接取消）
    POST_PRINT_STATUSES = {
        S.STATUS_PRINTING,
        S.STATUS_PRINT_COMPLETED,
        S.STATUS_QC_PENDING,
    }

    # ── 只读查询 ──
    @staticmethod
    def public_status_of(internal_status):
        """内部 status → 脱敏 public_status。未知返回 '未知'。"""
        return OrderStateMachine.PUBLIC_STATUS_MAP.get(internal_status, "未知")

    @staticmethod
    def next_statuses(internal_status):
        """当前状态可合法转到的后继状态集合（给前端显示可操作按钮）。"""
        return set(OrderStateMachine.TRANSITIONS.get(internal_status, set()))

    @staticmethod
    def can_transit(from_status, to_status):
        return to_status in OrderStateMachine.TRANSITIONS.get(from_status, set())

    @staticmethod
    def is_terminal(status):
        return status in OrderStateMachine.TERMINAL_STATUSES

    # ── 写操作 ──
    @staticmethod
    def transition(order, to_status, actor_id=None, note=None, source=None,
                   _commit=True):
        """校验并执行状态转换。

        - 同状态无操作（幂等直接返回）
        - from→to 不在转换表 → InvalidTransitionError，order 零变动
        - 更新 order.status + 自动算 public_status（脱敏）
        - 写一条状态变更日志到 PrintEventModel
        - commit；返回 order

        source 默认按 actor 推断：有 actor_id → 'admin'，无 → 'system'。
        credit 联动不在本方法 —— 订单蓝图在调 transition 前后组合 CreditService。
        """
        from_status = order.status
        if from_status == to_status:
            return order  # 幂等：同状态无操作

        if not OrderStateMachine.can_transit(from_status, to_status):
            raise InvalidTransitionError(from_status, to_status)

        order.status = to_status
        order.public_status = OrderStateMachine.public_status_of(to_status)

        event_source = source or ("admin" if actor_id else "system")
        db.session.add(PrintEventModel(
            order_id=order.id,
            bambuddy_job_id=None,
            source=event_source,
            event_type="status_change",
            normalized_payload={
                "from": from_status,
                "to": to_status,
                "actor_id": actor_id,
                "note": note,
            },
            idempotency_key=f"status_change:{order.id}:{uuid.uuid4().hex}",
        ))
        if _commit:
            db.session.commit()
        # _commit=False: 调用方统一 commit/rollback（订单蓝图组合事务）
        return order
