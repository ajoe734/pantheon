"""Pure projections for discovery read surfaces (BFF-DISCOVERY-READ-SURFACES).

Shapes come from the owning services:
- hypothesis cards: docs/contracts/hypothesis_card.v1.schema.json
  (services/source_ingestion/hypothesis_card.py)
- template evaluation: GET /api/research/alpha-templates/evaluations
  (services/research/main.py, services/research/alpha_templates/evaluator.py)
- in/out-of-sample metrics: replication backtest summary
  (services/research/alpha_replication/revalidation_worker.py)
- lineage: strategy_spec_seed lineage (services/source_ingestion/strategy_seed_builder.py)

Nothing here invents values: a field the owner did not supply stays ``None``.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

GOV_TW_ATTRIBUTION = (
    "資料來源：政府資料開放平臺 data.gov.tw（依政府資料開放授權條款－第1版提供），"
    "本系統衍生之數值非原機關發布之官方數據。"
)
GOV_TW_MARKER = "data.gov.tw"
SOURCE_RECORD_HREF_TEMPLATE = "/bff/management/evidence/{source_record_id}"

_IN_SAMPLE_KEYS = ("sharpe_ratio", "max_drawdown", "total_return", "round_trips")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def mentions_gov_tw(*values: Any) -> bool:
    """True when any string inside ``values`` (recursively) names data.gov.tw."""
    for value in values:
        if isinstance(value, str):
            if GOV_TW_MARKER in value.lower():
                return True
        elif isinstance(value, Mapping):
            if mentions_gov_tw(*value.values()):
                return True
        elif isinstance(value, (list, tuple, set)):
            if mentions_gov_tw(*value):
                return True
    return False


def attribution_block(*values: Any) -> Optional[Dict[str, Any]]:
    """Attribution payload when the values are data.gov.tw derived, else ``None``."""
    if not mentions_gov_tw(*values):
        return None
    return {"provider": GOV_TW_MARKER, "text": GOV_TW_ATTRIBUTION}


def source_record_link(source_record_id: str) -> Dict[str, str]:
    return {
        "source_record_id": source_record_id,
        "href": SOURCE_RECORD_HREF_TEMPLATE.format(source_record_id=source_record_id),
    }


def project_hypothesis_card(card: Any) -> Optional[Dict[str, Any]]:
    """hypothesis_card.v1 with each cited span linked to its source record."""
    if not isinstance(card, Mapping) or not card.get("claim"):
        return None
    out: Dict[str, Any] = {k: v for k, v in card.items() if k != "cited_spans"}
    spans: List[Dict[str, Any]] = []
    for span in card.get("cited_spans") or []:
        if not isinstance(span, Mapping):
            continue
        record_id = str(span.get("source_record_id") or "").strip()
        projected = dict(span)
        projected["source_record"] = source_record_link(record_id) if record_id else None
        spans.append(projected)
    out["cited_spans"] = spans
    out["source_records"] = _unique_links(s["source_record"] for s in spans if s["source_record"])
    return out


def _unique_links(links: Iterable[Mapping[str, str]]) -> List[Dict[str, str]]:
    seen: Dict[str, Dict[str, str]] = {}
    for link in links:
        seen.setdefault(link["source_record_id"], dict(link))
    return list(seen.values())


def project_sample_metrics(*sources: Any) -> Optional[Dict[str, Any]]:
    """Normalise in-sample / out-of-sample metrics from owner shapes.

    Accepts the flat replication summary (``in_sample_sharpe_ratio``,
    ``out_of_sample_*``) or the nested evaluation (``in_sample`` /
    ``out_of_sample`` mappings). Returns ``None`` when no source supplies
    any sample metric.
    """
    in_sample: Dict[str, Any] = {}
    out_sample: Dict[str, Any] = {}
    extras: Dict[str, Any] = {}
    for src in sources:
        m = _mapping(src)
        for key in _IN_SAMPLE_KEYS:
            nested_in = _mapping(m.get("in_sample")).get(key)
            nested_out = _mapping(m.get("out_of_sample")).get(key)
            flat_in = m.get(f"in_sample_{key}")
            flat_out = m.get(f"out_of_sample_{key}")
            if in_sample.get(key) is None:
                in_sample[key] = nested_in if nested_in is not None else flat_in
            if out_sample.get(key) is None:
                out_sample[key] = nested_out if nested_out is not None else flat_out
        for key in (
            "deflated_sharpe_ratio",
            "deflated_sharpe_threshold",
            "trial_count",
            "walk_forward_fold_sharpes",
            "buy_and_hold_sharpe_ratio",
            "tw_0050_sharpe_ratio",
            "backtest_run_id",
        ):
            if extras.get(key) is None and m.get(key) is not None:
                extras[key] = m[key]
    if not any(v is not None for v in (*in_sample.values(), *out_sample.values())):
        return None
    return {"in_sample": in_sample, "out_of_sample": out_sample, **extras}


def project_template_evaluation(item: Any) -> Optional[Dict[str, Any]]:
    """One row of the research owner's alpha-template evaluation list."""
    row = _mapping(item)
    if not row.get("template_id"):
        return None
    evaluation = row.get("evaluation")
    out: Dict[str, Any] = {
        "template_id": row.get("template_id"),
        "registry_id": row.get("registry_id"),
        "name": row.get("name"),
        "family": row.get("family"),
        "scope": row.get("scope"),
        "status": row.get("status"),
        "evaluation_ref": row.get("evaluation_ref"),
        "duplicate_of": row.get("duplicate_of"),
        "evaluation": dict(evaluation) if isinstance(evaluation, Mapping) else None,
    }
    if isinstance(row.get("attribution"), str):
        out["template_attribution"] = row["attribution"]
    return out


