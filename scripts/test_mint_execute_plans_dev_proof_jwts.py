import base64
import hashlib
import hmac
import json
from pathlib import Path

from mint_execute_plans_dev_proof_jwts import TTL_SECONDS, build_bundle, write_bundle


def _decode(segment: str) -> dict[str, object]:
    segment += "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment))


def test_bundle_has_distinct_mfa_verified_tenant_scoped_one_hour_hs256_tokens() -> None:
    secret = "unit-test-secret"
    tokens, metadata = build_bundle(
        secret=secret,
        issuer="pantheon-dev",
        audience="bff-operators",
        run_id="123-1",
        now=1_700_000_000,
    )

    assert set(tokens) == {"operator_a", "operator_b", "viewer"}
    assert set(metadata["rbac"]) == {"operator", "viewer"}
    assert len(tokens) == len(set(tokens.values()))
    subjects = set()
    for label, token in tokens.items():
        header_segment, claims_segment, signature_segment = token.split(".")
        assert _decode(header_segment) == {"alg": "HS256", "typ": "JWT"}
        claims = _decode(claims_segment)
        expected_roles = ["operator"] if label.startswith("operator_") else ["viewer"]
        assert claims["roles"] == expected_roles
        assert claims["user_id"] == claims["sub"]
        assert claims["sid"].startswith("pantheon-dev-proof-")
        assert claims["app_metadata"] == {"tenant_id": "tenant-dev"}
        assert claims["tenant_id"] == "tenant-dev"
        assert claims["allowed_tenants"] == ["tenant-dev"]
        assert claims["exp"] - claims["iat"] == TTL_SECONDS == 3600
        assert claims["nbf"] == claims["iat"] - 30
        assert claims["iss"] == "pantheon-dev"
        assert claims["aud"] == "bff-operators"
        assert claims["mfa_verified"] is True
        subjects.add(claims["sub"])
        padded_signature = signature_segment + "=" * (-len(signature_segment) % 4)
        actual_signature = base64.urlsafe_b64decode(padded_signature)
        expected_signature = hmac.new(
            secret.encode(), f"{header_segment}.{claims_segment}".encode(), hashlib.sha256
        ).digest()
        assert hmac.compare_digest(actual_signature, expected_signature)
    assert len(subjects) == len(tokens)


def test_write_bundle_keeps_tokens_out_of_manifest(tmp_path: Path) -> None:
    tokens, metadata = build_bundle(
        secret="unit-test-secret",
        issuer="pantheon-dev",
        audience="bff-operators",
        run_id="123-1",
        now=1_700_000_000,
    )
    output_dir = tmp_path / "credentials"
    write_bundle(output_dir, tokens, metadata)

    manifest = (output_dir / "manifest.json").read_text()
    assert all(token not in manifest for token in tokens.values())
    assert json.loads((output_dir / "rbac.json").read_text()) == metadata["rbac"]
    assert (output_dir.stat().st_mode & 0o777) == 0o700
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in output_dir.iterdir())


def test_workflow_is_dev_only_and_validates_before_secret_updates() -> None:
    workflow = Path(".github/workflows/rotate-execute-plans-dev-proof-jwts.yml").read_text()
    assert "github.ref == 'refs/heads/dev'" in workflow
    assert "environment: dev" in workflow
    assert "push:" in workflow
    assert "- dev" in workflow
    assert "- .github/workflows/rotate-execute-plans-dev-proof-jwts.yml" in workflow
    assert "- scripts/mint_execute_plans_dev_proof_jwts.py" in workflow
    assert "- scripts/test_mint_execute_plans_dev_proof_jwts.py" in workflow
    assert "staging" not in workflow.lower()
    assert "production" not in workflow.lower()
    validate_at = workflow.index('"${DEV_BFF_URL%/}/bff/me"')
    for secret_name in (
        "PANTHEON_BFF_OPERATOR_A_TOKEN",
        "PANTHEON_BFF_OPERATOR_B_TOKEN",
        "PANTHEON_BFF_VIEWER_TOKEN",
        "PANTHEON_BFF_RBAC_TOKENS_JSON",
    ):
        assert workflow.index(f"gh secret set {secret_name}") > validate_at
    assert "--body" not in workflow
    assert "COORDINATION_REPO_TOKEN" in workflow
    assert "--header 'X-Tenant-Id: tenant-dev'" in workflow
    assert "--arg tenant 'tenant-dev'" in workflow
    assert "X-Tenant-Id: pantheon-dev" not in workflow
