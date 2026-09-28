"""TestMind's seccomp profile (src/testmind/sandbox/seccomp.json): Docker's default without
the syscalls testmind.sandbox.seccomp removes. Its effect in a real container is
checked in tests/sandbox/test_docker_sandbox.py."""

import json

from testmind.sandbox.docker_sandbox import SECCOMP_PROFILE
from testmind.sandbox.seccomp import DENIED, derive

PROFILE = json.loads(SECCOMP_PROFILE.read_text())


def allowed(profile: dict) -> set[str]:  # type: ignore[type-arg]
    return {n for r in profile["syscalls"] if r["action"] == "SCMP_ACT_ALLOW" for n in r["names"]}


def test_the_profile_denies_by_default_and_never_allows_the_removed_syscalls() -> None:
    assert PROFILE["defaultAction"] == "SCMP_ACT_ERRNO"
    assert not allowed(PROFILE) & DENIED
    # What Python, pytest, coverage and Chromium need is still there.
    assert {"read", "write", "openat", "clone", "execve", "mmap", "futex", "memfd_create",
            "epoll_wait", "socket"} <= allowed(PROFILE)  # fmt: skip


def test_derivation_only_removes_the_denied_names() -> None:
    upstream = {
        "defaultAction": "SCMP_ACT_ERRNO",
        "syscalls": [
            {"names": ["read", "ptrace"], "action": "SCMP_ACT_ALLOW"},
            {"names": ["process_vm_readv"], "action": "SCMP_ACT_ALLOW",
             "includes": {"minKernel": "4.8"}},
            {"names": ["clone3"], "action": "SCMP_ACT_ERRNO", "errnoRet": 38},
        ],
    }  # fmt: skip
    assert derive(upstream) == {
        "defaultAction": "SCMP_ACT_ERRNO",
        "syscalls": [
            {"names": ["read"], "action": "SCMP_ACT_ALLOW"},
            {"names": ["clone3"], "action": "SCMP_ACT_ERRNO", "errnoRet": 38},
        ],
    }
    assert derive(PROFILE) == PROFILE  # the committed profile is already derived
