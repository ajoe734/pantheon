#!/usr/bin/env python3
"""Signed exact-head canonical review attestation gate.

The repository's Pantheon workers share one GitHub account.  A commit status,
PR comment, approving review, or merge request created by that account cannot
prove that the canonical reviewer (rather than the task owner) approved the
delivery.  This module moves the authority into an Ed25519 signature issued by
the protected command runtime and binds it to one exact repository, task, PR,
base, head branch, and head commit.

The GitHub workflow in ``.github/workflows/canonical-review-gate.yml`` checks
the signed envelope from trusted base-branch code and publishes a check run on
the exact PR head.  Branch protection must pin that check to the GitHub Actions
app id; a user-owned PAT can create neither the signature nor an app-owned
check run.

This module deliberately has no network client.  Callers capture PR/comments
JSON with ``gh api`` and pass files in, which keeps the verifier deterministic
and easy to test.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
CHECK_NAME = "Pantheon canonical reviewer attestation"
SCHEMA = "pantheon.canonical-review-attestation/v1"
SIGNATURE_ALGORITHM = "ed25519"
MARKER_PREFIX = "<!-- pantheon-canonical-review-attestation:v1 "
MARKER_SUFFIX = " -->"
MARKER_RE = re.compile(
    r"<!--\s*pantheon-canonical-review-attestation:v1\s+"
    r"([A-Za-z0-9_-]+)\s*-->",
    re.MULTILINE,
)
OID_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
NONCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
APPROVE = "approve"
REJECT = "reject"
DECISIONS = {APPROVE, REJECT}
DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60
MAX_TTL_SECONDS = 7 * 24 * 60 * 60
CLOCK_SKEW_SECONDS = 120
PUBLIC_KEYS_ENV = "PANTHEON_CANONICAL_REVIEW_PUBLIC_KEYS_JSON"
DEFAULT_ACTIONS_APP_ID = 15368


class CanonicalReviewError(RuntimeError):
    """The attestation or protection contract failed closed."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: Any, *, field_name: str) -> bytes:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise CanonicalReviewError(
            "attestation_malformed",
            f"{field_name} must be unpadded base64url",
        )
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise CanonicalReviewError(
            "attestation_malformed",
            f"{field_name} is not valid base64url",
        ) from exc


def _decode_base64(value: Any, *, field_name: str, expected_bytes: int) -> bytes:
    text = str(value or "").strip()
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise CanonicalReviewError(
            "key_malformed",
            f"{field_name} is not strict base64",
        ) from exc
    if len(decoded) != expected_bytes:
        raise CanonicalReviewError(
            "key_malformed",
            f"{field_name} must decode to {expected_bytes} bytes",
        )
    return decoded


