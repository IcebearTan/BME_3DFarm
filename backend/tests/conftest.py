"""pytest fixtures —— 连真实 MySQL（bme_3dfarm_test）测 CreditService。

SQLite 的 SELECT ... FOR UPDATE 是 no-op，并发语义测不了，必须用真实 MySQL。
独立测试库避免污染开发库 bme_3dfarm。
"""
import os

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
)
from services import CreditService


@pytest.fixture(scope="session")
def app():
    """session 级 Flask app，建所有表；session 结束 drop。"""
    app = Flask(__name__)
    app.config.from_object(config)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _clean_tables(app):
    """每个测试前清空相关表（按外键依赖顺序），保证隔离。"""
    with app.app_context():
        db.session.query(CreditTransactionModel).delete()
        db.session.query(CreditAccountModel).delete()
        db.session.query(PrintOrderModel).delete()
        db.session.query(UserModel).delete()
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
