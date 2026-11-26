"""Read-only research demand hints for deferred L2 work.

Only this fixed vocabulary can affect priority. The current persona-demand
contract carries a textual mandate; derive its terms/families here without
changing that owner contract or consulting a model. Hints never admit work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
from typing import Any, Mapping

import httpx

from services.service_token_file import configured_service_token
from services.source_ingestion.hypothesis_card import stored_text


STRATEGY_FAMILIES = frozenset({
    "momentum", "mean_reversion", "trend_following", "value", "quality",
    "carry", "arbitrage", "event_driven", "volatility", "liquidity",
})
MANDATE_TERMS = frozenset({
    "taiwan", "tw", "twse", "tpex", "us", "equity", "equities", "etf",
    "futures", "options", "crypto", "bitcoin", "ethereum", "revenue",
    "earnings", "fundamental", "price", "volume", "intraday", "daily",
    "monthly", "macro", "interest_rate", "broker", "institutional",
    "台灣", "臺灣", "台股", "營收", "動能", "均值回歸", "期貨", "選擇權",
})
VOCABULARY = STRATEGY_FAMILIES | MANDATE_TERMS


def vocabulary_terms(value: str) -> frozenset[str]:
    text = re.sub(r"[\s_-]+", " ", value.casefold())
    return frozenset(term for term in VOCABULARY if re.search(
        r"(?<![a-z0-9])" + re.escape(term.replace("_", " ")) + r"(?![a-z0-9])", text,
    ))


@dataclass(frozen=True)
class DemandPriority:
    terms: frozenset[str] = frozenset()
    reason: str = "no_open_demand"

    def score(self, payload: Mapping[str, Any]) -> int:
        meta = payload.get("metadata") or {}
        values = [str(payload.get("title") or ""), stored_text(meta)]
        for key in ("abstract", "keywords", "mandate_terms", "strategy_families"):
            value = meta.get(key)
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                values.extend(v for v in value if isinstance(v, str))
        return len(self.terms & vocabulary_terms(" ".join(values)))


class ResearchDemandReader:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url if base_url is not None else os.getenv(
            "DISTILLATION_RESEARCH_URL", "http://research-orchestrator-svc:8101",
        )).rstrip("/")

    def read(self, tenant_id: str, *, now: float) -> DemandPriority:
        if not tenant_id.strip():
            return DemandPriority(reason="demand_read_missing_tenant")
        try:
            # Resolve on every read: mounted credentials rotate independently.
            token = configured_service_token("DISTILLATION_RESEARCH_SERVICE_TOKEN")
            if not token:
                return DemandPriority(reason="demand_read_missing_credential")
            response = httpx.get(
                self.base_url + "/api/research/persona-demands",
                params={"status": "open"},
                headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant_id},
                timeout=5.0, follow_redirects=False,
            )
            if response.status_code != 200:
                return DemandPriority(reason=f"demand_read_http_{response.status_code}")
            body = response.json()
            rows = body["demands"]
            if not isinstance(rows, list):
                raise ValueError("invalid demand envelope")
            terms: set[str] = set()
            open_count = 0
            for row in rows:
                if row["tenant_id"] != tenant_id:
                    return DemandPriority(reason="demand_read_tenant_mismatch")
                if row["status"] != "open":
                    continue
                expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    raise ValueError("expiry missing timezone")
                if expires <= datetime.fromtimestamp(now, timezone.utc):
                    continue
                if row["schema_version"] != "research_demand.v1" or not isinstance(row["mandate"], str):
                    raise ValueError("invalid demand record")
                open_count += 1
                terms.update(vocabulary_terms(row["mandate"]))
            return DemandPriority(frozenset(terms), "open_demand" if open_count else "no_open_demand")
        except Exception as exc:
            # Never persist exception text: URLs/headers can contain credentials.
            return DemandPriority(reason=f"demand_read_unavailable:{type(exc).__name__}")
