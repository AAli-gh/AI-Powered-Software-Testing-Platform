"""The GitHub App client against the local GitHub stand-in (tests/github_fake.py)."""

import time
from pathlib import Path

import httpx
import pytest

from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.github.client import AppCredentials, GitHubClient, GitHubError, app_jwt
from tests.github_fake import APP_ID, FakeGitHub

REPO = "acme/shop"


def calls(github: FakeGitHub) -> list[tuple[str, str]]:
    return [(c.method, c.path) for c in github.calls]


async def test_installation_tokens_are_issued_for_the_app_and_cached(
    github: FakeGitHub, client: GitHubClient
) -> None:
    github.add_repository(REPO, installation_id=7)
    first = await client.installation_token(7)
    assert first.startswith("ghs_")
    assert await client.installation_token(7) == first  # cached
    assert calls(github) == [("POST", "/app/installations/7/access_tokens")]
    client._tokens[7] = (first, time.time() + 60)  # about to expire: renewed
    assert await client.installation_token(7) != first


async def test_a_wrong_app_or_key_is_refused(github: FakeGitHub, private_key: str) -> None:
    github.add_repository(REPO)
    wrong_app = GitHubClient(
        AppCredentials(APP_ID + 1, private_key), api_url="http://github.test",
        transport=httpx.ASGITransport(app=github.app),
    )  # fmt: skip
    with pytest.raises(GitHubError, match="GitHub answered 401: invalid issuer") as caught:
        await wrong_app.installation_token(1)
    assert caught.value.status == 401
    await wrong_app.close()
    with pytest.raises(GitHubError, match="private key cannot be used"):
        app_jwt(AppCredentials(APP_ID, "not a key"))
    assert "BEGIN" not in repr(AppCredentials(APP_ID, private_key))


async def test_reading_a_pull_request_and_a_ref(github: FakeGitHub, client: GitHubClient) -> None:
    github.add_repository(REPO)
    base = github.add_commit(REPO, {"a.py": "x = 1\n"}, ref="main")
    head = github.add_commit(REPO, {"a.py": "x = 2\n"})
    github.add_pull(REPO, 5, base, head, title="Fix x")
    pull = await client.pull_request(1, REPO, 5)
    assert (pull.number, pull.title, pull.base_sha, pull.head_sha, pull.base_ref) == (
        5, "Fix x", base, head, "main",
    )  # fmt: skip
    assert await client.commit_sha(1, REPO, "main") == base
    with pytest.raises(GitHubError, match="404"):
        await client.pull_request(1, REPO, 6)
    with pytest.raises(GitHubError, match="422"):
        await client.commit_sha(1, REPO, "missing")


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda c: c.pull_request(1, "../etc", 1), "Invalid repository name"),
        (lambda c: c.pull_request(1, "acme/shop/pulls", 1), "Invalid repository name"),
        (lambda c: c.commit_sha(1, REPO, "a/../b"), "Invalid git reference"),
        (lambda c: c.create_check_run(1, REPO, "not-a-sha", "x"), "Invalid commit sha"),
    ],
)
async def test_names_are_validated_before_any_request(
    github: FakeGitHub, client: GitHubClient, call: object, message: str
) -> None:
    with pytest.raises(GitHubError, match=message):
        await call(client)  # type: ignore[operator]
    assert github.calls == []


async def test_downloading_a_commit(
    github: FakeGitHub, client: GitHubClient, tmp_path: Path
) -> None:
    repository = github.add_repository(REPO)
    sha = github.add_commit(REPO, {"app.py": "x = 1\n", "pkg/mod.py": "y = 2\n"})
    repository.extras[sha] = [
        ("link.py", "symlink", "/etc/passwd"),
        ("hard.py", "hardlink", "app.py"),
        ("../escape.py", "file", "boom"),
        (".git/config", "file", "[core]"),
        ("/absolute.py", "file", "boom"),
    ]
    snapshot = await client.download(1, REPO, sha, tmp_path / "out")
    assert snapshot.files == 2
    assert (tmp_path / "out" / "pkg" / "mod.py").read_text() == "y = 2\n"
    assert sorted(snapshot.skipped) == [
        "../escape.py: unsafe path",
        ".git/config: unsafe path",
        "/absolute.py: unsafe path",
        "hard.py: hard link",
        "link.py: symbolic link",
    ]
    assert not (tmp_path / "escape.py").exists()
    assert ("GET", f"/_codeload/{REPO}/{sha}") in calls(github)  # the redirect was followed


