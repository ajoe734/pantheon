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


def test_permissive_plain_bearer_fails_closed():
    env = {"PANTHEON_RUNTIME_AUTH_MODE": "permissive", "PANTHEON_RUNTIME_JWT_SECRET": ""}
    with pytest.raises(A.AuthError) as e:
        A.validate_request_auth(authorization="Bearer definitely-invalid-no-role-token", env=env)
    assert e.value.status_code == 403
    assert e.value.code == "AUTH_TOKEN_FORMAT"


def test_tampered_signature_rejected():
    t = A.encode_jwt_hs256(_claims(), secret=SECRET)
    h, p, s = t.split(".")
    with pytest.raises(A.AuthError):
        _v(f"{h}.{p}.{s[:-2]}AA")


@pytest.mark.parametrize("bad_mode", ["strcit", "", "disabled", "strict-ish", "PERMISIVE"])
def test_malformed_auth_mode_fails_closed_to_strict(bad_mode):
    # A typo'd/unrecognized PANTHEON_RUNTIME_AUTH_MODE must not silently fall
    # through to permissive structured-token parsing.
    env = {"PANTHEON_RUNTIME_AUTH_MODE": bad_mode, "PANTHEON_RUNTIME_JWT_SECRET": ""}
    with pytest.raises(A.AuthError) as e:
        A.validate_request_auth(authorization="Bearer plain-actor:operator", env=env)
    assert e.value.code == "AUTH_TOKEN_FORMAT"
    assert e.value.status_code == 401


def test_jwks_configured_shared_default_rejects_hs256():
    """Default shared validator (allow_shared_secret=False) remains JWKS-exclusive."""
    env = {**ENV, "PANTHEON_RUNTIME_JWKS_URI": "https://auth.example.com/jwks.json"}
    token = A.encode_jwt_hs256(_claims(), secret=SECRET)
    with pytest.raises(A.AuthError) as exc_info:
        A.validate_request_auth(authorization="Bearer " + token, env=env)
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "AUTH_JWT_ALG_UNSUPPORTED"


def test_jwks_configured_with_allow_shared_secret_accepts_valid_hs256():
    """When allow_shared_secret=True, valid HS256 token is accepted even if JWKS is configured."""
    env = {**ENV, "PANTHEON_RUNTIME_JWKS_URI": "https://auth.example.com/jwks.json"}
    token = A.encode_jwt_hs256(_claims(), secret=SECRET)
    ctx = A.validate_request_auth(
        authorization="Bearer " + token,
        env=env,
        allow_shared_secret=True,
    )
    assert ctx.actor_id == "u1"
    assert "operator" in ctx.roles
    assert ctx.token_kind == "jwt"


@pytest.mark.parametrize(
    "tamper_field,tamper_val,expected_code,expected_status",
    [
        ("bad_sig", True, "AUTH_JWT_BAD_SIGNATURE", 401),
        ("exp", time.time() - 3600, "AUTH_JWT_EXPIRED", 401),
        ("iss", "untrusted-issuer", "AUTH_JWT_ISSUER_MISMATCH", 401),
        ("aud", "untrusted-audience", "AUTH_JWT_AUDIENCE_MISMATCH", 401),
    ],
)
def test_jwks_configured_with_allow_shared_secret_rejects_invalid_claims(
    tamper_field, tamper_val, expected_code, expected_status
):
    env = {**ENV, "PANTHEON_RUNTIME_JWKS_URI": "https://auth.example.com/jwks.json"}
    over = {}
    if tamper_field != "bad_sig":
        over[tamper_field] = tamper_val
    token = A.encode_jwt_hs256(_claims(**over), secret=SECRET)
    if tamper_field == "bad_sig":
        h, p, s = token.split(".")
        token = f"{h}.{p}.{s[:-2]}AA"
    with pytest.raises(A.AuthError) as exc_info:
        A.validate_request_auth(
            authorization="Bearer " + token,
            env=env,
            allow_shared_secret=True,
        )
    assert exc_info.value.code == expected_code
    assert exc_info.value.status_code == expected_status


