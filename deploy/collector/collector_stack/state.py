"""Per-source fetch state and consecutive 429/403/login-redirect auto-pause (SD §6.3)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

LOGIN_URL_INDICATORS = (
    "/accounts/login",
    "/login",
    "/signin",
    "/auth/login",
    "__coig_login=1",
    "is_from_rle",
    "service_login",
    "sso.cmoney.tw",
)


def classify_access_block(
    *,
    http_status: int,
    requested_url: str,
    final_url: str | None = None,
    location_header: str | None = None,
) -> str | None:
    """Return a block reason if the response is HTTP 429, 403, or a login redirect; else None."""

    for target in (final_url, location_header):
        if not target:
            continue
        lowered = str(target).strip().lower()
        parsed_path = urlparse(lowered).path
        if any(ind in lowered or parsed_path.startswith(ind) for ind in LOGIN_URL_INDICATORS):
            return f"login_redirect:{target}"

    if int(http_status) == 429:
        return f"http_429:{requested_url}"
    if int(http_status) == 403:
        return f"http_403:{requested_url}"
    if int(http_status) in {301, 302, 303, 307, 308}:
        target = str(final_url or location_header or "").strip().lower()
        if any(ind in target for ind in LOGIN_URL_INDICATORS):
            return f"login_redirect:{target}"

    return None


@dataclass
class SourceRuntimeState:
    source_id: str
    consecutive_blocks: int = 0
    paused: bool = False
    paused_reason: str | None = None
    paused_policy_hash: str | None = None
    last_run_id: str | None = None
    last_run_at: str | None = None
    last_status: str = "idle"
    block_history: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "consecutive_blocks": self.consecutive_blocks,
            "paused": self.paused,
            "paused_reason": self.paused_reason,
            "paused_policy_hash": self.paused_policy_hash,
            "last_run_id": self.last_run_id,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "block_history": list(self.block_history or []),
        }

    @classmethod
    def from_dict(cls, source_id: str, data: Mapping[str, Any]) -> "SourceRuntimeState":
        return cls(
            source_id=source_id,
            consecutive_blocks=int(data.get("consecutive_blocks") or 0),
            paused=bool(data.get("paused", False)),
            paused_reason=str(data["paused_reason"]) if data.get("paused_reason") else None,
            paused_policy_hash=(
                str(data["paused_policy_hash"]) if data.get("paused_policy_hash") else None
            ),
            last_run_id=str(data["last_run_id"]) if data.get("last_run_id") else None,
            last_run_at=str(data["last_run_at"]) if data.get("last_run_at") else None,
            last_status=str(data.get("last_status") or "idle"),
            block_history=[str(x) for x in (data.get("block_history") or [])],
        )


class CollectorStateStore:
    """Persists per-source auto-pause and quota state on the collector host."""

    def __init__(self, state_file: Path | str, *, auto_pause_threshold: int = 2) -> None:
        self.state_file = Path(state_file)
        self.auto_pause_threshold = auto_pause_threshold

    def _load_all(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {"sources": {}, "quotas": {}}
        try:
            loaded = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                loaded.setdefault("sources", {})
                loaded.setdefault("quotas", {})
                return loaded
        except ValueError:
            pass
        return {"sources": {}, "quotas": {}}

    def _save_all(self, data: Mapping[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.state_file.name}.", dir=str(self.state_file.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
            os.replace(tmp_name, self.state_file)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def get_source_state(
        self, source_id: str, *, current_policy_hash: str | None = None
    ) -> SourceRuntimeState:
        data = self._load_all()
        raw = data["sources"].get(source_id) or {}
        state = SourceRuntimeState.from_dict(source_id, raw)
        # Per SD §6.3: a paused source resumes only when its config (fetch_policy_hash) changes
        if (
            state.paused
            and current_policy_hash
            and state.paused_policy_hash
            and current_policy_hash != state.paused_policy_hash
        ):
            state.paused = False
            state.paused_reason = None
            state.paused_policy_hash = None
            state.consecutive_blocks = 0
            state.block_history = []
            state.last_status = "idle"
            self.save_source_state(state)
        return state

    def save_source_state(self, state: SourceRuntimeState) -> None:
        data = self._load_all()
        data["sources"][state.source_id] = state.to_dict()
        self._save_all(data)

    def record_run_outcome(
        self,
        *,
        source_id: str,
        run_id: str,
        finished_at: str,
        policy_hash: str,
        block_reason: str | None,
    ) -> SourceRuntimeState:
        state = self.get_source_state(source_id, current_policy_hash=policy_hash)
        state.last_run_id = run_id
        state.last_run_at = finished_at

        if block_reason is None:
            state.consecutive_blocks = 0
            state.block_history = []
            state.paused = False
            state.paused_reason = None
            state.paused_policy_hash = None
            state.last_status = "complete"
        else:
            history = list(state.block_history or [])
            history.append(block_reason)
            state.block_history = history
            state.consecutive_blocks += 1
            if state.consecutive_blocks >= self.auto_pause_threshold:
                state.paused = True
                state.paused_reason = (
                    f"auto_paused_after_{state.consecutive_blocks}_consecutive_blocks:"
                    + ";".join(history[-self.auto_pause_threshold :])
                )
                state.paused_policy_hash = policy_hash
                state.last_status = "paused"
            else:
                state.last_status = "partial"

        self.save_source_state(state)
        return state

    def get_daily_quota_usage(self, quota_key: str, date_str: str) -> int:
        data = self._load_all()
        return int((data.get("quotas", {}).get(quota_key) or {}).get(date_str) or 0)

    def add_daily_quota_usage(self, quota_key: str, date_str: str, units: int) -> int:
        data = self._load_all()
        quotas = data.setdefault("quotas", {})
        by_date = quotas.setdefault(quota_key, {})
        updated = int(by_date.get(date_str) or 0) + int(units)
        by_date[date_str] = updated
        self._save_all(data)
        return updated
