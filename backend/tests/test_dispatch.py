"""Phase 3 半自动调度测试（mock Bambuddy 写入，不依赖真实 Bambuddy/打印机）。

覆盖：_do_dispatch 建 job（upload+queue）/ 复用 archive_id / _do_cancel / admin HTTP dispatch / 拒绝场景。
"""
import io
import uuid

from exts import db
from models import (
    UserModel, PrintOrderModel, OrderFileModel, BambuddyJobModel,
)
from services import CreditService, OrderStateMachine
from services.storage import storage


def _setup_ready_order(app, uid):
    """建 READY_TO_PRINT 订单 + .gcode.3mf 文件上传到 MinIO（test bucket）。返回 order_id。"""
    with app.app_context():
        CreditService.grant(uid, 100, source="test")
        order = PrintOrderModel(
            order_no=f"PO{uuid.uuid4().hex[:8]}", user_id=uid,
            status=PrintOrderModel.STATUS_DRAFT, material="PLA", quantity=1,
        )
        db.session.add(order)
        db.session.commit()
        order.estimated_credit = 30
        for s in (PrintOrderModel.STATUS_FILE_UPLOADED, PrintOrderModel.STATUS_QUOTING,
                  PrintOrderModel.STATUS_WAITING_CONFIRM):
            OrderStateMachine.transition(order, s)
        CreditService.freeze(uid, order.id, 30, quote_version=1)
        OrderStateMachine.transition(order, PrintOrderModel.STATUS_CREDIT_RESERVED)
        order.frozen_credit = 30
        for s in (PrintOrderModel.STATUS_REVIEWING, PrintOrderModel.STATUS_APPROVED,
                  PrintOrderModel.STATUS_READY_TO_PRINT):
            OrderStateMachine.transition(order, s)
        db.session.commit()

        content = b"fake sliced gcode.3mf content"
        key = f"orders/{order.id}/{uuid.uuid4().hex}.gcode.3mf"
        storage.put_object(key, io.BytesIO(content), len(content),
                           content_type="application/octet-stream")
        db.session.add(OrderFileModel(
            order_id=order.id, file_type=OrderFileModel.FILE_SLICED,
            original_filename="part.gcode.3mf", storage_key=key,
            content_type="application/octet-stream",
            size_bytes=len(content), sha256="x",
        ))
        db.session.commit()
        return order.id


def _register(client, app, email, role="customer"):
    r = client.post("/auth/register", json={"email": email, "password": "123", "username": email})
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


def _mock_adapter(monkeypatch, upload_resp=None, queue_resp=None, cancel_calls=None,
                  queue_calls=None):
    from tasks import dispatch
    monkeypatch.setattr(
        dispatch.BambuddyAdapter, "upload_archive",
        lambda self, data, name, ct: upload_resp or {"id": 123},
    )
    monkeypatch.setattr(
        dispatch.BambuddyAdapter, "add_to_queue",
        lambda self, aid, pid, plate_id=1, ams_mapping=None, use_ams=True: (
            queue_calls.append({"aid": aid, "pid": pid, "ams_mapping": ams_mapping})
            if queue_calls is not None else None
        ) or (queue_resp or {"id": 456}),
    )
    if cancel_calls is not None:
        monkeypatch.setattr(
            dispatch.BambuddyAdapter, "remove_queue_item",
            lambda self, qid: cancel_calls.append(qid) or {},
        )
    # mock storage.get_object（避开 MinIO bucket 单例坑：app.py import 时 init_app 设 dev bucket）
    class _FakeResp:
        def read(self):
            return b"fake gcode.3mf content"

        def close(self):
            pass

    monkeypatch.setattr("services.storage.storage.get_object", lambda key: _FakeResp())


# ═══════════════════════════ _do_dispatch ═══════════════════════════
def test_do_dispatch_creates_job(app, make_user, monkeypatch):
    uid = make_user()
    oid = _setup_ready_order(app, uid)
    _mock_adapter(monkeypatch, upload_resp={"id": 123}, queue_resp={"id": 456})

    from tasks import dispatch
    with app.app_context():
        result = dispatch._do_dispatch(oid, 7)
        assert result["status"] == "dispatched"
        assert result["archive_id"] == 123
        assert result["queue_id"] == 456
        job = BambuddyJobModel.query.filter_by(order_id=oid).first()
        assert job.bambuddy_archive_id == 123
        assert job.bambuddy_queue_id == 456
        assert job.bambuddy_printer_id == 7
        assert job.mapping_confidence == "exact"
        assert job.dispatched_at is not None


