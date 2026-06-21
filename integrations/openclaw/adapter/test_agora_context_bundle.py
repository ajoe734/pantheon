"""Tests for AgoraContextBundle redaction and central persona privacy boundary.

Covers:
  - successful bundle construction with allowed fields only
  - privacy manifest flags (raw_prompt_included=False, user_identity_included=False)
  - rejection of forbidden private fields via extra_fields
  - ContextBundlePrivacyError carries RAW_PRIVATE_CONTENT_FORBIDDEN code
  - golden eval: privacy case — no raw_prompt/user_identity in any bundle
"""
from __future__ import annotations

import pytest

from integrations.openclaw.adapter.agora_context_bundle import (
    AgoraContextBundle,
    ContextBundlePrivacyError,
    ContextBundlePrivacyManifest,
    RAW_PRIVATE_CONTENT_FORBIDDEN,
    build_context_bundle,
)


_VALID_KWARGS = dict(
    strategy_spec_draft_ref="spec-ref-001",
    question="Is this momentum strategy overfit to 2022?",
    symbols=["TSLA", "NVDA"],
    evidence_refs=["ev-001", "ev-002"],
    data_cutoff="2026-06-01T00:00:00Z",
    required_output_schema="agora-expert-consult-output-v1",
)


# ------------------------------------------------------------------ #
# Golden eval 1: successful build with full allowed fields
# ------------------------------------------------------------------ #

def test_build_returns_context_bundle():
    bundle = build_context_bundle(**_VALID_KWARGS)
    assert isinstance(bundle, AgoraContextBundle)
    assert bundle.strategy_spec_draft_ref == "spec-ref-001"
    assert bundle.question == "Is this momentum strategy overfit to 2022?"
    assert bundle.symbols == ["TSLA", "NVDA"]
    assert bundle.evidence_refs == ["ev-001", "ev-002"]
    assert bundle.data_cutoff == "2026-06-01T00:00:00Z"
    assert bundle.required_output_schema == "agora-expert-consult-output-v1"


# ------------------------------------------------------------------ #
# Golden eval 2: privacy manifest is always clean
# ------------------------------------------------------------------ #

def test_privacy_manifest_flags_always_false():
    bundle = build_context_bundle(**_VALID_KWARGS)
    assert bundle.privacy_manifest.raw_prompt_included is False
    assert bundle.privacy_manifest.user_identity_included is False


def test_privacy_manifest_fields_shared_reflects_provided_fields():
    bundle = build_context_bundle(**_VALID_KWARGS)
    shared = bundle.privacy_manifest.fields_shared
    assert "strategy_spec_draft_ref" in shared
    assert "question" in shared
    assert "data_cutoff" in shared
    assert "symbols" in shared
    assert "evidence_refs" in shared
    assert "required_output_schema" in shared


def test_optional_fields_omitted_when_absent():
    bundle = build_context_bundle(
        strategy_spec_draft_ref="spec-ref-002",
        question="What is the drawdown risk?",
        data_cutoff="2026-06-01T00:00:00Z",
    )
    assert bundle.symbols == []
    assert bundle.evidence_refs == []
    assert bundle.required_output_schema == ""
    shared = bundle.privacy_manifest.fields_shared
    assert "symbols" not in shared
    assert "evidence_refs" not in shared
    assert "required_output_schema" not in shared


# ------------------------------------------------------------------ #
# Golden eval 3: forbidden field rejection → RAW_PRIVATE_CONTENT_FORBIDDEN
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("forbidden_field", [
    "raw_prompt",
    "user_identity",
    "user_id",
    "user_email",
    "user_name",
    "private_journal",
    "journal_entries",
    "session_history",
    "conversation_history",
    "capital_binding",
    "pii",
])
def test_forbidden_field_raises_privacy_error(forbidden_field: str):
    with pytest.raises(ContextBundlePrivacyError) as exc_info:
        build_context_bundle(
            **_VALID_KWARGS,
            extra_fields={forbidden_field: "some private value"},
        )
    err = exc_info.value
    assert err.code == RAW_PRIVATE_CONTENT_FORBIDDEN
    assert forbidden_field in err.violated_fields


def test_multiple_forbidden_fields_all_reported():
    with pytest.raises(ContextBundlePrivacyError) as exc_info:
        build_context_bundle(
            **_VALID_KWARGS,
            extra_fields={"raw_prompt": "text", "user_identity": "u-001"},
        )
    err = exc_info.value
    assert set(err.violated_fields) == {"raw_prompt", "user_identity"}


def test_safe_extra_fields_do_not_raise():
    bundle = build_context_bundle(
        **_VALID_KWARGS,
        extra_fields={"mode": "committee", "required_expertise": ["risk", "legal"]},
    )
    assert isinstance(bundle, AgoraContextBundle)


def test_privacy_error_to_dict():
    try:
        build_context_bundle(
            **_VALID_KWARGS,
            extra_fields={"raw_prompt": "leaked text"},
        )
    except ContextBundlePrivacyError as exc:
        d = exc.to_dict()
        assert d["code"] == RAW_PRIVATE_CONTENT_FORBIDDEN
        assert "raw_prompt" in d["violated_fields"]
        assert "message" in d


# ------------------------------------------------------------------ #
# Explicit user authorization: manifest records it, still no raw/PII
# ------------------------------------------------------------------ #

def test_explicit_user_authorization_recorded_in_manifest():
    bundle = build_context_bundle(**_VALID_KWARGS, explicit_user_authorization=True)
    assert bundle.privacy_manifest.explicit_user_authorization is True
    assert bundle.privacy_manifest.raw_prompt_included is False
    assert bundle.privacy_manifest.user_identity_included is False


# ------------------------------------------------------------------ #
# AgoraContextBundle model: required fields validated
# ------------------------------------------------------------------ #

def test_missing_required_field_raises():
    with pytest.raises(Exception):
        AgoraContextBundle(
            strategy_spec_draft_ref="",  # too short
            question="Q?",
            data_cutoff="2026-06-01T00:00:00Z",
        )


def test_privacy_manifest_literal_fields_immutable():
    manifest = ContextBundlePrivacyManifest()
    assert manifest.raw_prompt_included is False
    assert manifest.user_identity_included is False
