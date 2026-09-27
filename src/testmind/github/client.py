"""GitHub REST API as a GitHub App.

Authentication: a short-lived JWT signed with the App's private key (RS256) is exchanged for
an installation access token, cached until shortly before it expires. The private key and the
tokens are secrets. They are read from the environment only, never logged or stored in the
database, and never passed to the sandbox.

Every repository name and commit sha is validated before it becomes part of a URL. Answers
are read as untrusted input: only the fields TestMind needs are taken, with bounded sizes.
"""

from __future__ import annotations

import re
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.core.logging import get_logger
from testmind.vcs.archive import extract_archive
from testmind.vcs.git import Snapshot

logger = get_logger(__name__)

API_VERSION = "2022-11-28"
USER_AGENT = "TestMind"
JWT_LIFETIME = 540  # seconds; GitHub accepts at most 10 minutes
JWT_CLOCK_SKEW = 60
TOKEN_MARGIN = 300  # renew installation tokens this long before they expire
MAX_ERROR_TEXT = 500
MAX_COMMENT = 65_000  # GitHub's limit is 65 536 characters
REPOSITORY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.\.?$)[A-Za-z0-9._-]{1,100}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
REF = re.compile(r"^(?!.*\.\.)[A-Za-z0-9._/-]{1,200}$")


