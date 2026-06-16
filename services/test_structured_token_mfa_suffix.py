"""Regression: _parse_structured_token must split the `actor_id:roles:mfa[:caps]`
stub shape correctly — the `:mfa` suffix must not be glued onto the last role.

Paper-loop activation 2026-06-16: the live dev BFF runs permissive mode with
PANTHEON_BFF_AUTH_STUB=false, so it resolves identity via
runtime_auth_inbound._parse_structured_token (not the BFF stub parser). That
parser split on only the first colon, so the canonical dev token
`op-dev:admin:mfa` resolved to a single role "admin:mfa" — not a real role —
and every read endpoint 403'd ("requires viewer-level role"), blanking the FE.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import runtime_auth_inbound as A


def _ctx(token):
    return A._parse_structured_token(token, default_role="operator")


def test_single_role_with_mfa_suffix_resolves_role_not_role_colon_mfa():
    ctx = _ctx("op-dev:admin:mfa")
    assert "admin" in ctx.roles
    assert "admin:mfa" not in ctx.roles
    assert all(":" not in r for r in ctx.roles)
    assert ctx.mfa_verified is True


def test_viewer_token_grants_read_role():
    ctx = _ctx("op-dev:viewer:mfa")
    assert "viewer" in ctx.roles
    assert ctx.mfa_verified is True


def test_multi_role_with_mfa_suffix():
    ctx = _ctx("op-dev:admin,viewer:mfa")
    assert {"admin", "viewer"} <= ctx.roles
    assert all(":" not in r for r in ctx.roles)
    assert ctx.mfa_verified is True


def test_legacy_two_part_token_still_works():
    ctx = _ctx("op-dev:admin")
    assert "admin" in ctx.roles
    assert ctx.mfa_verified is False


def test_plain_token_defaults_role():
    ctx = _ctx("internal-caller")
    assert "operator" in ctx.roles
    assert ctx.mfa_verified is False


def test_non_mfa_third_segment_does_not_pollute_roles():
    ctx = _ctx("op-dev:admin:something")
    assert ctx.roles == frozenset({"admin"})
    assert ctx.mfa_verified is False
