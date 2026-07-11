"""Credit 额度服务 —— Phase 1 心脏。

设计见实施方案 §4：
  - 双余额（available 可用 + frozen 冻结），绝不只存单 balance
  - 流水驱动：每次变动写一笔 credit_transaction（含 before/after 快照 + 幂等键）
  - 并发安全：SELECT ... FOR UPDATE 锁账户行 + UNIQUE(idempotency_key) 兜底
  - 幂等：同 idempotency_key 重复调用只生效一次，返回上次结果

碰钱代码，正确性命门。所有写操作走统一的 _commit_change 骨架：
  1. 幂等快速路径：按 idempotency_key 查 transaction，命中直接 replay
  2. 进事务：FOR UPDATE 锁账户 → compute_delta 在锁内做业务校验并算余额增量
  3. 改余额、写流水（UNIQUE idempotency_key 兜底并发）
  4. commit；IntegrityError → rollback → replay（处理并发同 key 插入）

业务校验放锁内（compute_delta 回调）是为了规避 TOCTOU：否则锁外检查余额、锁内扣款，
并发下两个请求都能过检查，导致超扣。
"""
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError

from exts import db
from models import CreditAccountModel, CreditTransactionModel


# ─────────────────────── 异常 ───────────────────────
class CreditError(Exception):
    """Credit 服务基类异常。蓝图层捕获后映射到 {code, message}。"""


class InvalidAmountError(CreditError):
    """金额非法（<=0、零、或非数字）。"""


class AccountNotFoundError(CreditError):
    """账户不存在（订单操作前应已建账）。"""


class InsufficientCreditError(CreditError):
    """余额/冻结额度不足。"""

    def __init__(self, message, need=None, available=None):
        super().__init__(message)
        self.need = need
        self.available = available


def _to_decimal(amount, field_name="amount"):
    """入参转 Decimal（经 str 绕开 float 精度坑），失败抛 InvalidAmountError。"""
    try:
        return Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        raise InvalidAmountError(f"{field_name} 必须是数字: {amount!r}")


