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


class InvalidTargetError(AppError):
    """The target URL for a web scan is unusable (bad scheme, internal/loopback host, ...)."""

    code = "invalid_target"


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


class GitHubNotConfiguredError(AppError):
    """The GitHub App (or the part of it an operation needs) is not configured."""

    code = "github_not_configured"


class InvalidSignatureError(AppError):
    """A webhook delivery is not signed with the configured secret."""

    code = "invalid_signature"


class RepositoryNotAllowedError(AppError):
    """The repository's owner is not in TESTMIND_GITHUB_ALLOWED_OWNERS."""

    code = "repository_not_allowed"


class PayloadTooLargeError(AppError):
    code = "payload_too_large"


class GitHubRequestError(AppError):
    """GitHub refused or failed a request made on the user's behalf."""

    code = "github_error"
