"""Experiment-system exception hierarchy."""


class ExperimentError(Exception):
    """Base class for experiment-system failures."""


class ExperimentConfigError(ExperimentError, ValueError):
    """The submitted experiment document is structurally invalid."""


class ExperimentValidationError(ExperimentError, ValueError):
    """A structurally valid experiment cannot be planned safely."""


class ExperimentAccessError(ExperimentError, PermissionError):
    """A read-only resource exists but its disclosure is forbidden."""


class UnknownProviderError(ExperimentValidationError, LookupError):
    """The experiment references a provider that was not registered."""


class DuplicateProviderError(ExperimentValidationError):
    """Two providers attempted to register the same stable identifier."""


class ExperimentRepositoryError(ExperimentError):
    """Persistent experiment state is unavailable or inconsistent."""


class ExperimentRepositoryConflictError(ExperimentRepositoryError):
    """A write conflicts with existing experiment state."""


class ExperimentRepositoryIntegrityError(ExperimentRepositoryError):
    """Stored state failed a schema, checksum, or lifecycle invariant."""


class SingleRunExecutionError(ExperimentError):
    """One planned Run failed after its lifecycle record was created."""

    def __init__(self, run_id: str, cause: Exception) -> None:
        self.run_id = run_id
        self.cause = cause
        super().__init__(
            f"Run {run_id!r} failed: {type(cause).__name__}: {cause}"
        )
