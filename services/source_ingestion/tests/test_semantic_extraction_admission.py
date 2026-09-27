"""Tests for SemanticExtractionAdmission guard.

SIMPLIFY-EXTRACTION-CONTRACT-FOUNDATION-001:
Verifies that tenant, source status, license scope, point-in-time (as-of),
and sensitive content redaction checks are strictly enforced before any model call,
guaranteeing zero model turns on admission failure.
"""

from __future__ import annotations

import pytest

from services.source_ingestion.semantic_extraction import (
    AbstentionReason,
    AdmissionDecision,
    ExtractionFailureCode,
    ExtractionTaskType,
    SemanticExtractionAdmission,
    SemanticExtractionRequest,
)
from services.source_ingestion.semantic_extraction_client import (
    SemanticExtractionClient,
)


def _req(**kwargs) -> SemanticExtractionRequest:
    base = {
        "source_id": "src-admission-001",
        "text": "台股動能策略：突破20日均線時建立多頭部位，持有期3至5天，使用日K價量資料。",
        "task_type": ExtractionTaskType.COMPREHENSIVE,
        "tenant_id": "tenant_pantheon_alpha",
        "source_type": "internal_note",
        "source_status": "raw",
        "license_scope": "internal",
        "event_time": "2026-05-01T10:00:00Z",
        "as_of": "2026-06-01T00:00:00Z",
    }
    base.update(kwargs)
    return SemanticExtractionRequest(**base)


class TestSemanticExtractionAdmission:
    def test_clean_request_is_admitted(self):
        req = _req()
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is True
        assert decision.denial_code is None
        assert decision.denial_reason is None

    # --- Tenant Admission ---
    def test_missing_tenant_id_denied(self):
        req = _req(tenant_id="")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "TENANT_REQUIRED"

    def test_whitespace_tenant_id_denied(self):
        req = _req(tenant_id="   ")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "TENANT_REQUIRED"

    def test_invalid_characters_in_tenant_id_denied(self):
        for bad_id in ("tenant/bad", "tenant\\bad", "tenant bad", "tenant'bad", 'tenant"bad'):
            req = _req(tenant_id=bad_id)
            decision = SemanticExtractionAdmission.check(req)
            assert decision.admitted is False
            assert decision.denial_code == "INVALID_TENANT_ID"

    # --- Source Status Admission ---
    def test_rejected_source_status_denied(self):
        for bad_status in ("rejected", "quarantined", "prohibited"):
            req = _req(source_status=bad_status)
            decision = SemanticExtractionAdmission.check(req)
            assert decision.admitted is False
            assert decision.denial_code == "SOURCE_STATUS_REJECTED"

    def test_valid_source_status_admitted(self):
        for good_status in ("raw", "normalized", "indexed"):
            req = _req(source_status=good_status)
            decision = SemanticExtractionAdmission.check(req)
            assert decision.admitted is True

    # --- License Scope Admission ---
    def test_prohibited_license_scopes_denied(self):
        for bad_license in ("prohibited", "restricted_commercial", "expired", "none", "unauthorized", "unlicensed", ""):
            req = _req(license_scope=bad_license)
            decision = SemanticExtractionAdmission.check(req)
            assert decision.admitted is False
            assert decision.denial_code == "LICENSE_SCOPE_PROHIBITED"

    def test_permitted_license_scopes_admitted(self):
        for good_license in ("internal", "open", "vendor_research", "official_reference", "enterprise_research", "openalex"):
            req = _req(license_scope=good_license)
            decision = SemanticExtractionAdmission.check(req)
            assert decision.admitted is True

    # --- Point-in-Time (As-Of) Admission ---
    def test_point_in_time_event_before_or_on_as_of_admitted(self):
        req = _req(event_time="2026-05-01T00:00:00Z", as_of="2026-05-01T00:00:00Z")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is True

        req_past = _req(event_time="2026-04-30T23:59:59Z", as_of="2026-05-01T00:00:00Z")
        assert SemanticExtractionAdmission.check(req_past).admitted is True

    def test_point_in_time_lookahead_breach_denied(self):
        req = _req(event_time="2026-05-02T00:00:00Z", as_of="2026-05-01T00:00:00Z")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "POINT_IN_TIME_VIOLATION"
        assert "lookahead breach" in decision.denial_reason

    # --- Redaction / Sensitive Data Admission ---
    def test_pii_email_denied(self):
        req = _req(text="聯絡作者：researcher@quantfirm.com 獲取回測數據。")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "REDACTION_VIOLATION"
        assert any("pii:email" in f for f in decision.findings)

    def test_pii_phone_denied(self):
        req = _req(text="Call trading desk at +1-212-555-0199 for execution confirmation.")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "REDACTION_VIOLATION"
        assert any("pii:phone_us" in f for f in decision.findings)

    def test_credential_api_key_denied(self):
        req = _req(text="Set OPENAI_API_KEY=sk-proj12345678901234567890 for extraction.")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "REDACTION_VIOLATION"
        assert any("credential:api_key" in f for f in decision.findings)

    def test_credential_bearer_token_denied(self):
        req = _req(text="Header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDv-1")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "REDACTION_VIOLATION"
        assert any("credential:bearer" in f for f in decision.findings)

    def test_capital_amount_denied(self):
        req = _req(text="This strategy manages $5,000,000 AUM across cross-asset books.")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "REDACTION_VIOLATION"
        assert any("capital_amount:" in f for f in decision.findings)

    def test_broker_ref_denied(self):
        req = _req(text="Executed on IBKR account U1234567 for TW equity futures.")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "REDACTION_VIOLATION"
        assert any("broker_ref:" in f for f in decision.findings)

    def test_private_note_marker_denied(self):
        req = _req(text="PRIVATE: DO NOT SHARE: Internal risk limits for desk execution.")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "REDACTION_VIOLATION"
        assert any("private_marker:" in f for f in decision.findings)

    def test_raw_transcript_prompt_denied(self):
        req = _req(text="User: Can you optimize my strategy?\nAssistant: Yes, let us check...")
        decision = SemanticExtractionAdmission.check(req)
        assert decision.admitted is False
        assert decision.denial_code == "REDACTION_VIOLATION"
        assert any("raw_transcript_prompt" in f for f in decision.findings)


