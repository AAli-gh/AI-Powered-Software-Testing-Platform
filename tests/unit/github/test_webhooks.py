import json
from typing import Any

import pytest

from testmind.github.webhooks import (
    InvalidPayloadError,
    PullEvent,
    pull_request_event,
    valid_signature,
)
from tests.github_fake import sign

SHA_A, SHA_B = "a" * 40, "b" * 40


def test_signatures() -> None:
    body = b'{"zen": "x"}'
    assert valid_signature("s3", body, sign("s3", body))
    assert not valid_signature("s3", body, sign("other", body))
    assert not valid_signature("s3", body + b" ", sign("s3", body))
    assert not valid_signature("s3", body, None)
    assert not valid_signature("s3", body, "sha1=" + "0" * 40)


def event(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "opened",
        "installation": {"id": 9},
        "repository": {"full_name": "acme/shop"},
        "pull_request": {
            "number": 4, "draft": False, "head": {"sha": SHA_B}, "base": {"sha": SHA_A},
        },
    }  # fmt: skip
    payload.update(changes)
    return payload


def test_the_pull_request_events_that_are_analysed() -> None:
    assert pull_request_event(json.dumps(event()).encode()) == PullEvent(
        9, "acme/shop", 4, SHA_B, SHA_A
    )
    for action in ("synchronize", "reopened", "ready_for_review"):
        assert isinstance(pull_request_event(json.dumps(event(action=action)).encode()), PullEvent)
    closed = pull_request_event(json.dumps(event(action="closed")).encode())
    assert closed == "action 'closed' is not analysed"
    draft = event()
    draft["pull_request"]["draft"] = True
    assert pull_request_event(json.dumps(draft).encode()) == "draft pull request"


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps({"action": "opened"}).encode(),
        json.dumps(event(repository={"full_name": "../../etc"})).encode(),
        json.dumps(event(installation={"id": "x"})).encode(),
        json.dumps(
            event(pull_request={"number": 1, "head": {"sha": "zz"}, "base": {"sha": SHA_A}})
        ).encode(),
    ],
)
def test_unusable_payloads(body: bytes) -> None:
    with pytest.raises(InvalidPayloadError, match="Not a usable pull_request event"):
        pull_request_event(body)
