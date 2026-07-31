"""生产只读 canary —— 定时对 Bambuddy（NAT）跑只读冒烟，早期预警 schema drift + 连通性。

- 只读守卫：只允许 GET（httpx client 仅发起列出的 GET 端点），永不 import 任何写方法
  （stop_print / add_to_queue / cancel_queue_item / remove_queue_item / upload_archive / debug 钩子都不在此路径）。
- 对每个 2xx 响应跑 tools/bambuddy_schema 校验；非 2xx 或校验错误 → 计入 errors。

用法（.83 服务器，Windows 任务计划程序每 15 分钟，退出码驱动告警）：
  python tools/canary.py --base-url http://172.25.56.19:8000 [--api-key KEY] [--out canary.log]

退出码：0 = 全通过；1 = 有错误/不可达。
"""
import argparse
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.bambuddy_schema import validate_for, logical_path  # noqa: E402

_READ_ONLY_METHODS = {"GET"}

# 固定探针端点（打印机逐个的 status/inventory-remain 在其后动态追加）
_BASE_ENDPOINTS = [
    ("GET", "/health", "health"),
    ("GET", "/api/v1/printers/", "printers"),
    ("GET", "/api/v1/virtual-printers", "virtual-printers"),
    ("GET", "/api/v1/inventory/assignments", "inventory/assignments"),
    ("GET", "/api/v1/queue/", "queue"),
]


def _check(client, base, method, path, api_key):
    url = base + path
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        resp = client.get(url, headers=headers, timeout=10.0)
    except httpx.HTTPError as e:
        return {"path": path, "ok": False, "error": f"unreachable: {e}"}
    lp = logical_path(path)
    body = {}
    try:
        body = resp.json()
    except Exception:
        pass
    if resp.status_code >= 400:
        return {"path": path, "ok": False, "error": f"HTTP {resp.status_code}"}
    errs = validate_for(lp, body)
    if errs:
        return {"path": path, "ok": False, "error": "; ".join(errs)}
    return {"path": path, "ok": True}


def main():
    ap = argparse.ArgumentParser(description="Bambuddy 只读 canary")
    ap.add_argument("--base-url", required=True, help="Bambuddy 根地址（NAT 端口）")
    ap.add_argument("--api-key", default="", help="Bambuddy API key（生产有鉴权时）")
    ap.add_argument("--out", help="日志文件路径（可选；同时输出 stdout）")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    client = httpx.Client(timeout=10.0)
    checks = [_check(client, base, m, p, args.api_key) for m, p, _ in _BASE_ENDPOINTS]

    # 动态：对每台真机逐个 status + inventory-remain
    try:
        printers = client.get(base + "/api/v1/printers/", timeout=10.0).json()
    except Exception:
        printers = []
    for p in (printers if isinstance(printers, list) else []):
        pid = p.get("id")
        if pid is None:
            continue
        checks.append(_check(client, base, "GET", f"/api/v1/printers/{pid}/status", args.api_key))
        checks.append(_check(client, base, "GET", f"/api/v1/printers/{pid}/inventory-remain", args.api_key))

    errors = [c for c in checks if not c["ok"]]
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if errors:
        lines = [f"[{ts}] CANARY FAIL: {len(errors)}/{len(checks)} checks failed"] + [
            f"  FAIL {c['path']}: {c['error']}" for c in errors
        ]
        exit_code = 1
    else:
        lines = [f"[{ts}] CANARY OK: {len(checks)} checks, 0 errors"]
        exit_code = 0

    report = "\n".join(lines)
    print(report, flush=True)
    if args.out:
        with open(args.out, "a", encoding="utf-8") as f:
            f.write(report + "\n")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
