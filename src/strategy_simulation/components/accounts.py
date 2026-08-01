"""COIN-M account component resolution and construction for experiments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from experiment_system import ComponentSpec
from grid_rule.adapters import (
    InverseContractLedger,
    InverseContractMarginModel,
)
from simulation_runtime import (
    FlatMaintenanceMarginSchedule,
    MarginConfig,
    MarkPriceSampling,
)

from ._values import check_fields, decimal_value, string


COINM_INVERSE_V1 = "coinm-inverse/v1"
NO_MARGIN = "none"
FLAT_MAINTENANCE_V1 = "flat-maintenance/v1"
_CONTEXT = COINM_INVERSE_V1
_DEFAULTS: dict[str, object] = {
    "contract_size": "100",
    "base_asset": "BTC",
    "quote_asset": "USDT",
    "notional_asset": "USD",
    "margin_model": NO_MARGIN,
}
_BASE_FIELDS = {
    "instrument",
    "contract_size",
    "spot_btc",
    "futures_wallet_btc",
    "base_asset",
    "quote_asset",
    "notional_asset",
    "margin_model",
}
_MARGIN_FIELDS = {
    "leverage",
    "maintenance_margin_rate",
    "mark_price_sampling",
}


@dataclass(frozen=True, slots=True)
class CoinMAccountRuntime:
    instrument: str
    contract_size: Decimal
    base_asset: str
    ledger_factory: Callable[[], InverseContractLedger]
    margin_model: InverseContractMarginModel | None
    mark_price_sampling: MarkPriceSampling


def resolve_account_component(
    component: ComponentSpec,
) -> ComponentSpec:
    if component.type != COINM_INVERSE_V1:
        raise ValueError(
            f"unsupported account component type {component.type!r}"
        )
    parameters = {**_DEFAULTS, **dict(component.parameters)}
    if parameters.get("margin_model") == FLAT_MAINTENANCE_V1:
        parameters.setdefault("leverage", "3")
        parameters.setdefault("maintenance_margin_rate", "0.005")
        parameters.setdefault(
            "mark_price_sampling",
            MarkPriceSampling.ADVERSE_EXTREME.value,
        )
    return ComponentSpec(
        key=component.key,
        type=component.type,
        parameters=parameters,
    )


def build_account_runtime(
    component: ComponentSpec,
) -> CoinMAccountRuntime:
    if component.type != COINM_INVERSE_V1:
        raise ValueError(
            f"unsupported account component type {component.type!r}"
        )
    parameters = component.parameters
    check_fields(
        parameters,
        required=_BASE_FIELDS,
        optional=_MARGIN_FIELDS,
        context=_CONTEXT,
    )
    instrument = string(
        parameters,
        "instrument",
        context=_CONTEXT,
    )
    contract_size = decimal_value(
        parameters,
        "contract_size",
        context=_CONTEXT,
    )
    spot_btc = decimal_value(
        parameters,
        "spot_btc",
        context=_CONTEXT,
    )
    futures_wallet_btc = decimal_value(
        parameters,
        "futures_wallet_btc",
        context=_CONTEXT,
    )
    base_asset = string(
        parameters,
        "base_asset",
        context=_CONTEXT,
    ).upper()
    if base_asset != "BTC":
        raise ValueError(
            f"{_CONTEXT} 2C supports only BTC settlement"
        )
    quote_asset = string(
        parameters,
        "quote_asset",
        context=_CONTEXT,
    ).upper()
    notional_asset = string(
        parameters,
        "notional_asset",
        context=_CONTEXT,
    ).upper()

    def ledger_factory() -> InverseContractLedger:
        return InverseContractLedger(
            instrument=instrument,
            contract_size=contract_size,
            spot_base_balance=spot_btc,
            futures_wallet_balance=futures_wallet_btc,
            base_asset=base_asset,
            quote_asset=quote_asset,
            notional_asset=notional_asset,
        )

    ledger_factory()
    margin_kind = string(
        parameters,
        "margin_model",
        context=_CONTEXT,
    )
    if margin_kind == NO_MARGIN:
        unexpected = _MARGIN_FIELDS & set(parameters)
        if unexpected:
            raise ValueError(
                f"{_CONTEXT} margin parameters require "
                f"margin_model={FLAT_MAINTENANCE_V1!r}: "
                f"{sorted(unexpected)}"
            )
        margin_model = None
        sampling = MarkPriceSampling.CLOSE_ONLY
    elif margin_kind == FLAT_MAINTENANCE_V1:
        missing = _MARGIN_FIELDS - set(parameters)
        if missing:
            raise ValueError(
                f"{_CONTEXT} is missing margin parameters: "
                f"{sorted(missing)}"
            )
        margin_model = InverseContractMarginModel(
            MarginConfig(
                leverage=decimal_value(
                    parameters,
                    "leverage",
                    context=_CONTEXT,
                ),
                maintenance_schedule=(
                    FlatMaintenanceMarginSchedule(
                        decimal_value(
                            parameters,
                            "maintenance_margin_rate",
                            context=_CONTEXT,
                        )
                    )
                ),
            )
        )
        try:
            sampling = MarkPriceSampling(
                string(
                    parameters,
                    "mark_price_sampling",
                    context=_CONTEXT,
                )
            )
        except ValueError as exc:
            raise ValueError(
                f"{_CONTEXT}.mark_price_sampling must be "
                "'CLOSE_ONLY' or 'ADVERSE_EXTREME'"
            ) from exc
    else:
        raise ValueError(
            f"{_CONTEXT}.margin_model must be {NO_MARGIN!r} or "
            f"{FLAT_MAINTENANCE_V1!r}"
        )

    return CoinMAccountRuntime(
        instrument=instrument,
        contract_size=contract_size,
        base_asset=base_asset,
        ledger_factory=ledger_factory,
        margin_model=margin_model,
        mark_price_sampling=sampling,
    )
