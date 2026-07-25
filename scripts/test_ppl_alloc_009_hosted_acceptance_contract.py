from pathlib import Path

import pytest

from scripts.resolve_ppl_alloc_009_acceptance_harness import resolve_harness_sha


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    REPO_ROOT
    / ".github"
    / "workflows"
    / "ppl-alloc-009-hosted-acceptance.yml"
)
PUBLIC_SUPABASE_URL = "https://kwjtcynauaulrxngyetk.supabase.co"
EXPECTED_FRONTEND_SHA = "ef5185148157c422b41cc2a0ee497d2860e002a3"
STALE_HARNESS_SHA = "7492ad7fd0b430df40dd7fe7b6b0d187d8742350"


def test_empty_harness_ref_resolves_to_exact_accepted_frontend() -> None:
    assert (
        resolve_harness_sha(
            expected_frontend_sha=EXPECTED_FRONTEND_SHA,
            requested_test_sha="",
        )
        == EXPECTED_FRONTEND_SHA
    )


def test_exact_frontend_harness_ref_is_accepted() -> None:
    assert (
        resolve_harness_sha(
            expected_frontend_sha=EXPECTED_FRONTEND_SHA,
            requested_test_sha=EXPECTED_FRONTEND_SHA,
        )
        == EXPECTED_FRONTEND_SHA
    )


@pytest.mark.parametrize("requested", [STALE_HARNESS_SHA, "dev", "main"])
def test_stale_or_mutable_harness_ref_is_rejected(requested: str) -> None:
    with pytest.raises(ValueError):
        resolve_harness_sha(
            expected_frontend_sha=EXPECTED_FRONTEND_SHA,
            requested_test_sha=requested,
        )


def test_workflow_resolves_full_sha_before_harness_checkout_or_credentials() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert STALE_HARNESS_SHA not in workflow
    assert 'default: ""' in workflow
    assert "required: false" in workflow
    resolve_step = workflow.index("Resolve exact accepted frontend harness")
    identity_step = workflow.index("Load and mask the browser identity key")
    checkout_step = workflow.index(
        "Checkout immutable execute-plans acceptance harness"
    )
    assert resolve_step < identity_step < checkout_step
    assert "ref: ${{ steps.harness.outputs.test_sha }}" in workflow
    assert (
        '[[ "${PPL_ALLOC_009_TEST_SHA}" '
        '== "${PPL_ALLOC_009_EXPECTED_FE_SHA}" ]]'
        in workflow
    )
    assert "PPL_ALLOC_009_TEST_SHA: ${{ inputs.execute_plans_test_sha }}" not in workflow


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
