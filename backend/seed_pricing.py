"""建 pricing_config 表 + 种子默认费率（幂等）。Phase 1.5 自动报价用。

用法：cd backend && .venv/Scripts/python.exe seed_pricing.py
"""
from app import app
from exts import db
from models import PricingConfigModel

DEFAULTS = [
    # (key, value, category, label, unit)
    ("base_fee", 2, "global", "基础开机费", "次"),
    ("machine_hour_price", 10, "global", "机时单价", "hour"),
    ("material:PLA", 0.5, "material", "PLA", "g"),
    ("material:PETG", 0.6, "material", "PETG", "g"),
    ("material:ABS", 0.7, "material", "ABS", "g"),
]

with app.app_context():
    db.create_all()
    added = 0
    for key, value, cat, label, unit in DEFAULTS:
        if not PricingConfigModel.query.filter_by(key=key).first():
            db.session.add(PricingConfigModel(
                key=key, value=value, category=cat, label=label, unit=unit))
            added += 1
    db.session.commit()
    total = PricingConfigModel.query.count()
    print(f"pricing_config seeded: added {added}, total {total}")
