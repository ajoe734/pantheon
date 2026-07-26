"""Protected Human/Ops product-closeout verdict authority.

The verdict is an Ed25519-signed capability bound to one exact catalog,
closeout manifest, target environment, and deployed FE/BFF identity.  Verdicts
live in an append-only hash-chained ledger outside candidate-controlled
repositories.  The ledger also records revocation and exactly-once consumption
at the final ``done`` transition.

Fleet workers may assemble the seven-field :class:`CloseoutBinding`, but this
module will only issue or revoke a verdict for an authenticated, MFA-verified
human principal with an authorized Human/Ops role.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
except ImportError as exc:  # pragma: no cover - deployment dependency guard
    InvalidSignature = Exception  # type: ignore[assignment,misc]
    Ed25519PrivateKey = Any  # type: ignore[assignment,misc]
    Ed25519PublicKey = Any  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment]
    _CRYPTO_IMPORT_ERROR: ImportError | None = exc
else:
    _CRYPTO_IMPORT_ERROR = None


SIGNATURE_ALGORITHM = "ed25519"
DEFAULT_POLICY_VERSION = "product-closeout-v1"
DEFAULT_MAX_VERDICT_AGE_SECONDS = 3600
DEFAULT_MAX_TTL_SECONDS = 3600
DEFAULT_PROTECTED_POLICY_PATH = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/product-closeout-verdict-policy.json"
)
AUTHORIZED_ACTOR_ROLES = frozenset({"human", "ops", "human_ops"})
FLEET_ACTOR_IDS = frozenset(
    {
        "Claude",
        "Claude2",
        "Codex",
        "Codex2",
        "Gemini",
        "Gemini2",
        "Copilot",
        "Antigravity",
        "Antigravity2",
        "Grok",
        "Qwen",
    }
)
VERDICT_BINDING_FIELDS = (
    "program_id",
    "catalog_sha256",
    "task_id",
    "closeout_manifest_sha256",
    "target_environment",
    "frontend_sha",
    "bff_sha",
    "verdict_id",
    "verifier_capability_sha256",
    "signature_algorithm",
    "key_id",
    "policy_version",
    "signature",
    "revocation_checked_at",
    "ledger_entry_id",
    "actor_id",
    "actor_role",
    "decision",
    "issued_at",
    "expires_at",
    "nonce",
)
_SIGNED_FIELDS = tuple(field for field in VERDICT_BINDING_FIELDS if field != "signature")
_EXPECTED_BINDING_FIELDS = (
    "program_id",
    "catalog_sha256",
    "task_id",
    "closeout_manifest_sha256",
    "target_environment",
    "frontend_sha",
    "bff_sha",
)
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_FLEET_ACTOR_ID_RE = re.compile(
    r"^(?:claude|gemini|codex|copilot|antigravity|grok|qwen)"
    r"(?:2)?(?:$|[-_:0-9])",
    re.IGNORECASE,
)
_ZERO_HASH = "0" * 64


class VerdictError(RuntimeError):
    """Base class for a fail-closed verdict error."""


class VerdictAuthorizationError(VerdictError):
    """The caller is not an authenticated Human/Ops authority."""


class VerdictConflictError(VerdictError):
    """The verdict, nonce, or transition was already committed."""


class VerdictIntegrityError(VerdictError):
    """Signed or ledger-bound material is missing, malformed, or tampered."""


class VerdictRevokedError(VerdictError):
    """The protected ledger contains a revocation for the verdict."""


class VerdictExpiredError(VerdictError):
    """The verdict is expired, not yet valid, or stale."""


class VerdictReplayError(VerdictError):
    """The verdict was already consumed or has invalid consumption state."""


def _require_crypto() -> None:
    if _CRYPTO_IMPORT_ERROR is not None:
        raise VerdictIntegrityError(
            "cryptography with Ed25519 support is required for product closeout"
        ) from _CRYPTO_IMPORT_ERROR


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise VerdictIntegrityError("invalid base64url verdict material") from exc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise VerdictIntegrityError(f"{field} must be a non-empty RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerdictIntegrityError(f"{field} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise VerdictIntegrityError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _require_nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerdictIntegrityError(f"{field} must be a non-empty string")
    return value.strip()


def _require_sha256(value: Any, *, field: str) -> str:
    text = _require_nonempty(value, field=field)
    if not _HEX_64_RE.fullmatch(text):
        raise VerdictIntegrityError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _require_git_sha(value: Any, *, field: str) -> str:
    text = _require_nonempty(value, field=field)
    if not _GIT_SHA_RE.fullmatch(text):
        raise VerdictIntegrityError(f"{field} must be a full lowercase git/image SHA")
    return text


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_fleet_actor_id(value: Any) -> bool:
    actor_id = str(value or "").strip()
    return bool(actor_id) and (
        actor_id.casefold() in {item.casefold() for item in FLEET_ACTOR_IDS}
        or _FLEET_ACTOR_ID_RE.match(actor_id) is not None
    )


def _first_symlink_component(path: Path) -> Path | None:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                return current
            if not current.exists():
                return None
        except OSError:
            return current
    return None


def _validate_protected_path(path: Path, *, forbidden_roots: Sequence[Path]) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise VerdictIntegrityError("protected ledger path must be absolute")
    absolute = expanded.absolute()
    symlink = _first_symlink_component(absolute)
    if symlink is not None or absolute.is_symlink():
        raise VerdictIntegrityError(
            f"protected ledger path cannot contain a symlink: {symlink or absolute}"
        )
    for root in forbidden_roots:
        resolved_root = root.expanduser().absolute()
        if _inside(absolute, resolved_root):
            raise VerdictIntegrityError(
                f"protected ledger must be outside candidate-controlled root {resolved_root}"
            )
    return absolute


@dataclass(frozen=True)
class CloseoutBinding:
    program_id: str
    catalog_sha256: str
    task_id: str
    closeout_manifest_sha256: str
    target_environment: str
    frontend_sha: str
    bff_sha: str

    def to_dict(self) -> dict[str, str]:
        payload = {
            "program_id": _require_nonempty(self.program_id, field="program_id"),
            "catalog_sha256": _require_sha256(
                self.catalog_sha256, field="catalog_sha256"
            ),
            "task_id": _require_nonempty(self.task_id, field="task_id"),
            "closeout_manifest_sha256": _require_sha256(
                self.closeout_manifest_sha256,
                field="closeout_manifest_sha256",
            ),
            "target_environment": _require_nonempty(
                self.target_environment, field="target_environment"
            ),
            "frontend_sha": _require_git_sha(self.frontend_sha, field="frontend_sha"),
            "bff_sha": _require_git_sha(self.bff_sha, field="bff_sha"),
        }
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CloseoutBinding":
        missing = [field for field in _EXPECTED_BINDING_FIELDS if field not in value]
        if missing:
            raise VerdictIntegrityError(
                "closeout binding is missing fields: " + ", ".join(missing)
            )
        binding = cls(**{field: str(value[field]) for field in _EXPECTED_BINDING_FIELDS})
        binding.to_dict()
        return binding


@dataclass(frozen=True)
class HumanOpsIdentity:
    actor_id: str
    actor_role: str
    authenticated: bool
    mfa_verified: bool
    principal_type: str = "human"

    def authorize(self) -> None:
        actor_id = self.actor_id.strip()
        role = self.actor_role.strip().lower()
        principal_type = self.principal_type.strip().lower()
        if self.authenticated is not True:
            raise VerdictAuthorizationError(
                "product closeout requires an authenticated principal"
            )
        if principal_type != "human":
            raise VerdictAuthorizationError(
                "service, workload, fleet, and automation principals cannot issue verdicts"
            )
        if self.mfa_verified is not True:
            raise VerdictAuthorizationError(
                "product closeout verdict issuance requires verified MFA"
            )
        if not actor_id or _is_fleet_actor_id(actor_id):
            raise VerdictAuthorizationError(
                "fleet actors cannot issue or revoke Human/Ops verdicts"
            )
        if role not in AUTHORIZED_ACTOR_ROLES:
            raise VerdictAuthorizationError(
                f"actor role {self.actor_role!r} is not Human/Ops-authorized"
            )


@dataclass(frozen=True)
class VerdictPolicy:
    policy_version: str
    public_keys: Mapping[str, bytes]
    max_verdict_age_seconds: int = DEFAULT_MAX_VERDICT_AGE_SECONDS
    max_ttl_seconds: int = DEFAULT_MAX_TTL_SECONDS

    def __post_init__(self) -> None:
        _require_crypto()
        _require_nonempty(self.policy_version, field="policy_version")
        if not self.public_keys:
            raise VerdictIntegrityError("verdict policy requires at least one public key")
        if self.max_verdict_age_seconds <= 0 or self.max_ttl_seconds <= 0:
            raise VerdictIntegrityError("verdict age and TTL limits must be positive")
        for key_id, key_bytes in self.public_keys.items():
            _require_nonempty(key_id, field="key_id")
            try:
                Ed25519PublicKey.from_public_bytes(key_bytes)
            except (ValueError, TypeError) as exc:
                raise VerdictIntegrityError(
                    f"trusted key {key_id!r} is not an Ed25519 public key"
                ) from exc

    def capability_sha256(self, key_id: str) -> str:
        key_bytes = self.public_keys.get(key_id)
        if key_bytes is None:
            raise VerdictIntegrityError(f"untrusted verdict key_id: {key_id}")
        capability = {
            "signature_algorithm": SIGNATURE_ALGORITHM,
            "key_id": key_id,
            "policy_version": self.policy_version,
            "public_key": _b64url_encode(key_bytes),
        }
        return canonical_json_sha256(capability)


class ProtectedVerdictLedger:
    """Append-only, hash-chained verdict ledger with process-safe locking."""

    def __init__(
        self,
        path: Path | str,
        *,
        forbidden_roots: Sequence[Path] = (),
    ) -> None:
        self.path = _validate_protected_path(
            Path(path), forbidden_roots=forbidden_roots
        )
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for candidate in (self.path, self.lock_path):
            symlink = _first_symlink_component(candidate)
            if symlink is not None or candidate.is_symlink():
                raise VerdictIntegrityError(
                    f"protected ledger path became unsafe: {symlink or candidate}"
                )

    @contextmanager
    def transaction(self) -> Iterator[list[dict[str, Any]]]:
        self._ensure_parent()
        flags = (
            os.O_APPEND
            | os.O_CREAT
            | os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            lock_fd = os.open(self.lock_path, flags, 0o600)
        except OSError as exc:
            raise VerdictIntegrityError(
                "cannot open protected verdict ledger lock"
            ) from exc
        with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield self._read_records_unlocked()
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def records(self) -> list[dict[str, Any]]:
        with self.transaction() as records:
            return [dict(record) for record in records]

    def _read_records_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        previous_hash = _ZERO_HASH
        record_ids: set[str] = set()
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            ledger_fd = os.open(self.path, flags)
            if not stat.S_ISREG(os.fstat(ledger_fd).st_mode):
                os.close(ledger_fd)
                raise VerdictIntegrityError(
                    "protected verdict ledger is not a regular file"
                )
        except VerdictIntegrityError:
            raise
        except OSError as exc:
            raise VerdictIntegrityError(
                "cannot open protected verdict ledger"
            ) from exc
        with os.fdopen(ledger_fd, encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    raise VerdictIntegrityError(
                        f"blank protected-ledger row at line {line_number}"
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise VerdictIntegrityError(
                        f"invalid protected-ledger JSON at line {line_number}"
                    ) from exc
                if not isinstance(record, dict):
                    raise VerdictIntegrityError(
                        f"protected-ledger row {line_number} is not an object"
                    )
                record_hash = _require_sha256(
                    record.get("record_hash"), field="record_hash"
                )
                if record.get("previous_hash") != previous_hash:
                    raise VerdictIntegrityError(
                        f"protected-ledger chain mismatch at line {line_number}"
                    )
                unsigned = dict(record)
                unsigned.pop("record_hash", None)
                if canonical_json_sha256(unsigned) != record_hash:
                    raise VerdictIntegrityError(
                        f"protected-ledger record hash mismatch at line {line_number}"
                    )
                record_id = _require_nonempty(
                    record.get("record_id"), field="record_id"
                )
                if record_id in record_ids:
                    raise VerdictIntegrityError(
                        f"duplicate protected-ledger record_id {record_id}"
                    )
                record_ids.add(record_id)
                records.append(record)
                previous_hash = record_hash
        return records

    def append_unlocked(
        self,
        records: list[dict[str, Any]],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = dict(payload)
        record["previous_hash"] = (
            records[-1]["record_hash"] if records else _ZERO_HASH
        )
        record["record_hash"] = canonical_json_sha256(record)
        encoded = _canonical_json(record) + b"\n"
        flags = (
            os.O_APPEND
            | os.O_CREAT
            | os.O_WRONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise VerdictIntegrityError(
                "cannot append protected verdict ledger"
            ) from exc
        try:
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise VerdictIntegrityError(
                        "short write while appending protected verdict ledger"
                    )
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            parent_fd = os.open(
                self.path.parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError:
            parent_fd = None
        if parent_fd is not None:
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        records.append(record)
        return record


def _verdict_payload_for_signature(verdict: Mapping[str, Any]) -> bytes:
    if set(verdict) != set(VERDICT_BINDING_FIELDS):
        missing = sorted(set(VERDICT_BINDING_FIELDS) - set(verdict))
        extra = sorted(set(verdict) - set(VERDICT_BINDING_FIELDS))
        raise VerdictIntegrityError(
            f"verdict fields are not exact: missing={missing} extra={extra}"
        )
    return _canonical_json({field: verdict[field] for field in _SIGNED_FIELDS})


def public_key_bytes(private_key: Ed25519PrivateKey) -> bytes:
    _require_crypto()
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def private_key_from_base64(value: str) -> Ed25519PrivateKey:
    _require_crypto()
    raw = _b64url_decode(value)
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (ValueError, TypeError) as exc:
        raise VerdictIntegrityError("invalid Ed25519 private key material") from exc


def generate_private_key() -> Ed25519PrivateKey:
    _require_crypto()
    return Ed25519PrivateKey.generate()


class ProductCloseoutVerdictService:
    """Issue, revoke, inspect, verify, and consume protected verdicts."""

    def __init__(
        self,
        *,
        ledger: ProtectedVerdictLedger,
        policy: VerdictPolicy,
        private_key: Ed25519PrivateKey | None = None,
        key_id: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.ledger = ledger
        self.policy = policy
        self.private_key = private_key
        self.key_id = key_id
        self.clock = clock
        if private_key is not None:
            if not key_id or key_id not in policy.public_keys:
                raise VerdictIntegrityError(
                    "issuer key_id must name a trusted policy public key"
                )
            if public_key_bytes(private_key) != policy.public_keys[key_id]:
                raise VerdictIntegrityError(
                    "issuer private key does not match trusted policy key"
                )

    def issue(
        self,
        binding: CloseoutBinding,
        identity: HumanOpsIdentity,
        *,
        decision: str,
        ttl_seconds: int = 900,
        verdict_id: str | None = None,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        identity.authorize()
        if self.private_key is None or not self.key_id:
            raise VerdictAuthorizationError(
                "this verifier instance has no protected issuer capability"
            )
        normalized_decision = decision.strip().lower()
        if normalized_decision not in {"approved", "rejected"}:
            raise VerdictIntegrityError("decision must be approved or rejected")
        if ttl_seconds <= 0 or ttl_seconds > self.policy.max_ttl_seconds:
            raise VerdictIntegrityError(
                f"verdict TTL must be between 1 and {self.policy.max_ttl_seconds}"
            )
        now = self.clock().astimezone(UTC).replace(microsecond=0)
        issued_at = _format_time(now)
        resolved_verdict_id = verdict_id or f"pclose-{uuid.uuid4()}"
        resolved_nonce = nonce or uuid.uuid4().hex
        ledger_entry_id = f"pclose-issue-{uuid.uuid4()}"

        binding_values = binding.to_dict()
        with self.ledger.transaction() as records:
            for record in records:
                issued = record.get("verdict") if record.get("record_type") == "issued" else None
                if not isinstance(issued, dict):
                    continue
                if issued.get("verdict_id") == resolved_verdict_id:
                    raise VerdictConflictError(
                        f"verdict_id already exists: {resolved_verdict_id}"
                    )
                if issued.get("nonce") == resolved_nonce:
                    raise VerdictConflictError("verdict nonce has already been used")

            active = self._active_binding_decision(
                records,
                self._binding_key(binding_values),
                now=now,
            )
            if active is not None:
                raise VerdictConflictError(
                    "an active closeout decision already exists for this binding: "
                    f"{active.get('verdict_id')} ({active.get('decision')}); "
                    "revoke it or let it expire before issuing a replacement"
                )

            verdict: dict[str, Any] = {
                **binding_values,
                "verdict_id": resolved_verdict_id,
                "verifier_capability_sha256": self.policy.capability_sha256(
                    self.key_id
                ),
                "signature_algorithm": SIGNATURE_ALGORITHM,
                "key_id": self.key_id,
                "policy_version": self.policy.policy_version,
                "signature": "",
                "revocation_checked_at": issued_at,
                "ledger_entry_id": ledger_entry_id,
                "actor_id": identity.actor_id.strip(),
                "actor_role": identity.actor_role.strip().lower(),
                "decision": normalized_decision,
                "issued_at": issued_at,
                "expires_at": _format_time(now + timedelta(seconds=ttl_seconds)),
                "nonce": resolved_nonce,
            }
            signature = self.private_key.sign(_verdict_payload_for_signature(verdict))
            verdict["signature"] = _b64url_encode(signature)
            self.ledger.append_unlocked(
                records,
                {
                    "record_type": "issued",
                    "record_id": ledger_entry_id,
                    "recorded_at": issued_at,
                    "verdict": verdict,
                },
            )
        return dict(verdict)

    def revoke(
        self,
        verdict_id: str,
        identity: HumanOpsIdentity,
        *,
        reason: str,
    ) -> dict[str, Any]:
        identity.authorize()
        reason_text = _require_nonempty(reason, field="revocation reason")
        now = _format_time(self.clock())
        with self.ledger.transaction() as records:
            verdict = self._issued_verdict(records, verdict_id)
            if any(
                record.get("record_type") == "revoked"
                and record.get("verdict_id") == verdict_id
                for record in records
            ):
                raise VerdictConflictError(f"verdict is already revoked: {verdict_id}")
            if self._consumption_records(records, verdict_id):
                raise VerdictConflictError(
                    "a consumed verdict cannot be retroactively revoked"
                )
            record = self.ledger.append_unlocked(
                records,
                {
                    "record_type": "revoked",
                    "record_id": f"pclose-revoke-{uuid.uuid4()}",
                    "recorded_at": now,
                    "verdict_id": verdict["verdict_id"],
                    "ledger_entry_id": verdict["ledger_entry_id"],
                    "actor_id": identity.actor_id.strip(),
                    "actor_role": identity.actor_role.strip().lower(),
                    "reason": reason_text,
                },
            )
        return dict(record)

    def inspect(self, verdict_id: str) -> dict[str, Any]:
        with self.ledger.transaction() as records:
            verdict = self._issued_verdict(records, verdict_id)
            return {
                "verdict": dict(verdict),
                "revoked": any(
                    record.get("record_type") == "revoked"
                    and record.get("verdict_id") == verdict_id
                    for record in records
                ),
                "consumption_count": len(
                    self._consumption_records(records, verdict_id)
                ),
            }

    def verify(
        self,
        verdict_id: str,
        *,
        expected_binding: CloseoutBinding,
        consumption_state: str = "unconsumed",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        with self.ledger.transaction() as records:
            verdict = self._issued_verdict(records, verdict_id)
            self._verify_unlocked(
                records,
                verdict,
                expected_binding=expected_binding,
                consumption_state=consumption_state,
                now=now,
            )
            return dict(verdict)

    def consume(
        self,
        verdict_id: str,
        *,
        expected_binding: CloseoutBinding,
        transition: str,
        transition_actor: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if transition != "done":
            raise VerdictIntegrityError("protected verdict may only be consumed by done")
        actor = _require_nonempty(transition_actor, field="transition_actor")
        retry_key = _require_nonempty(
            idempotency_key,
            field="consumption idempotency_key",
        )
        checked_at = (now or self.clock()).astimezone(UTC)
        with self.ledger.transaction() as records:
            verdict = self._issued_verdict(records, verdict_id)
            existing = self._consumption_records(records, verdict_id)
            if existing:
                if len(existing) != 1:
                    raise VerdictReplayError(
                        "verdict has multiple consumption records"
                    )
                record = existing[0]
                if (
                    record.get("idempotency_key") != retry_key
                    or record.get("transition") != transition
                    or record.get("transition_actor") != actor
                    or record.get("task_id") != verdict.get("task_id")
                    or record.get("ledger_entry_id")
                    != verdict.get("ledger_entry_id")
                ):
                    raise VerdictReplayError(
                        "verdict was consumed by a different transition attempt"
                    )
                self._verify_unlocked(
                    records,
                    verdict,
                    expected_binding=expected_binding,
                    consumption_state="consumed",
                    now=checked_at,
                )
                return dict(record)
            self._verify_unlocked(
                records,
                verdict,
                expected_binding=expected_binding,
                consumption_state="unconsumed",
                now=checked_at,
            )
            record = self.ledger.append_unlocked(
                records,
                {
                    "record_type": "consumed",
                    "record_id": f"pclose-consume-{uuid.uuid4()}",
                    "recorded_at": _format_time(checked_at),
                    "verdict_id": verdict_id,
                    "ledger_entry_id": verdict["ledger_entry_id"],
                    "task_id": verdict["task_id"],
                    "transition": transition,
                    "transition_actor": actor,
                    "idempotency_key": retry_key,
                },
            )
        return dict(record)

    def consumption_record(self, verdict_id: str) -> dict[str, Any]:
        """Return the one durable consumption record for an issued verdict."""

        with self.ledger.transaction() as records:
            self._issued_verdict(records, verdict_id)
            consumptions = self._consumption_records(records, verdict_id)
            if len(consumptions) != 1:
                raise VerdictReplayError(
                    "protected verdict must have exactly one consumption record"
                )
            return dict(consumptions[0])

    @staticmethod
    def _issued_verdict(
        records: Sequence[Mapping[str, Any]], verdict_id: str
    ) -> dict[str, Any]:
        matches = [
            record.get("verdict")
            for record in records
            if record.get("record_type") == "issued"
            and isinstance(record.get("verdict"), dict)
            and record["verdict"].get("verdict_id") == verdict_id
        ]
        if len(matches) != 1:
            raise VerdictIntegrityError(
                f"protected ledger must contain exactly one issue record for {verdict_id}"
            )
        return dict(matches[0])

    @staticmethod
    def _binding_key(value: Mapping[str, Any]) -> tuple[str, ...]:
        """Return the exact seven-field identity a decision is bound to."""

        return tuple(str(value.get(field, "")) for field in _EXPECTED_BINDING_FIELDS)

    @classmethod
    def _active_binding_decision(
        cls,
        records: Sequence[Mapping[str, Any]],
        binding_key: tuple[str, ...],
        *,
        now: datetime,
    ) -> Mapping[str, Any] | None:
        """Return the still-active decision for one exact binding, if any.

        A decision stops being active only through an explicit revocation or
        its own expiry, so a replacement verdict is admitted exactly in the
        revoked/expired cases and blocked while a decision still stands.  An
        unreadable expiry is treated as active so the guard fails closed.
        """

        revoked_ids = {
            record.get("verdict_id")
            for record in records
            if record.get("record_type") == "revoked"
        }
        for record in records:
            if record.get("record_type") != "issued":
                continue
            issued = record.get("verdict")
            if not isinstance(issued, dict):
                continue
            if cls._binding_key(issued) != binding_key:
                continue
            if issued.get("verdict_id") in revoked_ids:
                continue
            try:
                expires_at = _parse_time(issued.get("expires_at"), field="expires_at")
            except VerdictIntegrityError:
                return issued
            if expires_at > now:
                return issued
        return None

    @staticmethod
    def _consumption_records(
        records: Sequence[Mapping[str, Any]], verdict_id: str
    ) -> list[Mapping[str, Any]]:
        return [
            record
            for record in records
            if record.get("record_type") == "consumed"
            and record.get("verdict_id") == verdict_id
        ]

    def _verify_unlocked(
        self,
        records: Sequence[Mapping[str, Any]],
        verdict: Mapping[str, Any],
        *,
        expected_binding: CloseoutBinding,
        consumption_state: str,
        now: datetime | None,
    ) -> None:
        _verdict_payload_for_signature(verdict)
        for field in VERDICT_BINDING_FIELDS:
            _require_nonempty(verdict.get(field), field=field)
        if verdict.get("signature_algorithm") != SIGNATURE_ALGORITHM:
            raise VerdictIntegrityError("unsupported verdict signature algorithm")
        if verdict.get("policy_version") != self.policy.policy_version:
            raise VerdictIntegrityError("verdict policy version mismatch")
        key_id = str(verdict["key_id"])
        public_key_bytes_value = self.policy.public_keys.get(key_id)
        if public_key_bytes_value is None:
            raise VerdictIntegrityError(f"untrusted verdict key_id: {key_id}")
        if (
            verdict.get("verifier_capability_sha256")
            != self.policy.capability_sha256(key_id)
        ):
            raise VerdictIntegrityError("verifier capability digest mismatch")
        try:
            Ed25519PublicKey.from_public_bytes(public_key_bytes_value).verify(
                _b64url_decode(str(verdict["signature"])),
                _verdict_payload_for_signature(verdict),
            )
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise VerdictIntegrityError("verdict signature verification failed") from exc

        expected = expected_binding.to_dict()
        for field, expected_value in expected.items():
            if verdict.get(field) != expected_value:
                raise VerdictIntegrityError(
                    f"verdict binding mismatch for {field}: "
                    f"expected {expected_value}, got {verdict.get(field)}"
                )
        if verdict.get("decision") != "approved":
            raise VerdictIntegrityError(
                f"closeout verdict decision is not approved: {verdict.get('decision')}"
            )
        if verdict.get("actor_role") not in AUTHORIZED_ACTOR_ROLES:
            raise VerdictAuthorizationError("signed verdict actor role is not authorized")
        if _is_fleet_actor_id(verdict.get("actor_id")):
            raise VerdictAuthorizationError("fleet-issued verdict is forbidden")

        issue_records = [
            record
            for record in records
            if record.get("record_type") == "issued"
            and record.get("record_id") == verdict.get("ledger_entry_id")
        ]
        if len(issue_records) != 1 or issue_records[0].get("verdict") != verdict:
            raise VerdictIntegrityError("verdict is not bound to its protected issue record")
        if any(
            record.get("record_type") == "revoked"
            and record.get("verdict_id") == verdict.get("verdict_id")
            for record in records
        ):
            raise VerdictRevokedError(f"verdict is revoked: {verdict['verdict_id']}")

        checked_at = (now or self.clock()).astimezone(UTC)
        issued_at = _parse_time(verdict.get("issued_at"), field="issued_at")
        expires_at = _parse_time(verdict.get("expires_at"), field="expires_at")
        revocation_checked_at = _parse_time(
            verdict.get("revocation_checked_at"), field="revocation_checked_at"
        )

        consumptions = self._consumption_records(records, str(verdict["verdict_id"]))
        if len(consumptions) > 1:
            raise VerdictReplayError("verdict has multiple consumption records")
        freshness_checked_at = checked_at
        if consumption_state == "unconsumed":
            if consumptions:
                raise VerdictReplayError("verdict has already been consumed")
        elif consumption_state == "consumed":
            if len(consumptions) != 1:
                raise VerdictReplayError(
                    "done closeout requires exactly one verdict consumption"
                )
            consumption = consumptions[0]
            if (
                consumption.get("task_id") != verdict.get("task_id")
                or consumption.get("transition") != "done"
                or consumption.get("ledger_entry_id")
                != verdict.get("ledger_entry_id")
                or not str(consumption.get("transition_actor") or "").strip()
                or not str(consumption.get("idempotency_key") or "").strip()
            ):
                raise VerdictReplayError("verdict consumption binding is invalid")
            consumed_at = _parse_time(
                consumption.get("recorded_at"),
                field="consumption recorded_at",
            )
            if consumed_at > checked_at + timedelta(seconds=5):
                raise VerdictReplayError(
                    "verdict consumption is in the future"
                )
            freshness_checked_at = consumed_at
        else:
            raise VerdictIntegrityError(
                f"unknown verdict consumption state: {consumption_state}"
            )

        if issued_at > freshness_checked_at + timedelta(seconds=5):
            raise VerdictExpiredError("verdict issued_at is in the future")
        if expires_at <= freshness_checked_at:
            raise VerdictExpiredError("verdict has expired")
        if expires_at <= issued_at:
            raise VerdictExpiredError("verdict expiry must follow issuance")
        if expires_at - issued_at > timedelta(seconds=self.policy.max_ttl_seconds):
            raise VerdictExpiredError("verdict TTL exceeds policy")
        if freshness_checked_at - issued_at > timedelta(
            seconds=self.policy.max_verdict_age_seconds
        ):
            raise VerdictExpiredError("verdict is stale")
        if (
            revocation_checked_at < issued_at
            or revocation_checked_at
            > freshness_checked_at + timedelta(seconds=5)
        ):
            raise VerdictIntegrityError("revocation_checked_at is invalid")


def policy_from_mapping(value: Mapping[str, Any]) -> VerdictPolicy:
    if value.get("schema_version") != 1:
        raise VerdictIntegrityError("verdict policy schema_version must be 1")
    keys = value.get("public_keys")
    if not isinstance(keys, dict) or not keys:
        raise VerdictIntegrityError("verdict policy public_keys must be an object")
    decoded: dict[str, bytes] = {}
    for key_id, encoded in keys.items():
        if not isinstance(encoded, str):
            raise VerdictIntegrityError("verdict public key must be base64url text")
        decoded[str(key_id)] = _b64url_decode(encoded)
    return VerdictPolicy(
        policy_version=str(value.get("policy_version") or ""),
        public_keys=decoded,
        max_verdict_age_seconds=int(
            value.get("max_verdict_age_seconds")
            or DEFAULT_MAX_VERDICT_AGE_SECONDS
        ),
        max_ttl_seconds=int(value.get("max_ttl_seconds") or DEFAULT_MAX_TTL_SECONDS),
    )


def load_verifier_service(
    *,
    policy_path: Path | str = DEFAULT_PROTECTED_POLICY_PATH,
    forbidden_roots: Sequence[Path] = (),
) -> ProductCloseoutVerdictService:
    """Load a verification-only service from the protected external policy.

    The policy file contains public verification material and the external
    ledger path.  Both paths must be absolute, regular, symlink-free, and
    outside every candidate-controlled repository root supplied by the caller.
    Private signing material is intentionally not accepted here.
    """

    resolved_policy_path = _validate_protected_path(
        Path(policy_path), forbidden_roots=forbidden_roots
    )
    if not resolved_policy_path.is_file() or resolved_policy_path.is_symlink():
        raise VerdictIntegrityError(
            f"protected verdict policy is missing or not regular: {resolved_policy_path}"
        )
    try:
        value = json.loads(resolved_policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerdictIntegrityError("cannot read protected verdict policy") from exc
    if not isinstance(value, dict):
        raise VerdictIntegrityError("protected verdict policy root must be an object")
    ledger_path = value.get("ledger_path")
    if not isinstance(ledger_path, str) or not ledger_path.strip():
        raise VerdictIntegrityError(
            "protected verdict policy ledger_path must be an absolute path"
        )
    policy = policy_from_mapping(value)
    ledger = ProtectedVerdictLedger(
        ledger_path,
        forbidden_roots=forbidden_roots,
    )
    return ProductCloseoutVerdictService(ledger=ledger, policy=policy)


def public_key_to_base64(public_key: bytes) -> str:
    return _b64url_encode(public_key)
