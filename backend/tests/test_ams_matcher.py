"""AMS 料盘匹配器单测（Phase 4）—— 纯函数，无 DB/IO。

8 个用例：exact/color/type/none/颜色归一/空 tray 跳过/多色全匹配/多色部分。
"""
from services.ams_matcher import match_ams


def _ams(trays_spec):
    """构造 Bambu 风格 AMS status。
    trays_spec: list of (state, tray_type, tray_color, remain, tray_info_idx)；None 省略字段。
    tray_id 按 1-based 物理槽（含空槽）。
    """
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
    return {"ams": [{"id": "0", "tray": tray_list}]}


# ═══════════════════════════ 优先级：exact / color / type / none ═══════════════════════════
def test_match_exact():
    """tray_info_idx + type + color 全等 → exact，ams_mapping 给出。"""
    exp = [{"extruder_id": "1", "type": "PLA", "color": "#00AE42",
            "tray_info_idx": "GFA00", "used_g": 36.0}]
    res = match_ams(exp, _ams([(3, "PLA", "00AE42FF", 100, "GFA00")]))
    assert res["matched"] is True
    assert res["mappings"][0]["match_type"] == "exact"
    assert res["ams_mapping"] == [1]


def test_match_color_without_tray_info_idx():
    """type + color 等、无 tray_info_idx → color（exact 需 idx）。"""
    exp = [{"extruder_id": "1", "type": "PLA", "color": "#00AE42",
            "tray_info_idx": None}]
    res = match_ams(exp, _ams([(3, "PLA", "00AE42FF", 100, None)]))
    assert res["matched"] is True
    assert res["mappings"][0]["match_type"] == "color"
    assert res["ams_mapping"] == [1]


def test_match_type_only_wrong_color():
    """type 等、颜色不同 → type（仍算匹配，ams_mapping 给出）。"""
    exp = [{"extruder_id": "1", "type": "PLA", "color": "#00AE42"}]
    res = match_ams(exp, _ams([(3, "PLA", "FF0000FF", 100, None)]))
    assert res["matched"] is True
    assert res["mappings"][0]["match_type"] == "type"
    assert res["ams_mapping"] == [1]


def test_match_none_no_material():
    """无对应材料 → none，matched=False，ams_mapping=None。"""
    exp = [{"extruder_id": "1", "type": "NYLON", "color": "#000000"}]
    res = match_ams(exp, _ams([(3, "PLA", "00AE42FF", 100, None)]))
    assert res["matched"] is False
    assert res["mappings"][0]["match_type"] == "none"
    assert res["ams_mapping"] is None


# ═══════════════════════════ 颜色归一 ═══════════════════════════
def test_color_normalize_hash_and_alpha():
    """gcode #00AE42 ↔ AMS 00AE42FF 归一后等价 → color 匹配。"""
    exp = [{"extruder_id": "1", "type": "PLA", "color": "#00AE42"}]
    # tray 颜色 8 位 RGBA、无 #，归一取前 6 位
    res = match_ams(exp, _ams([(3, "PLA", "00AE42FF", 100, None)]))
    assert res["matched"] is True
    assert res["mappings"][0]["match_type"] in ("color", "exact")


# ═══════════════════════════ 空 tray 跳过 ═══════════════════════════
def test_empty_and_depleted_trays_skipped():
    """state!=3 或 remain<=0 的 tray 跳过 → 无可用 → matched=False。"""
    exp = [{"extruder_id": "1", "type": "PLA", "color": "#00AE42"}]
    # 槽1 空(state=1)、槽2 用尽(remain=0)：都不可用
    res = match_ams(exp, _ams([
        (1, "PLA", "00AE42FF", 100, None),
        (3, "PLA", "00AE42FF", 0, None),
    ]))
    assert res["matched"] is False
    assert "无可用" in res["reason"]


def test_unknown_remain_counts_as_available():
    """remain=-1（未知）+ state==3 → 算可用。"""
    exp = [{"extruder_id": "1", "type": "PLA", "color": "#00AE42"}]
    res = match_ams(exp, _ams([(3, "PLA", "00AE42FF", -1, None)]))
    assert res["matched"] is True
    assert res["ams_mapping"] == [1]


# ═══════════════════════════ 多色 ═══════════════════════════
def test_multi_color_full_match():
    """双 extruder 各匹配一个 tray → 全匹配，ams_mapping=[1,2]。"""
    exp = [
        {"extruder_id": "1", "type": "PLA", "color": "#00AE42"},
        {"extruder_id": "2", "type": "PETG", "color": "#FFFFFF"},
    ]
    res = match_ams(exp, _ams([
        (3, "PLA", "00AE42FF", 100, None),
        (3, "PETG", "FFFFFFFF", 100, None),
    ]))
    assert res["matched"] is True
    assert res["ams_mapping"] == [1, 2]
    assert res["mappings"][0]["actual_tray_id"] == 1
    assert res["mappings"][1]["actual_tray_id"] == 2


def test_multi_color_partial_match():
    """双 extruder：一个匹配、一个无对应材料 → matched=False，ams_mapping=None。"""
    exp = [
        {"extruder_id": "1", "type": "PLA", "color": "#00AE42"},
        {"extruder_id": "2", "type": "NYLON", "color": "#000000"},
    ]
    res = match_ams(exp, _ams([
        (3, "PLA", "00AE42FF", 100, None),
        (3, "PETG", "FFFFFFFF", 100, None),
    ]))
    assert res["matched"] is False
    assert res["ams_mapping"] is None
    assert res["mappings"][0]["match_type"] == "color"   # PLA 色匹配
    assert res["mappings"][1]["match_type"] == "none"    # NYLON 无料
    # 不全匹配也要给 alternatives，供 admin 手选
    assert len(res["mappings"][1]["alternatives"]) == 2


# ═══════════════════════════ 防御性：缺期望 / 缺 ams ═══════════════════════════
def test_no_expected_returns_unverifiable():
    """gcode 无期望（注释路径）→ matched=False，reason 说明降级。"""
    res = match_ams([], _ams([(3, "PLA", "00AE42FF", 100, None)]))
    assert res["matched"] is False
    assert res["ams_mapping"] is None
    assert "无法校验" in res["reason"]


def test_no_ams_status_returns_unmatched():
    """打印机无 ams 字段 → matched=False，不抛异常。"""
    exp = [{"extruder_id": "1", "type": "PLA", "color": "#00AE42"}]
    res = match_ams(exp, None)
    assert res["matched"] is False
    res2 = match_ams(exp, {"ams": []})
    assert res2["matched"] is False
