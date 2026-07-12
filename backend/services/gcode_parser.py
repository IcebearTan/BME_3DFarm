"""解析 .gcode.3mf 提取耗材克重 + 打印时间（Phase 1.5 自动报价输入）。

.gcode.3mf 是 zip。两条解析路径，兼容 BambuStudio 真实导出格式：
  1. Metadata/slice_info.config（XML）—— BambuStudio 用 <metadata key="weight" value=".."/>
     和 <metadata key="prediction" value="秒"/>；<filament used_g=".."/>；兼容 <weight>X</weight> 标签
  2. zip 内 gcode entry 头部注释 —— ; total filament weight [g] : X / ; total estimated time: Xh Ym Zs
     兼容 ; used_filament = Xg / ; total_time = X
返回 {filament_used_g, print_time_s}；解析不到返回 None（订单走管理员手填）。
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
    """解析 slice_info.config XML。

    BambuStudio 真实格式：<metadata key="weight" value="36.56"/> + <metadata key="prediction" value="8657"/>（秒）
    兼容标签文本：<weight>36.56</weight> / <filament used_g=".."/>
    """
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
        if tag == "filament":
            for attr in ("used_g", "used_m", "weight"):
                if attr in el.attrib:
                    kv.setdefault(f"filament_{attr}", el.attrib[attr])

    def first(*keys):
        for k in keys:
            if k in kv:
                return kv[k]
            if k in tag_texts:
                return tag_texts[k][0]
        return None

    weight = _try_float(first("weight", "used_filament", "filament_used",
                              "filament_weight", "filament_used_g"))
    time_s = _try_float(first("prediction", "total_time", "time",
                              "estimated_print_time", "totaltime"))

    if weight is None and time_s is None:
        return None
    return {"filament_used_g": weight, "print_time_s": time_s}


def _parse_gcode_comments(gcode_text):
    """从 gcode 头部注释提取耗材/时间。

    BambuStudio：; total filament weight [g] : 36.56 / ; total estimated time: 2h 24m 17s
    兼容：; used_filament = 36.56g / ; total_time = 1800 / ; estimated printing time = 1h 30m 20s
    """
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
    """解析 .gcode.3mf。返回 {filament_used_g, print_time_s} 或 None。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()

            # 1. 优先 slice_info.config XML
            for name in names:
                if name.lower().endswith("slice_info.config"):
                    try:
                        res = _parse_slice_info_xml(zf.read(name))
                        if res:
                            return res
                    except Exception:
                        pass

            # 2. 兜底：找 .gcode entry 读头部注释
            for name in names:
                if name.lower().endswith(".gcode"):
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
