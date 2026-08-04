"""Derivative account component construction for strategy experiments."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from experiment_system import ComponentSpec
from grid_rule.adapters import (
    InverseContractLedger,
    InverseContractMarginModel,
)
from simulation_runtime import (
    FlatMaintenanceMarginSchedule,
    LinearLedger,
    MarginConfig,
    MarkPriceSampling,
    SimulationLedger,
)

from ..adapters import LinearContractMarginModel
from ._values import check_fields, decimal_value, string


COINM_INVERSE_V1 = "coinm-inverse/v1"
USDM_LINEAR_V1 = "usdm-linear/v1"
NO_MARGIN = "none"
FLAT_MAINTENANCE_V1 = "flat-maintenance/v1"
_COINM_DEFAULTS: dict[str, object] = {
    "contract_size": "100",
    "base_asset": "BTC",
    "quote_asset": "USDT",
    "notional_asset": "USD",
    "margin_model": NO_MARGIN,
}
_USDM_DEFAULTS: dict[str, object] = {
    "settlement_asset": "USDT",
    "notional_asset": "USDT",
    "margin_model": FLAT_MAINTENANCE_V1,
}
_COINM_BASE_FIELDS = {
    "instrument",
    "contract_size",
    "spot_btc",
    "futures_wallet_btc",
    "base_asset",
    "quote_asset",
    "notional_asset",
    "margin_model",
}
_USDM_BASE_FIELDS = {
    "instrument",
    "futures_wallet_usdt",
    "settlement_asset",
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
    ledger_factory: Callable[[], SimulationLedger]
    margin_model: InverseContractMarginModel | None
    mark_price_sampling: MarkPriceSampling
    market_type: str = "coinm"

    @property
    def settlement_asset(self) -> str:
        return self.base_asset


@dataclass(frozen=True, slots=True)
class UsdmAccountRuntime:
    instrument: str
    settlement_asset: str
    ledger_factory: Callable[[], SimulationLedger]
    margin_model: LinearContractMarginModel | None
    mark_price_sampling: MarkPriceSampling
    contract_size: Decimal = Decimal("0")
    market_type: str = "usdm"


AccountRuntime = CoinMAccountRuntime | UsdmAccountRuntime


def resolve_account_component(component: ComponentSpec) -> ComponentSpec:
    if component.type == COINM_INVERSE_V1:
        parameters = {**_COINM_DEFAULTS, **dict(component.parameters)}
    elif component.type == USDM_LINEAR_V1:
        parameters = {**_USDM_DEFAULTS, **dict(component.parameters)}
    else:
        raise ValueError(
            f"unsupported account component type {component.type!r}"
        )
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


def build_account_runtime(component: ComponentSpec) -> AccountRuntime:
    if component.type == COINM_INVERSE_V1:
        return _build_coinm_account(component)
    if component.type == USDM_LINEAR_V1:
        return _build_usdm_account(component)
    raise ValueError(
        f"unsupported account component type {component.type!r}"
    )


def _margin_settings(
    parameters: Mapping[str, object],
    *,
    context: str,
) -> tuple[MarginConfig | None, MarkPriceSampling]:
    margin_kind = string(parameters, "margin_model", context=context)
    if margin_kind == NO_MARGIN:
        unexpected = _MARGIN_FIELDS & set(parameters)
        if unexpected:
            raise ValueError(
                f"{context} margin parameters require "
                f"margin_model={FLAT_MAINTENANCE_V1!r}: "
                f"{sorted(unexpected)}"
            )
        return None, MarkPriceSampling.CLOSE_ONLY
    if margin_kind != FLAT_MAINTENANCE_V1:
        raise ValueError(
            f"{context}.margin_model must be {NO_MARGIN!r} or "
            f"{FLAT_MAINTENANCE_V1!r}"
        )
    missing = _MARGIN_FIELDS - set(parameters)
    if missing:
        raise ValueError(
            f"{context} is missing margin parameters: {sorted(missing)}"
        )
    config = MarginConfig(
        leverage=decimal_value(parameters, "leverage", context=context),
        maintenance_schedule=FlatMaintenanceMarginSchedule(
            decimal_value(
                parameters,
                "maintenance_margin_rate",
                context=context,
            )
        ),
    )
    try:
        sampling = MarkPriceSampling(
            string(parameters, "mark_price_sampling", context=context)
        )
    except ValueError as exc:
        raise ValueError(
            f"{context}.mark_price_sampling must be "
            "'CLOSE_ONLY' or 'ADVERSE_EXTREME'"
        ) from exc
    return config, sampling


def _build_coinm_account(component: ComponentSpec) -> CoinMAccountRuntime:
    context = COINM_INVERSE_V1
    parameters = component.parameters
    check_fields(
        parameters,
        required=_COINM_BASE_FIELDS,
        optional=_MARGIN_FIELDS,
        context=context,
    )
    instrument = string(parameters, "instrument", context=context)
    contract_size = decimal_value(
        parameters, "contract_size", context=context
    )
    spot_btc = decimal_value(parameters, "spot_btc", context=context)
    futures_wallet_btc = decimal_value(
        parameters, "futures_wallet_btc", context=context
    )
    base_asset = string(parameters, "base_asset", context=context).upper()
    if base_asset != "BTC":
        raise ValueError(f"{context} supports only BTC settlement")
    quote_asset = string(parameters, "quote_asset", context=context).upper()
    notional_asset = string(
        parameters, "notional_asset", context=context
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
    config, sampling = _margin_settings(parameters, context=context)
    margin_model = (
        None if config is None else InverseContractMarginModel(config)
    )
    return CoinMAccountRuntime(
        instrument=instrument,
        contract_size=contract_size,
        base_asset=base_asset,
        ledger_factory=ledger_factory,
        margin_model=margin_model,
        mark_price_sampling=sampling,
    )


def _build_usdm_account(component: ComponentSpec) -> UsdmAccountRuntime:
    context = USDM_LINEAR_V1
    parameters = component.parameters
    check_fields(
        parameters,
        required=_USDM_BASE_FIELDS,
        optional=_MARGIN_FIELDS,
        context=context,
    )
    instrument = string(parameters, "instrument", context=context)
    wallet = decimal_value(
        parameters, "futures_wallet_usdt", context=context
    )
    if wallet <= 0:
        raise ValueError(f"{context}.futures_wallet_usdt must be > 0")
    settlement_asset = string(
        parameters, "settlement_asset", context=context
    ).upper()
    notional_asset = string(
        parameters, "notional_asset", context=context
    ).upper()
    if settlement_asset != notional_asset:
        raise ValueError(
            f"{context} requires matching settlement and notional assets"
        )

    def ledger_factory() -> LinearLedger:
        return LinearLedger(wallet, equity_asset=settlement_asset)

    config, sampling = _margin_settings(parameters, context=context)
    margin_model = (
        None
        if config is None
        else LinearContractMarginModel(
            config,
            instrument=instrument,
            settlement_asset=settlement_asset,
        )
    )
    return UsdmAccountRuntime(
        instrument=instrument,
        settlement_asset=settlement_asset,
        ledger_factory=ledger_factory,
        margin_model=margin_model,
        mark_price_sampling=sampling,
    )
