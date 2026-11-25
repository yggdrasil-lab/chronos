"""Explicit domain exceptions for Chronos schedule operations."""


class ChronosDomainError(Exception):
    """Base exception for Chronos domain errors."""


class ScheduleValidationError(ChronosDomainError):
    """Raised when schedule entities or time invariants are invalid."""


class EntityNotFoundError(ChronosDomainError):
    """Raised when a requested schedule entity does not exist."""
