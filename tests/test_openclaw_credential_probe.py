"""OPGAP-GATE-HARDENING-20260901 — a health check must exercise the guarded path.

The predecessor of this probe ran `docker exec <gateway> claude -p ...`, which
inherits CLAUDE_CODE_OAUTH_TOKEN. OpenClaw strips that variable before launching
managed Claude CLI runs, so its runs authenticate from the on-disk credential
instead. When that credential expired, every OpenClaw-managed run failed for two
weeks while the keepalive logged OK on every tick — it was verifying a different
credential path than the one it was supposed to guard.

These assertions are static so they run in the repo-root suite CI executes; the
probe itself needs Docker and a live gateway.
"""
from __future__ import annotations

import re
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "scripts" / "openclaw_credential_probe.sh"


def _script() -> str:
    return PROBE.read_text(encoding="utf-8")


def _authoritative_section() -> str:
    """Everything after the advisory warm-up block."""
    script = _script()
    marker = "# Authoritative:"
    assert marker in script, "probe must mark which check decides the outcome"
    return script[script.index(marker) :]


def test_probe_exists_and_is_executable() -> None:
    assert PROBE.exists()
    assert PROBE.stat().st_mode & stat.S_IXUSR, f"{PROBE} must be executable"


def test_authoritative_check_runs_the_adapter_readiness_path() -> None:
    """The guarded path is the adapter's readiness, which is what the deploy gate
    consumes, so that is what must decide."""
    section = _authoritative_section()
    assert "AssistantOpenClawProvider" in section, (
        "the deciding check must run the adapter's own provider code"
    )
    assert "readiness(auth_probe=True)" in section, (
        "the deciding check must exercise the full answer probe, not a cheap ping"
    )


def test_authoritative_check_is_not_a_bare_openclaw_agent_call() -> None:
    """A bare `openclaw agent` call is easier to satisfy than the guarded path.

    Unpinned, the gateway answers from its own fallback chain (observed: it
    replied via openai/gpt-5.6-sol while the Claude credential was expired),
    whereas the adapter pins each candidate model with its own per-candidate
    budget. A probe greener than what it guards is the defect this file prevents.
    """
    code = "\n".join(
        line
        for line in _authoritative_section().splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "openclaw agent" not in code, (
        "substituting a bare `openclaw agent` call makes the probe pass in "
        "conditions where the adapter's readiness — and therefore the gate — fails"
    )


def test_authoritative_failure_exits_nonzero() -> None:
    section = _authoritative_section()
    assert re.search(r"\bexit 1\b", section), (
        "a failed OpenClaw-managed run must fail the probe, not be logged and ignored"
    )
    assert 'info.get("ready") is True' in section, (
        "the probe must require ready=true, not merely that the call completed"
    )


def test_raw_cli_warmup_cannot_decide_the_outcome() -> None:
    """The warm-up is allowed, but must never be the success criterion."""
    script = _script()
    warmup = script[: script.index("# Authoritative:")]
    assert "claude -p" in warmup, "warm-up expected in the advisory section"
    assert "advisory" in warmup.lower(), (
        "the raw-CLI warm-up must be explicitly marked non-authoritative"
    )
    assert not re.search(r"\bexit 0\b", warmup), (
        "the advisory warm-up must not short-circuit the probe to success"
    )


def test_probe_does_not_depend_on_inherited_oauth_token() -> None:
    """Relying on CLAUDE_CODE_OAUTH_TOKEN reintroduces the exact divergence:
    OpenClaw strips it, so a probe that needs it is testing something else."""
    section = _authoritative_section()
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in section, (
        "the authoritative check must not pass credentials OpenClaw strips"
    )
