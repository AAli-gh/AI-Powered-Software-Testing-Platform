"""Webhook deliveries: signature check and the pull request events TestMind acts on."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from testmind.github.client import GitHubError, check_repository, check_sha

ANALYSED_ACTIONS = frozenset({"opened", "synchronize", "reopened", "ready_for_review"})
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    """`X-Hub-Signature-256: sha256=<hex HMAC of the body>`, compared in constant time."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@dataclass(frozen=True, slots=True)
class PullEvent:
    installation_id: int
    repository: str
    number: int
    head_sha: str
    base_sha: str


class InvalidPayloadError(ValueError):
    pass


def pull_request_event(body: bytes) -> PullEvent | str:
    """The pull request to analyse, or why the event is ignored."""
    try:
        payload = json.loads(body)
        action = payload["action"]
        pull = payload["pull_request"]
        if action not in ANALYSED_ACTIONS:
            return f"action {action!r} is not analysed"
        if pull.get("draft"):
            return "draft pull request"
        return PullEvent(
            installation_id=int(payload["installation"]["id"]),
            repository=check_repository(str(payload["repository"]["full_name"])),
            number=int(pull["number"]),
            head_sha=check_sha(str(pull["head"]["sha"])),
            base_sha=check_sha(str(pull["base"]["sha"])),
        )
    except (ValueError, KeyError, TypeError, GitHubError) as exc:
        raise InvalidPayloadError(
            f"Not a usable pull_request event: {type(exc).__name__}"
        ) from None
