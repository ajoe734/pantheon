from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

CONTROL_PLANE_DIR = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
if str(CONTROL_PLANE_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_PLANE_DIR))

from privacy.private_content_models import (  # noqa: E402
    ConcurrentModification,
    PrivateContentAccessDenied,
    PrivateContentExpired,
    PrivateContentRedactionUnavailable,
    PrivateContentStoreUnavailable,
    StrategyReferenceMismatch,
    StrategyReferenceNotFound,
    WorkshopAlreadyConcluded,
    WorkshopArchived,
    WorkshopVersionRequired,
)
from privacy.private_content_policy import (  # noqa: E402
    RedactionResult,
    authorise_decrypt,
    validate_redaction_result,
)
from privacy.private_content_store import (  # noqa: E402
    PrivateContentStore,
    _DevKeyProvider,
    _decrypt_content,
    _encrypt_content,
    generate_private_content_ref,
)


DEV_KEK = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"


def test_private_content_store_protocol_has_no_list_method() -> None:
    assert "list" not in PrivateContentStore.__dict__
    assert {"put", "get_for_owner", "delete_for_owner", "expire_due"} <= set(
        PrivateContentStore.__dict__
    )


def test_private_content_ref_is_opaque_crockford_ulid() -> None:
    ref = generate_private_content_ref()
    assert re.fullmatch(r"pcnt_[0-9A-HJKMNP-TV-Z]{26}", ref)
    assert "tenant" not in ref
    assert "user" not in ref
    assert "/" not in ref


def test_private_content_ref_schema_example_matches_pattern() -> None:
    schema = json.loads(
        (
            ROOT
            / "services/control-plane/specs/agora/v3/private_content_ref.schema.json"
        ).read_text(encoding="utf-8")
    )
    pattern = re.compile(schema["pattern"])
    for example in schema["examples"]:
        assert pattern.fullmatch(example)


def test_dev_key_provider_encrypt_decrypt_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PANTHEON_ENV", raising=False)
    monkeypatch.setenv("AGORA_PRIVATE_CONTENT_DEV_KEK", DEV_KEK)
    key_provider = _DevKeyProvider()

    plaintext = b"owner-private strategy notes"
    ciphertext, _nonce, envelope = _encrypt_content(
        plaintext=plaintext,
        key_provider=key_provider,
        tenant_id="tenant-001",
        owner_user_id="user-001",
        workshop_id="workshop-001",
        event_id="event-001",
        content_type="text/plain",
    )

    assert envelope.ciphertext_sha256
    assert plaintext not in ciphertext
    assert (
        _decrypt_content(
            ct_with_tag=ciphertext,
            envelope=envelope,
            key_provider=key_provider,
            tenant_id="tenant-001",
            owner_user_id="user-001",
            workshop_id="workshop-001",
            event_id="event-001",
            content_type="text/plain",
        )
        == plaintext
    )


def test_dev_key_provider_refuses_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_ENV", "production")
    monkeypatch.setenv("AGORA_PRIVATE_CONTENT_DEV_KEK", DEV_KEK)

    with pytest.raises(RuntimeError):
        _DevKeyProvider()


def test_policy_owner_only_decrypt_and_fail_closed_redaction() -> None:
    allowed = authorise_decrypt(
        tenant_id="tenant-001",
        owner_user_id="user-001",
        request_tenant_id="tenant-001",
        request_user_id="user-001",
        actor_kind="owner",
        private_content_ref="pcnt_01KVM5JZBQZ1W50SPRG6S0850M",
        expires_at=None,
        now=datetime.now(timezone.utc),
    )
    denied = authorise_decrypt(
        tenant_id="tenant-001",
        owner_user_id="user-001",
        request_tenant_id="tenant-001",
        request_user_id="user-002",
        actor_kind="owner",
        private_content_ref="pcnt_01KVM5JZBQZ1W50SPRG6S0850M",
        expires_at=None,
        now=datetime.now(timezone.utc),
    )

    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.deny_reason == "not_owner"

    validate_redaction_result(
        RedactionResult(
            redacted_summary="summary",
            redaction_policy_version="1.0",
            redaction_status="completed",
        )
    )
    with pytest.raises(PrivateContentRedactionUnavailable):
        validate_redaction_result(None)


def test_error_codes_match_deep_closure_section_9() -> None:
    assert {
        cls.error_code: cls.http_status
        for cls in (
            PrivateContentStoreUnavailable,
            PrivateContentRedactionUnavailable,
            PrivateContentExpired,
            PrivateContentAccessDenied,
            StrategyReferenceMismatch,
            StrategyReferenceNotFound,
            WorkshopAlreadyConcluded,
            WorkshopArchived,
            WorkshopVersionRequired,
            ConcurrentModification,
        )
    } == {
        "PRIVATE_CONTENT_STORE_UNAVAILABLE": 503,
        "PRIVATE_CONTENT_REDACTION_UNAVAILABLE": 503,
        "PRIVATE_CONTENT_EXPIRED": 410,
        "PRIVATE_CONTENT_ACCESS_DENIED": 403,
        "STRATEGY_REFERENCE_MISMATCH": 409,
        "STRATEGY_REFERENCE_NOT_FOUND": 404,
        "WORKSHOP_ALREADY_CONCLUDED": 409,
        "WORKSHOP_ARCHIVED": 409,
        "WORKSHOP_VERSION_REQUIRED": 409,
        "CONCURRENT_MODIFICATION": 409,
    }
