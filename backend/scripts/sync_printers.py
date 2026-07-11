"""把 printers.yaml 同步到 printer 表（按 public_name upsert）。

用法（任意目录均可）:
    python scripts/sync_printers.py

设计：幂等。改了 yaml 再跑一次即可，已有的记录按 public_name 更新，
不会重复插入。bambuddy_printer_id / has_ams / enabled 都靠这个脚本维护。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from app import app  # noqa: E402
from exts import db  # noqa: E402
from models import PrinterModel  # noqa: E402

CONFIG = ROOT / "printers.yaml"


def main() -> None:
    if not CONFIG.exists():
        print(f"找不到配置文件: {CONFIG}")
        sys.exit(1)

    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    printers = data.get("printers", [])
    if not printers:
        print("printers.yaml 里没有打印机记录")
        return

    added, updated, skipped = 0, 0, 0
    with app.app_context():
        for p in printers:
            name = p.get("public_name")
            if not name:
                skipped += 1
                print(f"跳过缺 public_name 的记录: {p}")
                continue
            existing = PrinterModel.query.filter_by(public_name=name).first()
            if existing:
                for k, v in p.items():
                    setattr(existing, k, v)
                updated += 1
            else:
                db.session.add(PrinterModel(**p))
                added += 1
        db.session.commit()

    print(f"同步完成：新增 {added}，更新 {updated}，跳过 {skipped}，共 {len(printers)} 条配置")


if __name__ == "__main__":
    main()
