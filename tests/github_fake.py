"""A local stand-in for the GitHub REST API: the external boundary of the GitHub integration.

It checks what the real API checks for TestMind's calls: the App JWT is verified against the
App's public key (RS256, issuer, expiry), installation tokens must have been issued for an
installation that can access the repository, and unknown objects are 404s. Archives are real
gzip tarballs with GitHub's top-level directory, served through a redirect as GitHub does.
Every call is recorded, so tests assert what TestMind sent.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import secrets
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

APP_ID = 4242


def sign(secret: str, body: bytes) -> str:
    """The X-Hub-Signature-256 header GitHub sends with a delivery."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def pull_event(
    repository: str, number: int, base: str, head: str, installation: int = 1, **changes: Any
) -> dict[str, Any]:
    """A `pull_request` webhook payload (the fields TestMind reads, as GitHub sends them)."""
    payload: dict[str, Any] = {
        "action": "opened",
        "installation": {"id": installation},
        "repository": {"full_name": repository},
        "pull_request": {
            "number": number, "draft": False, "head": {"sha": head}, "base": {"sha": base},
        },
    }  # fmt: skip
    payload.update(changes)
    return payload


def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@dataclass
class Repository:
    installation_id: int
    commits: dict[str, dict[str, str | bytes]] = field(default_factory=dict)  # sha -> files
    refs: dict[str, str] = field(default_factory=dict)  # ref -> sha
    pulls: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Extra archive members per sha: (name, kind, content) with kind "file", "symlink", "hardlink"
    extras: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)


@dataclass
class Call:
    method: str
    path: str
    body: Any


