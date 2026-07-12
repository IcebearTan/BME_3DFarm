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
from services.gcode_parser import parse_gcode_3mf, extract_ams_expectation


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
        assert d["surcharge"] == Decimal("0")  # 默认不加手工费


def test_calc_surcharge_adds_manual_slice_fee(app):
    """surcharge=True 加 manual_slice_surcharge(默认5)：62 + 5 = 67"""
    with app.app_context():
        assert PricingService.calc(100, 3600, "PLA", surcharge=True) == Decimal("67.00")
        d = PricingService.describe(100, 3600, "PLA", surcharge=True)
        assert d["credit"] == Decimal("67.00")
        assert d["surcharge"] == Decimal("5")


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


# ═══════════════════════════ Phase 1：多色 filament / nozzle / AMS 期望 ═══════════════════════════
_MULTI_COLOR_XML = (
    '<?xml version="1.0"?><config><plate>'
    '<metadata key="index" value="1"/>'
    '<metadata key="prediction" value="8657"/>'
    '<filament id="1" type="PLA" color="#00AE42" used_g="36.56" tray_info_idx="GFA00"/>'
    '<filament id="2" type="PETG" color="#FFFFFFFF" used_g="12.34" tray_info_idx="GFA01"/>'
    '<nozzle extruder_id="1" nozzle_diameter="0.4"/>'
    '<nozzle extruder_id="2" nozzle_diameter="0.4"/>'
    '</plate></config>'
)


def test_parse_multi_color_filaments(app):
    """多色：filaments 收成 list 不丢料，weight=各 used_g 之和。"""
    with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as t:
        path = t.name
    try:
        _make_gcode_3mf(path, {"Metadata/slice_info.config": _MULTI_COLOR_XML})
        res = parse_gcode_3mf(path)
        assert res is not None
        assert len(res["filaments"]) == 2
        assert res["filaments"][0]["type"] == "PLA"
        assert res["filaments"][0]["tray_info_idx"] == "GFA00"
        assert res["filaments"][1]["type"] == "PETG"
        # 36.56 + 12.34 = 48.90
        assert res["filament_used_g"] == pytest.approx(48.90)
        assert res["print_time_s"] == pytest.approx(8657.0)
        assert res["plate_index"] == 1
    finally:
        os.unlink(path)


def test_parse_nozzles_collected(app):
    """nozzle 收集成 list，含 extruder_id。"""
    with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as t:
        path = t.name
    try:
        _make_gcode_3mf(path, {"Metadata/slice_info.config": _MULTI_COLOR_XML})
        res = parse_gcode_3mf(path)
        assert len(res["nozzles"]) == 2
        assert res["nozzles"][0]["extruder_id"] == "1"
        assert res["nozzles"][1]["extruder_id"] == "2"
    finally:
        os.unlink(path)


def test_parse_legacy_filament_weight_tag(app):
    """legacy 兼容：<filament><weight>X</weight> 落到 filament.used_g，weight 仍可算。"""
    with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as t:
        path = t.name
    try:
        _make_gcode_3mf(path, {
            "Metadata/slice_info.config": (
                '<?xml version="1.0"?><config>'
                '<filament id="1"><weight>18.0</weight></filament>'
                '<time>1200</time></config>'
            ),
        })
        res = parse_gcode_3mf(path)
        assert res is not None
        assert len(res["filaments"]) == 1
        assert res["filaments"][0]["used_g"] == "18.0"
        assert res["filament_used_g"] == pytest.approx(18.0)
    finally:
        os.unlink(path)


def test_parse_gcode_comments_path_has_empty_filaments(app):
    """gcode 注释兜底路径：有 weight/time 但 filaments 为空（AMS 校验降级）。"""
    with tempfile.NamedTemporaryFile(suffix=".gcode.3mf", delete=False) as t:
        path = t.name
    try:
        _make_gcode_3mf(path, {
            "Metadata/slice_info.config": "<slice_info/>",
            "Metadata/plate_1.gcode": "; used_filament = 9.9g\n; total_time = 600\n",
        })
        res = parse_gcode_3mf(path)
        assert res is not None
        assert res["filament_used_g"] == pytest.approx(9.9)
        assert res["filaments"] == []
        assert res["nozzles"] == []
        assert res["plate_index"] is None
    finally:
        os.unlink(path)


def test_extract_ams_expectation_multi(app):
    """多色派生 AMS 期望：extruder_id 取 filament.id。"""
    parsed = {
        "filaments": [
            {"id": "1", "type": "PLA", "color": "#00AE42", "used_g": "36.56", "tray_info_idx": "GFA00"},
            {"id": "2", "type": "PETG", "color": "#FFFFFFFF", "used_g": "12.34", "tray_info_idx": "GFA01"},
        ],
        "nozzles": [{"extruder_id": "1"}, {"extruder_id": "2"}],
    }
    exp = extract_ams_expectation(parsed)
    assert len(exp) == 2
    assert exp[0] == {
        "extruder_id": "1", "type": "PLA", "color": "#00AE42",
        "tray_info_idx": "GFA00", "used_g": 36.56,
    }
    assert exp[1]["extruder_id"] == "2"
    assert exp[1]["type"] == "PETG"


def test_extract_ams_expectation_empty_when_no_filaments(app):
    """gcode 注释路径（无 filaments）→ 空期望，AMS 校验降级。"""
    assert extract_ams_expectation({"filaments": [], "nozzles": []}) == []
    assert extract_ams_expectation(None) == []


def test_extract_ams_expectation_extruder_fallback_index(app):
    """filament 无 id 时按下标 +1 推断 extruder_id。"""
    parsed = {"filaments": [{"type": "PLA", "used_g": "10"}, {"type": "PETG", "used_g": "5"}]}
    exp = extract_ams_expectation(parsed)
    assert exp[0]["extruder_id"] == "1"
    assert exp[1]["extruder_id"] == "2"


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


