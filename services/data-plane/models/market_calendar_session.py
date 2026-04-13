"""MarketCalendarSession model.

Canonical definition of a market trading calendar session.
Based on Pantheon_Market_Data_Scope_and_Source_Plan_v1 §6.3
"""

from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone
import json
import re


TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class MarketCalendarSession:
    """Market trading calendar session.

    Attributes:
        market: Market code (e.g., "US", "TW", "CRYPTO").
        trade_date: Trading date in ISO 8601 format (e.g., "2026-04-13").
        session_open: Session open time in HH:MM:SS format (market local).
        session_close: Session close time in HH:MM:SS format (market local).
        early_close_flag: True if this session has an early close.
        holiday_flag: True if this date is a holiday (no trading).
        timezone: IANA timezone identifier (e.g., "America/New_York", "Asia/Taipei").
        metadata_json: Free-form session-specific metadata (e.g., pre/post-market flags).
        created_at: ISO 8601 timestamp of record creation.
    """
    market: str
    trade_date: str
    session_open: str
    session_close: str
    timezone: str
    early_close_flag: bool = False
    holiday_flag: bool = False
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "trade_date": self.trade_date,
            "session_open": self.session_open,
            "session_close": self.session_close,
            "early_close_flag": self.early_close_flag,
            "holiday_flag": self.holiday_flag,
            "timezone": self.timezone,
            "metadata_json": self.metadata_json,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketCalendarSession":
        return cls(
            market=data["market"],
            trade_date=data["trade_date"],
            session_open=data["session_open"],
            session_close=data["session_close"],
            timezone=data["timezone"],
            early_close_flag=data.get("early_close_flag", False),
            holiday_flag=data.get("holiday_flag", False),
            metadata_json=data.get("metadata_json", {}),
            created_at=data.get("created_at") or _utc_now_iso(),
        )

    @staticmethod
    def validate(cal: "MarketCalendarSession") -> tuple[bool, list[str]]:
        errors = []
        if not cal.market:
            errors.append("Missing market")
        if not cal.trade_date:
            errors.append("Missing trade_date")
        if not cal.timezone:
            errors.append("Missing timezone")
        if cal.holiday_flag:
            if cal.session_open and not TIME_PATTERN.fullmatch(cal.session_open):
                errors.append(f"Invalid holiday session_open: {cal.session_open}")
            if cal.session_close and not TIME_PATTERN.fullmatch(cal.session_close):
                errors.append(f"Invalid holiday session_close: {cal.session_close}")
        else:
            if not cal.session_open:
                errors.append("Missing session_open")
            elif not TIME_PATTERN.fullmatch(cal.session_open):
                errors.append(f"Invalid session_open: {cal.session_open}")
            if not cal.session_close:
                errors.append("Missing session_close")
            elif not TIME_PATTERN.fullmatch(cal.session_close):
                errors.append(f"Invalid session_close: {cal.session_close}")
        return len(errors) == 0, errors