class CreditService:
    """额度服务。静态方法、无状态，复刻 BME service 模式。"""

    ZERO = Decimal("0")

    # ═══════════ 内部工具 ═══════════
    @staticmethod
    def ensure_account(user_id):
        """get_or_create 用户的 credit_account（空账户 available=0/frozen=0）。

        FOR UPDATE 锁不住不存在的行，所以建账单独做（含 commit）。
        grant/adjust 调用前先 ensure_account；订单操作假设账户已存在。
        并发同时建账时 user_id UNIQUE 约束兜底。
        """
        account = CreditAccountModel.query.filter_by(user_id=user_id).first()
        if account:
            return account
        account = CreditAccountModel(
            user_id=user_id,
            available_credit=CreditService.ZERO,
            frozen_credit=CreditService.ZERO,
        )
        db.session.add(account)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            account = CreditAccountModel.query.filter_by(user_id=user_id).first()
            if account is None:
                raise
            return account
        return account

    @staticmethod
    def _lock_account(user_id):
        """事务内 SELECT ... FOR UPDATE 锁账户行。不存在抛 AccountNotFoundError。"""
        account = (
            db.session.query(CreditAccountModel)
            .filter_by(user_id=user_id)
            .with_for_update()
            .first()
        )
        if account is None:
            raise AccountNotFoundError(
                f"用户 {user_id} 无 credit 账户（订单操作前应已建账）"
            )
        return account

    @staticmethod
    def _replay(idempotency_key):
        """幂等快速路径：查已存在的 transaction，返回标准化 dict 或 None。"""
        txn = CreditTransactionModel.query.filter_by(
            idempotency_key=idempotency_key
        ).first()
        if txn is None:
            return None
        return CreditService._format(txn, replayed=True)

    @staticmethod
    def _format(txn, replayed=False):
        return {
            "transaction_id": txn.id,
            "type": txn.type,
            "amount": str(txn.amount),
            "after_available": str(txn.after_available),
            "after_frozen": str(txn.after_frozen),
            "idempotency_key": txn.idempotency_key,
            "replayed": replayed,
        }

    @staticmethod
    def _commit_change(
        *,
        user_id,
        idempotency_key,
        type,
        amount,
        compute_delta,
        order_id=None,
        source=None,
        reason=None,
        operator_id=None,
    ):
        """所有写操作的统一骨架。

        compute_delta(account) -> (delta_available, delta_frozen)
            在 FOR UPDATE 锁内执行；业务校验（如余额够不够）在此回调内做，
            不通过抛 InsufficientCreditError。骨架负责非负兜底 + 写流水 + 幂等兜底。
        """
        # 1. 幂等快速路径
        replay = CreditService._replay(idempotency_key)
        if replay:
            return replay

        try:
            # 2. 锁账户
            account = CreditService._lock_account(user_id)
            before_available = account.available_credit
            before_frozen = account.frozen_credit

            # 3. 锁内业务校验 + 算增量
            delta_available, delta_frozen = compute_delta(account)

            after_available = before_available + delta_available
            after_frozen = before_frozen + delta_frozen
            # 非负兜底（compute_delta 通常已校验，这是最后防线）
            if after_available < 0 or after_frozen < 0:
                raise InsufficientCreditError(
                    f"余额不可为负: after_available={after_available}, "
                    f"after_frozen={after_frozen}"
                )

            # 4. 改余额 + 写流水
            account.available_credit = after_available
            account.frozen_credit = after_frozen
            txn = CreditTransactionModel(
                user_id=user_id,
                order_id=order_id,
                type=type,
                amount=amount,
                before_available=before_available,
                after_available=after_available,
                before_frozen=before_frozen,
                after_frozen=after_frozen,
                source=source,
                reason=reason,
                operator_id=operator_id,
                idempotency_key=idempotency_key,
            )
            db.session.add(txn)
            db.session.commit()
            return CreditService._format(txn, replayed=False)
        except IntegrityError:
            # 5. 并发：另一个请求先插入了同 idempotency_key → replay
            db.session.rollback()
            replay = CreditService._replay(idempotency_key)
            if replay:
                return replay
            raise

    # ═══════════ 只读 ═══════════
    @staticmethod
    def get_balance(user_id):
        """返回 {available, frozen, total}，账户不存在则全零（不抛错，便于新用户查余额）。"""
        account = CreditAccountModel.query.filter_by(user_id=user_id).first()
        if account is None:
            return {"available": "0.00", "frozen": "0.00", "total": "0.00"}
        return {
            "available": str(account.available_credit),
            "frozen": str(account.frozen_credit),
            "total": str(account.available_credit + account.frozen_credit),
        }

    @staticmethod
    def list_transactions(user_id, page=1, per_page=20, type_filter=None):
        """分页查流水（newest first）。"""
        q = CreditTransactionModel.query.filter_by(user_id=user_id)
        if type_filter:
            q = q.filter_by(type=type_filter)
        q = q.order_by(
            CreditTransactionModel.created_at.desc(),
            CreditTransactionModel.id.desc(),
        )
        pag = q.paginate(page=page, per_page=per_page, error_out=False)
        return {
            "items": [CreditService._txn_to_dict(t) for t in pag.items],
            "total": pag.total,
            "page": pag.page,
            "per_page": pag.per_page,
            "pages": pag.pages,
        }

    @staticmethod
    def _txn_to_dict(txn):
        return {
            "id": txn.id,
            "type": txn.type,
            "amount": str(txn.amount),
            "before_available": str(txn.before_available),
            "after_available": str(txn.after_available),
            "before_frozen": str(txn.before_frozen),
            "after_frozen": str(txn.after_frozen),
            "order_id": txn.order_id,
            "source": txn.source,
            "reason": txn.reason,
            "operator_id": txn.operator_id,
            "created_at": txn.created_at.isoformat() if txn.created_at else None,
        }

    # ═══════════ 写操作：发放 / 调整 ═══════════
    @staticmethod
    def grant(user_id, amount, source="admin_grant", reason=None,
              operator_id=None, idempotency_key=None):
        """发放 credit（本平台 credit 唯一增加途径）。available += amount。

        source: admin_grant / activity / training_camp / system。
        idempotency_key: 外部系统（如训练营奖励回调）应传稳定 key 防重；
            不传则生成一次性 key（适合管理员单次操作，不防重复点击）。
        """
        amt = _to_decimal(amount)
        if amt <= 0:
            raise InvalidAmountError(f"grant amount 必须 > 0: {amount}")
        if idempotency_key is None:
            idempotency_key = f"grant:{user_id}:{source}:{uuid.uuid4().hex}"

        CreditService.ensure_account(user_id)
        return CreditService._commit_change(
            user_id=user_id,
            idempotency_key=idempotency_key,
            type=CreditTransactionModel.TYPE_GRANT,
            amount=amt,
            compute_delta=lambda acc: (amt, CreditService.ZERO),
            source=source,
            reason=reason,
            operator_id=operator_id,
        )

    @staticmethod
    def adjust(user_id, amount, reason, operator_id, idempotency_key=None):
        """管理员手动调整（可加可减）。available += amount。

        amount < 0 时为扣减，扣到余额不足抛 InsufficientCreditError。
        """
        amt = _to_decimal(amount)
        if amt == 0:
            raise InvalidAmountError("adjust amount 不可为 0")
        if idempotency_key is None:
            idempotency_key = f"adjust:{operator_id}:{uuid.uuid4().hex}"

        CreditService.ensure_account(user_id)

        def _delta(acc):
            new_available = acc.available_credit + amt
            if new_available < 0:
                raise InsufficientCreditError(
                    f"扣减后余额为负: 当前 {acc.available_credit}, 调整 {amt}"
                )
            return (amt, CreditService.ZERO)

        return CreditService._commit_change(
            user_id=user_id,
            idempotency_key=idempotency_key,
            type=CreditTransactionModel.TYPE_ADJUST,
            amount=amt,
            compute_delta=_delta,
            source="admin_adjust",
            reason=reason,
            operator_id=operator_id,
        )

    # ═══════════ 写操作：订单生命周期 ═══════════
    @staticmethod
    def freeze(user_id, order_id, amount, quote_version):
        """下单冻结。available -= amount, frozen += amount。可用不足抛错。

        幂等键: freeze:{order_id}:{quote_version}（同订单同报价版本重复冻结只生效一次）。
        """
        amt = _to_decimal(amount)
        if amt <= 0:
            raise InvalidAmountError(f"freeze amount 必须 > 0: {amount}")
        idempotency_key = f"freeze:{order_id}:{quote_version}"

        def _delta(acc):
            if acc.available_credit < amt:
                raise InsufficientCreditError(
                    f"可用额度不足: 需要 {amt}, 当前可用 {acc.available_credit}",
                    need=amt, available=acc.available_credit,
                )
            return (-amt, amt)

        return CreditService._commit_change(
            user_id=user_id,
            idempotency_key=idempotency_key,
            type=CreditTransactionModel.TYPE_FREEZE,
            amount=amt,
            compute_delta=_delta,
            order_id=order_id,
            reason=f"下单冻结 order={order_id} v{quote_version}",
        )

    @staticmethod
    def capture(user_id, order_id, frozen_amount, actual_credit=None,
                print_job_id=None):
        """完成实扣。frozen -= frozen_amount，按 actual_credit 多退少补。

        - actual_credit=None：按冻结额实扣（settle=frozen_amount），无差价。
        - actual < frozen：解冻 frozen_amount 后，未用差价 (frozen-actual) 退回 available。
        - actual > frozen：超出部分 (actual-frozen) 从 available 补扣，不足抛错。
        - print_job_id=None 时用 'final' 作幂等键后缀；同订单多次打印用不同 job_id 区分。

        流水 amount 字段记录 settle（实际结算额）。
        """
        frozen_amt = _to_decimal(frozen_amount, "frozen_amount")
        if frozen_amt <= 0:
            raise InvalidAmountError(f"frozen_amount 必须 > 0: {frozen_amount}")
        if actual_credit is None:
            settle = frozen_amt
        else:
            settle = _to_decimal(actual_credit, "actual_credit")
            if settle < 0:
                raise InvalidAmountError(f"actual_credit 不可为负: {actual_credit}")

        key_suffix = print_job_id if print_job_id is not None else "final"
        idempotency_key = f"capture:{order_id}:{key_suffix}"

        def _delta(acc):
            # frozen 先解冻 frozen_amount（不管多退少补，都先把这个订单的冻结还回来）
            if acc.frozen_credit < frozen_amt:
                raise InsufficientCreditError(
                    f"冻结额度不足以实扣: 需要 {frozen_amt}, 当前冻结 {acc.frozen_credit}",
                    need=frozen_amt, available=acc.frozen_credit,
                )
            delta_frozen = -frozen_amt

            if settle <= frozen_amt:
                # 未用差价退回 available
                delta_available = frozen_amt - settle
            else:
                # 超出部分从 available 补扣
                extra = settle - frozen_amt
                if acc.available_credit < extra:
                    raise InsufficientCreditError(
                        f"实扣超出冻结，补扣失败: 需补扣 {extra}, "
                        f"当前可用 {acc.available_credit}",
                        need=extra, available=acc.available_credit,
                    )
                delta_available = -extra
            return (delta_available, delta_frozen)

        return CreditService._commit_change(
            user_id=user_id,
            idempotency_key=idempotency_key,
            type=CreditTransactionModel.TYPE_CAPTURE,
            amount=settle,
            compute_delta=_delta,
            order_id=order_id,
            reason=f"完成实扣 order={order_id} settle={settle}",
        )

    @staticmethod
    def release(user_id, order_id, amount, reason=None, version=1):
        """失败/取消释放冻结。frozen -= amount, available += amount。

        幂等键: release:{order_id}:{version}。
        """
        amt = _to_decimal(amount)
        if amt <= 0:
            raise InvalidAmountError(f"release amount 必须 > 0: {amount}")
        idempotency_key = f"release:{order_id}:{version}"

        def _delta(acc):
            if acc.frozen_credit < amt:
                raise InsufficientCreditError(
                    f"冻结额度不足以释放: 需要 {amt}, 当前冻结 {acc.frozen_credit}",
                    need=amt, available=acc.frozen_credit,
                )
            return (amt, -amt)

        return CreditService._commit_change(
            user_id=user_id,
            idempotency_key=idempotency_key,
            type=CreditTransactionModel.TYPE_RELEASE,
            amount=amt,
            compute_delta=_delta,
            order_id=order_id,
            reason=reason or f"释放冻结 order={order_id} v{version}",
        )

    @staticmethod
    def refund(user_id, order_id, amount, reason_code, version=1):
        """退款（实扣退回账户）。available += amount。

        幂等键: refund:{order_id}:{reason_code}:{version}。
        refund 不校验来源（不要求之前有 capture），只做"加钱 + 记流水"，
        对账靠流水审计；业务层确保只在已实扣后退款。
        """
        amt = _to_decimal(amount)
        if amt <= 0:
            raise InvalidAmountError(f"refund amount 必须 > 0: {amount}")
        idempotency_key = f"refund:{order_id}:{reason_code}:{version}"

        return CreditService._commit_change(
            user_id=user_id,
            idempotency_key=idempotency_key,
            type=CreditTransactionModel.TYPE_REFUND,
            amount=amt,
            compute_delta=lambda acc: (amt, CreditService.ZERO),
            order_id=order_id,
            reason=reason_code,
        )