# ═══════════════════════════ admin 上传切片产物（Phase 4 路径 B + 手工费） ═══════════════════════════
def test_admin_upload_sliced_auto_quote(app):
    """admin 上传 .gcode.3mf 切片产物 → 解析 + 自动报价（含手工费） + QUOTING→WAITING_CONFIRM。"""
    client = app.test_client()
    cuid, ctoken = _register(client, app, "slicecust@x.com")
    _, atoken = _register(client, app, "sliceadmin@x.com", role="admin")
    # 客户建 .3mf 订单（无文件，QUOTING）
    oid = client.post("/orders/", headers=_h(ctoken), data={"material": "PLA"}).get_json()["data"]["id"]

    # admin upload-sliced（40g/3600s → calc=2+20+10 + 手工费5 = 37）
    r = client.post(
        f"/admin/orders/{oid}/upload-sliced", headers=_h(atoken),
        data={"file": (_gcode_3mf_buf(filament_g=40, time_s=3600), "sliced.gcode.3mf")},
        content_type="multipart/form-data",
    )
    assert r.get_json()["code"] == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["estimated_credit"] == "37.00"
    assert data["order_status"] == "WAITING_CONFIRM"
    # 报价明细含手工费
    assert data["quote"]["surcharge"] == "5.00"
    assert data["quote"]["credit"] == "37.00"

    # 订单查回确认
    detail = client.get(f"/orders/{oid}", headers=_h(ctoken)).get_json()["data"]
    assert detail["status"] == "WAITING_CONFIRM"
    assert detail["estimated_credit"] == "37.00"


# ═══════════════════════════ Phase 3：preview 接口 + 双路径 create ═══════════════════════════
def _gcode_3mf_multi_buf():
    """构造多色 .gcode.3mf（slice_info.config 多 filament）。"""
    xml = (
        '<?xml version="1.0"?><config><plate>'
        '<metadata key="index" value="1"/>'
        '<metadata key="prediction" value="3600"/>'
        '<filament id="1" type="PLA" color="#00AE42" used_g="40.00" tray_info_idx="GFA00"/>'
        '<filament id="2" type="PETG" color="#FFFFFFFF" used_g="10.00" tray_info_idx="GFA01"/>'
        '<nozzle extruder_id="1" nozzle_diameter="0.4"/>'
        '<nozzle extruder_id="2" nozzle_diameter="0.4"/>'
        '</plate></config>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", xml)
    buf.seek(0)
    return buf


def test_preview_gcode_returns_quote_no_db(app):
    """preview：解析 .gcode.3mf 返回材料/多色/报价，不建订单不冻 credit。"""
    client = app.test_client()
    _, token = _register(client, app, "prev@x.com")
    r = client.post(
        "/orders/preview", headers=_h(token),
        data={"file": (_gcode_3mf_multi_buf(), "m.gcode.3mf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    # 主材料按 used_g 最大 → PLA（40g > 10g）
    assert data["material"] == "PLA"
    assert len(data["filaments"]) == 2
    assert data["filament_used_g"] == pytest.approx(50.0)
    # calc(50g, 3600s, PLA, surcharge=False) = 2 + 25 + 10 = 37
    assert data["quote"]["credit"] == "37.00"
    assert data["quote"]["surcharge"] == "0"
    # 不落库：my_orders 应为空
    lst = client.get("/orders/", headers=_h(token)).get_json()["data"]
    assert lst["total"] == 0


def test_preview_unparseable_returns_422(app):
    """preview 解析失败 → 422。"""
    client = app.test_client()
    _, token = _register(client, app, "prevbad@x.com")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", "<slice_info/>")
    buf.seek(0)
    r = client.post(
        "/orders/preview", headers=_h(token),
        data={"file": (buf, "empty.gcode.3mf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 422


def test_create_order_3mf_marks_manual_slice_path(app):
    """.3mf 上传 → is_manual_slice_path=True + QUOTING。"""
    client = app.test_client()
    _, token = _register(client, app, "manual@x.com")
    r = client.post(
        "/orders/", headers=_h(token),
        data={"file": (io.BytesIO(b"fake 3mf model"), "model.3mf"),
              "customer_note": "帮我切红色"},
        content_type="multipart/form-data",
    )
    data = r.get_json()["data"]
    assert data["status"] == "QUOTING"
    assert data["is_manual_slice_path"] is True
    assert data["estimated_credit"] is None
    assert "需管理员切片" in r.get_json()["message"]


def test_create_order_gcode_stores_parsed_filaments(app):
    """gcode.3mf 多色 → 存 parsed_filaments + 主材料从 gcode 推导。"""
    client = app.test_client()
    _, token = _register(client, app, "gcodecust@x.com")
    r = client.post(
        "/orders/", headers=_h(token),
        data={"file": (_gcode_3mf_multi_buf(), "m.gcode.3mf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["status"] == "WAITING_CONFIRM"
    assert data["material"] == "PLA"  # 主材料从 gcode 推导（无需客户手填）
    assert len(data["parsed_filaments"]) == 2
    assert data["is_manual_slice_path"] is False
    # calc(50g, 3600s, PLA, surcharge=False) = 37
    assert data["estimated_credit"] == "37.00"


def test_create_order_no_material_required(app):
    """material 不再必填（无 material 无文件也能下单 → QUOTING）。"""
    client = app.test_client()
    _, token = _register(client, app, "nomat@x.com")
    r = client.post("/orders/", headers=_h(token), data={"customer_note": "描述需求"})
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "QUOTING"
