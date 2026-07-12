"""Phase 1.5 自动报价测试：PricingService + gcode 解析 + 订单自动报价 + admin 用户搜索。

费率默认（conftest 每测试前重置）：base_fee=2 / machine_hour=10 / PLA=0.5 / PETG=0.6 / ABS=0.7。
"""
import io
import os
import tempfile
import zipfile
from decimal import Decimal

import pytest

from exts import db
from models import UserModel
from services.pricing import PricingService
from services.gcode_parser import parse_gcode_3mf


# ═══════════════════════════ PricingService.calc ═══════════════════════════
def test_calc_pla(app):
    """calc(100g, 3600s, PLA) = 2 + 100×0.5 + 1×10 = 62"""
    with app.app_context():
        assert PricingService.calc(100, 3600, "PLA") == Decimal("62.00")


def test_calc_material_diff(app):
    """PETG 0.6 → 2 + 60 + 10 = 72"""
    with app.app_context():
        assert PricingService.calc(100, 3600, "PETG") == Decimal("72.00")


def test_calc_unknown_material_falls_back_pla(app):
    with app.app_context():
        assert PricingService.calc(100, 3600, "NYLON") == Decimal("62.00")


def test_calc_default_material_none(app):
    with app.app_context():
        assert PricingService.calc(100, 3600, None) == Decimal("62.00")


def test_calc_none_weight_time(app):
    """weight/time 为 None 当 0：calc = base_fee + 0 + 0 = 2"""
    with app.app_context():
        assert PricingService.calc(None, None, "PLA") == Decimal("2.00")


def test_describe_breakdown(app):
    with app.app_context():
        d = PricingService.describe(100, 3600, "PLA")
        assert d["credit"] == Decimal("62.00")
        assert d["base_fee"] == Decimal("2")
        assert d["material_cost"] == Decimal("50.00")
        assert d["machine_cost"] == Decimal("10.00")


