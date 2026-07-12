"""解析 .gcode.3mf 提取耗材克重 + 打印时间 + 多色 filament / nozzle（Phase 1 扩展）。

.gcode.3mf 是 zip。两条解析路径，兼容 BambuStudio 真实导出格式：
  1. Metadata/slice_info.config（XML）—— 多色真相源
       BambuStudio 真实格式（实测确认）：
         <filament id="1" type="PLA" color="#00AE42" used_g="36.56" tray_info_idx="GFA00"/>
         <nozzle extruder_id="1" nozzle_diameter="0.4"/>
         <metadata key="weight" value=".."/> + <metadata key="prediction" value="秒"/>
       兼容标签文本：<weight>X</weight> / <filament used_g=".."/> / <time>X</time>
  2. zip 内 gcode entry 头部注释 —— ; total filament weight [g] : X / ; total estimated time: Xh Ym Zs
     兼容 ; used_filament = Xg / ; total_time = X
     注释路径只给 weight/time，无 filaments → AMS 校验降级为"无法校验"。

返回（归一化，新字段总在）：
  {filament_used_g, print_time_s, filaments:[{id/tray_info_idx/type/color/used_m/used_g}],
   nozzles:[{id/extruder_id/nozzle_diameter}], plate_index}
解析不到返回 None（订单走人工报价）。

extract_ams_expectation(parsed) 派生 gcode 期望料盘列表（AMS 校验输入）。
"""
import re
import zipfile
import xml.etree.ElementTree as ET


def _try_float(s):
    if s is None:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _try_int(s):
    if s is None:
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _filament_from_el(el):
    """从 <filament> 元素抽 {id, tray_info_idx, type, color, used_m, used_g}。

    兼容两种写法：属性（BambuStudio 真实）+ 子标签文本（旧测试 <filament><weight>X</weight>）。
    """
    item = {}
    for attr in ("id", "tray_info_idx", "type", "color", "used_m", "used_g"):
        if attr in el.attrib:
            item[attr] = el.attrib[attr]
    for child in el:
        ctag = child.tag.split("}")[-1].lower()
        text = child.text.strip() if child.text and child.text.strip() else None
        if not text:
            continue
        if ctag == "weight" and "used_g" not in item:
            item["used_g"] = text
        elif ctag in ("used_g", "used_m", "type", "color", "tray_info_idx", "id") and ctag not in item:
            item[ctag] = text
    return item or None


def _nozzle_from_el(el):
    """从 <nozzle> 元素抽 {id, extruder_id, nozzle_diameter}（属性 + 子标签兼容）。"""
    item = {}
    for attr in ("id", "extruder_id", "nozzle_diameter"):
        if attr in el.attrib:
            item[attr] = el.attrib[attr]
    for child in el:
        ctag = child.tag.split("}")[-1].lower()
        text = child.text.strip() if child.text and child.text.strip() else None
        if text and ctag in ("extruder_id", "nozzle_diameter", "id") and ctag not in item:
            item[ctag] = text
    return item or None


