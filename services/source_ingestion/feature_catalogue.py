"""Governed point-in-time feature catalogue (design §7.4, §8.5; development plan §2).

The catalogue lists every governed feature dataset and field together with its
declared availability lag, the normalized storage dataset it is read from, the
event-time key, and the public source it is derived from. It is the *only*
sanctioned way to turn normalized rows into feature observations:

* :func:`observations_from_row` converts one normalized row into point-in-time
  observations that always carry ``event_time``, ``available_time`` and
  ``ingest_time``;
* :func:`read_feature_series` returns the values visible at a decision time,
  dropping anything whose ``available_time`` or ``event_time + lag`` is later
  than the decision time, and refuses fields that are not catalogued.

The factor rule evaluator (``services.registry.factor_rule``) seeds the same
lag floors; ``test_feature_catalogue.py`` pins the two tables to each other.
This module is deliberately stdlib-only so any service can import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

FEATURE_CATALOGUE_SCHEMA_VERSION = "feature_catalogue.v1"
FEATURE_OBSERVATION_SCHEMA_VERSION = "feature_observation.v1"
_TAIPEI = timezone(timedelta(hours=8))
_UTC = timezone.utc


class FeatureCatalogueError(ValueError):
    """Raised when a feature is not governed or an observation is malformed."""


@dataclass(frozen=True)
class DatasetSpec:
    """One governed feature dataset."""

    name: str
    normalized_dataset: str
    key_kind: str  # "symbol" | "series" | "market"
    granularity: str  # "daily" | "monthly" | "quarterly"
    event_time_key: str
    availability_lag_days: int
    sources: tuple[str, ...]
    description: str
    venue: str | None = None


@dataclass(frozen=True)
class CatalogueField:
    """One governed feature field."""

    dataset: str
    field: str
    source_field: str
    unit: str
    description: str
    availability_lag_days: int | None = None  # None -> dataset default


def _ds(
    name: str,
    normalized: str,
    key_kind: str,
    granularity: str,
    event_key: str,
    lag: int,
    sources: Sequence[str],
    description: str,
    venue: str | None = None,
) -> DatasetSpec:
    return DatasetSpec(
        name=name,
        normalized_dataset=normalized,
        key_kind=key_kind,
        granularity=granularity,
        event_time_key=event_key,
        availability_lag_days=lag,
        sources=tuple(sources),
        description=description,
        venue=venue,
    )


_PRICE_SOURCES = ("twse:exchangeReport/STOCK_DAY_ALL", "tpex:tpex_mainboard_daily_close_quotes")
_INST_FIELDS = (
    ("foreign_net_shares", "foreign_net", "Foreign investor net shares"),
    ("trust_net_shares", "investment_trust_net", "Investment trust net shares"),
    ("dealer_net_shares", "dealer_net", "Dealer net shares"),
)

DATASET_SPECS: tuple[DatasetSpec, ...] = (
    _ds("tw_price_daily", "tw_price_daily", "symbol", "daily", "date", 0, _PRICE_SOURCES,
        "Daily OHLCV bars for TWSE/TPEx equities"),
    _ds("tw_monthly_revenue", "tw_monthly_revenue", "symbol", "monthly", "event_time", 10,
        ("twse:opendata/t187ap05_L", "tpex:mopsfin_t187ap05_O"),
        "Listed/OTC company monthly revenue (MOPS filing deadline: day 10 of next month)"),
    _ds("tw_quarterly_statements", "tw_financial_statement", "symbol", "quarterly", "event_time", 45,
        ("twse:opendata/t187ap06_L_ci", "twse:opendata/t187ap07_L_ci",
         "tpex:mopsfin_t187ap06_O_ci", "tpex:mopsfin_t187ap07_O_ci"),
        "Quarterly income statement and balance sheet ratios (Q1-Q3 deadline +45d, Q4 +90d)"),
    _ds("tw_margin_transactions", "tw_margin_short_balance", "symbol", "daily", "date", 0,
        ("twse:exchangeReport/MI_MARGN", "tpex:tpex_mainboard_margin_balance"),
        "Daily margin purchase and short sale balances"),
    _ds("tw_valuation_bwibbu", "tw_valuation_bwibbu", "symbol", "daily", "event_time", 0,
        ("twse:exchangeReport/BWIBBU_ALL", "tpex:tpex_mainboard_peratio_analysis"),
        "Daily P/E, P/B and dividend yield"),
    _ds("tw_institutional_flows", "tw_institutional_flow", "symbol", "daily", "date", 0,
        ("twse:fund/T86 via collector bundle", "tpex:tpex_3insti_daily_trading"),
        "Daily three-institution net shares (TWSE and TPEx)"),
    _ds("tpex_institutional_flows", "tw_institutional_flow", "symbol", "daily", "date", 0,
        ("tpex:tpex_3insti_daily_trading",), "Daily three-institution net shares (TPEx only)", venue="TPEx"),
    _ds("tw_macro_cbc", "tw_macro_series", "series", "monthly", "event_time", 0,
        ("collector bundle: CBC (Central Bank of the Republic of China)",),
        "Central bank policy rate and monetary aggregates"),
    _ds("tw_macro_ndc", "tw_macro_series", "series", "monthly", "event_time", 0,
        ("collector bundle: NDC (National Development Council)",),
        "Business-cycle monitoring score and composite indexes"),
    _ds("tw_macro_dgbas", "tw_macro_series", "series", "monthly", "event_time", 0,
        ("collector bundle: DGBAS / MOEA",), "CPI, GDP and export orders"),
    _ds("tw_community_attention", "tw_community_daily", "symbol", "daily", "event_time", 0,
        ("collector bundle: community forum posts (design §8.5)",),
        "Daily per-symbol community attention and bull/bear features"),
)

_FIELDS: list[CatalogueField] = []
for _ds_name in ("tw_price_daily",):
    for _f, _u in (("open", "TWD"), ("high", "TWD"), ("low", "TWD"), ("close", "TWD"), ("volume", "shares")):
        _FIELDS.append(CatalogueField(_ds_name, _f, _f, _u, f"Daily {_f}"))
    _FIELDS.append(CatalogueField(_ds_name, "vwap", "vwap", "TWD", "Daily volume weighted average price"))
_FIELDS += [
    CatalogueField("tw_monthly_revenue", "yoy", "yoy", "percent", "Monthly revenue YoY (%)"),
    CatalogueField("tw_monthly_revenue", "mom", "mom", "percent", "Monthly revenue MoM (%)"),
    CatalogueField("tw_monthly_revenue", "revenue", "revenue", "TWD_thousand", "Monthly revenue"),
    CatalogueField("tw_quarterly_statements", "eps", "eps", "TWD", "Quarterly basic EPS"),
    CatalogueField("tw_quarterly_statements", "operating_margin", "operating_margin", "ratio",
                   "Quarterly operating income / revenue"),
    CatalogueField("tw_quarterly_statements", "gross_margin", "gross_margin", "ratio",
                   "Quarterly gross profit / revenue"),
    CatalogueField("tw_quarterly_statements", "roe", "roe", "ratio",
                   "Quarterly net income attributable to parent / parent equity (not annualised)"),
    CatalogueField("tw_margin_transactions", "margin_balance", "margin_balance", "shares",
                   "Daily margin purchase balance"),
    CatalogueField("tw_margin_transactions", "short_balance", "short_balance", "shares",
                   "Daily short sale balance"),
    CatalogueField("tw_margin_transactions", "margin_change", "margin_change", "shares",
                   "Margin balance minus previous-day balance"),
    CatalogueField("tw_valuation_bwibbu", "pe_ratio", "pe_ratio", "ratio", "Daily P/E ratio"),
    CatalogueField("tw_valuation_bwibbu", "pb_ratio", "pb_ratio", "ratio", "Daily P/B ratio"),
    CatalogueField("tw_valuation_bwibbu", "dividend_yield", "dividend_yield", "percent", "Daily dividend yield (%)"),
]
for _dataset in ("tw_institutional_flows", "tpex_institutional_flows"):
    for _f, _src, _d in _INST_FIELDS:
        _FIELDS.append(CatalogueField(_dataset, _f, _src, "shares", _d))
_FIELDS.append(
    CatalogueField("tpex_institutional_flows", "total_inst_net_shares", "total_net", "shares",
                   "TPEx total 3-institution net shares")
)
_MACRO: tuple[tuple[str, str, int, str, str], ...] = (
    ("tw_macro_cbc", "policy_rate", 1, "percent", "CBC policy discount rate"),
    ("tw_macro_cbc", "m1b_yoy", 25, "percent", "CBC M1B money supply YoY"),
    ("tw_macro_cbc", "m2_yoy", 25, "percent", "CBC M2 money supply YoY"),
    ("tw_macro_cbc", "usd_twd", 1, "TWD_per_USD", "CBC NT$ per US$ exchange rate (BP01D01 column 01)"),
    ("tw_macro_ndc", "monitoring_score", 28, "score", "NDC business cycle monitoring score"),
    ("tw_macro_ndc", "leading_index", 28, "index", "NDC composite leading index"),
    ("tw_macro_ndc", "coincident_index", 28, "index", "NDC composite coincident index"),
    ("tw_macro_dgbas", "cpi_yoy", 10, "percent", "DGBAS consumer price index YoY"),
    ("tw_macro_dgbas", "gdp_yoy", 30, "percent", "DGBAS quarterly GDP YoY"),
    ("tw_macro_dgbas", "export_orders_yoy", 20, "percent", "MOEA/DGBAS export orders YoY"),
)
for _dataset, _f, _lag, _u, _d in _MACRO:
    _FIELDS.append(CatalogueField(_dataset, _f, _f, _u, _d, availability_lag_days=_lag))
_FIELDS += [
    CatalogueField("tw_community_attention", "post_volume", "post_volume", "posts",
                   "Aggregated community post volume"),
    CatalogueField("tw_community_attention", "attention_zscore", "attention_zscore", "zscore",
                   "Community attention volume z-score vs prior 20 days"),
    CatalogueField("tw_community_attention", "bull_bear_ratio", "bull_bear_ratio", "ratio",
                   "Community (bull+1)/(bear+1) mention ratio"),
]

_SPEC_BY_NAME: dict[str, DatasetSpec] = {spec.name: spec for spec in DATASET_SPECS}
_FIELD_BY_KEY: dict[tuple[str, str], CatalogueField] = {(f.dataset, f.field): f for f in _FIELDS}
assert len(_SPEC_BY_NAME) == len(DATASET_SPECS)
assert len(_FIELD_BY_KEY) == len(_FIELDS)
assert all(f.dataset in _SPEC_BY_NAME for f in _FIELDS)


def macro_series_id(dataset: str, field: str) -> str:
    """Collector series id for a macro field, e.g. ``cbc_policy_rate``."""
    return f"{dataset.removeprefix('tw_macro_')}_{field}"


MACRO_SERIES_MAP: Mapping[str, tuple[str, str]] = {
    macro_series_id(d, f): (d, f) for d, f, *_ in _MACRO
}


@dataclass(frozen=True)
class ProviderSeriesMapping:
    """Maps one collector provider series (or metric of it) to a catalogued macro field.

    ``source`` is ``"value"`` (the item value) or ``"metrics.<key>"``; ``period_kind`` says how
    the provider period normalizes to the event date: ``day`` keeps it, ``month`` and
    ``quarter`` use the period end.
    """

    dataset: str
    field: str
    source: str = "value"
    period_kind: str = "day"


# Real collector pollers' series identities (deploy/collector/collector_stack/pollers.py,
# spiders.py). Only semantically verified series are mapped; everything else is rejected.
PROVIDER_SERIES_MAP: Mapping[str, tuple[ProviderSeriesMapping, ...]] = {
    "CBC:BP01D01:01": (ProviderSeriesMapping("tw_macro_cbc", "usd_twd", "value", "day"),),
    "NDC:6099:MONITORING_SCORE": (
        ProviderSeriesMapping("tw_macro_ndc", "monitoring_score", "value", "month"),
        ProviderSeriesMapping("tw_macro_ndc", "leading_index", "metrics.leading_index", "month"),
        ProviderSeriesMapping("tw_macro_ndc", "coincident_index", "metrics.coincident_index", "month"),
    ),
    "DGBAS_CPI_TOTAL": (ProviderSeriesMapping("tw_macro_dgbas", "cpi_yoy", "metrics.yoy_pct", "month"),),
    "DGBAS_GDP_REAL_YOY": (ProviderSeriesMapping("tw_macro_dgbas", "gdp_yoy", "value", "quarter"),),
}


def dataset_specs() -> tuple[DatasetSpec, ...]:
    return DATASET_SPECS


def catalogue_fields() -> tuple[CatalogueField, ...]:
    return tuple(sorted(_FIELDS, key=lambda f: (f.dataset, f.field)))


def get_dataset_spec(dataset: str) -> DatasetSpec:
    try:
        return _SPEC_BY_NAME[dataset]
    except KeyError as exc:
        raise FeatureCatalogueError(f"dataset {dataset!r} is not in the governed feature catalogue") from exc


def get_field(dataset: str, field: str) -> CatalogueField:
    try:
        return _FIELD_BY_KEY[(dataset, field)]
    except KeyError as exc:
        raise FeatureCatalogueError(
            f"feature {dataset}.{field} is not in the governed feature catalogue"
        ) from exc


def lag_days_for(dataset: str, field: str) -> int:
    """Declared availability lag in days for ``dataset.field``."""
    f = get_field(dataset, field)
    return f.availability_lag_days if f.availability_lag_days is not None else get_dataset_spec(dataset).availability_lag_days


def catalogue_document() -> dict[str, Any]:
    """Return the catalogue as a ``feature_catalogue.v1`` JSON document."""
    datasets = []
    for spec in sorted(DATASET_SPECS, key=lambda s: s.name):
        fields = [
            {
                "field": f.field,
                "source_field": f.source_field,
                "unit": f.unit,
                "dtype": "float",
                "availability_lag_days": lag_days_for(f.dataset, f.field),
                "description": f.description,
            }
            for f in catalogue_fields()
            if f.dataset == spec.name
        ]
        entry: dict[str, Any] = {
            "dataset": spec.name,
            "normalized_dataset": spec.normalized_dataset,
            "key_kind": spec.key_kind,
            "granularity": spec.granularity,
            "event_time_key": spec.event_time_key,
            "default_availability_lag_days": spec.availability_lag_days,
            "sources": list(spec.sources),
            "description": spec.description,
            "fields": fields,
        }
        if spec.venue:
            entry["venue"] = spec.venue
        datasets.append(entry)
    return {"schema_version": FEATURE_CATALOGUE_SCHEMA_VERSION, "datasets": datasets}


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------


def parse_time(value: Any, *, end_of_day: bool = False) -> datetime:
    """Parse ISO date/datetime into an aware UTC datetime (naive -> Asia/Taipei for dates)."""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=_UTC)).astimezone(_UTC)
    if isinstance(value, date):
        value = value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise FeatureCatalogueError(f"invalid timestamp: {value!r}")
    text = value.strip()
    if len(text) == 10:
        try:
            d = date.fromisoformat(text)
        except ValueError as exc:
            raise FeatureCatalogueError(f"invalid ISO date: {value!r}") from exc
        return datetime.combine(d, time(23, 59, 59) if end_of_day else time(0, 0), tzinfo=_UTC)
    if text[-1] in "zZ":
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FeatureCatalogueError(f"invalid ISO timestamp: {value!r}") from exc
    return (dt if dt.tzinfo else dt.replace(tzinfo=_UTC)).astimezone(_UTC)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(_UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_available_time(event_date: str, lag_days: int, *, hour: int = 18) -> str:
    """Conservative availability: ``event_date + lag_days`` at ``hour``:00 Asia/Taipei."""
    d = date.fromisoformat(event_date[:10]) + timedelta(days=lag_days)
    return iso_utc(datetime.combine(d, time(hour, 0), tzinfo=_TAIPEI))


# ---------------------------------------------------------------------------
# observations
# ---------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def _row_value(spec: DatasetSpec, field: CatalogueField, row: Mapping[str, Any]) -> float | None:
    if field.source_field == "margin_change":
        explicit = _number(row.get("margin_change"))
        if explicit is not None:
            return explicit
        cur, prev = _number(row.get("margin_balance")), _number(row.get("margin_previous_balance"))
        return None if cur is None or prev is None else cur - prev
    return _number(row.get(field.source_field))


def observations_from_row(
    dataset: str,
    row: Mapping[str, Any],
    *,
    ingest_time: str | None = None,
) -> list[dict[str, Any]]:
    """Convert one normalized row into catalogued point-in-time observations.

    Rows that cannot yield a trustworthy ``event_time`` are rejected; values that
    are missing are skipped rather than imputed.
    """
    spec = get_dataset_spec(dataset)
    if spec.venue and str(row.get("venue") or "").upper() != spec.venue.upper():
        return []
    if row.get("dataset") not in (None, spec.normalized_dataset, dataset):
        raise FeatureCatalogueError(
            f"row dataset {row.get('dataset')!r} does not match catalogued {spec.normalized_dataset!r}"
        )
    event_raw = row.get(spec.event_time_key) or row.get("event_time") or row.get("date")
    if not event_raw:
        raise FeatureCatalogueError(f"{dataset} row has no {spec.event_time_key}")
    event_date = str(event_raw)[:10]
    if spec.key_kind == "series":
        entity = str(row.get("series_id") or row.get("symbol") or "")
    elif spec.key_kind == "market":
        entity = "TW"
    else:
        entity = str(row.get("symbol_canonical") or row.get("symbol") or "")
    if not entity:
        raise FeatureCatalogueError(f"{dataset} row has no entity key")
    ingest = row.get("ingest_time") or ingest_time
    if not ingest:
        raise FeatureCatalogueError(f"{dataset} row has no ingest_time")
    out: list[dict[str, Any]] = []
    for f in catalogue_fields():
        if f.dataset != dataset:
            continue
        if spec.key_kind == "series" and row.get("field") not in (None, f.field):
            continue
        lag = lag_days_for(dataset, f.field)
        value = _row_value(spec, f, row) if spec.key_kind != "series" else _number(row.get("value"))
        if value is None:
            continue
        avail_raw = row.get("available_time")
        row_lag = row.get("availability_lag_days")
        if isinstance(row_lag, int) and not isinstance(row_lag, bool) and row_lag > lag:
            lag = row_lag
        avail = parse_time(avail_raw) if (avail_raw and len(str(avail_raw)) > 10) else None
        floor = parse_time(default_available_time(event_date, lag))
        if avail is None or (lag > 0 and avail < floor):
            avail = floor
        out.append(
            {
                "schema_version": FEATURE_OBSERVATION_SCHEMA_VERSION,
                "dataset": dataset,
                "field": f.field,
                "entity": entity,
                "event_time": event_date,
                "available_time": iso_utc(avail),
                "ingest_time": str(ingest),
                "availability_lag_days": lag,
                "value": value,
            }
        )
    if spec.key_kind == "series" and not out:
        return []
    return out


def read_feature_series(
    observations: Iterable[Mapping[str, Any]],
    dataset: str,
    field: str,
    *,
    decision_time: Any,
    entity: str | None = None,
    include_revisions: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Return observations of ``dataset.field`` visible at ``decision_time``.

    Unless ``include_revisions`` is set (audit/evidence use), exactly the latest
    visible version per entity/event_time is returned, so a late revision replaces
    rather than adds a daily observation; replayed duplicates collapse too.

    This is the only governed reader: unknown fields raise, the declared lag is
    applied on top of any row-level lag, and observations are ordered by event time.
    """
    lag = lag_days_for(dataset, field)
    decision = parse_time(decision_time, end_of_day=True)
    visible: list[dict[str, Any]] = []
    for obs in observations:
        if obs.get("dataset") != dataset or obs.get("field") != field:
            continue
        if entity is not None and obs.get("entity") != entity:
            continue
        row_lag = max(lag, int(obs.get("availability_lag_days") or 0))
        event = parse_time(obs["event_time"])
        if parse_time(obs["available_time"]) > decision:
            continue
        if event + timedelta(days=row_lag) > decision:
            continue
        visible.append(dict(obs))
    visible.sort(
        key=lambda o: (parse_time(o["event_time"]), parse_time(o["available_time"]), str(o.get("ingest_time") or ""))
    )
    if include_revisions:
        unique: dict[tuple[Any, ...], dict[str, Any]] = {}
        for o in visible:  # collapse replayed identical versions
            unique[(o.get("entity"), parse_time(o["event_time"]), parse_time(o["available_time"]), o.get("value"))] = o
        visible = list(unique.values())
    else:
        latest: dict[tuple[Any, Any], dict[str, Any]] = {}
        for o in visible:  # ascending version order: later versions overwrite
            latest[(o.get("entity"), parse_time(o["event_time"]))] = o
        visible = sorted(latest.values(), key=lambda o: (parse_time(o["event_time"]), str(o.get("entity") or "")))
    return tuple(visible)


