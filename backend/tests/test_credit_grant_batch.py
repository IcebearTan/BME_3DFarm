"""批量发 credit 接口测试（POST /admin/credit/grant-batch）。

覆盖：多用户全成功、部分失败明细、同 batch_id 重试幂等（已成功 replay、失败重试）、
参数校验与越权（403）。连真实 MySQL bme_3dfarm_test。
"""
from exts import db
from models import UserModel, CreditTransactionModel


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


def test_grant_batch_success(app):
    client = app.test_client()
    u1, t1 = _register(client, app, "b1@x.com")
    u2, t2 = _register(client, app, "b2@x.com")
    u3, t3 = _register(client, app, "b3@x.com")
    _, atoken = _register(client, app, "admin@x.com", role="admin")

    r = client.post("/admin/credit/grant-batch", headers=_h(atoken),
                    json={"user_ids": [u1, u2, u3], "amount": 50, "reason": "活动"})
    body = r.get_json()
    assert body["code"] == 200, body
    data = body["data"]
    assert data["success_count"] == 3
    assert data["fail_count"] == 0
    assert data["total"] == 3
    assert len(data["succeeded"]) == 3
    # 每人余额 +50
    assert _balance(client, t1)["available"] == "50.00"
    assert _balance(client, t2)["available"] == "50.00"
    assert _balance(client, t3)["available"] == "50.00"
    # 每人 1 笔 grant 流水
    assert _tx_count(app, u1, CreditTransactionModel.TYPE_GRANT) == 1


def test_grant_batch_partial_failure(app):
    """含不存在 user_id：该用户进 failed，其余正常发放。"""
    client = app.test_client()
    u1, t1 = _register(client, app, "p1@x.com")
    _, atoken = _register(client, app, "admin@x.com", role="admin")

    r = client.post("/admin/credit/grant-batch", headers=_h(atoken),
                    json={"user_ids": [u1, 999999], "amount": 20})
    body = r.get_json()
    assert body["code"] == 200, body
    data = body["data"]
    assert data["success_count"] == 1
    assert data["fail_count"] == 1
    assert data["failed"][0]["user_id"] == 999999
    assert "不存在" in data["failed"][0]["message"]
    # 存在的用户照常到账
    assert _balance(client, t1)["available"] == "20.00"


def test_grant_batch_retry_idempotent(app):
    """同 batch_id 重试：已成功的 replay（余额/流水不增），仅记录为重放。"""
    client = app.test_client()
    u1, t1 = _register(client, app, "r1@x.com")
    _, atoken = _register(client, app, "admin@x.com", role="admin")

    # 首次：u1 成功 + 不存在用户失败
    r1 = client.post("/admin/credit/grant-batch", headers=_h(atoken),
                     json={"user_ids": [u1, 999999], "amount": 30})
    d1 = r1.get_json()["data"]
    assert d1["success_count"] == 1 and d1["fail_count"] == 1
    batch_id = d1["batch_id"]
    assert _balance(client, t1)["available"] == "30.00"
    assert _tx_count(app, u1, CreditTransactionModel.TYPE_GRANT) == 1

    # 重试：同 batch_id 再发 u1 —— 应 replay，不重复入账
    r2 = client.post("/admin/credit/grant-batch", headers=_h(atoken),
                     json={"user_ids": [u1], "amount": 30, "batch_id": batch_id})
    d2 = r2.get_json()["data"]
    assert d2["success_count"] == 1
    assert d2["succeeded"][0]["replayed"] is True
    assert _balance(client, t1)["available"] == "30.00"  # 未重复发
    assert _tx_count(app, u1, CreditTransactionModel.TYPE_GRANT) == 1  # 仍 1 笔流水


def test_grant_batch_dedup_user_ids(app):
    """同一 user_id 重复出现只发一次。"""
    client = app.test_client()
    u1, t1 = _register(client, app, "d1@x.com")
    _, atoken = _register(client, app, "admin@x.com", role="admin")

    r = client.post("/admin/credit/grant-batch", headers=_h(atoken),
                    json={"user_ids": [u1, u1, u1], "amount": 10})
    data = r.get_json()["data"]
    assert data["total"] == 1          # 去重后只 1 个
    assert data["success_count"] == 1
    assert _balance(client, t1)["available"] == "10.00"  # 只加一次


def test_grant_batch_validation_and_forbidden(app):
    client = app.test_client()
    _, atoken = _register(client, app, "admin@x.com", role="admin")
    u1, _ = _register(client, app, "v1@x.com")

    # user_ids 空
    assert client.post("/admin/credit/grant-batch", headers=_h(atoken),
                       json={"user_ids": [], "amount": 10}).get_json()["code"] == 400
    # amount=0
    assert client.post("/admin/credit/grant-batch", headers=_h(atoken),
                       json={"user_ids": [u1], "amount": 0}).get_json()["code"] == 400
    # amount 非数字
    assert client.post("/admin/credit/grant-batch", headers=_h(atoken),
                       json={"user_ids": [u1], "amount": "abc"}).get_json()["code"] == 400
    # customer 越权 → 403
    _, ctoken = _register(client, app, "cust@x.com")
    r = client.post("/admin/credit/grant-batch", headers=_h(ctoken),
                    json={"user_ids": [u1], "amount": 10})
    assert r.status_code == 403
