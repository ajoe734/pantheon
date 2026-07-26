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
EXPECTED_FRONTEND_SHA = "6a8d2d9b4f725056735eefd7165ef47b52cda53d"
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
    identity_step = workflow.index("Load and mask real browser identity inputs")
    checkout_step = workflow.index(
        "Checkout immutable execute-plans acceptance harness"
    )
    assert resolve_step < identity_step < checkout_step
    assert "ref: ${{ steps.harness.outputs.test_sha }}" in workflow
    assert (
        '[[ "${PPL_ALLOC_009_BROWSER_DIAGNOSTIC_ONLY}" == "true" || '
        '"${PPL_ALLOC_009_TEST_SHA}" == "${PPL_ALLOC_009_EXPECTED_FE_SHA}" ]]'
        in workflow
    )
    assert 'if [[ "${BROWSER_DIAGNOSTIC_ONLY}" == "true" ]]; then' in workflow
    assert '[[ "${REQUESTED_TEST_SHA}" =~ ^[0-9a-f]{40}$ ]]' in workflow
    assert "PPL_ALLOC_009_TEST_SHA: ${{ inputs.execute_plans_test_sha }}" not in workflow


def test_read_only_browser_diagnostic_is_explicitly_bounded() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "browser_diagnostic_only:" in workflow
    assert "diagnostic_persona_id:" in workflow
    assert "diagnostic_pool_id:" in workflow
    assert "diagnostic_rebalance_id:" in workflow
    assert (
        "PPL_ALLOC_009_BROWSER_DIAGNOSTIC_ONLY: "
        "${{ inputs.browser_diagnostic_only }}"
        in workflow
    )
    assert '[[ -n "${PPL_ALLOC_009_DIAGNOSTIC_PERSONA_ID}" ]]' in workflow
    assert '[[ -n "${PPL_ALLOC_009_DIAGNOSTIC_POOL_ID}" ]]' in workflow
    assert '[[ -n "${PPL_ALLOC_009_DIAGNOSTIC_REBALANCE_ID}" ]]' in workflow
    assert "permissions:\n  contents: read" in workflow


def test_hosted_acceptance_preserves_browser_session_and_gcp_identity_contract() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    # GCP Identity Platform remains the protected browser identity gate added
    # by GCP-AUTH-MIGRATION-001. The harness signs in through the hosted
    # Firebase UI with a dedicated real dev account; it must never construct a
    # synthetic Firebase session from a BFF dev-login token. Load all identity
    # inputs from auto-masked dev environment secrets, add explicit runner
    # masks before the generic non-empty guard, then pass them to later steps
    # through GITHUB_ENV.
    assert (
        "GCP_IDENTITY_API_KEY: "
        "${{ secrets.PPL_ALLOC_009_GCP_IDENTITY_API_KEY }}"
        in workflow
    )
    assert (
        "GCP_IDENTITY_EMAIL: "
        "${{ secrets.PPL_ALLOC_009_GCP_IDENTITY_EMAIL }}"
        in workflow
    )
    assert (
        "GCP_IDENTITY_PASSWORD: "
        "${{ secrets.PPL_ALLOC_009_GCP_IDENTITY_PASSWORD }}"
        in workflow
    )
    assert "${{ vars.VITE_GCP_IDENTITY_API_KEY }}" not in workflow
    masks = [
        'echo "::add-mask::${GCP_IDENTITY_API_KEY}"',
        'echo "::add-mask::${GCP_IDENTITY_EMAIL}"',
        'echo "::add-mask::${GCP_IDENTITY_PASSWORD}"',
    ]
    guard = (
        'if [[ -z "${GCP_IDENTITY_API_KEY}" || '
        '-z "${GCP_IDENTITY_EMAIL}" || '
        '-z "${GCP_IDENTITY_PASSWORD}" ]]; then'
    )
    exports = [
        'echo "PPL_ALLOC_009_GCP_IDENTITY_API_KEY='
        '${GCP_IDENTITY_API_KEY}" >> "${GITHUB_ENV}"',
        'echo "PPL_ALLOC_009_GCP_IDENTITY_EMAIL='
        '${GCP_IDENTITY_EMAIL}" >> "${GITHUB_ENV}"',
        'echo "PPL_ALLOC_009_GCP_IDENTITY_PASSWORD='
        '${GCP_IDENTITY_PASSWORD}" >> "${GITHUB_ENV}"',
    ]
    for mask in masks:
        assert mask in workflow
        assert workflow.index(mask) < workflow.index(guard)
    assert guard in workflow
    for export in exports:
        assert export in workflow
        assert workflow.index(guard) < workflow.index(export)
        assert (
            workflow.index(export)
            < workflow.index("Reject unsafe target or incomplete proof inputs")
        )
    assert '[[ -n "${PPL_ALLOC_009_GCP_IDENTITY_API_KEY}" ]]' in workflow
    assert '[[ -n "${PPL_ALLOC_009_GCP_IDENTITY_EMAIL}" ]]' in workflow
    assert '[[ -n "${PPL_ALLOC_009_GCP_IDENTITY_PASSWORD}" ]]' in workflow

    # Do not reintroduce legacy Supabase session-key inputs. GCP Identity owns
    # browser authentication for the exact-pair acceptance.
    assert "      public_supabase_url:" not in workflow
    assert "PPL_ALLOC_009_PUBLIC_SUPABASE_URL" not in workflow
