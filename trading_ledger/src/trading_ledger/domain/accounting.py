"""Pure quantity, fee, and settlement calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from .model import RatioBasis, TradeSide
from .rules import CN_STOCK_FEE_SCHEDULE, CN_STOCK_LOT_SIZE, FeeSchedule


MONEY_QUANTUM = Decimal("0.01")


def round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class TradeCalculation:
    side: TradeSide
    ratio_basis: RatioBasis
    allocation_ratio: Decimal
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    commission_amount: Decimal
    stamp_tax_amount: Decimal
    transfer_fee_amount: Decimal
    net_cash_amount: Decimal


def calculate_fees(
    gross_amount: Decimal,
    side: TradeSide,
    schedule: FeeSchedule = CN_STOCK_FEE_SCHEDULE,
) -> tuple[Decimal, Decimal, Decimal]:
    commission = round_money(gross_amount * schedule.commission_rate)
    commission = max(commission, schedule.minimum_commission)
    if side is TradeSide.BUY:
        return commission, Decimal("0.00"), Decimal("0.00")
    return (
        commission,
        round_money(gross_amount * schedule.sell_stamp_tax_rate),
        round_money(gross_amount * schedule.sell_transfer_fee_rate),
    )


def calculate_buy(
    available_cash: Decimal,
    allocation_ratio: Decimal,
    price: Decimal,
    lot_size: int = CN_STOCK_LOT_SIZE,
) -> TradeCalculation:
    budget = round_money(available_cash * allocation_ratio)
    lots = int(
        (budget / price / Decimal(lot_size)).to_integral_value(rounding=ROUND_DOWN)
    )
    while lots > 0:
        quantity = Decimal(lots * lot_size)
        gross = round_money(quantity * price)
        commission, stamp_tax, transfer_fee = calculate_fees(gross, TradeSide.BUY)
        if gross + commission <= budget and gross + commission <= available_cash:
            return TradeCalculation(
                side=TradeSide.BUY,
                ratio_basis=RatioBasis.AVAILABLE_CASH,
                allocation_ratio=allocation_ratio,
                quantity=quantity,
                price=price,
                gross_amount=gross,
                commission_amount=commission,
                stamp_tax_amount=stamp_tax,
                transfer_fee_amount=transfer_fee,
                net_cash_amount=-(gross + commission),
            )
        lots -= 1
    raise ValueError("可用资金不足以买入一手并支付手续费。")


def calculate_sell(
    sellable_quantity: Decimal,
    allocation_ratio: Decimal,
    price: Decimal,
    lot_size: int = CN_STOCK_LOT_SIZE,
) -> TradeCalculation:
    if allocation_ratio == Decimal("1"):
        quantity = sellable_quantity
    else:
        raw_quantity = sellable_quantity * allocation_ratio
        lots = int(
            (raw_quantity / Decimal(lot_size)).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        quantity = Decimal(lots * lot_size)
    if quantity <= 0:
        raise ValueError("按填写比例计算后不足一手可卖持仓。")
    gross = round_money(quantity * price)
    commission, stamp_tax, transfer_fee = calculate_fees(gross, TradeSide.SELL)
    return TradeCalculation(
        side=TradeSide.SELL,
        ratio_basis=RatioBasis.SELLABLE_POSITION,
        allocation_ratio=allocation_ratio,
        quantity=quantity,
        price=price,
        gross_amount=gross,
        commission_amount=commission,
        stamp_tax_amount=stamp_tax,
        transfer_fee_amount=transfer_fee,
        net_cash_amount=gross - commission - stamp_tax - transfer_fee,
    )