def _parse_time(value: Any, *, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise CanonicalReviewError(
            "attestation_malformed",
            f"{field_name} is required",
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CanonicalReviewError(
            "attestation_malformed",
            f"{field_name} must be RFC3339",
        ) from exc
    if parsed.tzinfo is None:
        raise CanonicalReviewError(
            "attestation_malformed",
            f"{field_name} must include a timezone",
        )
    return parsed.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _require_text(
    payload: Mapping[str, Any],
    field_name: str,
    *,
    pattern: re.Pattern[str] | None = None,
) -> str:
    value = str(payload.get(field_name) or "").strip()
    if not value:
        raise CanonicalReviewError(
            "attestation_malformed",
            f"{field_name} is required",
        )
    if pattern is not None and not pattern.fullmatch(value):
        raise CanonicalReviewError(
            "attestation_malformed",
            f"{field_name} has an invalid shape",
        )
    return value


def _normalize_agent(value: Any) -> str:
    return str(value or "").strip().casefold()


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CanonicalReviewError(
            "input_unavailable",
            f"cannot read {path}: {exc}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise CanonicalReviewError(
            "input_malformed",
            f"{path} is not valid JSON",
        ) from exc


def _load_comments_file(path: Path) -> list[dict[str, Any]]:
    """Read a JSON array or gh's paginated one-JSON-object-per-line output."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CanonicalReviewError(
            "comments_unavailable",
            f"cannot read {path}: {exc}",
        ) from exc
    try:
        return _flatten_comments(json.loads(text))
    except json.JSONDecodeError:
        values: list[Any] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise CanonicalReviewError(
                    "comments_unavailable",
                    f"{path} line {line_number} is not valid JSON",
                ) from exc
        return _flatten_comments(values)


def _flatten_comments(value: Any) -> list[dict[str, Any]]:
    """Accept REST arrays and ``gh api --paginate --slurp`` arrays-of-arrays."""

    if not isinstance(value, list):
        raise CanonicalReviewError(
            "comments_unavailable",
            "GitHub comments payload is not an array",
        )
    flattened: list[dict[str, Any]] = []
    queue: list[Any] = list(value)
    while queue:
        item = queue.pop(0)
        if isinstance(item, list):
            queue[0:0] = item
            continue
        if isinstance(item, Mapping):
            flattened.append(dict(item))
    return flattened


@dataclass(frozen=True)
class PullRequestIdentity:
    repository: str
    number: int
    state: str
    draft: bool
    head_sha: str
    head_branch: str
    base_branch: str
    author_login: str

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        repository: str,
    ) -> "PullRequestIdentity":
        repository = str(repository or "").strip()
        if not REPOSITORY_RE.fullmatch(repository):
            raise CanonicalReviewError(
                "pr_identity_invalid",
                f"repository must be owner/name, got {repository!r}",
            )
        try:
            number = int(value.get("number"))
        except (TypeError, ValueError) as exc:
            raise CanonicalReviewError(
                "pr_identity_invalid",
                "PR number is missing or invalid",
            ) from exc
        head = value.get("head")
        base = value.get("base")
        author = value.get("user") or value.get("author")
        head_sha = str(
            value.get("headRefOid")
            or (head.get("sha") if isinstance(head, Mapping) else "")
            or ""
        ).strip().lower()
        head_branch = str(
            value.get("headRefName")
            or (head.get("ref") if isinstance(head, Mapping) else "")
            or ""
        ).strip()
        base_branch = str(
            value.get("baseRefName")
            or (base.get("ref") if isinstance(base, Mapping) else "")
            or ""
        ).strip()
        author_login = str(
            (author.get("login") if isinstance(author, Mapping) else author) or ""
        ).strip()
        state = str(value.get("state") or "").strip().upper()
        draft = bool(value.get("draft") or value.get("isDraft"))
        if number <= 0:
            raise CanonicalReviewError(
                "pr_identity_invalid",
                "PR number must be positive",
            )
        if not OID_RE.fullmatch(head_sha):
            raise CanonicalReviewError(
                "pr_identity_invalid",
                "PR head must be a full lowercase 40-hex oid",
            )
        if not head_branch or not base_branch:
            raise CanonicalReviewError(
                "pr_identity_invalid",
                "PR head and base branches are required",
            )
        if state not in {"OPEN", "MERGED", "CLOSED"}:
            raise CanonicalReviewError(
                "pr_identity_invalid",
                f"PR state {state or 'missing'!r} is unsupported",
            )
        return cls(
            repository=repository,
            number=number,
            state=state,
            draft=draft,
            head_sha=head_sha,
            head_branch=head_branch,
            base_branch=base_branch,
            author_login=author_login,
        )


@dataclass(frozen=True)
class TrustedKey:
    key_id: str
    reviewer: str
    public_key: bytes


def load_trusted_keys(value: Any) -> dict[str, TrustedKey]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CanonicalReviewError(
                "trusted_keys_unavailable",
                "trusted reviewer key registry is not valid JSON",
            ) from exc
    if not isinstance(value, Mapping):
        raise CanonicalReviewError(
            "trusted_keys_unavailable",
            "trusted reviewer key registry is missing",
        )
    raw_keys = value.get("keys") if isinstance(value.get("keys"), Mapping) else value
    assert isinstance(raw_keys, Mapping)
    keys: dict[str, TrustedKey] = {}
    for raw_key_id, raw_entry in raw_keys.items():
        key_id = str(raw_key_id or "").strip()
        if not key_id or not isinstance(raw_entry, Mapping):
            continue
        if raw_entry.get("enabled") is False:
            continue
        reviewer = str(raw_entry.get("reviewer") or "").strip()
        if not reviewer:
            raise CanonicalReviewError(
                "trusted_keys_unavailable",
                f"trusted key {key_id!r} has no reviewer identity",
            )
        public_key = _decode_base64(
            raw_entry.get("public_key_base64"),
            field_name=f"trusted key {key_id}.public_key_base64",
            expected_bytes=32,
        )
        keys[key_id] = TrustedKey(
            key_id=key_id,
            reviewer=reviewer,
            public_key=public_key,
        )
    if not keys:
        raise CanonicalReviewError(
            "trusted_keys_unavailable",
            "trusted reviewer key registry contains no enabled keys",
        )
    return keys


def _verify_ed25519(
    *,
    public_key: bytes,
    signature: bytes,
    message: bytes,
) -> None:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError:
        # SubjectPublicKeyInfo prefix for a raw Ed25519 public key.
        public_der = bytes.fromhex("302a300506032b6570032100") + public_key
        with tempfile.TemporaryDirectory(prefix="pantheon-review-verify-") as tmp:
            root = Path(tmp)
            public_path = root / "public.der"
            signature_path = root / "signature.bin"
            message_path = root / "message.bin"
            public_path.write_bytes(public_der)
            signature_path.write_bytes(signature)
            message_path.write_bytes(message)
            result = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    str(public_path),
                    "-keyform",
                    "DER",
                    "-rawin",
                    "-in",
                    str(message_path),
                    "-sigfile",
                    str(signature_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if result.returncode != 0:
            raise CanonicalReviewError(
                "signature_invalid",
                "Ed25519 signature does not match the trusted reviewer key",
            )
        return
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except (InvalidSignature, ValueError) as exc:
        raise CanonicalReviewError(
            "signature_invalid",
            "Ed25519 signature does not match the trusted reviewer key",
        ) from exc


def _sign_ed25519(*, private_key: bytes, message: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError:
        # PKCS#8 prefix for a raw 32-byte Ed25519 seed.
        private_der = bytes.fromhex("302e020100300506032b657004220420") + private_key
        with tempfile.TemporaryDirectory(prefix="pantheon-review-sign-") as tmp:
            root = Path(tmp)
            private_path = root / "private.der"
            message_path = root / "message.bin"
            signature_path = root / "signature.bin"
            private_path.write_bytes(private_der)
            message_path.write_bytes(message)
            result = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-sign",
                    "-inkey",
                    str(private_path),
                    "-keyform",
                    "DER",
                    "-rawin",
                    "-in",
                    str(message_path),
                    "-out",
                    str(signature_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise CanonicalReviewError(
                    "signing_failed",
                    (result.stderr or result.stdout or "openssl signing failed")[:300],
                )
            signature = signature_path.read_bytes()
        if len(signature) != 64:
            raise CanonicalReviewError(
                "signing_failed",
                "Ed25519 signer returned a non-64-byte signature",
            )
        return signature
    try:
        return Ed25519PrivateKey.from_private_bytes(private_key).sign(message)
    except ValueError as exc:
        raise CanonicalReviewError(
            "signing_failed",
            "protected reviewer private key is invalid",
        ) from exc


def encode_envelope(
    *,
    payload: Mapping[str, Any],
    key_id: str,
    private_key: bytes,
) -> str:
    body = canonical_json_bytes(dict(payload))
    signature = _sign_ed25519(private_key=private_key, message=body)
    envelope = {
        "key_id": key_id,
        "payload": dict(payload),
        "signature": _b64url_encode(signature),
        "signature_algorithm": SIGNATURE_ALGORITHM,
    }
    return _b64url_encode(canonical_json_bytes(envelope))


def decode_envelope(encoded: str) -> dict[str, Any]:
    raw = _b64url_decode(encoded, field_name="attestation envelope")
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CanonicalReviewError(
            "attestation_malformed",
            "attestation envelope is not JSON",
        ) from exc
    if not isinstance(envelope, Mapping):
        raise CanonicalReviewError(
            "attestation_malformed",
            "attestation envelope must be an object",
        )
    return dict(envelope)


def format_comment(encoded_envelope: str, *, payload: Mapping[str, Any]) -> str:
    decision = str(payload.get("decision") or "")
    reviewer = str(payload.get("reviewer") or "")
    task_id = str(payload.get("task_id") or "")
    head_sha = str(payload.get("head_sha") or "")
    return (
        f"Pantheon canonical reviewer `{reviewer}` recorded `{decision}` for "
        f"`{task_id}` at exact head `{head_sha}`.\n\n"
        f"{MARKER_PREFIX}{encoded_envelope}{MARKER_SUFFIX}"
    )


@dataclass(frozen=True)
class VerifiedAttestation:
    payload: dict[str, Any]
    key_id: str
    signature_sha256: str
    issued_at: datetime
    expires_at: datetime
    expired: bool


def verify_envelope(
    envelope: Mapping[str, Any],
    *,
    pr: PullRequestIdentity,
    trusted_keys: Mapping[str, TrustedKey],
    now: datetime,
) -> VerifiedAttestation:
    algorithm = str(envelope.get("signature_algorithm") or "").strip().lower()
    if algorithm != SIGNATURE_ALGORITHM:
        raise CanonicalReviewError(
            "attestation_malformed",
            f"signature_algorithm must be {SIGNATURE_ALGORITHM}",
        )
    key_id = str(envelope.get("key_id") or "").strip()
    trusted = trusted_keys.get(key_id)
    if trusted is None:
        raise CanonicalReviewError(
            "untrusted_reviewer_key",
            f"attestation key {key_id or 'missing'!r} is not trusted",
        )
    raw_payload = envelope.get("payload")
    if not isinstance(raw_payload, Mapping):
        raise CanonicalReviewError(
            "attestation_malformed",
            "attestation payload must be an object",
        )
    payload = dict(raw_payload)
    signature = _b64url_decode(
        envelope.get("signature"),
        field_name="attestation signature",
    )
    if len(signature) != 64:
        raise CanonicalReviewError(
            "attestation_malformed",
            "Ed25519 signature must be 64 bytes",
        )
    _verify_ed25519(
        public_key=trusted.public_key,
        signature=signature,
        message=canonical_json_bytes(payload),
    )

    if _require_text(payload, "schema") != SCHEMA:
        raise CanonicalReviewError(
            "attestation_malformed",
            f"schema must be {SCHEMA}",
        )
    repository = _require_text(payload, "repository", pattern=REPOSITORY_RE)
    task_id = _require_text(payload, "task_id", pattern=TASK_ID_RE)
    head_sha = _require_text(payload, "head_sha", pattern=OID_RE)
    head_branch = _require_text(payload, "head_branch")
    base_branch = _require_text(payload, "base")
    owner = _require_text(payload, "owner")
    reviewer = _require_text(payload, "reviewer")
    decision = _require_text(payload, "decision").lower()
    canonical_source = _require_text(payload, "canonical_source").lower()
    canonical_status = _require_text(payload, "canonical_status").lower()
    _require_text(payload, "nonce", pattern=NONCE_RE)
    _require_text(payload, "canonical_record_sha256", pattern=SHA256_RE)
    _require_text(payload, "review_message_sha256", pattern=SHA256_RE)
    if decision not in DECISIONS:
        raise CanonicalReviewError(
            "attestation_malformed",
            f"decision must be one of {sorted(DECISIONS)}",
        )
    if canonical_source not in {"active", "archive"}:
        raise CanonicalReviewError(
            "canonical_state_invalid",
            f"canonical_source {canonical_source!r} is not trusted",
        )
    if decision == APPROVE and canonical_status not in {
        "review_approved",
        "done",
    }:
        raise CanonicalReviewError(
            "canonical_state_invalid",
            f"approve attestation carries canonical status {canonical_status!r}",
        )
    if _normalize_agent(owner) == _normalize_agent(reviewer):
        raise CanonicalReviewError(
            "self_owned_attestation",
            "canonical reviewer must be distinct from task owner",
        )
    if _normalize_agent(trusted.reviewer) != _normalize_agent(reviewer):
        raise CanonicalReviewError(
            "reviewer_key_mismatch",
            f"trusted key {key_id!r} belongs to {trusted.reviewer!r}, "
            f"not payload reviewer {reviewer!r}",
        )
    try:
        pr_number = int(payload.get("pr"))
    except (TypeError, ValueError) as exc:
        raise CanonicalReviewError(
            "attestation_malformed",
            "pr must be a positive integer",
        ) from exc
    if pr_number <= 0:
        raise CanonicalReviewError(
            "attestation_malformed",
            "pr must be a positive integer",
        )

    expected_task_branch = f"task/{task_id}"
    mismatches: list[str] = []
    if repository != pr.repository:
        mismatches.append(f"repository {repository!r} != {pr.repository!r}")
    if pr_number != pr.number:
        mismatches.append(f"PR #{pr_number} != #{pr.number}")
    if head_sha != pr.head_sha:
        mismatches.append(f"head {head_sha} != {pr.head_sha}")
    if head_branch != pr.head_branch:
        mismatches.append(f"head branch {head_branch!r} != {pr.head_branch!r}")
    if head_branch != expected_task_branch:
        mismatches.append(
            f"head branch {head_branch!r} != canonical {expected_task_branch!r}"
        )
    if base_branch != pr.base_branch:
        mismatches.append(f"base {base_branch!r} != {pr.base_branch!r}")
    if mismatches:
        raise CanonicalReviewError(
            "stale_head_attestation",
            "; ".join(mismatches),
        )

    approval_event_at = _parse_time(
        payload.get("approval_event_at"),
        field_name="approval_event_at",
    )
    issued_at = _parse_time(payload.get("issued_at"), field_name="issued_at")
    expires_at = _parse_time(payload.get("expires_at"), field_name="expires_at")
    if issued_at + timedelta(seconds=CLOCK_SKEW_SECONDS) < approval_event_at:
        raise CanonicalReviewError(
            "attestation_time_invalid",
            "attestation was issued before the canonical decision event",
        )
    if expires_at <= issued_at:
        raise CanonicalReviewError(
            "attestation_time_invalid",
            "attestation expiry must be after issuance",
        )
    if (expires_at - issued_at).total_seconds() > MAX_TTL_SECONDS:
        raise CanonicalReviewError(
            "attestation_time_invalid",
            f"attestation TTL exceeds {MAX_TTL_SECONDS} seconds",
        )
    if issued_at - now > timedelta(seconds=CLOCK_SKEW_SECONDS):
        raise CanonicalReviewError(
            "attestation_time_invalid",
            "attestation issuance time is in the future",
        )
    return VerifiedAttestation(
        payload=payload,
        key_id=key_id,
        signature_sha256=hashlib.sha256(signature).hexdigest(),
        issued_at=issued_at,
        expires_at=expires_at,
        expired=now > expires_at + timedelta(seconds=CLOCK_SKEW_SECONDS),
    )


@dataclass(frozen=True)
class CheckResult:
    conclusion: str
    reason: str
    title: str
    summary: str
    task_id: str = ""
    reviewer: str = ""
    key_id: str = ""
    comment_id: int | None = None
    issued_at: str = ""
    expires_at: str = ""
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return self.conclusion == "success"

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_name": CHECK_NAME,
            "conclusion": self.conclusion,
            "reason": self.reason,
            "title": self.title,
            "summary": self.summary,
            "task_id": self.task_id,
            "reviewer": self.reviewer,
            "key_id": self.key_id,
            "comment_id": self.comment_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "diagnostics": list(self.diagnostics),
        }


def evaluate_comments(
    *,
    pr: PullRequestIdentity,
    comments: Iterable[Mapping[str, Any]],
    trusted_keys: Mapping[str, TrustedKey],
    now: datetime | None = None,
) -> CheckResult:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    if pr.state != "OPEN":
        return CheckResult(
            conclusion="failure",
            reason="pr_not_open",
            title="Canonical review blocked",
            summary=f"PR #{pr.number} is {pr.state.lower()}, not open.",
        )
    if pr.draft:
        return CheckResult(
            conclusion="failure",
            reason="pr_is_draft",
            title="Canonical review blocked",
            summary=f"PR #{pr.number} is a draft.",
        )

    candidates: list[tuple[VerifiedAttestation, int | None]] = []
    diagnostics: list[str] = []
    reason_counts: dict[str, int] = {}
    marker_count = 0
    for comment in comments:
        body = str(comment.get("body") or "")
        raw_comment_id = comment.get("id")
        try:
            comment_id = int(raw_comment_id) if raw_comment_id is not None else None
        except (TypeError, ValueError):
            comment_id = None
        for encoded in MARKER_RE.findall(body):
            marker_count += 1
            try:
                verified = verify_envelope(
                    decode_envelope(encoded),
                    pr=pr,
                    trusted_keys=trusted_keys,
                    now=reference,
                )
            except CanonicalReviewError as exc:
                reason_counts[exc.reason] = reason_counts.get(exc.reason, 0) + 1
                diagnostics.append(
                    f"comment {comment_id if comment_id is not None else '?'}: "
                    f"{exc.reason}: {exc.detail}"
                )
                continue
            candidates.append((verified, comment_id))

    if not candidates:
        if marker_count == 0:
            reason = "missing_attestation"
            summary = (
                f"PR #{pr.number} head {pr.head_sha} has no signed canonical "
                "review attestation."
            )
        else:
            priority = (
                "self_owned_attestation",
                "stale_head_attestation",
                "signature_invalid",
                "reviewer_key_mismatch",
                "untrusted_reviewer_key",
                "attestation_time_invalid",
                "attestation_malformed",
            )
            reason = next(
                (item for item in priority if reason_counts.get(item)),
                "invalid_attestation",
            )
            summary = (
                f"PR #{pr.number} has {marker_count} attestation marker(s), "
                "but none is valid for the exact current head."
            )
        return CheckResult(
            conclusion="failure",
            reason=reason,
            title="Canonical review blocked",
            summary=summary,
            diagnostics=tuple(diagnostics[-20:]),
        )

    candidates.sort(
        key=lambda item: (
            item[0].issued_at,
            item[1] if item[1] is not None else -1,
        )
    )
    latest_time = candidates[-1][0].issued_at
    newest = [item for item in candidates if item[0].issued_at == latest_time]
    newest_decisions = {item[0].payload["decision"] for item in newest}
    if len(newest_decisions) != 1:
        return CheckResult(
            conclusion="failure",
            reason="ambiguous_attestation",
            title="Canonical review blocked",
            summary=(
                "Conflicting signed reviewer decisions share the latest "
                f"issuance time {_format_time(latest_time)}."
            ),
            diagnostics=tuple(diagnostics[-20:]),
        )
    selected, comment_id = newest[-1]
    payload = selected.payload
    common = {
        "task_id": str(payload["task_id"]),
        "reviewer": str(payload["reviewer"]),
        "key_id": selected.key_id,
        "comment_id": comment_id,
        "issued_at": _format_time(selected.issued_at),
        "expires_at": _format_time(selected.expires_at),
        "diagnostics": tuple(diagnostics[-20:]),
    }
    if payload["decision"] == REJECT:
        return CheckResult(
            conclusion="failure",
            reason="attestation_rejected",
            title="Canonical reviewer rejected this head",
            summary=(
                f"{payload['reviewer']} signed a rejection for task "
                f"{payload['task_id']} PR #{pr.number} head {pr.head_sha}."
            ),
            **common,
        )
    if selected.expired:
        return CheckResult(
            conclusion="failure",
            reason="attestation_expired",
            title="Canonical review attestation expired",
            summary=(
                f"The exact-head approval expired at "
                f"{_format_time(selected.expires_at)}; re-review this unchanged head."
            ),
            **common,
        )
    return CheckResult(
        conclusion="success",
        reason="exact_head_independently_approved",
        title="Exact head independently approved",
        summary=(
            f"{payload['reviewer']} signed approval for task {payload['task_id']} "
            f"PR #{pr.number} head {pr.head_sha}; key={selected.key_id}, "
            f"expires={_format_time(selected.expires_at)}."
        ),
        **common,
    )


def _signer_file(
    path: Path,
    *,
    status_root: Path,
) -> tuple[str, str, bytes]:
    if not path.is_absolute():
        raise CanonicalReviewError(
            "signer_key_unprotected",
            "reviewer signer key path must be absolute",
        )
    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise CanonicalReviewError(
                "signer_key_unprotected",
                f"reviewer signer key path contains a symlink: {cursor}",
            )
    try:
        file_stat = absolute.stat()
    except OSError as exc:
        raise CanonicalReviewError(
            "signer_key_unavailable",
            f"cannot stat reviewer signer key: {exc}",
        ) from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise CanonicalReviewError(
            "signer_key_unprotected",
            "reviewer signer key must be a regular file",
        )
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise CanonicalReviewError(
            "signer_key_unprotected",
            "reviewer signer key must not be group/other accessible",
        )
    for forbidden in (ROOT.resolve(), status_root.resolve()):
        try:
            absolute.resolve().relative_to(forbidden)
        except ValueError:
            continue
        raise CanonicalReviewError(
            "signer_key_unprotected",
            f"reviewer signer key cannot live under candidate-controlled {forbidden}",
        )
    value = _load_json_file(absolute)
    if not isinstance(value, Mapping):
        raise CanonicalReviewError(
            "signer_key_unavailable",
            "reviewer signer key file must be a JSON object",
        )
    key_id = str(value.get("key_id") or "").strip()
    reviewer = str(value.get("reviewer") or "").strip()
    if not key_id or not reviewer:
        raise CanonicalReviewError(
            "signer_key_unavailable",
            "reviewer signer key requires key_id and reviewer",
        )
    private_key = _decode_base64(
        value.get("private_key_base64"),
        field_name="private_key_base64",
        expected_bytes=32,
    )
    return key_id, reviewer, private_key


def _load_gate_module() -> Any:
    import sys

    scripts_git = ROOT / "scripts" / "git"
    if str(scripts_git) not in sys.path:
        sys.path.insert(0, str(scripts_git))
    import task_review_merge_gate

    return task_review_merge_gate


def issue_from_canonical_state(
    *,
    repository: str,
    task_id: str,
    actor: str,
    decision: str,
    message: str,
    pr_json: Mapping[str, Any],
    status_root: Path,
    signer_key_file: Path,
    ttl_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
    nonce: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Issue a signed envelope only from exact canonical review state."""

    if decision not in DECISIONS:
        raise CanonicalReviewError(
            "decision_invalid",
            f"decision must be one of {sorted(DECISIONS)}",
        )
    if ttl_seconds <= 0 or ttl_seconds > MAX_TTL_SECONDS:
        raise CanonicalReviewError(
            "attestation_time_invalid",
            f"ttl_seconds must be in 1..{MAX_TTL_SECONDS}",
        )
    gate = _load_gate_module()
    contract = gate.load_task_contract(task_id, status_root=status_root)
    approval = gate.load_approval_record(task_id, status_root=status_root)
    pr = PullRequestIdentity.from_mapping(pr_json, repository=repository)
    normalized_pr = dict(pr_json)
    normalized_pr.update(
        {
            "number": pr.number,
            "state": pr.state,
            "isDraft": pr.draft,
            "headRefOid": pr.head_sha,
            "headRefName": pr.head_branch,
            "baseRefName": pr.base_branch,
        }
    )
    if _normalize_agent(contract.reviewer) != _normalize_agent(actor):
        raise CanonicalReviewError(
            "reviewer_mismatch",
            f"canonical reviewer is {contract.reviewer!r}, not {actor!r}",
        )
    if not contract.requires_independent_review:
        raise CanonicalReviewError(
            "self_owned_attestation",
            "canonical task has no reviewer distinct from owner",
        )
    event_at = ""
    if decision == APPROVE:
        gate_decision = gate.evaluate_gate(contract, approval, normalized_pr)
        if not gate_decision.allow_merge:
            raise CanonicalReviewError(
                f"canonical_{gate_decision.reason}",
                gate_decision.detail,
            )
        event_at = approval.approved_at_text
    else:
        if not approval.revoked:
            raise CanonicalReviewError(
                "canonical_rejection_missing",
                "canonical activity audit carries no rejection/reopen after approval",
            )
        if _normalize_agent(approval.revoked_by) != _normalize_agent(actor):
            raise CanonicalReviewError(
                "reviewer_mismatch",
                f"canonical rejection was recorded by {approval.revoked_by!r}, "
                f"not {actor!r}",
            )
        if approval.approved_pr_number != pr.number:
            raise CanonicalReviewError(
                "stale_head_attestation",
                "canonical rejection does not bind this PR",
            )
        if approval.approved_head_sha != pr.head_sha:
            raise CanonicalReviewError(
                "stale_head_attestation",
                "canonical rejection does not bind this exact head",
            )
        event_at = approval.revoked_at_text

    key_id, key_reviewer, private_key = _signer_file(
        signer_key_file,
        status_root=status_root,
    )
    if _normalize_agent(key_reviewer) != _normalize_agent(actor):
        raise CanonicalReviewError(
            "reviewer_key_mismatch",
            f"signer key belongs to {key_reviewer!r}, not {actor!r}",
        )
    reference = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    payload = {
        "approval_event_at": event_at,
        "base": pr.base_branch,
        "canonical_record_sha256": canonical_sha256(
            {
                "approval": approval.as_dict(),
                "contract": contract.as_dict(),
            }
        ),
        "canonical_source": contract.source,
        "canonical_status": contract.status,
        "decision": decision,
        "expires_at": _format_time(reference + timedelta(seconds=ttl_seconds)),
        "head_branch": pr.head_branch,
        "head_sha": pr.head_sha,
        "issued_at": _format_time(reference),
        "nonce": nonce or f"review-{uuid.uuid4()}",
        "owner": contract.owner,
        "pr": pr.number,
        "repository": pr.repository,
        "review_message_sha256": hashlib.sha256(
            message.encode("utf-8")
        ).hexdigest(),
        "reviewer": contract.reviewer,
        "schema": SCHEMA,
        "task_id": task_id,
    }
    encoded = encode_envelope(
        payload=payload,
        key_id=key_id,
        private_key=private_key,
    )
    return payload, format_comment(encoded, payload=payload)


def _protection_checks(protection: Mapping[str, Any]) -> list[dict[str, Any]]:
    required = protection.get("required_status_checks")
    if not isinstance(required, Mapping):
        return []
    checks: list[dict[str, Any]] = []
    raw_checks = required.get("checks")
    if isinstance(raw_checks, list):
        for raw in raw_checks:
            if not isinstance(raw, Mapping):
                continue
            context = str(raw.get("context") or "").strip()
            if not context:
                continue
            app_id = raw.get("app_id")
            checks.append(
                {
                    "context": context,
                    "app_id": int(app_id) if isinstance(app_id, int) else None,
                }
            )
    if not checks:
        for context in required.get("contexts") or []:
            text = str(context or "").strip()
            if text:
                checks.append({"context": text, "app_id": None})
    return checks


def summarize_protection(
    protection: Mapping[str, Any],
    *,
    repository: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw_reviews = protection.get("required_pull_request_reviews")
    reviews_present = isinstance(raw_reviews, Mapping)
    reviews = raw_reviews if reviews_present else {}
    admins = protection.get("enforce_admins")
    admins = admins if isinstance(admins, Mapping) else {}
    required = protection.get("required_status_checks")
    required = required if isinstance(required, Mapping) else {}
    summary = {
        "required_pull_request_reviews_present": reviews_present,
        "required_approving_review_count": (
            int(reviews.get("required_approving_review_count") or 0)
            if reviews_present
            else None
        ),
        "dismiss_stale_reviews": (
            bool(reviews.get("dismiss_stale_reviews"))
            if reviews_present
            else None
        ),
        "require_last_push_approval": (
            bool(reviews.get("require_last_push_approval"))
            if reviews_present
            else None
        ),
        "enforce_admins": bool(admins.get("enabled")),
        "strict_required_status_checks": bool(required.get("strict")),
        "required_status_checks": _protection_checks(protection),
    }
    if isinstance(repository, Mapping):
        summary["allow_auto_merge"] = bool(repository.get("allow_auto_merge"))
    return summary


def build_protection_plan(
    *,
    repository_slug: str,
    branch: str,
    protection: Mapping[str, Any],
    repository: Mapping[str, Any],
    context: str = CHECK_NAME,
    app_id: int = DEFAULT_ACTIONS_APP_ID,
) -> dict[str, Any]:
    if not REPOSITORY_RE.fullmatch(repository_slug):
        raise CanonicalReviewError(
            "protection_plan_invalid",
            "repository slug must be owner/name",
        )
    if not branch:
        raise CanonicalReviewError(
            "protection_plan_invalid",
            "branch is required",
        )
    baseline = summarize_protection(protection, repository=repository)
    original_checks = list(baseline["required_status_checks"])
    activated_checks = [
        item for item in original_checks if item.get("context") != context
    ]
    activated_checks.append({"context": context, "app_id": app_id})
    strict = bool(baseline["strict_required_status_checks"])
    branch_api = (
        f"repos/{repository_slug}/branches/{quote(branch, safe='')}/protection"
    )
    activation = [
        {
            "method": "PATCH",
            "endpoint": f"repos/{repository_slug}",
            "body": {"allow_auto_merge": False},
        },
        {
            "method": "POST",
            "endpoint": f"{branch_api}/enforce_admins",
            "body": {},
        },
        {
            "method": "PATCH",
            "endpoint": f"{branch_api}/required_status_checks",
            "body": {"strict": strict, "checks": activated_checks},
        },
    ]
    rollback_admin_method = (
        "POST" if baseline["enforce_admins"] else "DELETE"
    )
    rollback = [
        {
            "method": "PATCH",
            "endpoint": f"{branch_api}/required_status_checks",
            "body": {"strict": strict, "checks": original_checks},
        },
        {
            "method": rollback_admin_method,
            "endpoint": f"{branch_api}/enforce_admins",
            "body": {},
        },
        {
            "method": "PATCH",
            "endpoint": f"repos/{repository_slug}",
            "body": {
                "allow_auto_merge": bool(baseline.get("allow_auto_merge"))
            },
        },
    ]
    return {
        "schema": "pantheon.canonical-review-protection-plan/v1",
        "repository": repository_slug,
        "branch": branch,
        "baseline": baseline,
        "activation": activation,
        "expected_active": {
            "context": context,
            "app_id": app_id,
            "enforce_admins": True,
            "allow_auto_merge": False,
            "strict_required_status_checks": strict,
            "required_status_checks": activated_checks,
        },
        "rollback": rollback,
    }


def _plan_check_pairs(
    raw_checks: Any,
    *,
    field_name: str,
) -> list[tuple[str, int | None]]:
    if not isinstance(raw_checks, list):
        raise CanonicalReviewError(
            "protection_plan_invalid",
            f"{field_name} must be a list",
        )
    pairs: list[tuple[str, int | None]] = []
    for index, raw in enumerate(raw_checks):
        if not isinstance(raw, Mapping):
            raise CanonicalReviewError(
                "protection_plan_invalid",
                f"{field_name}[{index}] must be an object",
            )
        context = str(raw.get("context") or "").strip()
        if not context:
            raise CanonicalReviewError(
                "protection_plan_invalid",
                f"{field_name}[{index}].context is required",
            )
        raw_app_id = raw.get("app_id")
        if raw_app_id is not None and (
            not isinstance(raw_app_id, int) or isinstance(raw_app_id, bool)
        ):
            raise CanonicalReviewError(
                "protection_plan_invalid",
                f"{field_name}[{index}].app_id must be an integer or null",
            )
        pairs.append((context, raw_app_id))
    return sorted(
        pairs,
        key=lambda item: (
            item[0],
            -1 if item[1] is None else item[1],
        ),
    )


def _expected_readback_from_plan(
    plan: Mapping[str, Any],
    *,
    context: str,
    app_id: int,
) -> tuple[bool, list[tuple[str, int | None]]]:
    if plan.get("schema") != "pantheon.canonical-review-protection-plan/v1":
        raise CanonicalReviewError(
            "protection_plan_invalid",
            "readback plan has an unsupported schema",
        )
    baseline = plan.get("baseline")
    expected_active = plan.get("expected_active")
    activation = plan.get("activation")
    if not isinstance(baseline, Mapping) or not isinstance(
        expected_active, Mapping
    ):
        raise CanonicalReviewError(
            "protection_plan_invalid",
            "readback plan requires baseline and expected_active objects",
        )
    if not isinstance(activation, list) or len(activation) != 3:
        raise CanonicalReviewError(
            "protection_plan_invalid",
            "readback plan requires the three ordered activation operations",
        )
    strict = baseline.get("strict_required_status_checks")
    if not isinstance(strict, bool):
        raise CanonicalReviewError(
            "protection_plan_invalid",
            "baseline strict_required_status_checks must be boolean",
        )
    baseline_pairs = _plan_check_pairs(
        baseline.get("required_status_checks"),
        field_name="baseline.required_status_checks",
    )
    derived_pairs = [
        item for item in baseline_pairs if item[0] != context
    ]
    derived_pairs.append((context, app_id))
    derived_pairs.sort(
        key=lambda item: (
            item[0],
            -1 if item[1] is None else item[1],
        )
    )
    expected_pairs = _plan_check_pairs(
        expected_active.get("required_status_checks"),
        field_name="expected_active.required_status_checks",
    )
    if expected_pairs != derived_pairs:
        raise CanonicalReviewError(
            "protection_plan_invalid",
            "expected active checks do not preserve the full baseline "
            "context/app_id set plus the app-pinned canonical check",
        )
    if expected_active.get("strict_required_status_checks") is not strict:
        raise CanonicalReviewError(
            "protection_plan_invalid",
            "expected active strict setting does not preserve the baseline",
        )
    if (
        expected_active.get("context") != context
        or expected_active.get("app_id") != app_id
        or expected_active.get("enforce_admins") is not True
        or expected_active.get("allow_auto_merge") is not False
    ):
        raise CanonicalReviewError(
            "protection_plan_invalid",
            "expected active controls do not match the requested gate",
        )
    expected_operations = (
        ("PATCH", None, {"allow_auto_merge": False}),
        ("POST", "/enforce_admins", {}),
        (
            "PATCH",
            "/required_status_checks",
            {
                "strict": strict,
                "checks": expected_active.get("required_status_checks"),
            },
        ),
    )
    for index, (method, endpoint_suffix, body) in enumerate(
        expected_operations
    ):
        operation = activation[index]
        if not isinstance(operation, Mapping):
            raise CanonicalReviewError(
                "protection_plan_invalid",
                f"activation[{index}] must be an object",
            )
        endpoint = str(operation.get("endpoint") or "")
        if operation.get("method") != method or operation.get("body") != body:
            raise CanonicalReviewError(
                "protection_plan_invalid",
                f"activation[{index}] does not match the safe runbook",
            )
        if endpoint_suffix is None:
            if "/branches/" in endpoint or not endpoint.startswith("repos/"):
                raise CanonicalReviewError(
                    "protection_plan_invalid",
                    "activation[0] must disable repository auto-merge first",
                )
        elif not endpoint.endswith(endpoint_suffix):
            raise CanonicalReviewError(
                "protection_plan_invalid",
                f"activation[{index}] endpoint must end with {endpoint_suffix}",
            )
    return strict, expected_pairs


def verify_active_protection(
    *,
    protection: Mapping[str, Any],
    repository: Mapping[str, Any],
    plan: Mapping[str, Any],
    context: str = CHECK_NAME,
    app_id: int = DEFAULT_ACTIONS_APP_ID,
) -> dict[str, Any]:
    expected_strict, expected_pairs = _expected_readback_from_plan(
        plan,
        context=context,
        app_id=app_id,
    )
    summary = summarize_protection(protection, repository=repository)
    actual_pairs = _plan_check_pairs(
        summary["required_status_checks"],
        field_name="readback.required_status_checks",
    )
    matching = [
        item
        for item in summary["required_status_checks"]
        if item.get("context") == context
    ]
    failures: list[str] = []
    if len(matching) != 1:
        failures.append(
            f"required check {context!r} count is {len(matching)}, expected 1"
        )
    elif matching[0].get("app_id") != app_id:
        failures.append(
            f"required check {context!r} app_id is "
            f"{matching[0].get('app_id')!r}, expected {app_id}"
        )
    if summary["strict_required_status_checks"] is not expected_strict:
        failures.append(
            "required status checks strict setting changed from the "
            f"activation baseline: got "
            f"{summary['strict_required_status_checks']!r}, "
            f"expected {expected_strict!r}"
        )
    if actual_pairs != expected_pairs:
        failures.append(
            "required status checks do not match the activation plan's "
            "full context/app_id set"
        )
    if not summary["enforce_admins"]:
        failures.append("branch protection does not enforce administrators")
    if summary.get("allow_auto_merge") is not False:
        failures.append("repository auto-merge is still enabled")
    active = not failures
    entrypoints = [
        {
            "entrypoint": "web_ui_direct_merge",
            "control": "actions_app_pinned_required_check",
            "blocked_without_exact_approval": active,
        },
        {
            "entrypoint": "gh_pr_merge",
            "control": "actions_app_pinned_required_check",
            "blocked_without_exact_approval": active,
        },
        {
            "entrypoint": "rest_pull_merge",
            "control": "actions_app_pinned_required_check",
            "blocked_without_exact_approval": active,
        },
        {
            "entrypoint": "graphql_merge_pull_request",
            "control": "actions_app_pinned_required_check",
            "blocked_without_exact_approval": active,
        },
        {
            "entrypoint": "web_or_graphql_auto_merge_creation",
            "control": "repository_allow_auto_merge_false",
            "blocked_without_exact_approval": active,
        },
        {
            "entrypoint": "auto_merge_finalization",
            "control": "actions_app_pinned_required_check",
            "blocked_without_exact_approval": active,
        },
        {
            "entrypoint": "administrator_bypass",
            "control": "branch_protection_enforce_admins",
            "blocked_without_exact_approval": active,
        },
    ]
    return {
        "ok": active,
        "context": context,
        "app_id": app_id,
        "plan": {
            "schema": plan["schema"],
            "repository": plan.get("repository"),
            "branch": plan.get("branch"),
            "strict_required_status_checks": expected_strict,
            "required_status_checks": [
                {"context": item_context, "app_id": item_app_id}
                for item_context, item_app_id in expected_pairs
            ],
        },
        "summary": summary,
        "failures": failures,
        "entrypoints": entrypoints,
        "all_entrypoints_blocked_without_exact_approval": all(
            row["blocked_without_exact_approval"] for row in entrypoints
        ),
    }


def _write_json(path: Path | None, value: Mapping[str, Any]) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(rendered, end="")
        return
    path.write_text(rendered, encoding="utf-8")


def _trusted_keys_from_args(args: argparse.Namespace) -> dict[str, TrustedKey]:
    if args.trusted_keys_json:
        raw: Any = args.trusted_keys_json
    elif args.trusted_keys_file:
        raw = _load_json_file(args.trusted_keys_file)
    else:
        raw = os.environ.get(PUBLIC_KEYS_ENV, "")
    return load_trusted_keys(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Issue and verify signed exact-head canonical review attestations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check")
    check.add_argument("--repository", required=True)
    check.add_argument("--pr-json", type=Path, required=True)
    check.add_argument("--comments-json", type=Path, required=True)
    check.add_argument("--trusted-keys-file", type=Path)
    check.add_argument("--trusted-keys-json")
    check.add_argument("--now")
    check.add_argument("--result-file", type=Path)

    issue = subparsers.add_parser("issue")
    issue.add_argument("task_id")
    issue.add_argument("--repository", required=True)
    issue.add_argument("--actor", required=True)
    issue.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    issue.add_argument("--message", required=True)
    issue.add_argument("--pr-json", type=Path, required=True)
    issue.add_argument("--status-root", type=Path, required=True)
    issue.add_argument("--signer-key-file", type=Path, required=True)
    issue.add_argument("--ttl-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    issue.add_argument("--now")
    issue.add_argument("--nonce")
    issue.add_argument("--output", type=Path)

    summary = subparsers.add_parser("protection-summary")
    summary.add_argument("--protection-json", type=Path, required=True)
    summary.add_argument("--repository-json", type=Path)
    summary.add_argument("--output", type=Path)

    plan = subparsers.add_parser("protection-plan")
    plan.add_argument("--repository", required=True)
    plan.add_argument("--branch", required=True)
    plan.add_argument("--protection-json", type=Path, required=True)
    plan.add_argument("--repository-json", type=Path, required=True)
    plan.add_argument("--context", default=CHECK_NAME)
    plan.add_argument("--app-id", type=int, default=DEFAULT_ACTIONS_APP_ID)
    plan.add_argument("--output", type=Path)

    verify = subparsers.add_parser("verify-protection")
    verify.add_argument("--protection-json", type=Path, required=True)
    verify.add_argument("--repository-json", type=Path, required=True)
    verify.add_argument("--plan-json", type=Path, required=True)
    verify.add_argument("--context", default=CHECK_NAME)
    verify.add_argument("--app-id", type=int, default=DEFAULT_ACTIONS_APP_ID)
    verify.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            raw_pr = _load_json_file(args.pr_json)
            if not isinstance(raw_pr, Mapping):
                raise CanonicalReviewError(
                    "pr_identity_invalid",
                    "PR payload must be an object",
                )
            pr = PullRequestIdentity.from_mapping(
                raw_pr,
                repository=args.repository,
            )
            comments = _load_comments_file(args.comments_json)
            reference = (
                _parse_time(args.now, field_name="now")
                if args.now
                else datetime.now(UTC)
            )
            result = evaluate_comments(
                pr=pr,
                comments=comments,
                trusted_keys=_trusted_keys_from_args(args),
                now=reference,
            )
            _write_json(args.result_file, result.as_dict())
            return 0 if result.passed else 1
        if args.command == "issue":
            raw_pr = _load_json_file(args.pr_json)
            if not isinstance(raw_pr, Mapping):
                raise CanonicalReviewError(
                    "pr_identity_invalid",
                    "PR payload must be an object",
                )
            reference = (
                _parse_time(args.now, field_name="now")
                if args.now
                else datetime.now(UTC)
            )
            payload, comment = issue_from_canonical_state(
                repository=args.repository,
                task_id=args.task_id,
                actor=args.actor,
                decision=args.decision,
                message=args.message,
                pr_json=raw_pr,
                status_root=args.status_root,
                signer_key_file=args.signer_key_file,
                ttl_seconds=args.ttl_seconds,
                now=reference,
                nonce=args.nonce,
            )
            result = {
                "comment": comment,
                "key_id": decode_envelope(
                    MARKER_RE.search(comment).group(1)  # type: ignore[union-attr]
                )["key_id"],
                "payload": payload,
            }
            _write_json(args.output, result)
            return 0
        if args.command == "protection-summary":
            protection = _load_json_file(args.protection_json)
            repository = (
                _load_json_file(args.repository_json)
                if args.repository_json
                else None
            )
            if not isinstance(protection, Mapping):
                raise CanonicalReviewError(
                    "input_malformed",
                    "protection JSON must be an object",
                )
            if repository is not None and not isinstance(repository, Mapping):
                raise CanonicalReviewError(
                    "input_malformed",
                    "repository JSON must be an object",
                )
            _write_json(
                args.output,
                summarize_protection(protection, repository=repository),
            )
            return 0
        if args.command == "protection-plan":
            protection = _load_json_file(args.protection_json)
            repository = _load_json_file(args.repository_json)
            if not isinstance(protection, Mapping) or not isinstance(
                repository, Mapping
            ):
                raise CanonicalReviewError(
                    "input_malformed",
                    "protection and repository JSON must be objects",
                )
            _write_json(
                args.output,
                build_protection_plan(
                    repository_slug=args.repository,
                    branch=args.branch,
                    protection=protection,
                    repository=repository,
                    context=args.context,
                    app_id=args.app_id,
                ),
            )
            return 0
        if args.command == "verify-protection":
            protection = _load_json_file(args.protection_json)
            repository = _load_json_file(args.repository_json)
            plan = _load_json_file(args.plan_json)
            if (
                not isinstance(protection, Mapping)
                or not isinstance(repository, Mapping)
                or not isinstance(plan, Mapping)
            ):
                raise CanonicalReviewError(
                    "input_malformed",
                    "protection, repository, and plan JSON must be objects",
                )
            result = verify_active_protection(
                protection=protection,
                repository=repository,
                plan=plan,
                context=args.context,
                app_id=args.app_id,
            )
            _write_json(args.output, result)
            return 0 if result["ok"] else 1
    except CanonicalReviewError as exc:
        error = {
            "check_name": CHECK_NAME,
            "conclusion": "failure",
            "reason": exc.reason,
            "title": "Canonical review checker failed closed",
            "summary": exc.detail,
        }
        output = getattr(args, "result_file", None) or getattr(args, "output", None)
        _write_json(output, error)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
