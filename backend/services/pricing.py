"""费率计算服务（Phase 1.5 自动报价）。

公式（§4.5，受 Bambuddy cost_per_kg 启发）：
  credit = base_fee + weight_g × 材料单价 + (seconds/3600) × machine_hour_price

费率从 pricing_config 表读（admin 可配，见 blueprints/admin.py 的 /pricing）。
Decimal 精算，避免 float 误差（碰钱）。
"""
from decimal import Decimal, ROUND_HALF_UP

from models import PricingConfigModel


class PricingService:
    """自动报价。从 pricing_config 读费率，按公式算 credit。"""

    BASE_FEE_KEY = "base_fee"
    MACHINE_HOUR_KEY = "machine_hour_price"
    DEFAULT_MATERIAL = "PLA"

    @staticmethod
    def _get(key):
        row = PricingConfigModel.query.filter_by(key=key).first()
        return Decimal(row.value) if row else None

    @staticmethod
    def material_price(material):
        """材料每克单价。未配置回退默认 PLA；PLA 也没则 0。"""
        mat = (material or "").strip() or PricingService.DEFAULT_MATERIAL
        price = PricingService._get(f"material:{mat}")
        if price is not None:
            return price
        if mat != PricingService.DEFAULT_MATERIAL:
            fallback = PricingService._get(f"material:{PricingService.DEFAULT_MATERIAL}")
            if fallback is not None:
                return fallback
        return Decimal("0")

    @staticmethod
    def _to_dec(v):
        if v is None:
            return Decimal("0")
        return Decimal(str(v))

    @staticmethod
    def calc(weight_g, print_seconds, material=None):
        """根据克重/时长/材料算 credit。返回 Decimal(0.01)。费率缺失项当 0。"""
        weight = PricingService._to_dec(weight_g)
        seconds = PricingService._to_dec(print_seconds)
        base_fee = PricingService._get(PricingService.BASE_FEE_KEY) or Decimal("0")
        machine_hour = PricingService._get(PricingService.MACHINE_HOUR_KEY) or Decimal("0")
        mat_price = PricingService.material_price(material)

        credit = base_fee + weight * mat_price + (seconds / Decimal("3600")) * machine_hour
        return credit.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def describe(weight_g, print_seconds, material=None):
        """报价明细 dict（给前端展示构成）。"""
        weight = PricingService._to_dec(weight_g)
        seconds = PricingService._to_dec(print_seconds)
        base_fee = PricingService._get(PricingService.BASE_FEE_KEY) or Decimal("0")
        machine_hour = PricingService._get(PricingService.MACHINE_HOUR_KEY) or Decimal("0")
        mat_price = PricingService.material_price(material)
        return {
            "credit": PricingService.calc(weight_g, print_seconds, material),
            "base_fee": base_fee,
            "material_cost": (weight * mat_price).quantize(Decimal("0.01")),
            "machine_cost": ((seconds / Decimal("3600")) * machine_hour).quantize(Decimal("0.01")),
        }
