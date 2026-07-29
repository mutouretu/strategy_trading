"""Public API for the domain-neutral simulation runtime."""

from .fees import (
    FeeModel,
    FeeResult,
    FixedRateFeeModel,
    ZeroFeeModel,
    default_liquidity_role,
)
from .funding import (
    FixedFundingSchedule,
    FixedRateFundingModel,
    FundingModel,
    FundingSettlement,
    ZeroFundingModel,
)
from .ledger import LinearLedger, SimulationLedger
from .margin import (
    FlatMaintenanceMarginSchedule,
    LiquidationEvent,
    MaintenanceMarginSchedule,
    MaintenanceMarginTier,
    MarkPriceSampling,
    MarginConfig,
    MarginModel,
    MarginSnapshot,
    NoMarginModel,
    TieredMaintenanceMarginSchedule,
)
from .models import (
    EquitySnapshot,
    IntentRecord,
    IntentSnapshot,
    IntentStatus,
    LiquidityRole,
    OrderSide,
    SimFill,
    SimulationResult,
    SimulationTerminationReason,
    TradeInstruction,
    TradeIntentMode,
)
from .reporting import simulation_result_to_document
from .runner import (
    InsufficientMarginError,
    ReduceOnlyViolationError,
    SimulationRunner,
)
from .slippage import (
    FixedBpsSlippageModel,
    NoSlippageModel,
    SlippageModel,
)
from .trace import SimulationTracePort
from .trade import SimulationTradePort

__all__ = [
    "EquitySnapshot",
    "FeeModel",
    "FeeResult",
    "FixedRateFeeModel",
    "FixedFundingSchedule",
    "FixedRateFundingModel",
    "FixedBpsSlippageModel",
    "FlatMaintenanceMarginSchedule",
    "IntentRecord",
    "IntentSnapshot",
    "IntentStatus",
    "InsufficientMarginError",
    "LinearLedger",
    "LiquidationEvent",
    "LiquidityRole",
    "MaintenanceMarginSchedule",
    "MaintenanceMarginTier",
    "MarginConfig",
    "MarginModel",
    "MarginSnapshot",
    "MarkPriceSampling",
    "NoMarginModel",
    "NoSlippageModel",
    "OrderSide",
    "ReduceOnlyViolationError",
    "SimFill",
    "SimulationResult",
    "SimulationTerminationReason",
    "SimulationLedger",
    "SimulationRunner",
    "SlippageModel",
    "SimulationTracePort",
    "SimulationTradePort",
    "TradeInstruction",
    "TradeIntentMode",
    "TieredMaintenanceMarginSchedule",
    "ZeroFeeModel",
    "ZeroFundingModel",
    "FundingModel",
    "FundingSettlement",
    "default_liquidity_role",
    "simulation_result_to_document",
]
