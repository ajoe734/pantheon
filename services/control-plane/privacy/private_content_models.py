"""Private-content domain models and error codes (AG-DES-SW-PRIV-001 §3, §9)."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# §3.3  Opaque reference prefix
# ---------------------------------------------------------------------------

PRIVATE_CONTENT_REF_PREFIX = "pcnt_"


# ---------------------------------------------------------------------------
# §3.5  Retention classes
# ---------------------------------------------------------------------------

RetentionClass = Literal[
    "workshop_default",
    "user_saved",
    "ephemeral_attachment",
    "legal_hold",
]

RETENTION_CLASSES: frozenset[str] = frozenset(
    ("workshop_default", "user_saved", "ephemeral_attachment", "legal_hold")
)

RETENTION_DAYS: dict[str, Optional[int]] = {
    "workshop_default": 90,
    "user_saved": 365,
    "ephemeral_attachment": 30,
    "legal_hold": None,  # no automatic expiry
}


# ---------------------------------------------------------------------------
# §7.5  Private-content object state
# ---------------------------------------------------------------------------

ContentObjectState = Literal["active", "deleted", "orphaned"]


# ---------------------------------------------------------------------------
# §3.2  Return value from PrivateContentStore.put
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class PrivateContentDescriptor:
    """Immutable descriptor returned by PrivateContentStore.put.

    Callers persist private_content_ref and let the store own the rest.
    Object URI is never surfaced outside the store.
    """
    private_content_ref: str  # pcnt_<ULID>
    tenant_id: str
    owner_user_id: str
    workshop_id: str
    event_id: Optional[str]
    content_type: str
    retention_class: RetentionClass
    expires_at: Optional[datetime]
    state: ContentObjectState
    created_at: datetime


# ---------------------------------------------------------------------------
# §3.6  Decrypt audit record — every decrypt must emit one of these
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class DecryptAuditRecord:
    """Immutable record written to the audit sink for every decrypt operation."""
    private_content_ref: str
    tenant_id: str
    owner_user_id: str
    actor_ref: str
    purpose: str
    request_id: str
    accessed_at: datetime
    outcome: Literal["success", "denied", "expired", "not_found"]


# ---------------------------------------------------------------------------
# §3.4  Envelope encryption internals — never leave the store layer
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class _EncryptedEnvelope:
    """Internal only: AES-256-GCM envelope produced by the key provider.

    Field semantics (§3.4):
      - nonce: 12-byte random IV for AES-256-GCM
      - tag: 16-byte authentication tag (GCM auth tag)
      - encrypted_dek: DEK encrypted under the KEK; bytea in DB
      - kek_key_version: opaque KEK version string from the key provider
      - ciphertext_sha256: hex digest of the ciphertext bytes (not plaintext)
      - object_uri: opaque URI in object-store; never surfaced to callers

    No plaintext hash is stored (§3.4 rule).
    """
    nonce: bytes               # 12 bytes
    tag: bytes                 # 16 bytes
    encrypted_dek: bytes       # encrypted data-encryption key
    kek_key_version: str       # KEK version from key provider
    ciphertext_sha256: str     # hex string, 64 chars
    object_uri: str            # written to DB only; never returned to callers


# ---------------------------------------------------------------------------
# §9  Error codes
# ---------------------------------------------------------------------------

class PrivateContentError(Exception):
    """Base for all private-content errors.

    Subclasses carry http_status and error_code as class attributes so callers
    can build wire responses without importing HTTP framework internals here.
    """
    http_status: int
    error_code: str


class PrivateContentStoreUnavailable(PrivateContentError):
    """503 PRIVATE_CONTENT_STORE_UNAVAILABLE"""
    http_status = 503
    error_code = "PRIVATE_CONTENT_STORE_UNAVAILABLE"


class PrivateContentRedactionUnavailable(PrivateContentError):
    """503 PRIVATE_CONTENT_REDACTION_UNAVAILABLE (§3.8)"""
    http_status = 503
    error_code = "PRIVATE_CONTENT_REDACTION_UNAVAILABLE"


class PrivateContentExpired(PrivateContentError):
    """410 PRIVATE_CONTENT_EXPIRED"""
    http_status = 410
    error_code = "PRIVATE_CONTENT_EXPIRED"


class PrivateContentAccessDenied(PrivateContentError):
    """403 PRIVATE_CONTENT_ACCESS_DENIED"""
    http_status = 403
    error_code = "PRIVATE_CONTENT_ACCESS_DENIED"


class StrategyReferenceMismatch(PrivateContentError):
    """409 STRATEGY_REFERENCE_MISMATCH"""
    http_status = 409
    error_code = "STRATEGY_REFERENCE_MISMATCH"


class StrategyReferenceNotFound(PrivateContentError):
    """404 STRATEGY_REFERENCE_NOT_FOUND"""
    http_status = 404
    error_code = "STRATEGY_REFERENCE_NOT_FOUND"


class WorkshopAlreadyConcluded(PrivateContentError):
    """409 WORKSHOP_ALREADY_CONCLUDED"""
    http_status = 409
    error_code = "WORKSHOP_ALREADY_CONCLUDED"


class WorkshopArchived(PrivateContentError):
    """409 WORKSHOP_ARCHIVED"""
    http_status = 409
    error_code = "WORKSHOP_ARCHIVED"


class WorkshopVersionRequired(PrivateContentError):
    """409 WORKSHOP_VERSION_REQUIRED"""
    http_status = 409
    error_code = "WORKSHOP_VERSION_REQUIRED"


class ConcurrentModification(PrivateContentError):
    """409 CONCURRENT_MODIFICATION"""
    http_status = 409
    error_code = "CONCURRENT_MODIFICATION"
