"""通知系统测试 — 公告扇出 + 已读/列表/删除 + 订单事件触发 + 越权。

连真实 MySQL bme_3dfarm_test。复用 test_orders 的 _register/_h helper 风格。
"""
from exts import db
from models import UserModel, NotificationModel


def _register(client, app, email, role="customer"):
    r = client.post("/auth/register", json={"email": email, "password": "123", "username": email})
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


def _uid(app, email):
    with app.app_context():
        return UserModel.query.filter_by(email=email).first().id


def _count(app, user_id, read=None):
    with app.app_context():
        q = NotificationModel.query.filter_by(user_id=user_id)
        if read is not None:
            q = q.filter_by(is_read=read)
        return q.count()


def _one(app, user_id):
    with app.app_context():
        return NotificationModel.query.filter_by(user_id=user_id).order_by(
            NotificationModel.id.desc()).first()


# ═════════════════ 公告 ═════════════════
def test_announce_broadcast_all_customers(app):
    """全员公告：扇出给所有 customer，admin 自己不收。"""
    client = app.test_client()
    _, c1t = _register(client, app, "c1@x.com")
    _, c2t = _register(client, app, "c2@x.com")
    a1, at = _register(client, app, "admin@x.com", role="admin")

    r = client.post("/notifications/announce", headers=_h(at),
                    json={"title": "系统公告", "content": "农场周维护"})
    body = r.get_json()
    assert body["code"] == 200, body
    assert body["data"]["created_count"] == 2          # 2 个 customer
    assert _count(app, _uid(app, "c1@x.com")) == 1
    assert _count(app, _uid(app, "c2@x.com")) == 1
    assert _count(app, a1) == 0                        # admin 不收


def test_announce_specific_users(app):
    client = app.test_client()
    c1 = _register(client, app, "s1@x.com")[0]
    _register(client, app, "s2@x.com")
    _, at = _register(client, app, "admin@x.com", role="admin")
    r = client.post("/notifications/announce", headers=_h(at),
                    json={"title": "指定", "content": "x", "user_ids": [c1]})
    assert r.get_json()["data"]["created_count"] == 1
    assert _count(app, c1) == 1
    assert _count(app, _uid(app, "s2@x.com")) == 0


def test_announce_customer_forbidden(app):
    client = app.test_client()
    _, ct = _register(client, app, "cust@x.com")
    r = client.post("/notifications/announce", headers=_h(ct), json={"title": "x"})
    assert r.status_code == 403


# ═════════════════ 已读 / 列表 / 删除 ═════════════════
def test_list_unread_markread_delete(app):
    client = app.test_client()
    _, at = _register(client, app, "admin@x.com", role="admin")
    c1, c1t = _register(client, app, "c@x.com")
    client.post("/notifications/announce", headers=_h(at), json={"title": "t1", "content": "a"})
    client.post("/notifications/announce", headers=_h(at),
                json={"title": "t2", "content": "b", "user_ids": [c1]})

    # list 带 unread_count
    data = client.get("/notifications/list", headers=_h(c1t)).get_json()["data"]
    assert data["unread_count"] == 2
    assert len(data["items"]) == 2

    # unread_count 轻量接口
    assert client.get("/notifications/unread_count", headers=_h(c1t)
                      ).get_json()["data"]["unread_count"] == 2

    # mark_read 单条
    nid = data["items"][0]["id"]
    assert client.post("/notifications/mark_read", headers=_h(c1t), json={"id": nid}
                       ).get_json()["code"] == 200
    assert client.get("/notifications/unread_count", headers=_h(c1t)
                      ).get_json()["data"]["unread_count"] == 1

    # mark_all_read
    client.post("/notifications/mark_all_read", headers=_h(c1t), json={})
    assert client.get("/notifications/unread_count", headers=_h(c1t)
                      ).get_json()["data"]["unread_count"] == 0

    # delete 限已读（删 1 条已读）
    nid2 = data["items"][1]["id"]
    r = client.delete("/notifications/delete", headers=_h(c1t), json={"ids": [nid2]})
    assert r.get_json()["code"] == 200
    assert _count(app, c1) == 1


