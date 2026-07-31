"""Bambuddy 录制/回放代理 —— 让 dev 后端跑"生产形态数据"。

模式：
  record   —— 透传到真实 Bambuddy（--target），把响应存成 fixture（tests/fixtures/bambuddy/…）
  replay   —— 按规范化路径回放 fixture 目录；--latency 加人工延迟
  sequence —— replay 子模式：同 key 多份 fixture（.000/.001/…）按轮次依次返回，
              驱动 poller 多轮同步走完整生命周期
  fault    —— replay + 故障注入（--fault status:500 / latency:15000 / reset，按 --fault-path 作用于路径）

用法（standalone，在 backend/ 下跑）：
  record:   python tools/bambuddy_recorder.py --mode record --target http://127.0.0.1:8000 --out DIR --port 8100
  replay:   python tools/bambuddy_recorder.py --mode replay --fixtures DIR --port 8100 [--latency 50]
  fault:    python tools/bambuddy_recorder.py --mode replay --fixtures DIR \
              --fault-path 'printers/{id}/status' --fault status:500 --port 8100

pytest 里由 conftest 的 replay_proxy fixture 工厂以进程内线程启动（port=0 自动分配）。
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

# 独立运行（sys.path[0] 指向 tools/）时补 backend/ 到 path，让 tools 包可 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.bambuddy_schema import logical_path  # noqa: E402

_API_PREFIX = "/api/v1"


def _fixture_filename(key, seq=None):
    safe = key.replace("/", "__").replace("{id}", "ID")
    return f"{safe}.{seq:03d}.json" if seq is not None else f"{safe}.json"


def _safe_json(content):
    try:
        return json.loads(content.decode())
    except (ValueError, UnicodeDecodeError):
        return content.decode(errors="replace")


class ReplayServer:
    """HTTP 代理：record / replay(+fault+sequence)。可在 pytest 线程或 CLI 跑。"""

    def __init__(self, mode="replay", fixtures_dir=None, target=None,
                 latency_ms=0, faults=None, strict_sequence=False,
                 api_prefix=_API_PREFIX, port=0):
        self.mode = mode
        self.fixtures_dir = fixtures_dir
        self.target = target.rstrip("/") if target else None
        self.latency_ms = latency_ms
        self.faults = faults or {}          # {logical_path 或 "*": {status|latency_ms|reset}}
        self.strict_sequence = strict_sequence
        self.api_prefix = api_prefix
        self._index = {}                    # key -> [fixture, ...]（多份 = 序列）
        self._cursor = {}                   # key -> 已取次数
        self.server = None
        self.thread = None
        self.port = port
        if mode in ("replay", "fault") and fixtures_dir:
            self._load_fixtures()

    # ── fixture 装载 ──
    def _load_fixtures(self):
        self._index = {}
        for fn in sorted(os.listdir(self.fixtures_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(self.fixtures_dir, fn), encoding="utf-8") as f:
                fx = json.load(f)
            req = fx.get("request", {})
            method = (req.get("method") or "GET").upper()
            lp = req.get("logical_path")
            if not lp:
                continue
            key = f"{method}__{lp}"
            self._index.setdefault(key, []).append(fx)

    # ── 生命周期 ──
    def start(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self._make_handler())
        self.server.recorder = self
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.base_url()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.thread = None

    def base_url(self):
        return f"http://127.0.0.1:{self.port}{self.api_prefix}"

    def reset_sequence(self):
        self._cursor.clear()

    # ── handler ──
    def _make_handler(self):
        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _handle(self):
                recorder = self.server.recorder
                try:
                    recorder._route(self, self.command, self.path, self._body())
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:  # 代理内部错误 → 500，不吞掉
                    try:
                        self._send_json(500, {"detail": f"recorder error: {e}"})
                    except Exception:
                        pass

            do_GET = _handle
            do_POST = _handle
            do_PUT = _handle
            do_PATCH = _handle
            do_DELETE = _handle

            def _send_json(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_raw(self, status, headers, body):
                self.send_response(status)
                for k, v in headers.items():
                    if k.lower() in ("transfer-encoding", "connection"):
                        continue
                    self.send_header(k, v)
                if body:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def log_message(self, *a):
                pass

        return _Handler

    def _route(self, handler, method, raw_path, body):
        path = raw_path.split("?", 1)[0]
        # 控制端点：永不录制/回放
        if path.startswith("/__replay/"):
            if path.rstrip("/").endswith("/reset"):
                self._cursor.clear()
                handler._send_json(200, {"ok": True})
            else:
                handler._send_json(404, {"detail": "unknown control"})
            return

        if self.mode == "record":
            self._record(handler, method, raw_path, body)
            return

        lp = logical_path(raw_path, self.api_prefix)
        key = f"{method.upper()}__{lp}"

        # 故障注入（faults 存在即生效，无论 mode）
        rule = self.faults.get(lp) or self.faults.get("*")
        if rule:
            if rule.get("latency_ms"):
                time.sleep(rule["latency_ms"] / 1000.0)
            if rule.get("reset"):
                handler.connection.shutdown(socket.SHUT_RDWR)  # 强制重置 → httpx 报连接错误
                handler.connection.close()
                return
            if rule.get("status"):
                handler._send_json(rule["status"], {"detail": f"injected fault {rule['status']}"})
                return

        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0)

        # sequence：同 key 多份 fixture 按轮次取，越界重复最后一份（strict 则 404）
        group = self._index.get(key)
        if not group:
            handler._send_json(404, {"detail": f"Fixture not found: {key}"})
            return
        i = self._cursor.get(key, 0)
        if i < len(group):
            fx = group[i]
            if len(group) > 1 or self.strict_sequence:
                self._cursor[key] = i + 1
        else:
            if self.strict_sequence:
                handler._send_json(404, {"detail": f"Fixture sequence exhausted: {key}"})
                return
            fx = group[-1]

        resp = fx.get("response", {})
        status = resp.get("status", 200)
        headers = resp.get("headers") or {}
        body_raw = resp.get("body")
        body_enc = resp.get("body_encoding", "json")
        if body_enc == "json":
            # fixture 存的是已解析 JSON（dict/list/str/int/bool/None）→ 重新序列化回 JSON。
            # 不能用 str() 转（会把 list 变成 Python repr，含单引号/None，非法 JSON）。
            handler._send_json(status, body_raw)
        elif body_raw is None:
            handler._send_raw(status, headers, b"")
        elif isinstance(body_raw, bytes):
            handler._send_raw(status, headers, body_raw)
        else:
            handler._send_raw(status, headers, str(body_raw).encode())

    def _record(self, handler, method, raw_path, body):
        """透传到 target 并保存 fixture（保持原 path）。"""
        url = self.target + raw_path
        headers = {}
        for k in ("Content-Type", "X-API-Key", "Accept"):
            v = handler.headers.get(k)
            if v:
                headers[k] = v
        try:
            resp = httpx.request(method, url, headers=headers,
                                 content=body or None, timeout=15.0)
        except httpx.HTTPError as e:
            handler._send_json(502, {"detail": f"forward failed: {e}"})
            return

        lp = logical_path(raw_path, self.api_prefix)
        key = f"{method.upper()}__{lp}"
        content_type = resp.headers.get("content-type", "")
        fx = {
            "meta": {
                "capture_target": self.target,
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "note": "recorded",
            },
            "request": {
                "method": method.upper(),
                "logical_path": lp,
                "query": dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(raw_path).query)),
                "headers": {"x-api-key": headers.get("X-API-Key", "")},
            },
            "response": {
                "status": resp.status_code,
                "headers": {"content-type": content_type},
                "body": _safe_json(resp.content) if content_type.startswith("application/json") else resp.text,
                "body_encoding": "json" if content_type.startswith("application/json") else "text",
            },
        }
        if self.fixtures_dir:
            os.makedirs(self.fixtures_dir, exist_ok=True)
            out = os.path.join(self.fixtures_dir, _fixture_filename(key))
            n = 0
            while os.path.exists(out):
                n += 1
                out = os.path.join(self.fixtures_dir, _fixture_filename(key, n))
            with open(out, "w", encoding="utf-8") as f:
                json.dump(fx, f, ensure_ascii=False, indent=2)

        if content_type.startswith("application/json"):
            handler._send_json(resp.status_code, _safe_json(resp.content))
        else:
            handler._send_raw(resp.status_code, {"Content-Type": content_type}, resp.content)


def _main():
    ap = argparse.ArgumentParser(description="Bambuddy 录制/回放代理")
    ap.add_argument("--mode", choices=["record", "replay", "fault"], default="replay")
    ap.add_argument("--target", help="record 模式的转发目标（如 http://172.25.56.19:8000）")
    ap.add_argument("--fixtures", help="replay/fault 模式的 fixture 目录")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--latency", type=int, default=0, help="回放人工延迟 ms")
    ap.add_argument("--strict-sequence", action="store_true", help="序列耗尽后 404 而非重复最后一份")
    ap.add_argument("--fault", action="append", metavar="TYPE:VALUE",
                    help="status:500 / latency:15000 / reset（可多个）")
    ap.add_argument("--fault-path", action="append", metavar="PATH",
                    help="fault 作用的逻辑路径（默认全局 *）")
    args = ap.parse_args()

    faults = {}
    rules = {}
    for spec in (args.fault or []):
        kind, _, val = spec.partition(":")
        if kind == "reset":
            rules["reset"] = True
        elif kind == "status":
            rules["status"] = int(val)
        elif kind == "latency":
            rules["latency_ms"] = int(val)
    for path in (args.fault_path or ["*"]):
        faults[path] = dict(rules)

    srv = ReplayServer(
        mode=args.mode,
        fixtures_dir=args.fixtures,
        target=args.target,
        latency_ms=args.latency,
        faults=faults,
        strict_sequence=args.strict_sequence,
        port=args.port,
    )
    url = srv.start()
    print(f"Bambuddy recorder [{srv.mode}] at {url}  (fixtures={srv.fixtures_dir})")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    _main()
