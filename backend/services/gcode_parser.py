"""解析 .gcode.3mf 提取耗材克重 + 打印时间（Phase 1.5 自动报价输入）。

.gcode.3mf 是 zip。两条解析路径：
  1. Metadata/slice_info.config（XML）—— 结构化、优先
  2. zip 内 gcode entry 头部注释（; used_filament / ; total_time）—— 兜底
返回 {filament_used_g, print_time_s}；解析不到返回 None（不阻塞下单，订单走管理员手填）。
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


def _parse_slice_info_xml(xml_bytes):
    """解析 slice_info.config XML，提取耗材克重(g) + 时间(s)。

    Bambu slice_info.config 结构随版本略异，用宽松搜索（去命名空间 + 多字段名兜底）。
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    texts = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if el.text and el.text.strip():
            texts.setdefault(tag, []).append(el.text.strip())
        for k, v in el.attrib.items():
            texts.setdefault(f"@{k.split('}')[-1].lower()}", []).append(v)

    weight = None
    for key in ("weight", "used_filament", "filament_used", "filamentweight"):
        for v in texts.get(key, []):
            f = _try_float(v)
            if f is not None:
                weight = f
                break
        if weight is not None:
            break

    time_s = None
    for key in ("time", "total_time", "estimated_print_time", "totaltime"):
        for v in texts.get(key, []):
            f = _try_float(v)
            if f is not None:
                time_s = f
                break
        if time_s is not None:
            break

    if weight is None and time_s is None:
        return None
    return {"filament_used_g": weight, "print_time_s": time_s}


def _parse_gcode_comments(gcode_text):
    """从 gcode 头部注释提取耗材/时间。BambuStudio 格式示例：
       ; used_filament = 123.45g   ; total_time = 12.3
       ; filament used[g] = 123.45  ; estimated printing time = 1h 30m 20s
    """
    head = gcode_text[:8192]

    weight = None
    m = re.search(r";\s*(?:used_filament|filament\s*used)\s*[=:]\s*(\d+\.?\d*)\s*g?", head, re.I)
    if m:
        weight = _try_float(m.group(1))

    time_s = None
    m = re.search(r";\s*total_time\s*[=:]\s*(\d+\.?\d*)", head, re.I)
    if m:
        time_s = _try_float(m.group(1))
    else:
        m = re.search(
            r";\s*estimated\s*(?:printing\s*)?time[^\d]*(\d+)\s*h\s*(\d+)\s*m\s*(\d+)\s*s",
            head, re.I,
        )
        if m:
            time_s = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))

    if weight is None and time_s is None:
        return None
    return {"filament_used_g": weight, "print_time_s": time_s}


def parse_gcode_3mf(path):
    """解析 .gcode.3mf。返回 {filament_used_g, print_time_s} 或 None。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()

            # 1. 优先 slice_info.config XML
            for name in names:
                low = name.lower()
                if low.endswith("slice_info.config"):
                    try:
                        res = _parse_slice_info_xml(zf.read(name))
                        if res:
                            return res
                    except Exception:
                        pass

            # 2. 兜底：找 gcode entry 读头部注释
            for name in names:
                low = name.lower()
                if low.endswith(".gcode") or low.endswith(".gcode.3mf"):
                    try:
                        text = zf.read(name).decode("utf-8", errors="ignore")
                        res = _parse_gcode_comments(text)
                        if res:
                            return res
                    except Exception:
                        pass
    except (zipfile.BadZipFile, OSError):
        return None
    return None
