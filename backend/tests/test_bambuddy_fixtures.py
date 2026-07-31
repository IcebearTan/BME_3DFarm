"""Bambuddy 契约测试：录制 corpus → schema 校验 + 真实 adapter 解析。

- live/（本地 dev 采集）+ prod/（生产采集，可选）每份 2xx fixture 过 schema 校验
- replay 代理回放 corpus，真实 BambuddyAdapter 走真实 HTTP 解析（URL 拼装/JSON 解析/错误路径）
- 非 2xx 断言抛 BambuddyError；未知路径 404 → BambuddyError
- webhook payload 校验器拒绝嵌套 event dict（把未确认点变成显式失败）

跑法：python -m pytest tests/test_bambuddy_fixtures.py -x
"""
import json
import os

import pytest

from bambuddy_adapter import BambuddyAdapter, BambuddyError
from tools.bambuddy_schema import validate_for, validate_webhook_payload, fixture_body
from conftest import FIXTURES_DIR

# 要过契约的 corpus 子目录（prod 未采集则跳过）
_CORPUS = ["live"]
if os.path.isdir(os.path.join(FIXTURES_DIR, "prod")):
    _CORPUS.append("prod")


def _all_fixtures(subdir):
    root = os.path.join(FIXTURES_DIR, subdir)
    if not os.path.isdir(root):
        return
    for fn in sorted(os.listdir(root)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(root, fn), encoding="utf-8") as f:
            yield json.load(f), os.path.join(root, fn)


def _adapter_caller(adapter, lp):
    """逻辑路径 → 真实 adapter 方法调用。返回可调用对象（proxy 对任意 id 模板化，id 传 1 即可）。"""
    table = {
        "health": lambda: adapter.health_check(),
        "printers": lambda: adapter.list_printers(),
        "printers/{id}": lambda: adapter.get_printer(1),
        "printers/{id}/status": lambda: adapter.get_printer_status(1),
        "printers/{id}/jobs": lambda: adapter.get_printer_jobs(1),
        "printers/{id}/inventory-remain": lambda: adapter.get_inventory_remain(1),
        "virtual-printers": lambda: adapter._request("GET", "/virtual-printers"),
        "archives": lambda: adapter.list_archives(),
        "archives/{id}": lambda: adapter.get_archive(1),
        "queue": lambda: adapter.list_queue(),
        "inventory/assignments": lambda: adapter.list_inventory_assignments(),
    }
    return table.get(lp)


@pytest.mark.parametrize("corpus", _CORPUS)
def test_schema_on_corpus(corpus):
    """每份 2xx fixture 过对应 schema 校验，错误即 fail（Bambuddy 改字段时大声失败）。"""
    bad, n = [], 0
    for fx, path in _all_fixtures(corpus):
        n += 1
        status = fx["response"]["status"]
        if status >= 400:
            continue  # 错误 body 形态各异，不校验；由 adapter 抛错测试覆盖
        errs = validate_for(fx["request"]["logical_path"], fixture_body(fx))
        if errs:
            bad.append((path, status, errs))
    assert n > 0, f"{corpus}/ 无 fixture"
    assert not bad, (
        f"{len(bad)} 份 fixture schema 不通过:\n"
        + "\n".join(f"  {p} [{s}]: {e}" for p, s, e in bad)
    )


@pytest.mark.parametrize("corpus", _CORPUS)
def test_adapter_parses_corpus(corpus, replay_proxy, app):
    """replay 回放 corpus → 真实 adapter 每类路径可解析；非 2xx 断言抛 BambuddyError。"""
    p = replay_proxy(os.path.join(FIXTURES_DIR, corpus))
    with app.app_context():  # adapter 构造会读 current_app.config（api_key 空时）
        adapter = BambuddyAdapter(base_url=p["base_url"], api_key="")
        seen = set()
        checked = 0
        for fx, path in _all_fixtures(corpus):
            lp = fx["request"]["logical_path"]
            if lp in seen:
                continue
            seen.add(lp)
            status = fx["response"]["status"]
            fn = _adapter_caller(adapter, lp)
            assert fn is not None, f"{path}: 逻辑路径 {lp} 无对应 adapter 方法"
            if status >= 400:
                with pytest.raises(BambuddyError, match=f"{status}"):
                    fn()
            else:
                fn()  # 不抛即解析通过
            checked += 1
        assert checked > 0


def test_unknown_path_404_raises(replay_proxy, app):
    """无 fixture 的路径 → 代理 404 → adapter 抛 BambuddyError。"""
    p = replay_proxy(os.path.join(FIXTURES_DIR, "live"))
    with app.app_context():
        adapter = BambuddyAdapter(base_url=p["base_url"], api_key="")
        with pytest.raises(BambuddyError, match="404"):
            adapter.get_queue_item(99999)  # live/ 只有 queue 列表，无 queue/{id}


def test_webhook_payload_validator_rejects_nested_event():
    """嵌套 event 对象（未实测确认）必须显式失败，而非静默错解析。"""
    errs = validate_webhook_payload({"event": {"type": "print_complete", "printer_id": 2}})
    assert errs, "嵌套 event dict 应被判为不通过"
    assert validate_webhook_payload({"event_type": "print_complete", "archive_id": 1}) == []
    assert validate_webhook_payload({})  # 无事件字段 → 不通过
