from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    REPO_ROOT
    / ".github"
    / "workflows"
    / "ppl-alloc-009-hosted-acceptance.yml"
)
PUBLIC_SUPABASE_URL = "https://kwjtcynauaulrxngyetk.supabase.co"


def test_hosted_acceptance_preserves_browser_session_and_gcp_identity_contract() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    # The immutable execute-plans harness uses this public URL only to derive
    # the hosted browser session-storage key. It is not a credential.
    assert (
        f'PPL_ALLOC_009_PUBLIC_SUPABASE_URL: "{PUBLIC_SUPABASE_URL}"'
        in workflow
    )
    assert (
        '"${PPL_ALLOC_009_PUBLIC_SUPABASE_URL}" '
        f'== "{PUBLIC_SUPABASE_URL}"'
        in workflow
    )

    # GCP Identity Platform remains the protected browser identity gate added
    # by GCP-AUTH-MIGRATION-001; restoring the session-key input must not
    # weaken or replace it.
    assert (
        "PPL_ALLOC_009_GCP_IDENTITY_API_KEY: "
        "${{ vars.VITE_GCP_IDENTITY_API_KEY }}"
        in workflow
    )
    assert (
        '"${PPL_ALLOC_009_GCP_IDENTITY_API_KEY}" '
        "=~ ^AIza[A-Za-z0-9_-]{35}$"
        in workflow
    )

    # Keep the public session-key URL fixed by the trusted controller rather
    # than reintroducing an operator-supplied dispatch input.
    assert "      public_supabase_url:" not in workflow
