"""BME_3DFarm ORM 模型。

表清单（见实施方案 §5.1）：
  user / credit_account / credit_transaction
  print_order / order_file / printer
  bambuddy_job / print_event / audit_log

角色两级：admin（管理员，全权）/ customer（客户，默认）。
密码哈希复刻 BME 的 pbkdf2:sha256，兼容历史明文并在登录时自动升级。
"""
from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash

from exts import db


# ─────────────────────── 用户 ───────────────────────
class UserModel(db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(100), nullable=False, unique=True, index=True)
    username = db.Column(db.String(100), nullable=False)
    # 加盐哈希后的密码；历史数据可能是前端 MD5 明文，由 check_password 兼容并自动升级
    password = db.Column(db.String(255), nullable=False)
    # RBAC 两级：admin / customer
    role = db.Column(db.String(20), nullable=False, server_default="customer")
    status = db.Column(db.String(20), nullable=False, server_default="active")  # active/disabled
    # 给训练营 SSO 预留（现在不用）
    external_user_id = db.Column(db.String(100), nullable=True, index=True)
    sso_subject = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    _PWHASH_PREFIXES = ("pbkdf2:", "scrypt:", "argon2:")

    @property
    def password_is_hashed(self):
        return isinstance(self.password, str) and self.password.startswith(self._PWHASH_PREFIXES)

    def set_password(self, raw_password):
        self.password = generate_password_hash(raw_password, method="pbkdf2:sha256")

    def check_password(self, raw_password):
        if not isinstance(self.password, str) or not self.password:
            return False
        if self.password_is_hashed:
            return check_password_hash(self.password, raw_password)
        # 历史遗留明文
        return self.password == raw_password

    # ── 角色两级 ──
    ROLE_RANK = {"admin": 2, "customer": 1}

    @property
    def role_rank(self):
        return self.ROLE_RANK.get(self.role or "customer", 1)

    def is_admin_like(self):
        """管理员（系统级全权，对应打印农场后台操作员）。"""
        return self.role == "admin"


# ─────────────────────── Credit 额度 ───────────────────────
class CreditAccountModel(db.Model):
    __tablename__ = "credit_account"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True, index=True)
    available_credit = db.Column(db.Numeric(12, 2), nullable=False, server_default="0")
    frozen_credit = db.Column(db.Numeric(12, 2), nullable=False, server_default="0")
    currency_type = db.Column(db.String(20), nullable=False, server_default="point")
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    user = db.relationship("UserModel", backref=db.backref("credit_account", uselist=False))