def test_jwks_configured_with_allow_shared_secret_rejects_missing_secret():
    env = {
        **ENV,
        "PANTHEON_RUNTIME_JWKS_URI": "https://auth.example.com/jwks.json",
        "PANTHEON_RUNTIME_JWT_SECRET": "",
    }
    token = A.encode_jwt_hs256(_claims(), secret=SECRET)
    with pytest.raises(A.AuthError) as exc_info:
        A.validate_request_auth(
            authorization="Bearer " + token,
            env=env,
            allow_shared_secret=True,
        )
    assert exc_info.value.code == "AUTH_JWT_SECRET_MISSING"
    assert exc_info.value.status_code == 500


def test_jwks_configured_with_allow_shared_secret_rejects_none_and_unknown_alg():
    env = {**ENV, "PANTHEON_RUNTIME_JWKS_URI": "https://auth.example.com/jwks.json"}
    b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    forged_none = f"{b({'alg':'none','typ':'JWT'})}.{b(_claims())}.e30"
    with pytest.raises(A.AuthError) as exc_none:
        A.validate_request_auth(
            authorization="Bearer " + forged_none,
            env=env,
            allow_shared_secret=True,
        )
    assert exc_none.value.code == "AUTH_JWT_ALG_UNSUPPORTED"

    forged_unknown = f"{b({'alg':'HS384','typ':'JWT'})}.{b(_claims())}.dGVzdA"
    with pytest.raises(A.AuthError) as exc_unknown:
        A.validate_request_auth(
            authorization="Bearer " + forged_unknown,
            env=env,
            allow_shared_secret=True,
        )
    assert exc_unknown.value.code == "AUTH_JWT_ALG_UNSUPPORTED"


def test_jwks_configured_with_allow_shared_secret_enforces_roles_and_mfa():
    env = {
        **ENV,
        "PANTHEON_RUNTIME_JWKS_URI": "https://auth.example.com/jwks.json",
        "PANTHEON_RUNTIME_MFA_REQUIRED": "true",
    }
    token_viewer = A.encode_jwt_hs256(_claims(roles=["viewer"]), secret=SECRET)
    with pytest.raises(A.AuthError) as exc_role:
        A.validate_request_auth(
            authorization="Bearer " + token_viewer,
            required_roles=["operator", "admin"],
            env=env,
            allow_shared_secret=True,
        )
    assert exc_role.value.code == "AUTH_FORBIDDEN"
    assert exc_role.value.status_code == 403

    token_no_mfa = A.encode_jwt_hs256(_claims(), secret=SECRET)
    with pytest.raises(A.AuthError) as exc_mfa:
        A.validate_request_auth(
            authorization="Bearer " + token_no_mfa,
            required_roles=["operator"],
            mfa_required=True,
            env=env,
            allow_shared_secret=True,
        )
    assert exc_mfa.value.code == "MFA_REQUIRED"
    assert exc_mfa.value.status_code == 401


def test_jwks_configured_routes_rs256_to_jwks_even_with_allow_shared_secret():
    from unittest.mock import patch
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private_key = rsa.generate_private_key(65537, 2048)
    public_numbers = private_key.public_key().public_numbers()
    byte_len_n = (public_numbers.n.bit_length() + 7) // 8
    byte_len_e = (public_numbers.e.bit_length() + 7) // 8
    jwk = {
        "kty": "RSA",
        "kid": "paper-rsa-kid",
        "use": "sig",
        "alg": "RS256",
        "n": base64.urlsafe_b64encode(public_numbers.n.to_bytes(byte_len_n, "big")).rstrip(b"=").decode(),
        "e": base64.urlsafe_b64encode(public_numbers.e.to_bytes(byte_len_e, "big")).rstrip(b"=").decode(),
    }
    header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT", "kid": "paper-rsa-kid"}).encode()).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(json.dumps(_claims(sub="paper-operator")).encode()).rstrip(b"=").decode()
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    rs_token = f"{header_b64}.{payload_b64}.{sig_b64}"

    env = {**ENV, "PANTHEON_RUNTIME_JWKS_URI": "https://auth.example.com/jwks.json"}
    with patch.object(A, "_fetch_jwks_keys", return_value=[jwk]):
        ctx = A.validate_request_auth(
            authorization="Bearer " + rs_token,
            env=env,
            allow_shared_secret=True,
        )
    assert ctx.actor_id == "paper-operator"
    assert "operator" in ctx.roles

