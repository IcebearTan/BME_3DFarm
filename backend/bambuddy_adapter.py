"""BambuddyAdapter — 访问 Bambuddy 的唯一出口（接口抽象）。

业务代码只依赖这里的接口，不直接拼 Bambuddy URL。这是对冲 Bambuddy 风险的核心手段：
万一 Bambuddy 写入 API 不够用或项目停更，可换自研实现
（复用 BambuPrinterGateway 已验证的 bambu-connect 连接能力），业务层一行不改。

方法随阶段递增：
  Phase 0（只读）：list_printers / get_printer / get_printer_status / list_queue / list_archives / health_check
  Phase 2（只读+）：get_archive
  Phase 3（写入）：add_to_queue / remove_queue_item / upload_archive  ← 取决于 Phase 0 实测

注意：实际路径/参数以 Phase 0 在 Bambuddy API Browser 的实测为准。
"""
import httpx

from flask import current_app


class BambuddyError(RuntimeError):
    """Bambuddy 调用失败的统一异常。"""


class BambuddyAdapter:
    def __init__(self, base_url: str = None, api_key: str = None, timeout: float = 10.0):
        self.base_url = (base_url or current_app.config["BAMBUDDY_BASE_URL"]).rstrip("/")
        self.api_key = api_key or current_app.config.get("BAMBUDDY_API_KEY") or ""
        self.timeout = timeout

    def _headers(self):
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(method, url, headers=self._headers(), timeout=self.timeout, **kwargs)
        except httpx.HTTPError as e:
            raise BambuddyError(f"Bambuddy 请求失败: {e}") from e
        if resp.status_code >= 400:
            raise BambuddyError(f"Bambuddy {method} {path} -> {resp.status_code}: {resp.text}")
        return resp.json() if resp.content else {}

    # ── 只读档（Phase 0）──
    def health_check(self):
        # /health 在根路径，不在 /api/v1 下
        root = self.base_url.rsplit("/api/v1", 1)[0]
        try:
            resp = httpx.get(f"{root}/health", headers=self._headers(), timeout=self.timeout)
        except httpx.HTTPError as e:
            raise BambuddyError(f"Bambuddy health 请求失败: {e}") from e
        if resp.status_code >= 400:
            raise BambuddyError(f"Bambuddy health -> {resp.status_code}")
        return resp.json() if resp.content else {}

    def list_printers(self):
        return self._request("GET", "/printers/")

    def get_printer(self, printer_id):
        return self._request("GET", f"/printers/{printer_id}")

    def get_printer_status(self, printer_id):
        return self._request("GET", f"/printers/{printer_id}/status")

    def list_queue(self):
        return self._request("GET", "/queue/")

    def list_archives(self):
        return self._request("GET", "/archives/")

    def get_archive(self, archive_id):
        return self._request("GET", f"/archives/{archive_id}")

    # ── 只读+（Phase 2）──
    def get_queue_item(self, queue_id):
        return self._request("GET", f"/queue/{queue_id}")

    def get_printer_jobs(self, printer_id):
        """打印机当前/历史任务（路径以 Bambuddy 实测为准，先试 /printers/{id}/jobs）。"""
        return self._request("GET", f"/printers/{printer_id}/jobs")
