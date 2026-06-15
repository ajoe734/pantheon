"""Security regression: JWT attack-matrix coverage for validate_request_auth.

The existing suite (services/runtime-manager/test_runtime_hardening.py) covers
expired / bad-signature / missing-bearer / MFA. This file locks the cases it
does not: algorithm-confusion (alg:none), issuer/audience enforcement, and
strict-mode fail-closed on a missing secret.

Verification campaign 2026-06-14, round 12. F10 (no-exp acceptance) is recorded
as a hardening item in round-12-results.md, not enforced here, because
service-to-service tokens minted elsewhere may legitimately omit exp.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import runtime_auth_inbound as A  # noqa: E402

SECRET = "campaign-r12-secret"
ISSUER = "pantheon"
AUDIENCE = "bff"
ENV = {
    "PANTHEON_RUNTIME_AUTH_MODE": "strict",
    "PANTHEON_RUNTIME_JWT_SECRET": SECRET,
    "PANTHEON_RUNTIME_JWT_ISSUER": ISSUER,
    "PANTHEON_RUNTIME_JWT_AUDIENCE": AUDIENCE,
}


def _claims(**over):
    base = {
        "sub": "u1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": time.time() + 3600,
        "roles": ["operator"],
    }
    base.update(over)
    return base


def _v(token, env=ENV):
    return A.validate_request_auth(authorization="Bearer " + token, env=env)


def test_valid_token_accepted():
    ctx = _v(A.encode_jwt_hs256(_claims(), secret=SECRET))
    assert ctx.actor_id == "u1"
    assert "operator" in ctx.roles


def test_alg_none_forgery_rejected():
    b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    forged = f"{b({'alg':'none','typ':'JWT'})}.{b(_claims())}."
    with pytest.raises(A.AuthError):
        _v(forged)


def test_wrong_issuer_rejected():
    with pytest.raises(A.AuthError) as e:
        _v(A.encode_jwt_hs256(_claims(iss="evil"), secret=SECRET))
    assert e.value.code == "AUTH_JWT_ISSUER_MISMATCH"


def test_wrong_audience_rejected():
    with pytest.raises(A.AuthError) as e:
        _v(A.encode_jwt_hs256(_claims(aud="evil"), secret=SECRET))
    assert e.value.code == "AUTH_JWT_AUDIENCE_MISMATCH"


def test_strict_mode_missing_secret_fails_closed():
    env = {**ENV, "PANTHEON_RUNTIME_JWT_SECRET": ""}
    with pytest.raises(A.AuthError) as e:
        _v(A.encode_jwt_hs256(_claims(), secret=SECRET), env=env)
    # Fail-closed: refuse rather than accept unverifiable tokens.
    assert e.value.status_code in (401, 500)
    assert e.value.code in ("AUTH_JWT_SECRET_MISSING", "AUTH_JWT_UNVERIFIED")


def test_tampered_signature_rejected():
    t = A.encode_jwt_hs256(_claims(), secret=SECRET)
    h, p, s = t.split(".")
    with pytest.raises(A.AuthError):
        _v(f"{h}.{p}.{s[:-2]}AA")
