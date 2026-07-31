"""Bambuddy 响应 schema 校验 —— 断言 3DFarm 消费的全部字段形态。

poller / dispatch / webhook 从 Bambuddy 响应里读哪些字段，这里就校验哪些。
用途：
  - tests/test_bambuddy_fixtures.py：对每份 fixture 跑校验，Bambuddy 换版本改字段时大声失败
  - tools/canary.py：生产只读冒烟，实时抓 schema drift
  - tools/bambuddy_recorder.py：路径规范化（logical_path）复用

校验风格：宽松——只查"字段存在 + 类型可解析"，不过度约束。
目标是"能解析"，不是"响应长这样"。非 2xx 响应不跑校验（错误 body 形态各异）。
"""
import json


# ─────────────────────── 路径规范化 ───────────────────────
def logical_path(raw_path, api_prefix="/api/v1"):
    """请求路径 → 逻辑键（去前缀/去尾斜杠/数字段模板化为 {id}）。

    /api/v1/printers/2/status → printers/{id}/status
    /health                    → health
    /printers/                 → printers
    数字段模板化让 dev(VP id 1) 与 prod(P1S id 2) 的采集可对任意打印机 id 回放。
    """
    path = raw_path.split("?", 1)[0]
    if path.startswith(api_prefix):
        path = path[len(api_prefix):]
    if not path.startswith("/"):
        path = "/" + path
    segs = [s for s in path.strip("/").split("/") if s != ""]
    out = []
    for i, s in enumerate(segs):
        if i > 0 and s.isdigit():
            out.append("{id}")
        else:
            out.append(s)
    return "/".join(out)


# ─────────────────────── 校验器（返回错误字符串列表，空 = 通过）───────────────────────
def _is_num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def validate_printer_status(d):
    """GET /printers/{id}/status —— poller 逐字段读取的主响应。"""
    if not isinstance(d, dict):
        return ["printer_status 非 dict"]
    errs = []
    has_state = bool(
        d.get("gcode_state") or d.get("state")
        or (d.get("running") is not None)
    )
    if not has_state:
        errs.append("无 gcode_state/state/running 字段")
    for k in ("mc_percent", "progress", "mc_remaining_time", "mc_remaining", "remaining_time"):
        if d.get(k) is not None and not _is_num(d[k]):
            errs.append(f"{k} 非数字: {d[k]!r}")
    ams = d.get("ams")
    if ams is not None:
        if not isinstance(ams, list):
            errs.append("ams 非 list")
        else:
            for unit in ams:
                if not isinstance(unit, dict):
                    errs.append("ams 单元非 dict")
                    continue
                tray = unit.get("tray")
                if tray is not None and not isinstance(tray, list):
                    errs.append("ams[].tray 非 list")
    return errs


def validate_printer(d):
    """GET /printers/{id} —— 单台打印机对象。"""
    if not isinstance(d, dict):
        return ["printer 非 dict"]
    errs = []
    if d.get("id") is None:
        errs.append("printer 缺 id")
    return errs


def validate_printers_list(d):
    """GET /printers/ —— 真机列表。"""
    if not isinstance(d, list):
        return ["printers 非 list"]
    errs = []
    for p in d:
        if not isinstance(p, dict):
            errs.append("printer 条目非 dict")
            continue
        if p.get("id") is None:
            errs.append(f"printer 条目缺 id: {p!r}")
    return errs


def validate_virtual_printers(d):
    """GET /virtual-printers —— poller 直接 _request 调用。"""
    if not isinstance(d, dict):
        return ["virtual-printers 非 dict"]
    pl = d.get("printers")
    if not isinstance(pl, list):
        return ["virtual-printers.printers 非 list"]
    errs = []
    for p in pl:
        if not isinstance(p, dict):
            errs.append("vp 条目非 dict")
            continue
        if p.get("id") is None:
            errs.append("vp 条目缺 id")
    return errs


def validate_queue_item(d):
    """GET /queue/{id} —— dispatch._queue_item_reusable 读 status。"""
    if not isinstance(d, dict):
        return ["queue item 非 dict"]
    errs = []
    if d.get("id") is None:
        errs.append("queue item 缺 id")
    if "status" not in d:
        errs.append("queue item 缺 status")
    return errs


def validate_queue_list(d):
    """GET /queue/ —— 队列列表。"""
    if not isinstance(d, list):
        return ["queue 非 list"]
    errs = []
    for item in d:
        errs.extend(validate_queue_item(item))
    return errs


def validate_inventory_remain(d):
    """GET /printers/{id}/inventory-remain —— 各槽剩余克数。"""
    if not isinstance(d, dict):
        return ["inventory-remain 非 dict"]
    raw = d.get("inventory_remain_g")
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return ["inventory_remain_g 非 dict"]
    errs = []
    for slot, grams in raw.items():
        if not _is_num(grams):
            errs.append(f"槽 {slot} 克数非数字: {grams!r}")
    return errs


def validate_inventory_assignments(d):
    """GET /inventory/assignments —— 槽位↔料盘分配。"""
    if not isinstance(d, list):
        return ["inventory assignments 非 list"]
    errs = []
    for a in d:
        if not isinstance(a, dict):
            errs.append("assignment 条目非 dict")
            continue
        if a.get("printer_id") is None or a.get("tray_id") is None:
            errs.append(f"assignment 缺 printer_id/tray_id: {a!r}")
    return errs


def validate_archives_list(d):
    """GET /archives/。"""
    if not isinstance(d, list):
        return ["archives 非 list"]
    return []


def validate_archive_item(d):
    """GET /archives/{id}。"""
    if not isinstance(d, dict):
        return ["archive 非 dict"]
    return []


def validate_health(d):
    """GET /health。"""
    if not isinstance(d, dict):
        return ["health 非 dict"]
    return []


def validate_webhook_payload(d):
    """Bambuddy → 3DFarm webhook body。

    关键：event_type/event/type 必须是非 dict 字符串。Bambuddy 若发嵌套 event 对象，
    webhook_bambuddy.py 的扁平取值（payload.get("event")）会拿到 dict 而非事件名，
    导致 handle_print_event 收到非字符串 event_type —— 此处刻意把它变成显式失败。
    """
    if not isinstance(d, dict):
        return ["webhook payload 非 dict"]
    et = d.get("event_type") or d.get("event") or d.get("type")
    if et is None:
        return ["webhook payload 无 event_type/event/type"]
    if isinstance(et, dict):
        return ["event 字段是 dict（嵌套）——webhook_bambuddy 扁平取值会解析失败"]
    if not isinstance(et, str):
        return [f"event 字段非字符串: {et!r}"]
    return []


_VALIDATORS = {
    "health": validate_health,
    "printers": validate_printers_list,
    "printers/{id}": validate_printer,
    "printers/{id}/status": validate_printer_status,
    "printers/{id}/jobs": validate_printers_list,
    "printers/{id}/inventory-remain": validate_inventory_remain,
    "virtual-printers": validate_virtual_printers,
    "archives": validate_archives_list,
    "archives/{id}": validate_archive_item,
    "queue": validate_queue_list,
    "queue/{id}": validate_queue_item,
    "inventory/assignments": validate_inventory_assignments,
}


def validate_for(logical_path_name, body):
    """按规范化路径选校验器。未知路径返回 []（不校验）。"""
    fn = _VALIDATORS.get(logical_path_name)
    return fn(body) if fn else []


def fixture_body(fx):
    """读 fixture 的响应 body（dict 或字符串 → 解析）。"""
    body = (fx.get("response") or {}).get("body")
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        try:
            return json.loads(body)
        except ValueError:
            return {"__raw": body}
    return body
