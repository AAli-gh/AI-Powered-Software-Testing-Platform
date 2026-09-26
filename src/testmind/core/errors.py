"""Domain exceptions.

Services raise these; the API layer translates them into HTTP responses and the worker records
them on the run. Nothing below the API layer knows about HTTP.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for expected, user-facing errors."""

    code: str = "app_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    code = "not_found"


class ConflictError(AppError):
    code = "conflict"


class InvalidStateTransitionError(ConflictError):
    code = "invalid_state_transition"


class InvalidSourceError(AppError):
    """The repository source given for a project is unusable (missing, outside root, ...)."""

    code = "invalid_source"


class DispatchError(AppError):
    """A background job could not be handed to the task queue."""

    code = "dispatch_failed"


class LLMUnavailableError(AppError):
    """AI features were requested but no LLM provider is configured."""

    code = "llm_not_configured"


class InvalidRevisionError(AppError):
    """A requested git revision does not exist or is malformed."""

    code = "invalid_revision"


class NotVersionedError(ConflictError):
    """The project's source is not a (supported) git repository."""

    code = "not_a_git_repository"