def _parse_slice_info_xml(xml_bytes):
    """解析 slice_info.config XML。返回归一化 dict 或 None。"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    kv = {}  # <metadata key value> 对
    tag_texts = {}  # 标签文本（兼容 <weight>X</weight>）
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if el.text and el.text.strip():
            tag_texts.setdefault(tag, []).append(el.text.strip())
        if tag == "metadata" and "key" in el.attrib and "value" in el.attrib:
            kv[el.attrib["key"].lower()] = el.attrib["value"]

    def first(*keys):
        for k in keys:
            if k in kv:
                return kv[k]
            if k in tag_texts:
                return tag_texts[k][0]
        return None

    # ── 多色 filament / nozzle 收集（root.iter 不限深度，<plate> 下也能抓到）──
    filaments = [f for f in (_filament_from_el(el) for el in root.iter("filament")) if f]
    nozzles = [n for n in (_nozzle_from_el(el) for el in root.iter("nozzle")) if n]

    plate_index = _try_int(first("index", "plate_index"))

    # weight：优先各 filament used_g 之和（多色不丢料），否则回退 metadata/裸标签
    weight = None
    if filaments:
        total = sum(_try_float(f.get("used_g")) or 0 for f in filaments)
        weight = total if total > 0 else None
    if weight is None:
        weight = _try_float(first("weight", "used_filament", "filament_used",
                                  "filament_weight", "filament_used_g"))
    time_s = _try_float(first("prediction", "total_time", "time",
                              "estimated_print_time", "totaltime"))

    if weight is None and time_s is None and not filaments:
        return None
    return {
        "filament_used_g": weight,
        "print_time_s": time_s,
        "filaments": filaments,
        "nozzles": nozzles,
        "plate_index": plate_index,
    }


def _parse_gcode_comments(gcode_text):
    """从 gcode 头部注释提取耗材/时间（无 filaments，AMS 校验降级）。"""
    head = gcode_text[:8192]

    weight = None
    for pat in (
        r";\s*total\s+filament\s+weight\s*\[g\]\s*[:=]\s*(\d+\.?\d*)",
        r";\s*(?:used_filament|filament\s*used)\s*[=:]\s*(\d+\.?\d*)\s*g?",
    ):
        m = re.search(pat, head, re.I)
        if m:
            weight = _try_float(m.group(1))
            break

    time_s = None
    m = re.search(r";\s*total_time\s*[=:]\s*(\d+\.?\d*)", head, re.I)
    if m:
        time_s = _try_float(m.group(1))
    else:
        m = re.search(
            r";\s*(?:total\s+estimated\s+time|model\s+printing\s+time|"
            r"estimated\s+printing\s+time)[^\d]*(\d+)\s*h\s*(\d+)\s*m\s*(\d+)\s*s",
            head, re.I,
        )
        if m:
            time_s = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))

    if weight is None and time_s is None:
        return None
    return {"filament_used_g": weight, "print_time_s": time_s}


def parse_gcode_3mf(path):
    """解析 .gcode.3mf。返回归一化 dict（含 filaments/nozzles/plate_index）或 None。"""
    result = None
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()

            # 1. 优先 slice_info.config XML（多色真相源）
            for name in names:
                if name.lower().endswith("slice_info.config"):
                    try:
                        res = _parse_slice_info_xml(zf.read(name))
                        if res:
                            result = res
                            break
                    except Exception:
                        pass

            # 2. 兜底：gcode entry 头部注释（无 filaments）
            if result is None:
                for name in names:
                    if name.lower().endswith(".gcode"):
                        try:
                            text = zf.read(name).decode("utf-8", errors="ignore")
                            res = _parse_gcode_comments(text)
                            if res:
                                result = res
                                break
                        except Exception:
                            pass
    except (zipfile.BadZipFile, OSError):
        return None

    if result is None:
        return None
    # 归一化：保证新字段总在（gcode 注释路径无 filaments）
    result.setdefault("filaments", [])
    result.setdefault("nozzles", [])
    result.setdefault("plate_index", None)
    return result


def extract_ams_expectation(parsed):
    """从 parsed 派生 gcode 期望料盘列表（AMS 校验输入）。

    每个 extruder 一条：{extruder_id, type, color, tray_info_idx, used_g}
    extruder_id 优先 filament.id（BambuStudio 设为挤出机序号），否则按下标 +1。
    无 filaments 返回 []（AMS 校验降级为"无法校验"）。
    """
    if not parsed:
        return []
    filaments = parsed.get("filaments") or []
    expectation = []
    for i, f in enumerate(filaments):
        eid = f.get("id") or f.get("extruder_id") or str(i + 1)
        expectation.append({
            "extruder_id": str(eid),
            "type": f.get("type"),
            "color": f.get("color"),
            "tray_info_idx": f.get("tray_info_idx"),
            "used_g": _try_float(f.get("used_g")),
        })
    return expectation


def main_material(parsed):
    """取 parsed 中 used_g 最大的 filament.type 作为主材料（计费 + 展示用）。

    多色按克重占比选主色；单色直接返回其 type；无 filament 返回 None。
    """
    if not parsed:
        return None
    filaments = parsed.get("filaments") or []
    if not filaments:
        return None
    best = max(filaments, key=lambda f: _try_float(f.get("used_g")) or 0)
    return best.get("type")


def extract_preview_png(path, max_bytes=2 * 1024 * 1024):
    """从 .gcode.3mf / .3mf 抽预览图 PNG 字节（BambuStudio 内嵌 Metadata/plate_1.png）。

    优先 plate_1 / cover（主板件预览），否则首个 PNG。无图、超 max_bytes、坏 zip 返回 None。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            pngs = [n for n in zf.namelist() if n.lower().endswith(".png")]
            if not pngs:
                return None

            def prio(n):
                low = n.lower()
                for i, key in enumerate(("plate_1", "plate 1", "cover")):
                    if key in low:
                        return i
                return 9

            pngs.sort(key=prio)
            info = zf.getinfo(pngs[0])
            if info.file_size > max_bytes:
                return None
            return zf.read(pngs[0])
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