class TestZeroModelCallsOnAdmissionFailure:
    def test_client_makes_zero_model_calls_when_admission_fails(self):
        calls = []

        def fake_transport(payload: dict) -> dict:
            calls.append(payload)
            raise AssertionError("Model transport MUST NOT be called when admission fails!")

        client = SemanticExtractionClient(transport_fn=fake_transport)

        # 1. Denied via rejected source status
        req_rejected = _req(source_status="rejected")
        res = client.extract(req_rejected)
        assert res.is_abstained is True
        assert res.abstention_reason == AbstentionReason.ADMISSION_DENIED.value
        assert res.failure_code == ExtractionFailureCode.ADMISSION_DENIED.value
        assert len(calls) == 0

        # 2. Denied via credential leak
        req_cred = _req(text="Here is sk-ant-api03-abcdefghijklmnop1234567890 key")
        res_cred = client.extract(req_cred)
        assert res_cred.is_abstained is True
        assert res_cred.abstention_reason == AbstentionReason.ADMISSION_DENIED.value
        assert len(calls) == 0

        # 3. Denied via lookahead breach
        req_lookahead = _req(event_time="2026-07-01T00:00:00Z", as_of="2026-06-01T00:00:00Z")
        res_lookahead = client.extract(req_lookahead)
        assert res_lookahead.is_abstained is True
        assert res_lookahead.abstention_reason == AbstentionReason.ADMISSION_DENIED.value
        assert len(calls) == 0

        # 4. Denied via missing tenant
        req_no_tenant = _req(tenant_id="")
        res_no_tenant = client.extract(req_no_tenant)
        assert res_no_tenant.is_abstained is True
        assert len(calls) == 0