def test_do_dispatch_reuses_archive_id(app, make_user, monkeypatch):
    """已绑 archive_id 时不重复 upload。"""
    uid = make_user()
    oid = _setup_ready_order(app, uid)
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        db.session.add(BambuddyJobModel(
            order_id=oid, order_no=order.order_no, bambuddy_archive_id=999,
        ))
        db.session.commit()

    upload_calls = []
    from tasks import dispatch
    monkeypatch.setattr(
        dispatch.BambuddyAdapter, "upload_archive",
        lambda self, *a, **kw: upload_calls.append(1) or {"id": 0},
    )
    monkeypatch.setattr(
        dispatch.BambuddyAdapter, "add_to_queue",
        lambda self, aid, pid, plate_id=1, ams_mapping=None, use_ams=True: {"id": 456},
    )
    with app.app_context():
        result = dispatch._do_dispatch(oid, 7)
        assert result["archive_id"] == 999  # 复用，没 upload
        assert upload_calls == []


def test_do_dispatch_wrong_status(app, make_user, monkeypatch):
    uid = make_user()
    oid = _setup_ready_order(app, uid)
    _mock_adapter(monkeypatch)
    # 把订单改成 PRINTING（非 READY_TO_PRINT）
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        OrderStateMachine.transition(order, PrintOrderModel.STATUS_PRINTING)
        db.session.commit()
    from tasks import dispatch
    with app.app_context():
        result = dispatch._do_dispatch(oid, 7)
        assert result["status"] == "wrong_status"


# ═══════════════════════════ _do_cancel ═══════════════════════════
def test_do_cancel_removes_queue(app, make_user, monkeypatch):
    uid = make_user()
    oid = _setup_ready_order(app, uid)
    _mock_adapter(monkeypatch)
    cancel_calls = []
    from tasks import dispatch
    with app.app_context():
        dispatch._do_dispatch(oid, 7)
    _mock_adapter(monkeypatch, cancel_calls=cancel_calls)
    with app.app_context():
        result = dispatch._do_cancel(oid)
        assert result["status"] == "cancelled"
        assert cancel_calls == [456]
        job = BambuddyJobModel.query.filter_by(order_id=oid).first()
        assert job.bambuddy_queue_id is None


# ═══════════════════════════ admin HTTP ═══════════════════════════
def test_admin_dispatch_http(app, make_user, monkeypatch):
    cuid = make_user()
    oid = _setup_ready_order(app, cuid)
    _mock_adapter(monkeypatch)
    client = app.test_client()
    _, atoken = _register(client, app, "dadmin@x.com", role="admin")

    r = client.post(f"/admin/orders/{oid}/dispatch", headers=_h(atoken),
                    json={"bambuddy_printer_id": 7})
    assert r.get_json()["code"] == 200
    assert r.get_json()["data"]["status"] == "dispatched"


def test_admin_dispatch_no_sliced_file_rejected(app, make_user, monkeypatch):
    """订单无 .gcode.3mf → 409。"""
    cuid = make_user()
    oid = _setup_ready_order(app, cuid)
    # 删 order_file
    with app.app_context():
        OrderFileModel.query.filter_by(order_id=oid).delete()
        db.session.commit()
    _mock_adapter(monkeypatch)
    client = app.test_client()
    _, atoken = _register(client, app, "dadmin2@x.com", role="admin")
    r = client.post(f"/admin/orders/{oid}/dispatch", headers=_h(atoken),
                    json={"bambuddy_printer_id": 7})
    assert r.status_code == 409


# ═══════════════════════════ Phase 4：ams_mapping 透传 + AMS 校验 ═══════════════════════════
def test_do_dispatch_passes_and_persists_ams_mapping(app, make_user, monkeypatch):
    """_do_dispatch(ams_mapping=[1,2]) → 透传给 add_to_queue + 持久化到 job + 返回。"""
    uid = make_user()
    oid = _setup_ready_order(app, uid)
    queue_calls = []
    _mock_adapter(monkeypatch, queue_calls=queue_calls)
    from tasks import dispatch
    with app.app_context():
        result = dispatch._do_dispatch(oid, 7, ams_mapping=[1, 2])
        assert result["status"] == "dispatched"
        assert result["ams_mapping"] == [1, 2]
        # 透传给 add_to_queue
        assert queue_calls[0]["ams_mapping"] == [1, 2]
        # 持久化到 job
        job = BambuddyJobModel.query.filter_by(order_id=oid).first()
        assert job.ams_mapping == [1, 2]


def _set_order_filaments(app, oid, filaments):
    """给订单塞 parsed_filaments（模拟 gcode 解析产物，供 AMS 校验）。"""
    with app.app_context():
        order = db.session.get(PrintOrderModel, oid)
        order.parsed_filaments = filaments
        db.session.commit()