def project_lineage(lineage: Any, *, source_ids: Iterable[str] = ()) -> Dict[str, Any]:
    """Lineage back to source records (links, not copies)."""
    lin = _mapping(lineage)
    ids = [str(s) for s in (list(source_ids) or list(lin.get("source_ids") or [])) if s]
    refs = [str(r) for r in lin.get("source_record_refs") or [] if r]
    return {
        "evidence_bundle_id": lin.get("evidence_bundle_id"),
        "source_ids": ids,
        "source_record_refs": refs,
        "source_records": _unique_links(source_record_link(i) for i in ids),
        "citation_refs": list(lin.get("citation_refs") or []),
        "trace_refs": list(lin.get("trace_refs") or []),
    }


_DISABLED_LIFECYCLES = frozenset(
    {"configured_disabled", "validated_disabled", "canary_passed_disabled", "disabled", "degraded_disabled", "retired"}
)


def project_source_health(desired: Any, observed: Any) -> Dict[str, Any]:
    """Data-source health and pause reason from desired/observed state.

    ``pause_reasons`` come from the owner's ``observed.reasons``; a source that
    is not enabled but has none reports ``None`` rather than a guessed reason.
    """
    d, o = _mapping(desired), _mapping(observed)
    desired_lifecycle = d.get("desired_lifecycle")
    effective = o.get("effective_lifecycle")
    paused = (effective or desired_lifecycle) in _DISABLED_LIFECYCLES
    reasons = [str(r) for r in o.get("reasons") or [] if r]
    return {
        "health_state": o.get("health_state"),
        "effective_lifecycle": effective,
        "desired_lifecycle": desired_lifecycle,
        "paused": paused,
        "pause_reasons": reasons if paused and reasons else None,
        "freshness": o.get("freshness"),
        "last_run": o.get("last_run"),
        "dlq_unresolved_count": o.get("dlq_unresolved_count"),
    }


def project_discovery_block(record: Any) -> Dict[str, Any]:
    """Discovery fields for a record that may carry owner-supplied card/metrics/lineage.

    Used by the evolution journal, evolution candidates and strategy detail.
    Missing owner data yields ``None`` values, never defaults.
    """
    r = _mapping(record)
    meta = _mapping(r.get("metadata"))
    card = r.get("hypothesis_card") or meta.get("hypothesis_card")
    lineage = r.get("lineage") if isinstance(r.get("lineage"), Mapping) else meta.get("lineage")
    return {
        "hypothesis_card": project_hypothesis_card(card),
        "metrics": project_sample_metrics(
            r.get("metrics"),
            r.get("evaluation_summary"),
            r.get("backtest_summary"),
            meta.get("metrics"),
            meta.get("backtest_summary"),
        ),
        "lineage": project_lineage(lineage) if isinstance(lineage, Mapping) else None,
        "attribution": attribution_block(r),
    }
