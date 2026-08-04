"""Execution-cost component resolution and construction for experiments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from experiment_system import ComponentSpec
from grid_rule.adapters import (
    FixedRateInverseContractFundingModel,
    InverseContractFeeModel,
)
from simulation_runtime import (
    FeeModel,
    FixedRateFeeModel,
    FixedRateFundingModel,
    FundingModel,
)

from ._values import check_fields, decimal_value, integer, string


DAILY_BAR_EXECUTION_V1 = "daily-bar-execution/v1"
NO_FUNDING = "none"
FIXED_DAILY_FUNDING_V1 = "fixed-daily/v1"
_CONTEXT = DAILY_BAR_EXECUTION_V1
_DEFAULTS: dict[str, object] = {
    "maker_fee_rate": "0",
    "taker_fee_rate": "0",
    "fee_asset": "BTC",
    "funding_model": NO_FUNDING,
}
_BASE_FIELDS = {
    "maker_fee_rate",
    "taker_fee_rate",
    "fee_asset",
    "funding_model",
}
_FUNDING_FIELDS = {
    "funding_rate",
    "funding_interval_seconds",
    "settlement_offset_seconds",
}


@dataclass(frozen=True, slots=True)
class DailyExecutionRuntime:
    fee_model: FeeModel
    funding_model: FundingModel | None


def resolve_execution_component(
    component: ComponentSpec,
) -> ComponentSpec:
    if component.type != DAILY_BAR_EXECUTION_V1:
        raise ValueError(
            f"unsupported execution component type {component.type!r}"
        )
    parameters = {**_DEFAULTS, **dict(component.parameters)}
    if parameters.get("funding_model") == FIXED_DAILY_FUNDING_V1:
        parameters.setdefault("funding_rate", "0")
        parameters.setdefault("funding_interval_seconds", 86_400)
        parameters.setdefault("settlement_offset_seconds", 0)
    return ComponentSpec(
        key=component.key,
        type=component.type,
        parameters=parameters,
    )


def build_execution_runtime(
    component: ComponentSpec,
    *,
    contract_size: Decimal,
    settlement_asset: str,
    market_type: str = "coinm",
) -> DailyExecutionRuntime:
    if component.type != DAILY_BAR_EXECUTION_V1:
        raise ValueError(
            f"unsupported execution component type {component.type!r}"
        )
    parameters = component.parameters
    check_fields(
        parameters,
        required=_BASE_FIELDS,
        optional=_FUNDING_FIELDS,
        context=_CONTEXT,
    )
    fee_asset = string(
        parameters,
        "fee_asset",
        context=_CONTEXT,
    ).upper()
    if fee_asset != settlement_asset.upper():
        raise ValueError(
            f"{_CONTEXT}.fee_asset must match account settlement_asset"
        )
    maker_fee_rate = decimal_value(
        parameters, "maker_fee_rate", context=_CONTEXT
    )
    taker_fee_rate = decimal_value(
        parameters, "taker_fee_rate", context=_CONTEXT
    )
    if market_type == "coinm":
        fee_model: FeeModel = InverseContractFeeModel(
            contract_size=contract_size,
            maker_fee_rate=maker_fee_rate,
            taker_fee_rate=taker_fee_rate,
            fee_asset=fee_asset,
        )
    elif market_type == "usdm":
        fee_model = FixedRateFeeModel(
            maker_fee_rate=maker_fee_rate,
            taker_fee_rate=taker_fee_rate,
            fee_asset=fee_asset,
        )
    else:
        raise ValueError(f"unsupported derivative market_type {market_type!r}")

    funding_kind = string(
        parameters,
        "funding_model",
        context=_CONTEXT,
    )
    if funding_kind == NO_FUNDING:
        unexpected = _FUNDING_FIELDS & set(parameters)
        if unexpected:
            raise ValueError(
                f"{_CONTEXT} funding parameters require "
                f"funding_model={FIXED_DAILY_FUNDING_V1!r}: "
                f"{sorted(unexpected)}"
            )
        funding_model = None
    elif funding_kind == FIXED_DAILY_FUNDING_V1:
        missing = _FUNDING_FIELDS - set(parameters)
        if missing:
            raise ValueError(
                f"{_CONTEXT} is missing funding parameters: "
                f"{sorted(missing)}"
            )
        funding_rate = decimal_value(
            parameters, "funding_rate", context=_CONTEXT
        )
        funding_interval_seconds = integer(
            parameters, "funding_interval_seconds", context=_CONTEXT
        )
        settlement_offset_seconds = integer(
            parameters, "settlement_offset_seconds", context=_CONTEXT
        )
        if market_type == "coinm":
            funding_model = FixedRateInverseContractFundingModel(
                funding_rate=funding_rate,
                funding_interval_seconds=funding_interval_seconds,
                settlement_offset_seconds=settlement_offset_seconds,
            )
        else:
            funding_model = FixedRateFundingModel(
                funding_rate=funding_rate,
                funding_interval_seconds=funding_interval_seconds,
                settlement_offset_seconds=settlement_offset_seconds,
                funding_asset=fee_asset,
            )
    else:
        raise ValueError(
            f"{_CONTEXT}.funding_model must be {NO_FUNDING!r} or "
            f"{FIXED_DAILY_FUNDING_V1!r}"
        )
    return DailyExecutionRuntime(
        fee_model=fee_model,
        funding_model=funding_model,
    )