# ═════════════════ 订单事件触发通知 ═════════════════
def _make_printing_order(client, app, cust_email):
    """建订单推进到 PRINTING（复用旧报价路径：无文件 → QUOTING → quote → confirm → approve → start）。"""
    cuid, ctoken = _register(client, app, cust_email)
    _, atoken = _register(client, app, f"admin-{cust_email}", role="admin")
    client.post("/admin/credit/grant", headers=_h(atoken), json={"user_id": cuid, "amount": 100})
    oid = client.post("/orders/", headers=_h(ctoken), data={"material": "PLA"}
                      ).get_json()["data"]["id"]
    client.post(f"/admin/orders/{oid}/quote", headers=_h(atoken), json={"estimated_credit": 30})
    client.post(f"/orders/{oid}/confirm", headers=_h(ctoken))
    client.post(f"/admin/orders/{oid}/approve", headers=_h(atoken))
    client.post(f"/admin/orders/{oid}/start", headers=_h(atoken))
    return oid, cuid, ctoken, atoken


def _make_ready_order(client, app, cust_email):
    """推进到 READY_TO_PRINT（approve 后不 start，可 cancel）。"""
    cuid, ctoken = _register(client, app, cust_email)
    _, atoken = _register(client, app, f"admin-{cust_email}", role="admin")
    client.post("/admin/credit/grant", headers=_h(atoken), json={"user_id": cuid, "amount": 100})
    oid = client.post("/orders/", headers=_h(ctoken), data={"material": "PLA"}
                      ).get_json()["data"]["id"]
    client.post(f"/admin/orders/{oid}/quote", headers=_h(atoken), json={"estimated_credit": 30})
    client.post(f"/orders/{oid}/confirm", headers=_h(ctoken))
    client.post(f"/admin/orders/{oid}/approve", headers=_h(atoken))
    return oid, cuid, atoken


def test_order_fail_notifies(app):
    client = app.test_client()
    oid, cuid, ct, at = _make_printing_order(client, app, "fail@x.com")
    # reason 必填
    assert client.post(f"/admin/orders/{oid}/fail", headers=_h(at), json={}
                       ).get_json()["code"] == 400
    # 带 reason → 失败 + 通知
    r = client.post(f"/admin/orders/{oid}/fail", headers=_h(at), json={"reason": "耗材断料"})
    assert r.get_json()["code"] == 200, r.get_json()
    assert _count(app, cuid) == 1
    n = _one(app, cuid)
    assert n.source_type == "order_failed"
    assert n.source_id == oid
    assert "耗材断料" in n.content


def test_order_complete_and_delivery_note(app):
    client = app.test_client()
    oid, cuid, ct, at = _make_printing_order(client, app, "comp@x.com")
    # complete 带 completion_note → 通知 + 写字段
    r = client.post(f"/admin/orders/{oid}/complete", headers=_h(at),
                    json={"completion_note": "可取件"})
    assert r.get_json()["code"] == 200
    assert _count(app, cuid) == 1
    # 补充交付说明 → 再发一条
    r2 = client.post(f"/admin/orders/{oid}/delivery-note", headers=_h(at),
                     json={"completion_note": "柜台 A 取件，周一前"})
    assert r2.get_json()["code"] == 200
    assert _count(app, cuid) == 2
    n = _one(app, cuid)  # 最新一条
    assert n.source_type == "order_completed"
    assert n.content == "柜台 A 取件，周一前"

    # delivery-note 状态校验：未完成订单不允许
    oid2, _, _, at2 = _make_printing_order(client, app, "comp2@x.com")
    r3 = client.post(f"/admin/orders/{oid2}/delivery-note", headers=_h(at2),
                     json={"completion_note": "x"})
    assert r3.get_json()["code"] == 409


def test_order_cancel_notifies(app):
    client = app.test_client()
    oid, cuid, at = _make_ready_order(client, app, "canc@x.com")
    r = client.post(f"/admin/orders/{oid}/cancel", headers=_h(at), json={"reason": "缺料"})
    assert r.get_json()["code"] == 200, r.get_json()
    assert _count(app, cuid) == 1
    assert _one(app, cuid).source_type == "order_cancelled"


def test_order_refund_notifies(app):
    client = app.test_client()
    oid, cuid, ct, at = _make_printing_order(client, app, "ref@x.com")
    client.post(f"/admin/orders/{oid}/complete", headers=_h(at), json={})
    r = client.post(f"/admin/orders/{oid}/refund", headers=_h(at),
                    json={"amount": 10, "reason": "质量瑕疵"})
    assert r.get_json()["code"] == 200, r.get_json()
    # complete + refund 各一条
    assert _count(app, cuid) == 2
    with app.app_context():
        types = [n.source_type for n in NotificationModel.query.filter_by(user_id=cuid).all()]
    assert "order_completed" in types
    assert "order_refunded" in types