def _make_printer(app, bambuddy_id, trays_spec):
    """建 PrinterModel + status_detail.ams（trays_spec 同 test_ams_matcher 的 _ams 格式）。"""
    from models import PrinterModel
    tray_list = []
    for i, (state, ttype, tcolor, remain, idx) in enumerate(trays_spec):
        tray = {"id": str(i), "state": state, "remain": remain}
        if ttype is not None:
            tray["tray_type"] = ttype
        if tcolor is not None:
            tray["tray_color"] = tcolor
        if idx is not None:
            tray["tray_info_idx"] = idx
        tray_list.append(tray)
    with app.app_context():
        db.session.add(PrinterModel(
            public_name=f"P-{bambuddy_id}", bambuddy_printer_id=bambuddy_id,
            model="P1S", has_ams=True, status="idle", source="real",
            status_detail={"ams": [{"id": "0", "tray": tray_list}]},
        ))
        db.session.commit()


def test_dispatch_auto_match_uses_ams_mapping(app, make_user, monkeypatch):
    """没传 ams_mapping + 打印机料盘匹配 → 用 match_ams 的映射透传下发。"""
    cuid = make_user()
    oid = _setup_ready_order(app, cuid)
    _set_order_filaments(app, oid, [
        {"id": "1", "type": "PLA", "color": "#00AE42", "used_g": "36.0", "tray_info_idx": "GFA00"}
    ])
    _make_printer(app, 7, [(3, "PLA", "00AE42FF", 100, "GFA00")])
    queue_calls = []
    _mock_adapter(monkeypatch, queue_calls=queue_calls)
    client = app.test_client()
    _, atoken = _register(client, app, "autoadmin@x.com", role="admin")

    r = client.post(f"/admin/orders/{oid}/dispatch", headers=_h(atoken),
                    json={"bambuddy_printer_id": 7})
    assert r.get_json()["code"] == 200, r.get_json()
    # auto-match 算出 ams_mapping=[1] 并透传
    assert queue_calls[0]["ams_mapping"] == [1]


def test_dispatch_mismatch_returns_409(app, make_user, monkeypatch):
    """没传 ams_mapping + 打印机无对应料 → 409 + match_result（提示手选）。"""
    cuid = make_user()
    oid = _setup_ready_order(app, cuid)
    _set_order_filaments(app, oid, [
        {"id": "1", "type": "NYLON", "color": "#000000", "used_g": "20.0"}
    ])
    _make_printer(app, 7, [(3, "PLA", "00AE42FF", 100, None)])
    _mock_adapter(monkeypatch)
    client = app.test_client()
    _, atoken = _register(client, app, "misadmin@x.com", role="admin")

    r = client.post(f"/admin/orders/{oid}/dispatch", headers=_h(atoken),
                    json={"bambuddy_printer_id": 7})
    assert r.status_code == 409
    data = r.get_json()["data"]
    assert data["match_result"]["matched"] is False


def test_dispatch_explicit_ams_mapping_skips_check(app, make_user, monkeypatch):
    """admin 手选（传 ams_mapping）→ 跳过校验直接透传，即使料盘不匹配。"""
    cuid = make_user()
    oid = _setup_ready_order(app, cuid)
    _set_order_filaments(app, oid, [
        {"id": "1", "type": "NYLON", "color": "#000000", "used_g": "20.0"}
    ])
    _make_printer(app, 7, [(3, "PLA", "00AE42FF", 100, None)])
    queue_calls = []
    _mock_adapter(monkeypatch, queue_calls=queue_calls)
    client = app.test_client()
    _, atoken = _register(client, app, "manadmin@x.com", role="admin")

    r = client.post(f"/admin/orders/{oid}/dispatch", headers=_h(atoken),
                    json={"bambuddy_printer_id": 7, "ams_mapping": [1]})
    assert r.get_json()["code"] == 200, r.get_json()
    # admin 手选的 [1] 原样透传，未被 match_ams 改写
    assert queue_calls[0]["ams_mapping"] == [1]


def test_dispatch_preview_returns_match_result(app, make_user, monkeypatch):
    """dispatch/preview 纯查询：返回期望 + match_result，不下发。"""
    cuid = make_user()
    oid = _setup_ready_order(app, cuid)
    _set_order_filaments(app, oid, [
        {"id": "1", "type": "PLA", "color": "#00AE42", "used_g": "36.0"}
    ])
    _make_printer(app, 7, [(3, "PLA", "00AE42FF", 100, None)])
    _mock_adapter(monkeypatch)  # 不应被调用（preview 不下发）
    client = app.test_client()
    _, atoken = _register(client, app, "prevadmin@x.com", role="admin")

    r = client.post(f"/admin/orders/{oid}/dispatch/preview", headers=_h(atoken),
                    json={"bambuddy_printer_id": 7})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert len(data["expected_filaments"]) == 1
    assert data["match_result"]["matched"] is True
    assert data["match_result"]["ams_mapping"] == [1]
