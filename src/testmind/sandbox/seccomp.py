"""How seccomp.json is derived from Docker's default seccomp profile.

Sandbox containers drop every capability, so Docker's default profile already refuses the
syscalls it only allows with a capability (bpf, unshare, setns, mount, perf_event_open, …).
TestMind's profile also removes what Docker allows without one and a test sandbox never
needs: reading or writing other processes (ptrace, process_vm_readv, process_vm_writev) and
file handles (name_to_handle_at, half of the "shocker" container escape). Everything else is
unchanged. scripts/make_seccomp_profile.py regenerates the file.
"""

from __future__ import annotations

from typing import Any

DENIED = frozenset({"ptrace", "process_vm_readv", "process_vm_writev", "name_to_handle_at"})


def derive(profile: dict[str, Any]) -> dict[str, Any]:
    """The profile without the DENIED syscalls in its allow rules."""
    rules = []
    for rule in profile["syscalls"]:
        if rule["action"] != "SCMP_ACT_ALLOW":
            rules.append(rule)
            continue
        names = [n for n in rule["names"] if n not in DENIED]
        if names:
            rules.append({**rule, "names": names})
    return {**profile, "syscalls": rules}
