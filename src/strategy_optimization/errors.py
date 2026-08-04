"""Errors raised by strategy research study configuration and storage."""


class StudyError(Exception):
    """Base class for strategy optimization errors."""


class StudyConfigError(StudyError, ValueError):
    """A Study or protocol document is malformed or inconsistent."""


class StudyRepositoryError(StudyError):
    """Study state cannot be persisted or read safely."""


class StudyRepositoryConflictError(StudyRepositoryError):
    """Stored Study state conflicts with the requested Study."""
