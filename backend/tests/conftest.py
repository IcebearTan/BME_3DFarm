"""pytest fixtures —— 连真实 MySQL（bme_3dfarm_test）测 CreditService。

SQLite 的 SELECT ... FOR UPDATE 是 no-op，并发语义测不了，必须用真实 MySQL。
独立测试库避免污染开发库 bme_3dfarm。
"""
import os
import uuid

# 必须在任何 import config 之前覆盖 DB_NAME，让 config 读到测试库
os.environ["DB_NAME"] = os.environ.get("TEST_DB_NAME", "bme_3dfarm_test")

import pytest
from flask import Flask

import config
from exts import db
import models  # noqa: F401  注册所有模型
from models import (
    UserModel,
    CreditAccountModel,
    CreditTransactionModel,
    PrintOrderModel,
    OrderFileModel,
    PrintEventModel,
    BambuddyJobModel,
    PricingConfigModel,
    PrinterModel,
    NotificationModel,
)
from services import CreditService, OrderStateMachine
from services.storage import storage
from flask_jwt_extended import JWTManager
from blueprints import (
    auth_bp, orders_bp, credit_bp, admin_bp, webhook_bp, internal_bp, printers_bp,
    notifications_bp,
)

# 费率默认种子（与 seed_pricing.py 一致；每个测试前重置，PricingService 依赖）
_PRICING_DEFAULTS = [
    ("base_fee", 2, "global", "基础开机费", "次"),
    ("machine_hour_price", 10, "global", "机时单价", "hour"),
    ("manual_slice_surcharge", 5, "global", "手工切片费", "次"),
    ("material:PLA", 0.5, "material", "PLA", "g"),
    ("material:PETG", 0.6, "material", "PETG", "g"),
    ("material:ABS", 0.7, "material", "ABS", "g"),
]


@pytest.fixture(scope="session")
def app():
    """session 级 Flask app，建所有表；session 结束 drop。"""
    app = Flask(__name__)
    app.config.from_object(config)
    app.config["MINIO_BUCKET"] = "bme-3dfarm-test-models"  # 测试用独立 bucket
    db.init_app(app)
    storage.init_app(app)
    JWTManager(app)
    for bp in (auth_bp, orders_bp, credit_bp, admin_bp, webhook_bp, internal_bp, printers_bp, notifications_bp):
        app.register_blueprint(bp)
    with app.app_context():
        db.create_all()
        try:
            storage.ensure_bucket()
        except Exception as exc:  # MinIO 未起时不阻塞测试收集
            print(f"[conftest] MinIO 不可用，文件相关测试将失败: {exc}")
    # yield 不在 app_context 内：每个测试/请求自己推 context，避免一个长期
    # 存活的 session 跨测试污染（test_client 会复用旧 session 的 identity map）。
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _clean_tables(app):
    """每个测试前清空相关表（按外键依赖顺序），保证隔离。"""
    with app.app_context():
        # 按外键依赖顺序删（子表先于父表）
        db.session.query(PrintEventModel).delete()
        db.session.query(BambuddyJobModel).delete()
        db.session.query(PrinterModel).delete()
        db.session.query(CreditTransactionModel).delete()
        db.session.query(CreditAccountModel).delete()
        db.session.query(OrderFileModel).delete()
        db.session.query(PrintOrderModel).delete()
        db.session.query(NotificationModel).delete()
        db.session.query(UserModel).delete()
        # pricing_config：清后重置默认费率（PricingService.calc 依赖）
        db.session.query(PricingConfigModel).delete()
        for key, value, cat, label, unit in _PRICING_DEFAULTS:
            db.session.add(PricingConfigModel(
                key=key, value=value, category=cat, label=label, unit=unit))
        db.session.commit()
    yield


@pytest.fixture
def make_user(app):
    """建用户 + 空账户，可选 grant 初始额度。返回 user_id。"""
    counter = {"i": 0}

    def _make(credit=0, role="customer"):
        with app.app_context():
            counter["i"] += 1
            n = counter["i"]
            user = UserModel(
                email=f"u{n}@test.com", username=f"user{n}", role=role
            )
            user.set_password("pw")
            db.session.add(user)
            db.session.commit()
            CreditService.ensure_account(user.id)
            if credit:
                CreditService.grant(user.id, credit, source="test_setup")
            return user.id

    return _make


