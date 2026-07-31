"""只读采集 Bambuddy 响应 → fixture corpus（tests/fixtures/bambuddy/{live,prod}/）。

只做 GET（硬守卫），枚举 3DFarm 实际调用的端点，产出与 recorder 相同的 fixture 格式。

本地 dev：python tools/capture_bambuddy.py --base-url http://127.0.0.1:8000 \
              --out tests/fixtures/bambuddy/live
生产 .83 服务器对 .19：python tools/capture_bambuddy.py \
              --base-url http://172.25.56.19:8000 --out tests/fixtures/bambuddy/prod \
              --printer-id <真机id> --api-key <若有>

注意：NAT 端点把 Bambuddy 8000 映射出来，端口以实际映射为准（例 18000）。
"""
import argparse
import json
import os
import sys
import time
import urllib.parse

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.bambuddy_recorder import _fixture_filename, _safe_json  # noqa: E402
from tools.bambuddy_schema import logical_path  # noqa: E402

# 只读守卫：采集只允许 GET
_READ_ONLY_METHODS = {"GET"}

# 3DFarm 实际调用的端点（path 模板里的 {pid} 用 --printer-id 替换）
_ENDPOINTS = [
    ("GET", "/health"),
    ("GET", "/api/v1/printers/"),
    ("GET", "/api/v1/printers/{pid}"),
    ("GET", "/api/v1/printers/{pid}/status"),
    ("GET", "/api/v1/printers/{pid}/jobs"),
    ("GET", "/api/v1/printers/{pid}/inventory-remain"),
    ("GET", "/api/v1/virtual-printers"),
    ("GET", "/api/v1/archives/"),
    ("GET", "/api/v1/archives/1"),
    ("GET", "/api/v1/queue/"),
    ("GET", "/api/v1/inventory/assignments"),
]


def capture_one(client, base, method, raw_path, api_key, out_dir):
    url = base + raw_path
    headers = {"X-API-Key": api_key} if api_key else {}
    resp = client.get(url, headers=headers)
    lp = logical_path(raw_path)
    content_type = resp.headers.get("content-type", "")
    fx = {
        "meta": {
            "capture_target": base,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "note": "read-only capture",
        },
        "request": {
            "method": method.upper(),
            "logical_path": lp,
            "query": dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(raw_path).query)),
            "headers": {"x-api-key": api_key or ""},
        },
        "response": {
            "status": resp.status_code,
            "headers": {"content-type": content_type},
            "body": _safe_json(resp.content) if content_type.startswith("application/json") else resp.text,
            "body_encoding": "json" if content_type.startswith("application/json") else "text",
        },
    }
    os.makedirs(out_dir, exist_ok=True)
    key = f"{method.upper()}__{lp}"
    out = os.path.join(out_dir, _fixture_filename(key))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(fx, f, ensure_ascii=False, indent=2)
    return resp.status_code, lp


def main():
    ap = argparse.ArgumentParser(description="只读采集 Bambuddy 响应为 fixture")
    ap.add_argument("--base-url", required=True, help="Bambuddy 根地址（如 http://127.0.0.1:8000）")
    ap.add_argument("--out", required=True, help="输出 fixture 目录")
    ap.add_argument("--printer-id", type=int, default=1, help="探针打印机 id（默认 1，逻辑路径会模板化）")
    ap.add_argument("--api-key", default="", help="Bambuddy API key（生产有鉴权时）")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    client = httpx.Client(timeout=15.0)
    ok = fail = 0
    for method, tmpl in _ENDPOINTS:
        assert method in _READ_ONLY_METHODS, f"只允许只读方法: {method}"
        raw = tmpl.replace("{pid}", str(args.printer_id))
        status, lp = capture_one(client, base, method, raw, args.api_key, args.out)
        flag = "[ok]" if status < 400 else "[fail]"
        if status < 400:
            ok += 1
        else:
            fail += 1
        print(f"  {flag} {method:4s} {raw}  ->  {status}  ({lp})")
    print(f"\n采集完成: {ok} 成功 / {fail} 非 2xx（非 2xx 已原样保留，契约测试会断言其抛错）")
    print(f"输出目录: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
