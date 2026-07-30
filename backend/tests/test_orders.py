"""订单蓝图端到端测试（HTTP test_client 走完整请求栈）。

覆盖：完整商业闭环（命门）、失败/退款分支、事务原子性、多租户隔离、
越权（403）、文件上传到 MinIO、幂等 confirm。
连真实 MySQL bme_3dfarm_test + 真实 MinIO test bucket。
"""
import io

from exts import db
from models import UserModel, OrderFileModel, CreditTransactionModel
from services.storage import storage


def _register(client, app, email, role="customer"):
    """注册用户；admin 角色自动提升。返回 (uid, token)。"""
    r = client.post("/auth/register",
                    json={"email": email, "password": "123", "username": email})
    assert r.status_code == 200, r.get_json()
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


def _balance(client, token):
    return client.get("/credit/me", headers=_h(token)).get_json()["data"]


def _tx_count(app, uid, type_filter=None):
    with app.app_context():
        q = CreditTransactionModel.query.filter_by(user_id=uid)
        if type_filter:
            q = q.filter_by(type=type_filter)
        return q.count()


def _create_order(client, token, material="PLA"):
    return client.post("/orders/", headers=_h(token),
                       data={"material": material}).get_json()["data"]["id"]


# ═══════════════════════════ 完整闭环（命门） ═══════════════════════════
def test_full_closed_loop(app):
    client = app.test_client()
    cuid, ctoken = _register(client, app, "cust@x.com")
    _, atoken = _register(client, app, "admin@x.com", role="admin")

    # admin 发 100 credit
    r = client.post("/admin/credit/grant", headers=_h(atoken),
                    json={"user_id": cuid, "amount": 100})
    assert r.get_json()["code"] == 200
    assert _balance(client, ctoken)["available"] == "100.00"

    # 客户下单
    oid = _create_order(client, ctoken)
    order = client.get(f"/orders/{oid}", headers=_h(ctoken)).get_json()["data"]
    assert order["status"] == "QUOTING"
    assert order["public_status"] == "报价中"

    # admin 报价 30
    r = client.post(f"/admin/orders/{oid}/quote", headers=_h(atoken),
                    json={"estimated_credit": 30})
    assert r.get_json()["data"]["status"] == "WAITING_CONFIRM"

    # 客户确认（冻结 30）
    r = client.post(f"/orders/{oid}/confirm", headers=_h(ctoken))
    assert r.get_json()["code"] == 200
    bal = _balance(client, ctoken)
    assert bal["available"] == "70.00"
    assert bal["frozen"] == "30.00"

    # admin approve（连走 3 步到 READY_TO_PRINT）
    r = client.post(f"/admin/orders/{oid}/approve", headers=_h(atoken))
    assert r.get_json()["data"]["status"] == "READY_TO_PRINT"

    # admin start
    r = client.post(f"/admin/orders/{oid}/start", headers=_h(atoken))
    assert r.get_json()["data"]["status"] == "PRINTING"

    # admin complete（实扣 actual=25，退差价 5）
    r = client.post(f"/admin/orders/{oid}/complete", headers=_h(atoken),
                    json={"actual_credit": 25})
    assert r.get_json()["code"] == 200
    assert r.get_json()["data"]["status"] == "PRINT_COMPLETED"
    bal = _balance(client, ctoken)
    assert bal["available"] == "75.00"  # 70 + 5 退差价
    assert bal["frozen"] == "0.00"
    # 流水：grant, freeze, capture = 3 笔
    assert _tx_count(app, cuid) == 3


# ═══════════════════════════ 失败分支 ═══════════════════════════
def test_fail_releases_frozen(app):
    client = app.test_client()
    cuid, ctoken = _register(client, app, "cust2@x.com")
    _, atoken = _register(client, app, "admin2@x.com", role="admin")
    client.post("/admin/credit/grant", headers=_h(atoken),
                json={"user_id": cuid, "amount": 100})
    oid = _create_order(client, ctoken)
    client.post(f"/admin/orders/{oid}/quote", headers=_h(atoken),
                json={"estimated_credit": 40})
    client.post(f"/orders/{oid}/confirm", headers=_h(ctoken))  # frozen=40, avail=60
    client.post(f"/admin/orders/{oid}/approve", headers=_h(atoken))
    client.post(f"/admin/orders/{oid}/start", headers=_h(atoken))
    r = client.post(f"/admin/orders/{oid}/fail", headers=_h(atoken),
                    json={"reason": "测试失败"})
    assert r.get_json()["code"] == 200
    bal = _balance(client, ctoken)
    assert bal["available"] == "100.00"  # 60 + 40 释放
    assert bal["frozen"] == "0.00"


# ═══════════════════════════ 退款分支 ═══════════════════════════
def test_refund_after_complete(app):
    client = app.test_client()
    cuid, ctoken = _register(client, app, "cust3@x.com")
    _, atoken = _register(client, app, "admin3@x.com", role="admin")
    client.post("/admin/credit/grant", headers=_h(atoken),
                json={"user_id": cuid, "amount": 100})
    oid = _create_order(client, ctoken)
    client.post(f"/admin/orders/{oid}/quote", headers=_h(atoken),
                json={"estimated_credit": 30})
    client.post(f"/orders/{oid}/confirm", headers=_h(ctoken))
    client.post(f"/admin/orders/{oid}/approve", headers=_h(atoken))
    client.post(f"/admin/orders/{oid}/start", headers=_h(atoken))
    client.post(f"/admin/orders/{oid}/complete", headers=_h(atoken))  # actual=frozen=30
    # avail=70, frozen=0
    r = client.post(f"/admin/orders/{oid}/refund", headers=_h(atoken),
                    json={"amount": 10, "reason": "质检瑕疵"})
    assert r.get_json()["code"] == 200
    assert r.get_json()["data"]["status"] == "REFUNDED"
    assert _balance(client, ctoken)["available"] == "80.00"  # 70 + 10