@pytest.fixture
def make_order(app):
    """建最小 PrintOrderModel（freeze/capture 的 order_id 需满足外键约束）。"""
    counter = {"i": 0}

    def _make(user_id):
        with app.app_context():
            counter["i"] += 1
            order = PrintOrderModel(
                order_no=f"O{counter['i']}",
                user_id=user_id,
                status=PrintOrderModel.STATUS_DRAFT,
            )
            db.session.add(order)
            db.session.commit()
            return order.id

    return _make


def balance_of(app, user_id):
    """辅助：在新的 app context 里读余额（返回 Decimal，便于断言）。"""
    from decimal import Decimal

    with app.app_context():
        bal = CreditService.get_balance(user_id)
        return {
            "available": Decimal(bal["available"]),
            "frozen": Decimal(bal["frozen"]),
            "total": Decimal(bal["total"]),
        }


def tx_count(app, user_id, type_filter=None):
    """辅助：数某用户的某类流水条数。"""
    with app.app_context():
        q = CreditTransactionModel.query.filter_by(user_id=user_id)
        if type_filter:
            q = q.filter_by(type=type_filter)
        return q.count()


# ═══════════════════ Bambuddy 测试基础设施 ═══════════════════

# fixture corpus 根目录（live=本地采集 / prod=生产采集 / seq_*=合成序列）
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "bambuddy")


@pytest.fixture
def make_ready_order(app):
    """建一个 READY_TO_PRINT + 绑 BambuddyJob + 冻结 credit 的订单。返回 order.id。

    走真实事件核心（OrderStateMachine + CreditService）到 READY_TO_PRINT，
    绑定已知 archive_id 的 job，供 webhook/poller 生命周期驱动直接喂事件。
    """
    counter = {"i": 0}

    def _make(user_id, frozen=30, archive_id=999, printer_id=2):
        with app.app_context():
            counter["i"] += 1
            order = PrintOrderModel(
                order_no=f"PO{uuid.uuid4().hex[:8]}", user_id=user_id,
                status=PrintOrderModel.STATUS_DRAFT, material="PLA", quantity=1,
            )
            db.session.add(order)
            db.session.commit()
            order.estimated_credit = frozen
            # DRAFT → … → WAITING_CONFIRM → freeze → CREDIT_RESERVED → … → READY_TO_PRINT
            for s in (PrintOrderModel.STATUS_FILE_UPLOADED, PrintOrderModel.STATUS_QUOTING,
                      PrintOrderModel.STATUS_WAITING_CONFIRM):
                OrderStateMachine.transition(order, s)
            CreditService.freeze(user_id, order.id, frozen, quote_version=1)
            OrderStateMachine.transition(order, PrintOrderModel.STATUS_CREDIT_RESERVED)
            order.frozen_credit = frozen
            for s in (PrintOrderModel.STATUS_REVIEWING, PrintOrderModel.STATUS_APPROVED,
                      PrintOrderModel.STATUS_READY_TO_PRINT):
                OrderStateMachine.transition(order, s)
            db.session.commit()
            job = BambuddyJobModel(
                order_id=order.id, order_no=order.order_no,
                bambuddy_printer_id=printer_id, bambuddy_archive_id=archive_id,
                filename=f"{order.order_no}.gcode.3mf", mapping_confidence="manual",
            )
            db.session.add(job)
            db.session.commit()
            return order.id

    return _make


@pytest.fixture
def replay_proxy(app):
    """函数级：起一个 Bambuddy replay 代理线程，返回工厂。

    用法：
        p = replay_proxy(FIXTURES_DIR / "seq_print")
        point_bambuddy(app, p["base_url"])

    teardown 时停代理 + 还原 BAMBUDDY_BASE_URL（app 是 session 级，防止配置污染后续测试）。
    """
    servers = []
    original_base_url = app.config.get("BAMBUDDY_BASE_URL")

    def _make(fixtures_dir, **kwargs):
        from tools.bambuddy_recorder import ReplayServer
        srv = ReplayServer(mode="replay", fixtures_dir=fixtures_dir, port=0, **kwargs)
        srv.start()
        servers.append(srv)
        return {"base_url": srv.base_url(), "reset": srv.reset_sequence, "server": srv}

    yield _make
    for srv in servers:
        srv.stop()
    app.config["BAMBUDDY_BASE_URL"] = original_base_url


def point_bambuddy(app, base_url):
    """把 app 的 Bambuddy 指向给定 base_url（adapter 每次构造时读 config，即刻生效）。"""
    app.config["BAMBUDDY_BASE_URL"] = base_url
