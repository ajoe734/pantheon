"""ContractMaster model.

Canonical definition of a derivative contract identity.
Based on Pantheon_Market_Data_Scope_and_Source_Plan_v1 §6.2
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime, timezone
from enum import Enum
import json
import math


TAIFEX_STF_SPEC_URL = "https://www.taifex.com.tw/cht/2/sTF"
TAIFEX_STF_TICK_AMENDMENT = "台期交字第1150001529號函 (issued 2026-05-26, effective 2026-07-06)"
TAIFEX_STF_TICK_AMENDMENT_URL = (
    "https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/"
    "%E5%8F%B0%E6%9C%9F%E4%BA%A4%E5%AD%97%E7%AC%AC1150001529%E8%99%9F%E5%87%BD.pdf"
)
TAIFEX_STF_TICK_SIZE_SOURCE = (
    f"{TAIFEX_STF_SPEC_URL} ; {TAIFEX_STF_TICK_AMENDMENT} {TAIFEX_STF_TICK_AMENDMENT_URL}"
)


class ContractType(str, Enum):
    """Derivative contract types."""
    FUTURE = "future"
    OPTION = "option"
    FUTURE_OPTION = "future_option"
    SWAP = "swap"
    FORWARD = "forward"
    SPREAD = "spread"


class OptionRight(str, Enum):
    """Option direction."""
    CALL = "call"
    PUT = "put"


class SettlementType(str, Enum):
    """Settlement mechanism."""
    CASH = "cash"
    PHYSICAL = "physical"


class MarginType(str, Enum):
    """Margin calculation method."""
    PORTFOLIO = "portfolio"
    STRAT_SCAN = "strat_scan"
    FIXED = "fixed"
    NONE = "none"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ContractMaster:
    """Canonical derivative contract identity.

    Attributes:
        contract_id: Globally unique identifier (e.g., "CON-US-ES-202606").
        underlying_id: Reference to the underlying SecurityMaster security_id.
        market: Market code (e.g., "US", "TW", "CRYPTO").
        venue: Primary venue / exchange code (e.g., "CME", "TAIFEX").
        contract_type: One of the ContractType enum values.
        expiry: Contract expiry date in ISO 8601 format.
        strike: Strike price for options; null for futures/forwards.
        option_right: CALL or PUT for options; null otherwise.
        multiplier: Contract multiplier (e.g., 50 for ES futures).
        tick_size: Minimum price increment.
        settlement_type: Cash or physical settlement.
        margin_type: Margin calculation method.
        metadata_json: Free-form venue or contract-specific metadata.
        created_at: ISO 8601 timestamp of record creation.
        updated_at: ISO 8601 timestamp of last update.
    """
    contract_id: str
    underlying_id: str
    market: str
    venue: str
    contract_type: str
    expiry: str
    multiplier: float
    tick_size: float
    settlement_type: str = SettlementType.CASH.value
    margin_type: str = MarginType.NONE.value
    strike: Optional[float] = None
    option_right: Optional[str] = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "underlying_id": self.underlying_id,
            "market": self.market,
            "venue": self.venue,
            "contract_type": self.contract_type,
            "expiry": self.expiry,
            "strike": self.strike,
            "option_right": self.option_right,
            "multiplier": self.multiplier,
            "tick_size": self.tick_size,
            "settlement_type": self.settlement_type,
            "margin_type": self.margin_type,
            "metadata_json": self.metadata_json,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContractMaster":
        return cls(
            contract_id=data["contract_id"],
            underlying_id=data["underlying_id"],
            market=data["market"],
            venue=data["venue"],
            contract_type=data["contract_type"],
            expiry=data["expiry"],
            multiplier=data["multiplier"],
            tick_size=data["tick_size"],
            strike=data.get("strike"),
            option_right=data.get("option_right"),
            settlement_type=data.get("settlement_type", SettlementType.CASH.value),
            margin_type=data.get("margin_type", MarginType.NONE.value),
            metadata_json=data.get("metadata_json", {}),
            created_at=data.get("created_at") or _utc_now_iso(),
            updated_at=data.get("updated_at") or _utc_now_iso(),
        )

    @staticmethod
    def ssf_tick_size(underlying_kind: Any, price: Any) -> float:
        """Minimum price fluctuation for TAIFEX single-stock / ETF futures (sTF).

        Official specification: https://www.taifex.com.tw/cht/2/sTF (amended by
        台期交字第1150001529號函, issued 2026-05-26, effective 2026-07-06).
        Stock underlying:
          < 10 -> 0.01; 10 <= p < 50 -> 0.05; 50 <= p < 100 -> 0.1;
          100 <= p < 500 -> 0.5; 500 <= p < 2500 -> 1.0; >= 2500 -> 5.0.
        ETF underlying:
          < 50 -> 0.01; >= 50 -> 0.05.
        """
        if price is None or isinstance(price, bool):
            raise ValueError("settlement_price is required to derive TAIFEX SSF tick_size")
        try:
            numeric_price = float(price)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid settlement_price for TAIFEX SSF tick_size: {price!r}") from exc
        if not math.isfinite(numeric_price) or numeric_price <= 0:
            raise ValueError(f"settlement_price must be positive to derive TAIFEX SSF tick_size: {price!r}")
        kind = str(underlying_kind or "").strip().lower()
        if kind == "etf":
            return 0.01 if numeric_price < 50.0 else 0.05
        if kind == "stock":
            if numeric_price < 10.0:
                return 0.01
            if numeric_price < 50.0:
                return 0.05
            if numeric_price < 100.0:
                return 0.1
            if numeric_price < 500.0:
                return 0.5
            if numeric_price < 2500.0:
                return 1.0
            return 5.0
        raise ValueError(f"Unsupported underlying_kind for TAIFEX SSF tick_size: {underlying_kind!r}")

    @classmethod
    def from_ssf_normalized_row(cls, row: dict[str, Any]) -> "ContractMaster":
        """Build the master record for one ``taifex_ssf_contracts`` normalized row.

        ``contract_id`` is ``ssf:<code>:<month>``; ``underlying_id`` is the
        normalised security code; ``multiplier`` is the contract unit. Rate-based
        margins are ``portfolio`` margin type, fixed amounts are ``fixed``. The
        margin figures, the latest regular session settlement price and its date
        go to ``metadata_json``. ``tick_size`` is derived from ``underlying_kind``
        and ``settlement_price`` per the official TAIFEX ``sTF`` price-tier rules
        (``https://www.taifex.com.tw/cht/2/sTF``, amended by 台期交字第1150001529號函
        issued 2026-05-26 / effective 2026-07-06) and recorded in ``tick_size_source``.
        """
        fixed = row.get("margin_mode") == "fixed"
        margin = (
            {
                "clearing_margin_amount": row.get("clearing_margin_amount"),
                "maintenance_margin_amount": row.get("maintenance_margin_amount"),
                "initial_margin_amount": row.get("initial_margin_amount"),
            }
            if fixed
            else {
                "margin_tier": row.get("margin_tier"),
                "clearing_margin_rate": row.get("clearing_margin_rate"),
                "maintenance_margin_rate": row.get("maintenance_margin_rate"),
                "initial_margin_rate": row.get("initial_margin_rate"),
            }
        )
        stamp = row.get("available_time") or _utc_now_iso()
        tick_size = cls.ssf_tick_size(row.get("underlying_kind"), row.get("settlement_price"))
        return cls(
            contract_id=f"ssf:{row['contract']}:{row['contract_month']}",
            underlying_id=str(row["underlying_code"]),
            market="TW",
            venue="TAIFEX",
            contract_type=ContractType.FUTURE.value,
            expiry=str(row["last_trading_day"]),
            multiplier=float(row["unit"]),
            tick_size=tick_size,
            settlement_type=SettlementType.CASH.value,
            margin_type=MarginType.FIXED.value if fixed else MarginType.PORTFOLIO.value,
            metadata_json={
                "dataset": "taifex_ssf_contracts",
                "contract": row["contract"],
                "contract_family": row.get("contract_family"),
                "contract_month": row["contract_month"],
                "contract_name": row.get("contract_name"),
                "underlying_kind": row.get("underlying_kind"),
                "margin_mode": row.get("margin_mode"),
                **margin,
                "settlement_price": row.get("settlement_price"),
                "settlement_price_date": row.get("price_date"),
                "margin_date": row.get("margin_date"),
                "volume": row.get("volume"),
                "open_interest": row.get("open_interest"),
                "last_trading_day_basis": row.get("last_trading_day_basis"),
                "tick_size_source": TAIFEX_STF_TICK_SIZE_SOURCE,
            },
            created_at=stamp,
            updated_at=stamp,
        )

    @classmethod
    def from_ssf_normalized_rows(cls, rows: Any) -> list["ContractMaster"]:
        """Latest record per ``contract_id`` (newest ``price_date``/``available_time``/``ingest_time`` wins)."""
        latest: dict[str, tuple[tuple[str, str, str, str, int], dict[str, Any]]] = {}
        for index, row in enumerate(rows):
            key = f"ssf:{row['contract']}:{row['contract_month']}"
            price_date = str(row.get("price_date") or row.get("date") or "")
            margin_date = str(row.get("margin_date") or price_date)
            avail = str(row.get("available_time") or "")
            ingest = str(row.get("ingest_time") or "")
            sort_key = (price_date, margin_date, avail, ingest, index)
            if key not in latest or sort_key >= latest[key][0]:
                latest[key] = (sort_key, row)
        return [cls.from_ssf_normalized_row(latest[key][1]) for key in sorted(latest)]

    @staticmethod
    def validate(contract: "ContractMaster") -> tuple[bool, list[str]]:
        errors = []
        if not contract.contract_id:
            errors.append("Missing contract_id")
        if not contract.underlying_id:
            errors.append("Missing underlying_id")
        if not contract.market:
            errors.append("Missing market")
        if not contract.venue:
            errors.append("Missing venue")
        if not contract.contract_type:
            errors.append("Missing contract_type")
        elif contract.contract_type not in {item.value for item in ContractType}:
            errors.append(f"Invalid contract_type: {contract.contract_type}")
        if not contract.expiry:
            errors.append("Missing expiry")
        if contract.multiplier is None or contract.multiplier <= 0:
            errors.append("Invalid multiplier: must be positive")
        if (
            contract.tick_size is None
            or isinstance(contract.tick_size, bool)
            or not math.isfinite(contract.tick_size)
            or contract.tick_size <= 0
        ):
            errors.append("Invalid tick_size: must be positive")
        elif isinstance(contract.metadata_json, dict) and contract.metadata_json.get("dataset") == "taifex_ssf_contracts":
            if contract.metadata_json.get("tick_size_source") in (None, "", "not_sourced"):
                errors.append("Invalid tick_size_source for TAIFEX SSF contract: official source required")
            try:
                expected_tick = ContractMaster.ssf_tick_size(
                    contract.metadata_json.get("underlying_kind"),
                    contract.metadata_json.get("settlement_price"),
                )
            except ValueError as exc:
                errors.append(f"Invalid TAIFEX SSF tick_size inputs: {exc}")
            else:
                if contract.tick_size != expected_tick:
                    errors.append(
                        f"Invalid tick_size for TAIFEX SSF contract: got {contract.tick_size}, expected {expected_tick}"
                    )
        if contract.option_right is not None and contract.option_right not in {item.value for item in OptionRight}:
            errors.append(f"Invalid option_right: {contract.option_right}")
        if contract.settlement_type not in {item.value for item in SettlementType}:
            errors.append(f"Invalid settlement_type: {contract.settlement_type}")
        if contract.margin_type not in {item.value for item in MarginType}:
            errors.append(f"Invalid margin_type: {contract.margin_type}")
        # Options must have strike and option_right
        if contract.contract_type in ("option", "future_option"):
            if contract.strike is None:
                errors.append("Options must have strike price")
            if contract.option_right is None:
                errors.append("Options must have option_right (call/put)")
        return len(errors) == 0, errors
