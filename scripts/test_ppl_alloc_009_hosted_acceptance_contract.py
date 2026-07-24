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
    # by GCP-AUTH-MIGRATION-001. The browser key is deliberately web-public,
    # but the acceptance workflow must not print it in the runner's job-level
    # environment. Load it from an auto-masked dev environment secret, add an
    # explicit runner mask before the generic non-empty guard, then pass it to
    # later steps through GITHUB_ENV.
    assert (
        "GCP_IDENTITY_API_KEY: "
        "${{ secrets.PPL_ALLOC_009_GCP_IDENTITY_API_KEY }}"
        in workflow
    )
    assert "${{ vars.VITE_GCP_IDENTITY_API_KEY }}" not in workflow
    mask = 'echo "::add-mask::${GCP_IDENTITY_API_KEY}"'
    guard = 'if [[ -z "${GCP_IDENTITY_API_KEY}" ]]; then'
    export = (
        'echo "PPL_ALLOC_009_GCP_IDENTITY_API_KEY='
        '${GCP_IDENTITY_API_KEY}" >> "${GITHUB_ENV}"'
    )
    assert mask in workflow
    assert guard in workflow
    assert export in workflow
    assert workflow.index(mask) < workflow.index(guard) < workflow.index(export)
    assert (
        workflow.index(export)
        < workflow.index("Reject unsafe target or incomplete proof inputs")
    )
    assert '[[ -n "${PPL_ALLOC_009_GCP_IDENTITY_API_KEY}" ]]' in workflow

    # Keep the public session-key URL fixed by the trusted controller rather
    # than reintroducing an operator-supplied dispatch input.
    assert "      public_supabase_url:" not in workflow