class CreditTransactionModel(db.Model):
    __tablename__ = "credit_transaction"

    # type 枚举（见实施方案 §4.3）
    TYPE_GRANT = "grant"        # 发放（管理员/活动）
    TYPE_FREEZE = "freeze"      # 下单冻结
    TYPE_CAPTURE = "capture"    # 完成实扣
    TYPE_RELEASE = "release"    # 失败/取消释放
    TYPE_REFUND = "refund"      # 退款
    TYPE_ADJUST = "adjust"      # 管理员手动调整
    TYPE_REWARD = "reward"      # 训练营奖励（预留）

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("print_order.id"), nullable=True, index=True)
    type = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    before_available = db.Column(db.Numeric(12, 2), nullable=False)
    after_available = db.Column(db.Numeric(12, 2), nullable=False)
    before_frozen = db.Column(db.Numeric(12, 2), nullable=False)
    after_frozen = db.Column(db.Numeric(12, 2), nullable=False)
    # 来源：admin_grant / activity / training_camp / system
    source = db.Column(db.String(32), nullable=True)
    reason = db.Column(db.Text, nullable=True)
    operator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    idempotency_key = db.Column(db.String(128), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    user = db.relationship("UserModel", foreign_keys=[user_id])
    operator = db.relationship("UserModel", foreign_keys=[operator_id])


# ─────────────────────── 打印订单 ───────────────────────
class PrintOrderModel(db.Model):
    __tablename__ = "print_order"

    # 订单状态机（见实施方案 §5.3，无 READY_FOR_PICKUP）
    STATUS_DRAFT = "DRAFT"
    STATUS_FILE_UPLOADED = "FILE_UPLOADED"
    STATUS_QUOTING = "QUOTING"
    STATUS_WAITING_CONFIRM = "WAITING_CONFIRM"
    STATUS_CREDIT_RESERVED = "CREDIT_RESERVED"
    STATUS_REVIEWING = "REVIEWING"
    STATUS_APPROVED = "APPROVED"
    STATUS_READY_TO_PRINT = "READY_TO_PRINT"
    STATUS_PRINTING = "PRINTING"
    STATUS_PRINT_COMPLETED = "PRINT_COMPLETED"
    STATUS_QC_PENDING = "QC_PENDING"
    STATUS_CLOSED = "CLOSED"
    STATUS_REJECTED = "REJECTED"
    STATUS_REFUNDED = "REFUNDED"
    STATUS_PRINT_FAILED = "PRINT_FAILED"
    STATUS_NEED_REVIEW = "NEED_REVIEW"
    STATUS_CANCELLED = "CANCELLED"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    order_no = db.Column(db.String(64), nullable=False, unique=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, server_default=STATUS_DRAFT, index=True)
    public_status = db.Column(db.String(32), nullable=True)  # 脱敏后的客户可见状态
    # 需求参数
    material = db.Column(db.String(64), nullable=True)
    color = db.Column(db.String(32), nullable=True)
    layer_height = db.Column(db.Numeric(5, 3), nullable=True)
    nozzle_size = db.Column(db.Numeric(4, 2), nullable=True)
    quantity = db.Column(db.Integer, nullable=False, server_default="1")
    # 预估（Phase 4 自动报价用，Phase 1 人工填）
    estimate_weight_g = db.Column(db.Numeric(10, 2), nullable=True)
    estimate_print_seconds = db.Column(db.Integer, nullable=True)
    # Phase 3：gcode 解析产物（多色 filament/nozzle，下发 AMS 校验 + 计费用）+ 切片路径标记
    parsed_filaments = db.Column(db.JSON, nullable=True)
    parsed_nozzles = db.Column(db.JSON, nullable=True)
    is_manual_slice_path = db.Column(db.Boolean, nullable=False, server_default="0")  # .3mf 走 admin 手动切片
    # credit
    estimated_credit = db.Column(db.Numeric(12, 2), nullable=True)
    frozen_credit = db.Column(db.Numeric(12, 2), nullable=False, server_default="0")
    actual_credit = db.Column(db.Numeric(12, 2), nullable=True)
    # 其他
    priority = db.Column(db.Integer, nullable=False, server_default="0")
    due_at = db.Column(db.DateTime, nullable=True)
    customer_note = db.Column(db.Text, nullable=True)
    admin_note = db.Column(db.Text, nullable=True)
    # 通知相关：admin 填写的失败原因 / 成功后续（取件/发货安排），客户可见
    fail_reason = db.Column(db.Text, nullable=True)
    completion_note = db.Column(db.Text, nullable=True)
    # 进度
    public_progress = db.Column(db.Integer, nullable=False, server_default="0")  # 0-100
    remaining_seconds = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    user = db.relationship("UserModel", backref=db.backref("orders", lazy="dynamic"))
    files = db.relationship("OrderFileModel", backref="order", lazy="dynamic")


class OrderFileModel(db.Model):
    __tablename__ = "order_file"

    FILE_SOURCE_MODEL = "source_model"      # 客户上传的 .3mf 模型
    FILE_SLICED = "sliced_file"             # 已切片 .gcode.3mf（客户直传或管理员切片产物）
    FILE_PREVIEW = "preview"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    order_id = db.Column(db.Integer, db.ForeignKey("print_order.id"), nullable=False, index=True)
    file_type = db.Column(db.String(32), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    storage_key = db.Column(db.String(512), nullable=False)  # MinIO 对象 key
    content_type = db.Column(db.String(100), nullable=True)
    size_bytes = db.Column(db.BigInteger, nullable=True)
    sha256 = db.Column(db.String(64), nullable=True)
    version = db.Column(db.Integer, nullable=False, server_default="1")
    scan_status = db.Column(db.String(20), nullable=False, server_default="pending")  # pending/clean/infected/error
    created_at = db.Column(db.DateTime, default=datetime.now)


# ─────────────────────── 打印机（业务侧抽象） ───────────────────────
class PrinterModel(db.Model):
    __tablename__ = "printer"

    STATUS_IDLE = "idle"
    STATUS_PRINTING = "printing"
    STATUS_OFFLINE = "offline"
    STATUS_ERROR = "error"
    STATUS_MAINTENANCE = "maintenance"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    public_name = db.Column(db.String(100), nullable=False)          # 客户可见名，如 Printer-03
    internal_name = db.Column(db.String(100), nullable=True)
    bambuddy_printer_id = db.Column(db.Integer, nullable=True, index=True)  # Bambuddy 侧 printer id
    model = db.Column(db.String(64), nullable=False, server_default="P1S")
    has_ams = db.Column(db.Boolean, nullable=False, server_default="0")  # 是否加装 AMS（决定能否多色）
    capabilities = db.Column(db.JSON, nullable=True)                # 尺寸/AMS 槽/喷嘴/材料能力
    status = db.Column(db.String(32), nullable=False, server_default=STATUS_OFFLINE, index=True)
    last_seen_at = db.Column(db.DateTime, nullable=True)
    enabled = db.Column(db.Boolean, nullable=False, server_default="1")
    # Phase 4.5 打印机监控（Poller 从 Bambuddy 同步）
    source = db.Column(db.String(20), nullable=True)  # virtual / real
    status_detail = db.Column(db.JSON, nullable=True)  # 温度/进度/AMS 等 Bambuddy status 快照
    queue_count = db.Column(db.Integer, nullable=False, server_default="0")  # 该机队列长度
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


# ─────────────────────── 订单 ↔ Bambuddy 任务映射（Phase 2） ───────────────────────
class BambuddyJobModel(db.Model):
    __tablename__ = "bambuddy_job"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    order_id = db.Column(db.Integer, db.ForeignKey("print_order.id"), nullable=False, index=True)
    order_no = db.Column(db.String(64), nullable=False, index=True)
    bambuddy_printer_id = db.Column(db.Integer, nullable=True)
    bambuddy_queue_id = db.Column(db.Integer, nullable=True, index=True)
    bambuddy_archive_id = db.Column(db.Integer, nullable=True, index=True)
    filename = db.Column(db.String(255), nullable=True)  # 文件名带 order_no，便于反查
    job_token = db.Column(db.String(128), nullable=True, unique=True, index=True)
    bambuddy_status = db.Column(db.String(64), nullable=True)
    # Phase 4：下发用的 AMS 料盘映射（持久化追溯；admin 手选或自动匹配的 ams_mapping）
    ams_mapping = db.Column(db.JSON, nullable=True)
    dispatched_at = db.Column(db.DateTime, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    last_sync_at = db.Column(db.DateTime, nullable=True)
    mapping_confidence = db.Column(db.String(20), nullable=True)  # exact/filename/manual

    order = db.relationship("PrintOrderModel", backref=db.backref("bambuddy_jobs", lazy="dynamic"))


# ─────────────────────── 打印事件（Phase 2 Webhook/Poller 落库） ───────────────────────
class PrintEventModel(db.Model):
    __tablename__ = "print_event"

    SOURCE_WEBHOOK = "webhook"
    SOURCE_POLLER = "poller"
    SOURCE_ADMIN = "admin"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    order_id = db.Column(db.Integer, db.ForeignKey("print_order.id"), nullable=True, index=True)
    bambuddy_job_id = db.Column(db.Integer, db.ForeignKey("bambuddy_job.id"), nullable=True, index=True)
    source = db.Column(db.String(20), nullable=False)
    event_type = db.Column(db.String(64), nullable=False)  # print_started/progress/complete/failed/...
    raw_payload = db.Column(db.JSON, nullable=True)
    normalized_payload = db.Column(db.JSON, nullable=True)
    idempotency_key = db.Column(db.String(128), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)


# ─────────────────────── 审计日志（装饰器 audit_log 使用） ───────────────────────
class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    username = db.Column(db.String(100), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    operation = db.Column(db.String(100), nullable=True)
    operation_url = db.Column(db.String(500), nullable=True)
    operation_data = db.Column(db.Text, nullable=True)
    result = db.Column(db.String(20), nullable=True)  # 成功/失败
    timestamp = db.Column(db.DateTime, default=datetime.now, index=True)


# ─────────────────────── 费率配置（Phase 1.5 自动报价） ───────────────────────
class PricingConfigModel(db.Model):
    """key-value 费率配置。

    category=global：base_fee（基础开机费）/ machine_hour_price（机时单价）
    category=material：material:<NAME> 材料每克单价（受 Bambuddy cost_per_kg 启发）
    单位（unit）仅显示用：g / hour / 次
    """
    __tablename__ = "pricing_config"

    CAT_GLOBAL = "global"
    CAT_MATERIAL = "material"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    key = db.Column(db.String(64), nullable=False, unique=True, index=True)
    value = db.Column(db.Numeric(12, 2), nullable=False)
    category = db.Column(db.String(20), nullable=False, default=CAT_GLOBAL)
    label = db.Column(db.String(100), nullable=True)
    unit = db.Column(db.String(20), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


# ─────────────────────── 通知（公告 + 订单提醒） ───────────────────────
class NotificationModel(db.Model):
    """站内通知。设计照搬 BME notification 蓝本（系统 A）：

    - 广播 = 写时扇出（每用户一行），不引入 broadcast + 关联表（千级用户够用）
    - 已读 = is_read 布尔直接挂行（每用户有自己的行，天然隔离）
    - 业务点（admin 订单操作等）主动调 notifications.create_notification() 扇出
    """
    __tablename__ = "notification"

    CAT_SYSTEM = "system"   # 公告
    CAT_ORDER = "order"     # 订单事件提醒

    SRC_ANNOUNCEMENT = "announcement"
    SRC_ORDER_FAILED = "order_failed"
    SRC_ORDER_COMPLETED = "order_completed"
    SRC_ORDER_CANCELLED = "order_cancelled"
    SRC_ORDER_REFUNDED = "order_refunded"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)  # 接收人
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(20), nullable=False, index=True)
    source_type = db.Column(db.String(20), nullable=True)   # 点击跳回源的依据
    source_id = db.Column(db.Integer, nullable=True)        # 订单类 = order_id
    is_read = db.Column(db.Boolean, nullable=False, server_default="0")
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    # 复合索引：未读计数 WHERE user_id=? AND is_read=False 一次命中
    __table_args__ = (
        db.Index("ix_notification_user_read", "user_id", "is_read"),
    )