# ═══════════════════════════ 事务原子性 ═══════════════════════════
def test_confirm_insufficient_rolls_back(app):
    """confirm 余额不足 → freeze+transition 全回滚：状态不变、余额不变、无半截流水。"""
    client = app.test_client()
    cuid, ctoken = _register(client, app, "cust4@x.com")
    _, atoken = _register(client, app, "admin4@x.com", role="admin")
    client.post("/admin/credit/grant", headers=_h(atoken),
                json={"user_id": cuid, "amount": 20})  # 只 20
    oid = _create_order(client, ctoken)
    client.post(f"/admin/orders/{oid}/quote", headers=_h(atoken),
                json={"estimated_credit": 50})  # 报价 50，超余额
    r = client.post(f"/orders/{oid}/confirm", headers=_h(ctoken))
    assert r.status_code == 402
    # 状态仍是 WAITING_CONFIRM
    order = client.get(f"/orders/{oid}", headers=_h(ctoken)).get_json()["data"]
    assert order["status"] == "WAITING_CONFIRM"
    # 余额不变
    bal = _balance(client, ctoken)
    assert bal["available"] == "20.00"
    assert bal["frozen"] == "0.00"
    # 只有 grant 1 笔流水（无半截 freeze）
    assert _tx_count(app, cuid) == 1


# ═══════════════════════════ 多租户隔离 ═══════════════════════════
def test_multi_tenant_isolation(app):
    """客户 B 查/操作 A 的订单 → 404。"""
    client = app.test_client()
    _, token_a = _register(client, app, "a@x.com")
    _, token_b = _register(client, app, "b@x.com")
    oid_a = _create_order(client, token_a)
    # B 查 A 的订单
    assert client.get(f"/orders/{oid_a}", headers=_h(token_b)).status_code == 404
    # B 取消 A 的订单
    assert client.post(f"/orders/{oid_a}/cancel",
                       headers=_h(token_b)).status_code == 404


# ═══════════════════════════ 越权 ═══════════════════════════
def test_customer_cannot_access_admin(app):
    client = app.test_client()
    _, ctoken = _register(client, app, "cust6@x.com")
    assert client.get("/admin/orders", headers=_h(ctoken)).status_code == 403
    # 客户调 admin grant 也 403
    r = client.post("/admin/credit/grant", headers=_h(ctoken),
                    json={"user_id": 1, "amount": 10})
    assert r.status_code == 403


# ═══════════════════════════ 幂等 ═══════════════════════════
def test_idempotent_confirm(app):
    """重复 confirm → freeze 同 key replay + transition 同状态 noop，余额只扣一次。"""
    client = app.test_client()
    cuid, ctoken = _register(client, app, "cust7@x.com")
    _, atoken = _register(client, app, "admin7@x.com", role="admin")
    client.post("/admin/credit/grant", headers=_h(atoken),
                json={"user_id": cuid, "amount": 100})
    oid = _create_order(client, ctoken)
    client.post(f"/admin/orders/{oid}/quote", headers=_h(atoken),
                json={"estimated_credit": 30})
    r1 = client.post(f"/orders/{oid}/confirm", headers=_h(ctoken))
    r2 = client.post(f"/orders/{oid}/confirm", headers=_h(ctoken))
    assert r1.get_json()["code"] == 200
    assert r2.get_json()["code"] == 200
    bal = _balance(client, ctoken)
    assert bal["available"] == "70.00"
    assert bal["frozen"] == "30.00"
    assert _tx_count(app, cuid, type_filter="freeze") == 1


# ═══════════════════════════ 文件上传 ═══════════════════════════
def test_file_upload_to_minio(app):
    client = app.test_client()
    _, ctoken = _register(client, app, "cust8@x.com")
    content = b"fake 3mf binary content"
    r = client.post("/orders/", headers=_h(ctoken),
                    data={"material": "PLA", "quantity": "2",
                          "file": (io.BytesIO(content), "model.3mf")},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert len(data["files"]) == 1
    f = data["files"][0]
    assert f["original_filename"] == "model.3mf"
    assert f["file_type"] == "source_model"
    with app.app_context():
        of = OrderFileModel.query.filter_by(order_id=data["id"]).first()
        assert of.size_bytes == len(content)
        assert of.sha256 is not None
        storage.stat_object(of.storage_key)  # 不抛即对象存在


def test_sliced_gcode_3mf_recognized(app):
    """上传 .gcode.3mf 识别为 sliced_file。"""
    client = app.test_client()
    _, ctoken = _register(client, app, "cust10@x.com")
    r = client.post("/orders/", headers=_h(ctoken),
                    data={"material": "PLA",
                          "file": (io.BytesIO(b"sliced"), "part.gcode.3mf")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.get_json()["data"]["files"][0]["file_type"] == "sliced_file"


def test_file_type_rejected(app):
    """非法文件类型被拒（order 不建）。"""
    client = app.test_client()
    _, ctoken = _register(client, app, "cust9@x.com")
    r = client.post("/orders/", headers=_h(ctoken),
                    data={"material": "PLA",
                          "file": (io.BytesIO(b"x"), "evil.exe")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
