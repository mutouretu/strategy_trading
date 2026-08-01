"""Metric evaluation errors."""


class MetricError(Exception):
    """Base class for metric-system failures."""


class MetricDefinitionError(MetricError, ValueError):
    """A metric definition or set is invalid."""


class MetricInputError(MetricError, ValueError):
    """Stored experiment facts cannot form a valid metric input."""


class MetricEvaluationError(MetricError, RuntimeError):
    """A requested metric evaluation cannot be completed safely."""