def feature_inputs_for_factor_rule(
    observations: Iterable[Mapping[str, Any]],
    inputs: Sequence[Mapping[str, Any]],
    *,
    decision_time: Any,
    entity: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build the ``features`` mapping for a factor rule from governed observations."""
    obs_list = list(observations)
    features: dict[str, list[dict[str, Any]]] = {}
    for item in inputs:
        series = read_feature_series(
            obs_list, str(item["dataset"]), str(item["field"]), decision_time=decision_time, entity=entity
        )
        features[str(item["name"])] = [
            {"value": o["value"], "event_time": o["event_time"], "available_time": o["available_time"]}
            for o in series
        ]
    return features


# ---------------------------------------------------------------------------
# yearly macro release calendar
# ---------------------------------------------------------------------------

RELEASE_CALENDAR_SCHEMA_VERSION = "tw_macro_release_calendar.v1"


def validate_release_calendar(document: Mapping[str, Any]) -> None:
    """Validate a manually maintained yearly Taiwan macro release calendar.

    Every entry must reference a catalogued feature and a cited source; a
    ``confirmed`` entry needs an in-year ISO ``release_date`` and a ``rule`` entry
    must not carry one (rules never imply exact dates).
    """
    if document.get("schema_version") != RELEASE_CALENDAR_SCHEMA_VERSION:
        raise FeatureCatalogueError("release calendar schema_version mismatch")
    year = document.get("year")
    sources = document.get("sources")
    entries = document.get("entries")
    if not isinstance(year, int) or not isinstance(sources, Mapping) or not isinstance(entries, list) or not entries:
        raise FeatureCatalogueError("release calendar needs year, sources and entries")
    for source_id, source in sources.items():
        if not isinstance(source, Mapping) or not str(source.get("url") or "").startswith("https://"):
            raise FeatureCatalogueError(f"source {source_id!r} must cite an https url")
    for idx, entry in enumerate(entries):
        dataset, _, field = str(entry.get("feature") or "").partition(".")
        get_field(dataset, field)
        if entry.get("source") not in sources:
            raise FeatureCatalogueError(f"entries[{idx}] cites unknown source {entry.get('source')!r}")
        status = entry.get("status")
        if status == "confirmed":
            released = parse_time(str(entry.get("release_date") or ""))
            if released.year != year:
                raise FeatureCatalogueError(f"entries[{idx}] release_date is outside {year}")
        elif status == "rule":
            if entry.get("release_date") or not str(entry.get("rule") or "").strip():
                raise FeatureCatalogueError(f"entries[{idx}] rule entries need a rule and no release_date")
        else:
            raise FeatureCatalogueError(f"entries[{idx}] status must be confirmed or rule")
