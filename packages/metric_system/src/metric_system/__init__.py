"""Public API for versioned simulation evaluation metrics."""

from .aggregation import aggregate_scenario
from .core import CORE_METRIC_SET, CoreMetricCalculator, decimal_quantile
from .errors import (
    MetricDefinitionError,
    MetricError,
    MetricEvaluationError,
    MetricInputError,
)
from .inputs import (
    EquityPoint,
    EquitySeries,
    MetricInput,
    MetricInputBuilder,
    MetricInputContributor,
    PositionPoint,
    decimal_value,
    interval_milliseconds,
)
from .models import (
    AdverseDirection,
    MetricDefinition,
    MetricEvaluationStatus,
    MetricInputLevel,
    MetricSet,
    MetricValue,
    MetricValueStatus,
    MetricValueType,
    RunMetricEvaluation,
    canonical_document,
    document_hash,
)
from .registry import MetricCalculator, MetricRegistry
from .service import EvaluationBatchOutcome, MetricEvaluationService

__all__ = [
    "AdverseDirection",
    "CORE_METRIC_SET",
    "CoreMetricCalculator",
    "EquityPoint",
    "EquitySeries",
    "EvaluationBatchOutcome",
    "MetricCalculator",
    "MetricDefinition",
    "MetricDefinitionError",
    "MetricError",
    "MetricEvaluationError",
    "MetricEvaluationService",
    "MetricEvaluationStatus",
    "MetricInput",
    "MetricInputBuilder",
    "MetricInputContributor",
    "MetricInputError",
    "MetricInputLevel",
    "MetricRegistry",
    "MetricSet",
    "MetricValue",
    "MetricValueStatus",
    "MetricValueType",
    "PositionPoint",
    "RunMetricEvaluation",
    "aggregate_scenario",
    "canonical_document",
    "decimal_quantile",
    "decimal_value",
    "document_hash",
    "interval_milliseconds",
]