# ═══════════════════════════ gcode 解析 ═══════════════════════════
def _make_gcode_3mf(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def test_parse_from_gcode_comments(app):
    """gcode 注释路径：; used_filament / ; total_time"""
    with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as t:
        path = t.name
    try:
        _make_gcode_3mf(path, {
            "Metadata/slice_info.config": "<slice_info/>",
            "Metadata/plate_1.gcode": (
                "; BambuStudio\n; used_filament = 25.50g\n; total_time = 1800\nG0 X0\n"
            ),
        })
        res = parse_gcode_3mf(path)
        assert res is not None
        assert res["filament_used_g"] == pytest.approx(25.5)
        assert res["print_time_s"] == pytest.approx(1800.0)
    finally:
        os.unlink(path)


def test_parse_from_slice_info_xml(app):
    """XML 路径：slice_info.config 含 weight/time"""
    with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as t:
        path = t.name
    try:
        _make_gcode_3mf(path, {
            "Metadata/slice_info.config": (
                '<?xml version="1.0"?><config>'
                "<filament><weight>30.5</weight></filament>"
                "<time>2400</time></config>"
            ),
        })
        res = parse_gcode_3mf(path)
        assert res is not None
        assert res["filament_used_g"] == pytest.approx(30.5)
        assert res["print_time_s"] == pytest.approx(2400.0)
    finally:
        os.unlink(path)


def test_parse_bad_zip_returns_none(app):
    with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as t:
        t.write(b"not a zip")
        path = t.name
    try:
        assert parse_gcode_3mf(path) is None
    finally:
        os.unlink(path)


# ═══════════════════════════ 订单自动报价（HTTP） ═══════════════════════════
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


def _gcode_3mf_buf(filament_g=20.0, time_s=3600):
    """构造一个最小 .gcode.3mf（zip + gcode 注释）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "Metadata/plate_1.gcode",
            f"; used_filament = {filament_g}g\n; total_time = {time_s}\nG0 X0\n",
        )
    buf.seek(0)
    return buf


def test_create_order_gcode_auto_quote(app):
    """上传 .gcode.3mf → 自动报价 + WAITING_CONFIRM + estimated_credit 填。"""
    client = app.test_client()
    _, token = _register(client, app, "autoq@x.com")
    # calc(20g, 3600s, PLA) = 2 + 20×0.5 + 1×10 = 22
    r = client.post(
        "/orders/", headers=_h(token),
        data={"material": "PLA", "file": (_gcode_3mf_buf(), "part.gcode.3mf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["status"] == "WAITING_CONFIRM"
    assert data["public_status"] == "待确认"
    assert data["estimated_credit"] == "22.00"
    assert "自动报价" in r.get_json()["message"]


def test_create_order_3mf_no_auto_quote(app):
    """上传 .3mf（未切片）→ QUOTING + estimated_credit=None。"""
    client = app.test_client()
    _, token = _register(client, app, "manualq@x.com")
    r = client.post(
        "/orders/", headers=_h(token),
        data={"material": "PLA", "file": (io.BytesIO(b"fake 3mf"), "model.3mf")},
        content_type="multipart/form-data",
    )
    data = r.get_json()["data"]
    assert data["status"] == "QUOTING"
    assert data["estimated_credit"] is None


# ═══════════════════════════ admin 用户搜索 + 费率配置 ═══════════════════════════
def test_admin_search_users(app):
    client = app.test_client()
    _register(client, app, "alice@x.com")
    _register(client, app, "bob@x.com")
    _, atoken = _register(client, app, "admin@x.com", role="admin")

    r = client.get("/admin/users?q=alice", headers=_h(atoken))
    data = r.get_json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["email"] == "alice@x.com"
    assert "available" in data["items"][0]

    r = client.get("/admin/users", headers=_h(atoken))
    assert r.get_json()["data"]["total"] == 3


def test_admin_pricing_crud(app):
    client = app.test_client()
    _, atoken = _register(client, app, "padmin@x.com", role="admin")

    # 列表（默认 5 条）
    r = client.get("/admin/pricing", headers=_h(atoken))
    assert r.get_json()["code"] == 200
    assert len(r.get_json()["data"]["items"]) >= 3

    # 加新材料 NYLON
    r = client.post("/admin/pricing", headers=_h(atoken),
                    json={"key": "material:NYLON", "value": 0.8, "label": "NYLON"})
    assert r.get_json()["code"] == 200
    pid = r.get_json()["data"]["id"]

    # 改 value
    r = client.put(f"/admin/pricing/{pid}", headers=_h(atoken), json={"value": 0.9})
    assert r.get_json()["code"] == 200
    assert r.get_json()["data"]["value"] == "0.90"

    # NYLON 单价生效
    with app.app_context():
        # calc(100g NYLON) 现在 0.9：2 + 90 + 10 = 102
        assert PricingService.calc(100, 3600, "NYLON") == Decimal("102.00")


# ═══════════════════════════ admin 上传切片产物（Phase 4 路径 B） ═══════════════════════════
def test_admin_upload_sliced_auto_quote(app):
    """admin 上传 .gcode.3mf 切片产物 → 解析 + 自动报价 + QUOTING→WAITING_CONFIRM。"""
    client = app.test_client()
    cuid, ctoken = _register(client, app, "slicecust@x.com")
    _, atoken = _register(client, app, "sliceadmin@x.com", role="admin")
    # 客户建 .3mf 订单（无文件，QUOTING）
    oid = client.post("/orders/", headers=_h(ctoken), data={"material": "PLA"}).get_json()["data"]["id"]

    # admin upload-sliced（40g/3600s → calc=2+20+10=32）
    r = client.post(
        f"/admin/orders/{oid}/upload-sliced", headers=_h(atoken),
        data={"file": (_gcode_3mf_buf(filament_g=40, time_s=3600), "sliced.gcode.3mf")},
        content_type="multipart/form-data",
    )
    assert r.get_json()["code"] == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["estimated_credit"] == "32.00"
    assert data["order_status"] == "WAITING_CONFIRM"

    # 订单查回确认
    detail = client.get(f"/orders/{oid}", headers=_h(ctoken)).get_json()["data"]
    assert detail["status"] == "WAITING_CONFIRM"
    assert detail["estimated_credit"] == "32.00"
