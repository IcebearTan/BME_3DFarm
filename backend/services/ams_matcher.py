"""AMS 料盘匹配器（Phase 4）—— 纯函数，无 DB/IO，可单测。

把 gcode 期望料盘（extract_ams_expectation 的输出）与打印机 AMS 实际料盘
（printer.status_detail.ams[*].tray[*]）对比，给出每个 extruder 的最佳匹配 tray
+ 一个可下发的 ams_mapping 数组（全匹配时）。

匹配优先级：exact（tray_info_idx + type + color 全等）> color（type + color）> type > none。
颜色归一：去 #、取前 6 位、大写（gcode `#00AE42` ↔ AMS `00AE42FF`）。
可用 tray：state==3 且（remain>0 或 remain==-1 未知）。

tray_id 约定：AMS 物理槽位 1-based 全局序号（unit0 槽 0→1..4，unit1→5..8，与 Bambu
ams_mapping 取值一致）。admin 可在 UI 手选覆盖（match_result 给出 alternatives）。
"""


def _try_float(s):
    if s is None:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _try_int(s, fallback=None):
    if s is None:
        return fallback
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return fallback


def _norm_color(c):
    """颜色归一：去 #、取前 6 位、大写。无返回 None。"""
    if c is None:
        return None
    s = str(c).strip().lstrip("#").upper()
    if not s:
        return None
    return s[:6] if len(s) >= 6 else s


def _tray_available(tray):
    """tray 是否可用：state==3 且 remain 充足（-1 表未知，算可用）。"""
    try:
        if int(tray.get("state")) != 3:
            return False
    except (ValueError, TypeError):
        return False
    remain = _try_float(tray.get("remain"))
    if remain is None:
        return True  # state==3 已表明有料；remain 缺失保守当可用
    return remain == -1 or remain > 0


def _extract_trays(printer_ams_status):
    """从 status_detail.ams 抽可用 tray 列表。

    防御性解析 ams 多种形态（list/{"ams":[...]}/None）+ tray 字段名兼容
    (tray_type/tray_color)。tray_id 用物理槽 1-based 全局序号。
    """
    ams = printer_ams_status
    if isinstance(ams, dict):
        ams = ams.get("ams") or ams.get("ams_list") or []
    if not isinstance(ams, list):
        return []
    trays = []
    slot = 0
    for unit in ams:
        if not isinstance(unit, dict):
            continue
        unit_trays = unit.get("tray") or unit.get("trays") or []
        if isinstance(unit_trays, dict):
            unit_trays = unit_trays.get("tray") or []
        if not isinstance(unit_trays, list):
            continue
        for t in unit_trays:
            if not isinstance(t, dict):
                continue
            slot += 1  # 物理槽序号（含空槽，保持位置对齐）
            if not _tray_available(t):
                continue
            trays.append({
                "tray_id": slot,
                "type": (str(t.get("tray_type") or t.get("type") or "")).strip() or None,
                "raw_color": t.get("tray_color") or t.get("color"),
                "tray_info_idx": (str(t.get("tray_info_idx") or "")).strip() or None,
                "remain": _try_float(t.get("remain")),
            })
    return trays


def _classify(expected, tray):
    """期望 filament vs 实际 tray 的匹配等级：exact > color > type > none。"""
    e_type = (str(expected.get("type") or "")).strip().upper() or None
    e_color = _norm_color(expected.get("color"))
    e_idx = (str(expected.get("tray_info_idx") or "")).strip().upper() or None

    t_type = (str(tray.get("type") or "")).strip().upper() or None
    t_color = _norm_color(tray.get("raw_color"))
    t_idx = (str(tray.get("tray_info_idx") or "")).strip().upper() or None

    type_match = bool(e_type) and e_type == t_type
    color_match = bool(e_color) and e_color == t_color
    idx_match = bool(e_idx) and e_idx == t_idx

    if idx_match and type_match and color_match:
        return "exact"
    if type_match and color_match:
        return "color"
    if type_match:
        return "type"
    return "none"


def match_ams(expected_filaments, printer_ams_status):
    """匹配 gcode 期望料盘 vs 打印机 AMS 实际料盘。

    返回：
      {matched: bool,
       mappings: [{extruder_id, expected, actual_tray_id, actual_tray, match_type, alternatives}],
       ams_mapping: [int,...] | None,   # 全匹配才有；下标=extruder 序(0-based)，值=tray_id
       reason?: str}                    # matched=False 时的原因（无期望/无可用 tray）
    """
    expected = list(expected_filaments or [])
    trays = _extract_trays(printer_ams_status)

    if not expected:
        return {"matched": False, "mappings": [], "ams_mapping": None,
                "reason": "gcode 无期望料盘信息（AMS 校验降级为无法校验）"}
    if not trays:
        return {"matched": False, "mappings": [], "ams_mapping": None,
                "reason": "打印机无可用 AMS 料盘（state==3 且有料）"}

    # 按优先级分轮指派（exact → color → type），一个 tray 只能给一个 extruder
    assigned = {}    # extruder_id -> tray_id
    by_type = {}     # extruder_id -> match_type
    used = set()
    for target in ("exact", "color", "type"):
        for exp in expected:
            eid = str(exp.get("extruder_id"))
            if eid in assigned:
                continue
            for t in trays:
                if t["tray_id"] in used:
                    continue
                if _classify(exp, t) == target:
                    assigned[eid] = t["tray_id"]
                    by_type[eid] = target
                    used.add(t["tray_id"])
                    break

    all_matched = len(assigned) == len(expected)
    tray_by_id = {t["tray_id"]: t for t in trays}
    mappings = []
    for exp in expected:
        eid = str(exp.get("extruder_id"))
        chosen_id = assigned.get(eid)
        chosen = tray_by_id.get(chosen_id) if chosen_id is not None else None
        alternatives = [
            {
                "tray_id": t["tray_id"], "type": t.get("type"),
                "color": t.get("raw_color"), "match_type": _classify(exp, t),
            }
            for t in trays if t["tray_id"] != chosen_id
        ]
        mappings.append({
            "extruder_id": eid,
            "expected": {
                "type": exp.get("type"), "color": exp.get("color"),
                "tray_info_idx": exp.get("tray_info_idx"), "used_g": exp.get("used_g"),
            },
            "actual_tray_id": chosen_id,
            "actual_tray": ({
                "tray_id": chosen["tray_id"], "type": chosen.get("type"),
                "color": chosen.get("raw_color"),
                "tray_info_idx": chosen.get("tray_info_idx"),
                "remain": chosen.get("remain"),
            } if chosen else None),
            "match_type": by_type.get(eid, "none"),
            "alternatives": alternatives,
        })

    ams_mapping = None
    if all_matched:
        ordered = sorted(mappings, key=lambda m: _try_int(m["extruder_id"], 0))
        ams_mapping = [m["actual_tray_id"] for m in ordered]
    return {"matched": all_matched, "mappings": mappings, "ams_mapping": ams_mapping}
