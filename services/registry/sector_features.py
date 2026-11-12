"""Prospective Taiwan industry joins and complete-sector institutional net shares.

The derived measure is shares, not TWD capital flow. Missing constituents never
become zero. Revisions are selected through the catalogue at the decision cutoff.
"""
from __future__ import annotations

from datetime import timedelta, timezone
import math
from typing import Any, Iterable, Mapping, Sequence

from services.source_ingestion import feature_catalogue as fc

INDUSTRY = "tw_industry_classification"
SECTOR = "tw_sector_flows"
FLOW = "tw_institutional_flows"
FLOW_FIELDS = ("foreign_net_shares", "trust_net_shares", "dealer_net_shares")


def classification_at(observations: Iterable[Mapping[str, Any]], *, decision_time: Any) -> dict[str, dict]:
    """Latest acquired classification per entity; no value before first observation."""
    result = {}
    for row in fc.read_feature_series(observations, INDUSTRY, "industry_code", decision_time=decision_time):
        code = row.get("value")
        if isinstance(code, bool) or not isinstance(code, (int, float)) or not math.isfinite(code):
            continue
        if int(code) != code or not 0 < code < 100:
            continue
        result[str(row["entity"])] = row
    return result


def sector_flow_observations(
    observations: Iterable[Mapping[str, Any]], *, decision_time: Any,
    required_entities: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Latest complete same-session sector totals, projected onto each member.

    The caller supplies the complete governed market. Without an explicit entity
    set we require every entity present in either classification or flow inputs.
    Every member needs all three flows on the same day and classification known
    by that day's end. Today’s profile cannot classify any prior day's flow.
    """
    rows = list(observations)
    entities = set(required_entities) if required_entities is not None else {
        str(o["entity"]) for o in rows if o.get("dataset") in {INDUSTRY, FLOW} and o.get("entity")
    }
    if not entities:
        return []
    by_field = {
        field: fc.read_feature_series(rows, FLOW, field, decision_time=decision_time)
        for field in FLOW_FIELDS
    }
    latest = {}
    for field, series in by_field.items():
        for row in series:
            if row.get("entity") in entities:
                latest[(row["entity"], field)] = row
    if any((entity, field) not in latest for entity in entities for field in FLOW_FIELDS):
        return []
    dates = {str(o["event_time"])[:10] for o in latest.values()}
    if len(dates) != 1:
        return []
    day = dates.pop()
    # Classification after the flow session is never used to reconstruct it.
    cutoff = min(fc.parse_time(decision_time, end_of_day=True),
                 fc.parse_time(day).replace(tzinfo=timezone(timedelta(hours=8)))
                 + timedelta(days=1) - timedelta(microseconds=1))
    industries = classification_at(rows, decision_time=cutoff)
    if entities - industries.keys():
        return []
    groups: dict[int, list[str]] = {}
    for entity in sorted(entities):
        groups.setdefault(int(industries[entity]["value"]), []).append(entity)
    out = []
    for code, members in sorted(groups.items()):
        constituents = [latest[(e, f)] for e in members for f in FLOW_FIELDS]
        values = [o.get("value") for o in constituents]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            return []
        total = math.fsum(values)
        if not math.isfinite(total):
            return []
        sources = [*constituents, *(industries[e] for e in members)]
        available = max(max(fc.parse_time(o["available_time"]),
                            fc.parse_time(o["event_time"]) + timedelta(days=int(o.get("availability_lag_days") or 0)))
                        for o in sources)
        for entity in members:
            out.append({"dataset": SECTOR, "field": "net_shares", "entity": entity,
                        "event_time": day, "available_time": available.isoformat().replace("+00:00", "Z"),
                        "ingest_time": max(str(o.get("ingest_time") or o["available_time"]) for o in sources),
                        "availability_lag_days": fc.lag_days_for(SECTOR, "net_shares"),
                        "value": total, "industry_code": code, "constituent_entities": members})
    return out


def market_with_sector_features(symbols: Mapping[str, Mapping], inputs: Sequence[Mapping],
                                *, decision_time: Any) -> dict[str, dict]:
    """Screen production entry: resolve sector inputs from the complete market feed.

    Input aliases and catalogue-qualified keys are supported just like ordinary
    factor inputs. Base classification and flows must carry real dated observations.
    """
    from . import factor_rule as fr

    targets = [i for i in inputs if i["dataset"] == SECTOR]
    if not targets:
        return dict(symbols)
    observations = []
    for entity, payload in symbols.items():
        features = payload.get("features") or {}
        for dataset, field in [(INDUSTRY, "industry_code"), *((FLOW, f) for f in FLOW_FIELDS)]:
            aliases = [i["name"] for i in inputs if (i["dataset"], i["field"]) == (dataset, field)]
            raw = fr._lookup_raw_feature(features, name=aliases[0] if aliases else f"{dataset}.{field}",
                                         dataset=dataset, field=field)
            if not isinstance(raw, list):
                continue
            for row in raw:
                if not isinstance(row, Mapping) or not row.get("event_time") or not row.get("available_time"):
                    continue
                observations.append({**row, "dataset": dataset, "field": field, "entity": entity})
    derived = sector_flow_observations(observations, decision_time=decision_time, required_entities=list(symbols))
    result = {}
    for entity, payload in symbols.items():
        features = dict(payload.get("features") or {})
        for target in targets:
            # Derive afresh; never accept a stale or caller-imputed aggregate.
            features[target["name"]] = [o for o in derived if o["entity"] == entity]
        result[entity] = {**payload, "features": features}
    return result