class GitHubError(Exception):
    """A GitHub API call failed (status 0: no answer at all)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class AppCredentials:
    app_id: int
    private_key: str  # PEM

    def __repr__(self) -> str:  # never print the key
        return f"AppCredentials(app_id={self.app_id})"


@dataclass(frozen=True, slots=True)
class PullRequest:
    repository: str
    number: int
    title: str
    html_url: str
    draft: bool
    head_sha: str
    head_ref: str
    base_sha: str
    base_ref: str


def check_repository(name: str) -> str:
    if not REPOSITORY.match(name):
        raise GitHubError(f"Invalid repository name: {name!r} (expected owner/name)")
    return name


def check_sha(sha: str) -> str:
    if not SHA.match(sha):
        raise GitHubError(f"Invalid commit sha: {sha!r}")
    return sha


def check_ref(ref: str) -> str:
    if not REF.match(ref):
        raise GitHubError(f"Invalid git reference: {ref!r}")
    return ref


def app_jwt(credentials: AppCredentials, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time()) - JWT_CLOCK_SKEW
    claims = {
        "iat": issued,
        "exp": issued + JWT_CLOCK_SKEW + JWT_LIFETIME,
        "iss": str(credentials.app_id),
    }
    try:
        return jwt.encode(claims, credentials.private_key, algorithm="RS256")
    except (ValueError, TypeError, jwt.PyJWTError) as exc:
        raise GitHubError(
            f"The GitHub App private key cannot be used: {type(exc).__name__}"
        ) from None


class GitHubClient:
    def __init__(
        self,
        credentials: AppCredentials,
        *,
        api_url: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
        max_archive_bytes: int = 200 * 1024 * 1024,
        limits: AnalysisLimits | None = None,
    ) -> None:
        self._credentials = credentials
        self._http = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            transport=transport,
            timeout=timeout,
            follow_redirects=True,  # archives redirect to codeload.github.com
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            },
        )
        self._max_archive = max_archive_bytes
        self._limits = limits or AnalysisLimits()
        self._tokens: dict[int, tuple[str, float]] = {}

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ authentication
    async def installation_token(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached is not None and cached[1] - TOKEN_MARGIN > time.time():
            return cached[0]
        data = await self._request(
            "POST",
            f"/app/installations/{int(installation_id)}/access_tokens",
            auth=f"Bearer {app_jwt(self._credentials)}",
        )
        token = _string(data, "token")
        expires = _timestamp(_string(data, "expires_at"))
        self._tokens[installation_id] = (token, expires)
        return token

    # ------------------------------------------------------------------------ reading
    async def pull_request(self, installation_id: int, repository: str, number: int) -> PullRequest:
        data = await self._api(
            installation_id, "GET", f"/repos/{check_repository(repository)}/pulls/{int(number)}"
        )
        head, base = _object(data, "head"), _object(data, "base")
        return PullRequest(
            repository=repository,
            number=int(number),
            title=_string(data, "title")[:300],
            html_url=_string(data, "html_url")[:500],
            draft=bool(data.get("draft", False)),
            head_sha=check_sha(_string(head, "sha")),
            head_ref=_string(head, "ref")[:200],
            base_sha=check_sha(_string(base, "sha")),
            base_ref=_string(base, "ref")[:200],
        )

    async def commit_sha(self, installation_id: int, repository: str, ref: str) -> str:
        path = f"/repos/{check_repository(repository)}/commits/{quote(check_ref(ref), safe='')}"
        return check_sha(_string(await self._api(installation_id, "GET", path), "sha"))

    async def download(
        self, installation_id: int, repository: str, sha: str, destination: Path
    ) -> Snapshot:
        """The commit's files, from GitHub's tarball, extracted safely into `destination`."""
        token = await self.installation_token(installation_id)
        url = f"/repos/{check_repository(repository)}/tarball/{check_sha(sha)}"
        with tempfile.NamedTemporaryFile(prefix="testmind-archive-", suffix=".tar.gz") as file:
            size = 0
            try:
                async with self._http.stream("GET", url, headers=_auth(f"token {token}")) as reply:
                    if reply.status_code != httpx.codes.OK:
                        await reply.aread()
                        raise _error(reply)
                    async for chunk in reply.aiter_bytes():
                        size += len(chunk)
                        if size > self._max_archive:
                            raise GitHubError(
                                f"The repository archive exceeds {self._max_archive // 2**20} MB"
                            )
                        file.write(chunk)
            except httpx.HTTPError as exc:
                raise GitHubError(f"GitHub could not be reached: {type(exc).__name__}") from None
            file.flush()
            return extract_archive(Path(file.name), destination, self._limits)

    # ------------------------------------------------------------------------ writing
    async def create_check_run(
        self, installation_id: int, repository: str, head_sha: str, name: str
    ) -> int:
        data = await self._api(
            installation_id,
            "POST",
            f"/repos/{check_repository(repository)}/check-runs",
            json={"name": name, "head_sha": check_sha(head_sha), "status": "in_progress"},
        )
        return _integer(data, "id")

    async def complete_check_run(
        self,
        installation_id: int,
        repository: str,
        check_run_id: int,
        *,
        conclusion: str,
        title: str,
        summary: str,
    ) -> None:
        await self._api(
            installation_id,
            "PATCH",
            f"/repos/{check_repository(repository)}/check-runs/{int(check_run_id)}",
            json={
                "status": "completed",
                "conclusion": conclusion,
                "output": {"title": title[:250], "summary": summary[:MAX_COMMENT]},
            },
        )

    async def upsert_comment(
        self, installation_id: int, repository: str, number: int, body: str, comment_id: int | None
    ) -> int:
        """Update TestMind's comment on the pull request, or post one."""
        base = f"/repos/{check_repository(repository)}/issues"
        if comment_id is not None:
            try:
                await self._api(
                    installation_id, "PATCH", f"{base}/comments/{int(comment_id)}",
                    json={"body": body[:MAX_COMMENT]},
                )  # fmt: skip
                return comment_id
            except GitHubError as exc:
                if exc.status != httpx.codes.NOT_FOUND:  # deleted by someone: post a new one
                    raise
        data = await self._api(
            installation_id,
            "POST",
            f"{base}/{int(number)}/comments",
            json={"body": body[:MAX_COMMENT]},
        )
        return _integer(data, "id")

    async def create_issue(
        self, installation_id: int, repository: str, *, title: str, body: str, labels: list[str]
    ) -> tuple[int, str]:
        data = await self._api(
            installation_id,
            "POST",
            f"/repos/{check_repository(repository)}/issues",
            json={"title": title[:250], "body": body[:MAX_COMMENT], "labels": labels},
        )
        return _integer(data, "number"), _string(data, "html_url")[:500]

    # ----------------------------------------------------------------------- plumbing
    async def _api(
        self, installation_id: int, method: str, path: str, json: object | None = None
    ) -> Mapping[str, Any]:
        token = await self.installation_token(installation_id)
        return await self._request(method, path, auth=f"token {token}", json=json)

    async def _request(
        self, method: str, path: str, *, auth: str, json: object | None = None
    ) -> Mapping[str, Any]:
        try:
            reply = await self._http.request(method, path, headers=_auth(auth), json=json)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub could not be reached: {type(exc).__name__}") from None
        if reply.status_code >= httpx.codes.BAD_REQUEST:
            raise _error(reply)
        logger.info("github_api", method=method, path=path, status=reply.status_code)
        try:
            data = reply.json()
        except ValueError:
            raise GitHubError("GitHub answered with something that is not JSON") from None
        if not isinstance(data, dict):
            raise GitHubError("GitHub answered with an unexpected document")
        return data


def _auth(value: str) -> dict[str, str]:
    return {"Authorization": value}


def _error(reply: httpx.Response) -> GitHubError:
    try:
        message = str(reply.json().get("message", ""))
    except ValueError:
        message = reply.text
    return GitHubError(
        f"GitHub answered {reply.status_code}: {message[:MAX_ERROR_TEXT] or reply.reason_phrase}",
        reply.status_code,
    )


def _object(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise GitHubError(f"GitHub's answer has no {key!r} object")
    return value


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise GitHubError(f"GitHub's answer has no {key!r}")
    return value


def _integer(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise GitHubError(f"GitHub's answer has no {key!r}")
    return value


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise GitHubError(f"GitHub's answer has an invalid expiry: {value[:40]!r}") from None