async def test_download_limits_and_errors(
    github: FakeGitHub, private_key: str, tmp_path: Path
) -> None:
    github.add_repository(REPO)
    sha = github.add_commit(REPO, {"big.py": "x" * 5000})
    small = GitHubClient(
        AppCredentials(APP_ID, private_key), api_url="http://github.test",
        transport=httpx.ASGITransport(app=github.app), max_archive_bytes=100,
    )  # fmt: skip
    with pytest.raises(GitHubError, match="archive exceeds 0 MB"):
        await small.download(1, REPO, sha, tmp_path / "a")
    await small.close()
    limited = GitHubClient(
        AppCredentials(APP_ID, private_key), api_url="http://github.test",
        transport=httpx.ASGITransport(app=github.app), limits=AnalysisLimits(max_read_bytes=10),
    )  # fmt: skip
    snapshot = await limited.download(1, REPO, sha, tmp_path / "b")
    assert snapshot.skipped == ("big.py: too large",)
    with pytest.raises(GitHubError, match="404"):
        await limited.download(1, REPO, "f" * 40, tmp_path / "c")
    await limited.close()


async def test_check_runs_comments_and_issues(github: FakeGitHub, client: GitHubClient) -> None:
    github.add_repository(REPO)
    sha = github.add_commit(REPO, {"a.py": ""})
    check = await client.create_check_run(1, REPO, sha, "TestMind")
    assert github.check_runs[check]["status"] == "in_progress"
    await client.complete_check_run(
        1, REPO, check, conclusion="failure", title="1 regression", summary="x" * 70_000
    )
    assert github.check_runs[check]["conclusion"] == "failure"
    assert len(github.check_runs[check]["output"]["summary"]) == 65_000  # GitHub's limit

    first = await client.upsert_comment(1, REPO, 3, "first", None)
    assert await client.upsert_comment(1, REPO, 3, "second", first) == first
    assert github.comments[first]["body"] == "second"
    del github.comments[first]  # deleted on GitHub: a new comment is posted
    replacement = await client.upsert_comment(1, REPO, 3, "third", first)
    assert replacement != first
    github.fail["PATCH /repos/acme/shop/issues/comments"] = 500
    with pytest.raises(GitHubError, match="500"):
        await client.upsert_comment(1, REPO, 3, "fourth", replacement)

    number, url = await client.create_issue(1, REPO, title="t", body="b", labels=["testmind"])
    assert (number, url) == (1, "https://github.test/acme/shop/issues/1")
    assert github.issues[0]["labels"] == ["testmind"]


async def test_unusable_answers(private_key: str) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(201, json={"token": "t", "expires_at": "tomorrow"})
        return httpx.Response(200, text="<html>")

    client = GitHubClient(
        AppCredentials(APP_ID, private_key), transport=httpx.MockTransport(answer)
    )
    with pytest.raises(GitHubError, match="invalid expiry"):
        await client.installation_token(1)
    client._tokens[1] = ("t", time.time() + 3600)
    with pytest.raises(GitHubError, match="not JSON"):
        await client.pull_request(1, REPO, 1)

    def listing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2])

    listing_client = GitHubClient(
        AppCredentials(APP_ID, private_key), transport=httpx.MockTransport(listing)
    )
    listing_client._tokens[1] = ("t", time.time() + 3600)
    with pytest.raises(GitHubError, match="unexpected document"):
        await listing_client.pull_request(1, REPO, 1)

    def shapes(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls/1"):
            return httpx.Response(200, json={"title": "t", "html_url": "u", "head": "x"})
        if path.endswith("/check-runs"):
            return httpx.Response(201, json={"id": True})
        return httpx.Response(500, text="plain failure")

    shaped = GitHubClient(
        AppCredentials(APP_ID, private_key), transport=httpx.MockTransport(shapes)
    )
    shaped._tokens[1] = ("t", time.time() + 3600)
    with pytest.raises(GitHubError, match="no 'head' object"):
        await shaped.pull_request(1, REPO, 1)
    with pytest.raises(GitHubError, match="no 'id'"):
        await shaped.create_check_run(1, REPO, "a" * 40, "x")
    with pytest.raises(GitHubError, match="GitHub answered 500: plain failure"):
        await shaped.commit_sha(1, REPO, "main")
    for c in (client, listing_client, shaped):
        await c.close()


async def test_network_failures(private_key: str, tmp_path: Path) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = GitHubClient(AppCredentials(APP_ID, private_key), transport=httpx.MockTransport(down))
    with pytest.raises(GitHubError, match="could not be reached: ConnectError") as caught:
        await client.installation_token(1)
    assert caught.value.status == 0
    client._tokens[1] = ("t", time.time() + 3600)
    with pytest.raises(GitHubError, match="could not be reached"):
        await client.download(1, REPO, "a" * 40, tmp_path)
    await client.close()


async def test_a_token_answer_without_a_token(private_key: str) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"expires_at": "2030-01-01T00:00:00Z"})

    client = GitHubClient(
        AppCredentials(APP_ID, private_key), transport=httpx.MockTransport(answer)
    )
    with pytest.raises(GitHubError, match="has no 'token'"):
        await client.installation_token(1)
    await client.close()