class FakeGitHub:
    def __init__(self, private_key: str, app_id: int = APP_ID) -> None:
        key = serialization.load_pem_private_key(private_key.encode(), None)
        assert isinstance(key, rsa.RSAPrivateKey)
        self.public_key = key.public_key()
        self.app_id = app_id
        self.repositories: dict[str, Repository] = {}
        self.tokens: dict[str, int] = {}  # token -> installation id
        self.calls: list[Call] = []
        self.check_runs: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, dict[str, Any]] = {}
        self.issues: list[dict[str, Any]] = []
        self.fail: dict[str, int] = {}  # "METHOD path-prefix" -> status to answer
        self._ids = 1000
        self.app = self._build()

    # --------------------------------------------------------------------------- setup
    def add_repository(self, name: str, installation_id: int = 1) -> Repository:
        self.repositories[name] = Repository(installation_id=installation_id)
        return self.repositories[name]

    def add_commit(
        self, name: str, files: Mapping[str, str | bytes], ref: str | None = None
    ) -> str:
        sha = secrets.token_hex(20)
        repository = self.repositories[name]
        repository.commits[sha] = dict(files)
        if ref is not None:
            repository.refs[ref] = sha
        return sha

    def add_pull(self, name: str, number: int, base: str, head: str, **extra: Any) -> None:
        self.repositories[name].pulls[number] = {
            "number": number, "title": extra.get("title", f"Change {number}"),
            "html_url": f"https://github.test/{name}/pull/{number}",
            "draft": extra.get("draft", False),
            "head": {"sha": head, "ref": extra.get("head_ref", "feature")},
            "base": {"sha": base, "ref": extra.get("base_ref", "main")},
        }  # fmt: skip

    def next_id(self) -> int:
        self._ids += 1
        return self._ids

    # ---------------------------------------------------------------------------- app
    def _build(self) -> FastAPI:  # noqa: PLR0915 - one route per GitHub endpoint
        app = FastAPI()
        fake = self

        @app.exception_handler(HTTPException)
        async def github_error(request: Request, exc: HTTPException) -> Response:
            return JSONResponse({"message": exc.detail}, status_code=exc.status_code)

        @app.middleware("http")
        async def record(request: Request, call_next: Any) -> Response:
            body: Any = None
            raw = await request.body()
            if raw:
                body = json.loads(raw) if raw.startswith(b"{") else raw
            fake.calls.append(Call(request.method, request.url.path, body))
            for prefix, status in fake.fail.items():
                method, _, path = prefix.partition(" ")
                if request.method == method and request.url.path.startswith(path):
                    return Response(f'{{"message": "failure {status}"}}', status)
            response: Response = await call_next(request)
            return response

        def installation_for(request: Request, name: str) -> Repository:
            header = request.headers.get("authorization", "")
            if not header.startswith("token ") or header[6:] not in fake.tokens:
                raise HTTPException(401, "Bad credentials")
            repository = fake.repositories.get(name)
            if repository is None or repository.installation_id != fake.tokens[header[6:]]:
                raise HTTPException(404, "Not Found")
            return repository

        @app.post("/app/installations/{installation_id}/access_tokens", status_code=201)
        async def access_token(installation_id: int, request: Request) -> dict[str, Any]:
            header = request.headers.get("authorization", "")
            if not header.startswith("Bearer "):
                raise HTTPException(401, "A JSON web token could not be decoded")
            try:
                claims = jwt.decode(header[7:], fake.public_key, algorithms=["RS256"])
            except jwt.PyJWTError as exc:
                raise HTTPException(401, f"invalid JWT: {exc}") from None
            if claims.get("iss") != str(fake.app_id) or claims["exp"] - claims["iat"] > 600:
                raise HTTPException(401, "invalid issuer or lifetime")
            if not any(r.installation_id == installation_id for r in fake.repositories.values()):
                raise HTTPException(404, "Not Found")
            token = "ghs_" + secrets.token_hex(16)
            fake.tokens[token] = installation_id
            expires = datetime.now(UTC) + timedelta(hours=1)
            return {"token": token, "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ")}

        @app.get("/repos/{owner}/{repo}/pulls/{number}")
        async def pull(owner: str, repo: str, number: int, request: Request) -> dict[str, Any]:
            repository = installation_for(request, f"{owner}/{repo}")
            if number not in repository.pulls:
                raise HTTPException(404, "Not Found")
            return repository.pulls[number]

        @app.get("/repos/{owner}/{repo}/commits/{ref:path}")
        async def commit(owner: str, repo: str, ref: str, request: Request) -> dict[str, Any]:
            repository = installation_for(request, f"{owner}/{repo}")
            sha = repository.refs.get(ref, ref if ref in repository.commits else None)
            if sha is None:
                raise HTTPException(422, f"No commit found for SHA: {ref}")
            return {"sha": sha}

        @app.get("/repos/{owner}/{repo}/tarball/{sha}")
        async def tarball(owner: str, repo: str, sha: str, request: Request) -> Response:
            repository = installation_for(request, f"{owner}/{repo}")
            if sha not in repository.commits:
                raise HTTPException(404, "Not Found")
            return RedirectResponse(f"/_codeload/{owner}/{repo}/{sha}", status_code=302)

        @app.get("/_codeload/{owner}/{repo}/{sha}")
        async def codeload(owner: str, repo: str, sha: str) -> Response:
            repository = fake.repositories[f"{owner}/{repo}"]
            archive = _tarball(
                f"{owner}-{repo}-{sha[:7]}", repository.commits[sha], repository.extras.get(sha, [])
            )
            return Response(archive, media_type="application/x-gzip")

        @app.post("/repos/{owner}/{repo}/check-runs", status_code=201)
        async def create_check(owner: str, repo: str, request: Request) -> dict[str, Any]:
            installation_for(request, f"{owner}/{repo}")
            data = await request.json()
            check_id = fake.next_id()
            fake.check_runs[check_id] = {**data, "repository": f"{owner}/{repo}"}
            return {"id": check_id}

        @app.patch("/repos/{owner}/{repo}/check-runs/{check_id}")
        async def update_check(
            owner: str, repo: str, check_id: int, request: Request
        ) -> dict[str, Any]:
            installation_for(request, f"{owner}/{repo}")
            if check_id not in fake.check_runs:
                raise HTTPException(404, "Not Found")
            fake.check_runs[check_id].update(await request.json())
            return {"id": check_id}

        @app.post("/repos/{owner}/{repo}/issues/{number}/comments", status_code=201)
        async def comment(owner: str, repo: str, number: int, request: Request) -> dict[str, Any]:
            installation_for(request, f"{owner}/{repo}")
            comment_id = fake.next_id()
            fake.comments[comment_id] = {"number": number, **await request.json()}
            return {"id": comment_id}

        @app.patch("/repos/{owner}/{repo}/issues/comments/{comment_id}")
        async def edit(owner: str, repo: str, comment_id: int, request: Request) -> dict[str, Any]:
            installation_for(request, f"{owner}/{repo}")
            if comment_id not in fake.comments:
                raise HTTPException(404, "Not Found")
            fake.comments[comment_id].update(await request.json())
            return {"id": comment_id}

        @app.post("/repos/{owner}/{repo}/issues", status_code=201)
        async def issue(owner: str, repo: str, request: Request) -> dict[str, Any]:
            installation_for(request, f"{owner}/{repo}")
            number = len(fake.issues) + 1
            fake.issues.append({"number": number, **await request.json()})
            return {
                "number": number,
                "html_url": f"https://github.test/{owner}/{repo}/issues/{number}",
            }

        return app


def _tarball(top: str, files: dict[str, str | bytes], extras: list[tuple[str, str, str]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        directory = tarfile.TarInfo(top)
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        for path, content in sorted(files.items()):
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(f"{top}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, kind, content in extras:
            info = tarfile.TarInfo(name if name.startswith("/") else f"{top}/{name}")
            if kind == "file":
                data = content.encode()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                continue
            info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            info.linkname = content
            tar.addfile(info)
    return buffer.getvalue()
