"""Personal data handling and host-local HMAC author hashing (SD §6.4)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_SALT_PATH = Path("/run/secrets/author_hmac_salt")
MIN_SALT_BYTES = 16

FORBIDDEN_PII_FIELDS = frozenset(
    {
        "author",
        "author_handle",
        "author_name",
        "author_username",
        "avatar",
        "avatar_url",
        "bio",
        "display_name",
        "follower_count_list",
        "follower_list",
        "followers",
        "following",
        "full_name",
        "handle",
        "nickname",
        "profile_html",
        "profile_page",
        "profile_pic_url",
        "profile_url",
        "raw_author",
        "screen_name",
        "user_handle",
        "username",
    }
)

_PLACEHOLDER_SALTS = frozenset(
    {
        b"changeme",
        b"placeholder",
        b"default",
        b"0" * 32,
        b"example-salt-do-not-use-in-prod",
    }
)


class PrivacyPolicyError(ValueError):
    """Raised when an item or raw artifact violates SD §6.4 PDPA controls."""


@dataclass(frozen=True)
class AuthorHasher:
    """HMAC-SHA256 hasher using a host-local salt that never leaves the collector host."""

    _salt: bytes

    def __post_init__(self) -> None:
        if not isinstance(self._salt, (bytes, bytearray)):
            raise PrivacyPolicyError("HMAC salt must be bytes")
        stripped = bytes(self._salt).strip()
        if len(stripped) < MIN_SALT_BYTES:
            raise PrivacyPolicyError(
                f"host-local HMAC salt must be at least {MIN_SALT_BYTES} bytes"
            )
        if stripped.lower() in _PLACEHOLDER_SALTS:
            raise PrivacyPolicyError("host-local HMAC salt must not be a placeholder value")
        object.__setattr__(self, "_salt", stripped)

    @classmethod
    def from_file(cls, path: Path | str | None = None) -> "AuthorHasher":
        resolved = Path(
            path
            or os.environ.get("COLLECTOR_AUTHOR_HMAC_SALT_FILE")
            or DEFAULT_SALT_PATH
        )
        if not resolved.is_file():
            raise PrivacyPolicyError(f"host-local HMAC salt file does not exist: {resolved}")
        return cls(resolved.read_bytes())

    def hash_author(self, platform: str, handle: str) -> str:
        norm_platform = str(platform or "").strip().lower()
        norm_handle = str(handle or "").strip().lstrip("@").lower()
        if not norm_platform:
            raise PrivacyPolicyError("platform is required for author HMAC hashing")
        if not norm_handle:
            raise PrivacyPolicyError("author handle is required for author HMAC hashing")
        message = f"{norm_platform}+{norm_handle}".encode("utf-8")
        return hmac.new(self._salt, message, hashlib.sha256).hexdigest()


def scrub_item_pii(item: Mapping[str, Any]) -> dict[str, Any]:
    """Remove any forbidden PII/display-name/profile fields from an item dictionary."""

    cleaned: dict[str, Any] = {}
    for key, value in item.items():
        if str(key).strip().lower() in FORBIDDEN_PII_FIELDS:
            continue
        if isinstance(value, Mapping):
            cleaned[key] = scrub_item_pii(value)
        elif isinstance(value, list):
            cleaned[key] = [
                scrub_item_pii(v) if isinstance(v, Mapping) else v for v in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def scrub_raw_html_or_json(
    raw_bytes: bytes,
    *,
    platform: str,
    hasher: AuthorHasher,
    handles: Sequence[str] = (),
    display_names: Sequence[str] = (),
) -> bytes:
    """Replace raw author handles with their HMAC and strip display names / profile blocks before disk write."""

    text = raw_bytes.decode("utf-8", errors="replace")

    # Strip any embedded profile-header / follower-list / avatar / display-name HTML elements
    text = re.sub(
        r"<(?:div|section|aside)[^>]*\b(?:class|data-testid)=[\"'][^\"']*(?:profile-page|profile-header|follower-list|user-avatar|display-name)[^\"']*[\"'][^>]*>.*?</(?:div|section|aside)>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<span[^>]*\bclass=[\"'][^\"']*(?:display-name|author-nickname|user-fullname|push-userid-name)[^\"']*[\"'][^>]*>.*?</span>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<img[^>]*\bclass=[\"'][^\"']*(?:avatar|profile-pic)[^\"']*[\"'][^>]*/?>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for display_name in display_names:
        dn = str(display_name or "").strip()
        if dn:
            text = text.replace(dn, "[REDACTED_DISPLAY_NAME]")

    for raw_handle in handles:
        clean_handle = str(raw_handle or "").strip().lstrip("@")
        if not clean_handle:
            continue
        digest = hasher.hash_author(platform, clean_handle)
        text = text.replace(f"@{clean_handle}", f"hmac:{digest}")
        text = re.sub(
            rf"\b{re.escape(clean_handle)}\b",
            f"hmac:{digest}",
            text,
        )

    return text.encode("utf-8")


def assert_privacy_compliant(
    item: Mapping[str, Any],
    *,
    raw_bytes: bytes | None = None,
    forbidden_literals: Sequence[str] = (),
    require_hashed_author: bool = False,
) -> None:
    """Fail closed if any forbidden PII key, unhashed handle, display name, or profile page remains."""

    def _walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, Mapping):
            for k, v in obj.items():
                key_lower = str(k).strip().lower()
                if key_lower in FORBIDDEN_PII_FIELDS:
                    raise PrivacyPolicyError(
                        f"forbidden personal-data field {path + '.' + str(k) if path else k!r} must never be stored"
                    )
                _walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(obj, list):
            for idx, elem in enumerate(obj):
                _walk(elem, f"{path}[{idx}]")

    _walk(item)

    if require_hashed_author:
        hashed_author = str(item.get("hashed_author") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", hashed_author):
            raise PrivacyPolicyError(
                f"hashed_author must be a 64-character hex HMAC-SHA256 digest, got {hashed_author!r}"
            )

    serialized = json.dumps(dict(item), ensure_ascii=False)
    for literal in forbidden_literals:
        token = str(literal or "").strip()
        if not token:
            continue
        if token in serialized:
            raise PrivacyPolicyError(
                f"unhashed personal data literal {token!r} leaked into item payload"
            )
        if raw_bytes is not None and token.encode("utf-8") in raw_bytes:
            raise PrivacyPolicyError(
                f"unhashed personal data literal {token!r} leaked into raw file bytes"
            )
