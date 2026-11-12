"""Bounded factor-rule grammar, point-in-time input resolver, and deterministic evaluator.

This module implements the ``factor_rule_v1`` strategy logic kind (design §9.2
and development plan §3.4). A factor rule is a bounded JSON expression tree over
governed point-in-time feature inputs and strategy parameters:

* Every numeric constant in a ``factor_rule_v1`` ``StrategyArtifact`` is a
  ``{"const_param": "<key>"}`` reference whose parameter is declared in
  ``parameters`` and in ``mutation_surface.controls`` with an ``allowed_range``
  and ``step``.
* The structure of the rule (``strategy_logic``) is immutable within a single
  ``StrategyArtifact``.
* Point-in-time inputs are filtered by ``available_time <= decision_time`` and
  ``event_time + timedelta(days=availability_lag_days) <= decision_time`` before
  any operator executes, so future observations are strictly invisible.
* Both vectorbt backtests and paper signal evaluation call the same evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
import re
from typing import Any, Mapping, Sequence


class FactorRuleValidationError(ValueError):
    """Raised when a factor rule AST, input spec, or evaluation input is invalid."""


_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_EPSILON = 1e-12
MAX_FACTOR_AST_DEPTH = 12
MAX_FACTOR_WINDOW_BARS = 504

BUILT_IN_PRICE_VOLUME_FIELDS: frozenset[str] = frozenset(
    {"open", "high", "low", "close", "volume", "vwap"}
)
# SD_INTRADAY_MINUTE_BARS §6.1: governed minute datasets (1m stored, coarser derived).
INTRADAY_PRICE_VOLUME_DATASETS: frozenset[str] = frozenset(
    {"tw_price_1m", "tw_price_5m", "tw_price_15m", "tw_price_30m", "tw_price_60m"}
)
AVAILABILITY_LAG_UNITS: tuple[str, ...] = ("days", "minutes")

PRICE_VOLUME_DATASETS: frozenset[str] = frozenset(
    {
        "tw_price_volume",
        "tw_price_daily",
        "price_volume",
        "ohlcv",
        "tw_official_daily",
        "STOCK_DAY_ALL",
        "tpex_mainboard_daily_close_quotes",
    }
) | INTRADAY_PRICE_VOLUME_DATASETS


@dataclass(frozen=True)
class FeatureDefinition:
    """Governed point-in-time feature definition in the feature catalogue."""

    dataset: str
    field: str
    min_availability_lag_days: int = 0
    description: str = ""
    # SD §6.3: ``min_availability_lag`` carries a unit.  Minute features are lagged
    # in minutes beyond bar close; every other feature keeps its day-denominated lag.
    availability_lag_unit: str = "days"
    min_availability_lag: int | None = None
    # Indicator windows reset at the 09:00 session start unless declared otherwise.
    cross_session: bool = False

    def __post_init__(self) -> None:
        if self.availability_lag_unit not in AVAILABILITY_LAG_UNITS:
            raise FactorRuleValidationError(
                "feature definition availability_lag_unit must be one of "
                + "/".join(AVAILABILITY_LAG_UNITS)
            )
        if self.min_availability_lag is None:
            lag = self.min_availability_lag_days if self.availability_lag_unit == "days" else 0
            object.__setattr__(self, "min_availability_lag", lag)
        lag = self.min_availability_lag
        if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
            raise FactorRuleValidationError(
                "feature definition min_availability_lag must be a non-negative integer"
            )
        if self.availability_lag_unit == "minutes" and self.min_availability_lag_days != 0:
            raise FactorRuleValidationError(
                "minute-unit feature definitions must not carry a day lag"
            )
        if self.availability_lag_unit == "days" and lag != self.min_availability_lag_days:
            raise FactorRuleValidationError(
                "day-unit min_availability_lag must equal min_availability_lag_days"
            )
        if not isinstance(self.cross_session, bool):
            raise FactorRuleValidationError("feature definition cross_session must be a boolean")


# Seeded governed feature catalogue (Design §5, §8.5, §9.2 and Plan §3.4).
# Additional datasets/fields can be registered via register_feature_definition.
_FEATURE_CATALOGUE: dict[tuple[str, str], FeatureDefinition] = {}


def register_feature_definition(definition: FeatureDefinition) -> None:
    """Register or update a governed feature definition in the catalogue."""
    if not isinstance(definition.dataset, str) or not definition.dataset.strip():
        raise FactorRuleValidationError("feature definition dataset must be a non-empty string")
    if not isinstance(definition.field, str) or not definition.field.strip():
        raise FactorRuleValidationError("feature definition field must be a non-empty string")
    if (
        isinstance(definition.min_availability_lag_days, bool)
        or not isinstance(definition.min_availability_lag_days, int)
        or definition.min_availability_lag_days < 0
    ):
        raise FactorRuleValidationError(
            "feature definition min_availability_lag_days must be a non-negative integer"
        )
    _FEATURE_CATALOGUE[(definition.dataset.strip(), definition.field.strip())] = definition


def get_feature_definition(dataset: str, field: str) -> FeatureDefinition | None:
    """Return a registered feature definition if present in the catalogue."""
    return _FEATURE_CATALOGUE.get((dataset, field))


def list_feature_definitions() -> tuple[FeatureDefinition, ...]:
    """Return all registered feature definitions sorted by (dataset, field)."""
    return tuple(
        _FEATURE_CATALOGUE[key] for key in sorted(_FEATURE_CATALOGUE.keys())
    )


def _seed_default_feature_catalogue() -> None:
    for ds in PRICE_VOLUME_DATASETS:
        intraday = ds in INTRADAY_PRICE_VOLUME_DATASETS
        for fld in BUILT_IN_PRICE_VOLUME_FIELDS:
            register_feature_definition(
                FeatureDefinition(
                    dataset=ds,
                    field=fld,
                    min_availability_lag_days=0,
                    description=(
                        f"Intraday price-volume {fld} ({ds}), 0 minutes beyond bar close"
                        if intraday
                        else f"Daily price-volume {fld} ({ds})"
                    ),
                    availability_lag_unit="minutes" if intraday else "days",
                )
            )
    seeded: tuple[tuple[str, str, int, str], ...] = (
        ("tw_monthly_revenue", "yoy", 10, "Taiwan listed company monthly revenue YoY (%)"),
        ("tw_monthly_revenue", "mom", 10, "Taiwan listed company monthly revenue MoM (%)"),
        ("tw_monthly_revenue", "revenue", 10, "Taiwan listed company monthly revenue"),
        ("tw_quarterly_statements", "eps", 45, "Quarterly earnings per share"),
        ("tw_quarterly_statements", "operating_margin", 45, "Quarterly operating margin"),
        ("tw_quarterly_statements", "gross_margin", 45, "Quarterly gross margin"),
        ("tw_quarterly_statements", "roe", 45, "Quarterly return on equity"),
        ("tw_margin_transactions", "margin_balance", 0, "Daily margin purchase balance"),
        ("tw_margin_transactions", "short_balance", 0, "Daily short sale balance"),
        ("tw_margin_transactions", "margin_change", 0, "Daily margin balance change"),
        ("tw_valuation_bwibbu", "pe_ratio", 0, "Daily P/E ratio from BWIBBU_ALL"),
        ("tw_valuation_bwibbu", "pb_ratio", 0, "Daily P/B ratio from BWIBBU_ALL"),
        ("tw_valuation_bwibbu", "dividend_yield", 0, "Daily dividend yield (%) from BWIBBU_ALL"),
        ("tpex_institutional_flows", "foreign_net_shares", 0, "TPEx foreign investor net shares"),
        ("tpex_institutional_flows", "trust_net_shares", 0, "TPEx investment trust net shares"),
        ("tpex_institutional_flows", "dealer_net_shares", 0, "TPEx dealer net shares"),
        ("tpex_institutional_flows", "total_inst_net_shares", 0, "TPEx total 3-institution net shares"),
        ("tw_institutional_flows", "foreign_net_shares", 0, "TWSE/TPEx foreign investor net shares"),
        ("tw_institutional_flows", "trust_net_shares", 0, "TWSE/TPEx investment trust net shares"),
        ("tw_institutional_flows", "dealer_net_shares", 0, "TWSE/TPEx dealer net shares"),
        ("tw_macro_cbc", "policy_rate", 1, "CBC policy discount rate"),
        ("tw_macro_cbc", "m1b_yoy", 25, "CBC M1B money supply YoY"),
        ("tw_macro_cbc", "m2_yoy", 25, "CBC M2 money supply YoY"),
        ("tw_macro_cbc", "usd_twd", 1, "CBC NT$ per US$ exchange rate (BP01D01 column 01)"),
        ("tw_macro_ndc", "monitoring_score", 28, "NDC business cycle monitoring score"),
        ("tw_macro_ndc", "leading_index", 28, "NDC composite leading index"),
        ("tw_macro_ndc", "coincident_index", 28, "NDC composite coincident index"),
        ("tw_macro_dgbas", "cpi_yoy", 10, "DGBAS consumer price index YoY"),
        ("tw_macro_dgbas", "gdp_yoy", 30, "DGBAS quarterly GDP YoY"),
        ("tw_macro_dgbas", "export_orders_yoy", 20, "MOEA/DGBAS export orders YoY"),
        ("tw_community_attention", "post_volume", 0, "Aggregated community post volume"),
        ("tw_community_attention", "attention_zscore", 0, "Community attention volume z-score"),
        ("tw_community_attention", "bull_bear_ratio", 0, "Community bull-to-bear mention ratio"),
        ("tw_community_attention", "unique_authors", 0, "Distinct hashed community authors"),
        ("tw_community_attention", "engagement_weighted_sentiment", 0, "Engagement-weighted community sentiment"),
    )
    for dataset, field, lag_days, desc in seeded:
        register_feature_definition(
            FeatureDefinition(
                dataset=dataset,
                field=field,
                min_availability_lag_days=lag_days,
                description=desc,
            )
        )


_seed_default_feature_catalogue()


def assert_governed_feature_inputs(strategy_logic: Mapping[str, Any]) -> None:
    """Require every declared ``inputs[]`` entry to exist in the governed feature catalogue.

    Price-volume built-ins resolve through the seeded price datasets. Anything else
    must be catalogued so its availability lag is governed; the paper signal producer
    calls this before evaluating so features can only enter through the catalogue.
    """
    if not isinstance(strategy_logic, Mapping) or strategy_logic.get("kind") != "factor_rule_v1":
        return
    for idx, item in enumerate(strategy_logic.get("inputs") or ()):
        if not isinstance(item, Mapping):
            continue
        dataset, field = str(item.get("dataset") or ""), str(item.get("field") or "")
        if get_feature_definition(dataset, field) is None:
            raise FactorRuleValidationError(
                f"inputs[{idx}] ({dataset}.{field}) is not in the governed feature catalogue"
            )


def _require_aware_timestamp(value: Any, label: str) -> None:
    if isinstance(value, datetime):
        aware = value.tzinfo is not None and value.utcoffset() is not None
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if len(text) == 10:
            raise FactorRuleValidationError(f"{label} must be a timezone-aware minute timestamp")
        try:
            parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in "Zz" else text)
        except ValueError as exc:
            raise FactorRuleValidationError(f"{label} is not a valid ISO timestamp: {value!r}") from exc
        aware = parsed.tzinfo is not None and parsed.utcoffset() is not None
    else:
        aware = False
    if not aware:
        raise FactorRuleValidationError(f"{label} must be a timezone-aware minute timestamp")


def _require_minute_observations(
    feature_map: Mapping[str, Any], *, name: str, dataset: str, field: str
) -> None:
    series = _lookup_raw_feature(feature_map, name=name, dataset=dataset, field=field)
    if not isinstance(series, (list, tuple)) or not series:
        raise FactorRuleValidationError(
            f"minute input '{name}' ({dataset}.{field}) requires dataset-bound minute observations; "
            "daily/generic close or synthetic vwap fallback is not allowed"
        )
    for idx, obs in enumerate(series):
        if (
            not isinstance(obs, Mapping)
            or not _is_number(obs.get("value"))
            or not obs.get("event_time")
            or not obs.get("available_time")
        ):
            raise FactorRuleValidationError(
                f"minute input '{name}[{idx}]' must carry value, event_time and available_time"
            )
        _require_aware_timestamp(obs["event_time"], f"minute input '{name}[{idx}].event_time'")
        _require_aware_timestamp(obs["available_time"], f"minute input '{name}[{idx}].available_time'")


def assert_governed_feature_observations(
    strategy_logic: Mapping[str, Any],
    features: Mapping[str, Any] | None,
    decision_time: Any,
) -> None:
    """Require catalogued, timestamped, already-available observations for non-price inputs.

    Raw numbers or untimestamped series cannot carry an availability lag, so they are
    rejected, as is an observation lacking ``event_time`` / ``available_time``.
    Future-available observations are hidden by the evaluator, never read.
    """
    if not isinstance(strategy_logic, Mapping) or strategy_logic.get("kind") != "factor_rule_v1":
        return
    feature_map: Mapping[str, Any] = features if isinstance(features, Mapping) else {}
    decision_checked = False
    for item in strategy_logic.get("inputs") or ():
        if not isinstance(item, Mapping):
            continue
        name, dataset, field = str(item.get("name")), str(item.get("dataset")), str(item.get("field"))
        if dataset in PRICE_VOLUME_DATASETS and dataset not in INTRADAY_PRICE_VOLUME_DATASETS:
            continue
        if dataset in INTRADAY_PRICE_VOLUME_DATASETS:
            # SD §6.3: minute inputs must be dataset-bound, timezone-aware observations
            # evaluated at an explicit decision_time; no daily/generic/raw fallback.
            _parse_timestamp_utc(decision_time, is_decision_time=True)
            _require_minute_observations(feature_map, name=name, dataset=dataset, field=field)
            continue
        if not decision_checked:
            # decision_time stays optional for timestamp-free built-in price inputs;
            # any non-price (point-in-time) input requires a valid one.
            _parse_timestamp_utc(decision_time, is_decision_time=True)
            decision_checked = True
        cat_def = get_feature_definition(dataset, field)
        if cat_def is None:
            raise FactorRuleValidationError(f"{dataset}.{field} is not in the governed feature catalogue")
        series = _lookup_raw_feature(feature_map, name=name, dataset=dataset, field=field)
        if not isinstance(series, (list, tuple)) or not series:
            raise FactorRuleValidationError(
                f"feature input '{name}' ({dataset}.{field}) requires catalogued timestamped observations"
            )
        for idx, obs in enumerate(series):
            if (
                not isinstance(obs, Mapping)
                or not _is_number(obs.get("value"))
                or not obs.get("event_time")
                or not obs.get("available_time")
            ):
                raise FactorRuleValidationError(
                    f"feature input '{name}[{idx}]' must carry value, event_time and available_time"
                )
            # Observations that are not yet available (or still inside the declared lag)
            # stay invisible: the evaluator drops them, and it raises when none remain.
            _parse_timestamp_utc(obs["available_time"], is_decision_time=False)
            _parse_timestamp_utc(obs["event_time"], is_decision_time=False)


# Design §9.2 (22 operators) + Plan §3.4 (14 time-series operators) = 36 operators.
DESIGN_9_2_OPERATORS: frozenset[str] = frozenset(
    {
        "lag",
        "delta",
        "pct_change",
        "ts_mean",
        "ts_std",
        "ts_zscore",
        "ts_rank",
        "ts_min",
        "ts_max",
        "ratio",
        "add",
        "sub",
        "mul",
        "div",
        "abs",
        "sign",
        "clip01",
        "gt",
        "lt",
        "and",
        "or",
        "not",
    }
)

PLAN_3_4_TIME_SERIES_OPERATORS: frozenset[str] = frozenset(
    {
        "ts_sum",
        "ts_corr",
        "ts_cov",
        "ts_slope",
        "ts_rsquare",
        "ts_resi",
        "ts_quantile",
        "ts_argmax",
        "ts_argmin",
        "ts_decay_linear",
        "log",
        "signed_power",
        "max2",
        "min2",
    }
)

TIME_SERIES_OPERATORS: frozenset[str] = DESIGN_9_2_OPERATORS | PLAN_3_4_TIME_SERIES_OPERATORS

CROSS_SECTIONAL_OPERATORS: frozenset[str] = frozenset(
    {
        "cs_rank",
        "cs_scale",
        "industry_neutralize",
    }
)

# Exact arity table for every supported operator.
OPERATOR_ARITY: Mapping[str, int] = {
    # Design §9.2
    "lag": 2,
    "delta": 2,
    "pct_change": 2,
    "ts_mean": 2,
    "ts_std": 2,
    "ts_zscore": 2,
    "ts_rank": 2,
    "ts_min": 2,
    "ts_max": 2,
    "ratio": 2,
    "add": 2,
    "sub": 2,
    "mul": 2,
    "div": 2,
    "abs": 1,
    "sign": 1,
    "clip01": 1,
    "gt": 2,
    "lt": 2,
    "and": 2,
    "or": 2,
    "not": 1,
    # Plan §3.4 time-series additions
    "ts_sum": 2,
    "ts_corr": 3,
    "ts_cov": 3,
    "ts_slope": 2,
    "ts_rsquare": 2,
    "ts_resi": 2,
    "ts_quantile": 3,
    "ts_argmax": 2,
    "ts_argmin": 2,
    "ts_decay_linear": 2,
    "log": 1,
    "signed_power": 2,
    "max2": 2,
    "min2": 2,
    # Plan §3.4 cross-sectional operators (parsed for AlphaTemplate, rejected in v1 evaluator)
    "cs_rank": 1,
    "cs_scale": 1,
    "industry_neutralize": 1,
}

# Operators whose second argument is an integer window/lag parameter.
_WINDOW_ARG1_OPERATORS: frozenset[str] = frozenset(
    {
        "lag",
        "delta",
        "pct_change",
        "ts_mean",
        "ts_std",
        "ts_zscore",
        "ts_rank",
        "ts_min",
        "ts_max",
        "ts_sum",
        "ts_slope",
        "ts_rsquare",
        "ts_resi",
        "ts_quantile",
        "ts_argmax",
        "ts_argmin",
        "ts_decay_linear",
    }
)

# Operators whose third argument is an integer window parameter.
_WINDOW_ARG2_OPERATORS: frozenset[str] = frozenset({"ts_corr", "ts_cov"})

# Operators that require window d >= 2 for meaningful regression/correlation/z-score.
_MIN_WINDOW_2_OPERATORS: frozenset[str] = frozenset(
    {"ts_corr", "ts_slope", "ts_rsquare", "ts_resi"}
)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_int(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float) and value.is_integer() and math.isfinite(value):
        return True
    return False


def parse_factor_expression(
    node: Any,
    *,
    allow_cross_sectional: bool = False,
    allow_literal_constants: bool = False,
    _depth: int = 1,
) -> dict[str, Any]:
    """Parse and validate a factor rule AST node into a normalized dict.

    When ``allow_cross_sectional=False`` (the default for ``factor_rule_v1``
    artifacts and per-symbol evaluation), cross-sectional operators
    (``cs_rank``, ``cs_scale``, ``industry_neutralize``) are rejected.
    When ``allow_cross_sectional=True`` (used when storing cross-sectional
    ``AlphaTemplate`` definitions), cross-sectional operators are accepted in
    the AST.
    """
    if _depth > MAX_FACTOR_AST_DEPTH:
        raise FactorRuleValidationError(
            f"factor expression exceeds maximum AST depth {MAX_FACTOR_AST_DEPTH}"
        )
    if not isinstance(node, Mapping):
        if _is_number(node) and not allow_literal_constants:
            raise FactorRuleValidationError(
                "numeric constants in factor_rule_v1 must be declared as {'const_param': '<name>'} controls, not bare literals"
            )
        raise FactorRuleValidationError("factor expression node must be a JSON object")

    keys = set(node.keys())
    if keys == {"ref"}:
        ref_name = node.get("ref")
        if not isinstance(ref_name, str) or not _IDENTIFIER_RE.match(ref_name):
            raise FactorRuleValidationError(
                f"factor expression ref must be a valid identifier, got {ref_name!r}"
            )
        return {"ref": ref_name}

    if keys == {"const_param"}:
        param_name = node.get("const_param")
        if not isinstance(param_name, str) or not _IDENTIFIER_RE.match(param_name):
            raise FactorRuleValidationError(
                f"factor expression const_param must be a valid parameter identifier, got {param_name!r}"
            )
        return {"const_param": param_name}

    if "const" in keys or "value" in keys:
        if not allow_literal_constants:
            raise FactorRuleValidationError(
                "numeric constants in factor_rule_v1 must be declared as {'const_param': '<name>'} controls, not literal constants"
            )
        lit = node.get("const", node.get("value"))
        if not _is_number(lit):
            raise FactorRuleValidationError("literal constant must be a finite number")
        return {"const": float(lit)}

    if keys != {"op", "args"}:
        raise FactorRuleValidationError(
            f"factor expression node must have keys {{'op', 'args'}}, {{'ref'}}, or {{'const_param'}}, got {sorted(keys)}"
        )

    op = node.get("op")
    if not isinstance(op, str) or not op:
        raise FactorRuleValidationError("factor expression op must be a non-empty string")

    if op in CROSS_SECTIONAL_OPERATORS:
        if not allow_cross_sectional:
            raise FactorRuleValidationError(
                f"cross-sectional operator '{op}' is not allowed in per-symbol factor_rule_v1 evaluation"
            )
    elif op not in TIME_SERIES_OPERATORS:
        raise FactorRuleValidationError(f"unknown factor operator '{op}'")

    raw_args = node.get("args")
    if not isinstance(raw_args, Sequence) or isinstance(raw_args, (str, bytes)):
        raise FactorRuleValidationError(f"operator '{op}' args must be a list")

    expected_arity = OPERATOR_ARITY[op]
    if len(raw_args) != expected_arity:
        raise FactorRuleValidationError(
            f"operator '{op}' requires {expected_arity} argument(s), got {len(raw_args)}"
        )

    parsed_args = [
        parse_factor_expression(
            arg,
            allow_cross_sectional=allow_cross_sectional,
            allow_literal_constants=allow_literal_constants,
            _depth=_depth + 1,
        )
        for arg in raw_args
    ]
    return {"op": op, "args": parsed_args}


def collect_expression_refs_and_params(
    node: Mapping[str, Any],
    *,
    refs: set[str] | None = None,
    const_params: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Return ``(refs, const_params)`` referenced anywhere inside ``node``."""
    if refs is None:
        refs = set()
    if const_params is None:
        const_params = set()

    if "ref" in node:
        refs.add(str(node["ref"]))
    elif "const_param" in node:
        const_params.add(str(node["const_param"]))
    elif "op" in node:
        for arg in node.get("args", ()):
            if isinstance(arg, Mapping):
                collect_expression_refs_and_params(arg, refs=refs, const_params=const_params)
    return refs, const_params


def _validate_window_And_param_constraints(
    node: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> None:
    """Validate operator-specific parameter constraints (e.g. integer window sizes, quantile bounds)."""
    if "op" not in node:
        return
    op = str(node["op"])
    args = list(node["args"])

    window_arg: Mapping[str, Any] | None = None
    if op in _WINDOW_ARG1_OPERATORS:
        window_arg = args[1]
    elif op in _WINDOW_ARG2_OPERATORS:
        window_arg = args[2]

    if window_arg is not None:
        if "const_param" not in window_arg:
            raise FactorRuleValidationError(
                f"operator '{op}' window/lag argument must be a {{'const_param': '<name>'}} reference"
            )
        param_name = str(window_arg["const_param"])
        if param_name not in parameters:
            raise FactorRuleValidationError(
                f"operator '{op}' references unknown parameter '{param_name}'"
            )
        raw_val = parameters[param_name]
        if not _is_int(raw_val):
            raise FactorRuleValidationError(
                f"operator '{op}' window/lag parameter '{param_name}' must be an integer, got {raw_val!r}"
            )
        win_val = int(raw_val)
        min_win = 0 if op == "lag" else (2 if op in _MIN_WINDOW_2_OPERATORS else 1)
        if win_val < min_win or win_val > MAX_FACTOR_WINDOW_BARS:
            raise FactorRuleValidationError(
                f"operator '{op}' window/lag parameter '{param_name}' must be in [{min_win}, {MAX_FACTOR_WINDOW_BARS}], got {win_val}"
            )

    if op == "ts_quantile":
        q_arg = args[2]
        if "const_param" not in q_arg:
            raise FactorRuleValidationError(
                "operator 'ts_quantile' quantile argument must be a {'const_param': '<name>'} reference"
            )
        q_param = str(q_arg["const_param"])
        if q_param not in parameters:
            raise FactorRuleValidationError(
                f"operator 'ts_quantile' references unknown parameter '{q_param}'"
            )
        q_val = parameters[q_param]
        if not _is_number(q_val) or float(q_val) < 0.0 or float(q_val) > 1.0:
            raise FactorRuleValidationError(
                f"operator 'ts_quantile' parameter '{q_param}' must be in [0.0, 1.0], got {q_val!r}"
            )

    for arg in args:
        if isinstance(arg, Mapping):
            _validate_window_And_param_constraints(arg, parameters)


def validate_factor_rule_logic(
    strategy_logic: Mapping[str, Any],
    parameters: Mapping[str, Any],
    mutation_surface: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a ``factor_rule_v1`` ``strategy_logic`` block against parameters and mutation surface."""
    if not isinstance(strategy_logic, Mapping):
        raise FactorRuleValidationError("strategy_logic must be a JSON object")
    if strategy_logic.get("kind") != "factor_rule_v1":
        raise FactorRuleValidationError(
            f"expected strategy_logic.kind == 'factor_rule_v1', got {strategy_logic.get('kind')!r}"
        )

    expected_keys = {"kind", "inputs", "signal", "entry", "exit", "strength"}
    actual_keys = set(strategy_logic.keys())
    if actual_keys != expected_keys:
        raise FactorRuleValidationError(
            f"factor_rule_v1 strategy_logic keys must be {sorted(expected_keys)}, got {sorted(actual_keys)}"
        )

    raw_inputs = strategy_logic.get("inputs")
    if not isinstance(raw_inputs, Sequence) or isinstance(raw_inputs, (str, bytes)):
        raise FactorRuleValidationError("factor_rule_v1 inputs must be a list")

    declared_inputs: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(raw_inputs):
        if not isinstance(item, Mapping):
            raise FactorRuleValidationError(f"inputs[{idx}] must be a JSON object")
        req_input_keys = {"name", "dataset", "field", "availability_lag_days"}
        if set(item.keys()) - {"availability_lag_minutes"} != req_input_keys:
            raise FactorRuleValidationError(
                f"inputs[{idx}] must have keys {sorted(req_input_keys)}, got {sorted(item.keys())}"
            )
        name = item.get("name")
        dataset = item.get("dataset")
        field = item.get("field")
        lag_days = item.get("availability_lag_days")
        if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
            raise FactorRuleValidationError(f"inputs[{idx}].name must be a valid identifier")
        if name == "signal":
            raise FactorRuleValidationError("inputs[].name cannot be reserved name 'signal'")
        if name in declared_inputs:
            raise FactorRuleValidationError(f"duplicate input name '{name}' in factor_rule_v1 inputs")
        if not isinstance(dataset, str) or not dataset.strip():
            raise FactorRuleValidationError(f"inputs[{idx}].dataset must be a non-empty string")
        if not isinstance(field, str) or not field.strip():
            raise FactorRuleValidationError(f"inputs[{idx}].field must be a non-empty string")
        if isinstance(lag_days, bool) or not isinstance(lag_days, int) or lag_days < 0:
            raise FactorRuleValidationError(
                f"inputs[{idx}].availability_lag_days must be a non-negative integer"
            )
        cat_def = get_feature_definition(dataset, field)
        lag_minutes = item.get("availability_lag_minutes")
        minute_feature = cat_def is not None and cat_def.availability_lag_unit == "minutes"
        if lag_minutes is not None and (
            not minute_feature or isinstance(lag_minutes, bool) or not isinstance(lag_minutes, int) or lag_minutes < 0
        ):
            raise FactorRuleValidationError(
                f"inputs[{idx}].availability_lag_minutes must be a non-negative integer on a minute-unit feature"
            )
        if minute_feature:
            assert cat_def is not None
            if lag_days != 0:
                raise FactorRuleValidationError(
                    f"inputs[{idx}] ({dataset}.{field}) is minute-unit; availability_lag_days must be 0"
                )
            if (lag_minutes or 0) < int(cat_def.min_availability_lag or 0):
                raise FactorRuleValidationError(
                    f"inputs[{idx}] ({dataset}.{field}) availability_lag_minutes={lag_minutes or 0} is below governed minimum {cat_def.min_availability_lag} minutes"
                )
        elif cat_def is not None and lag_days < cat_def.min_availability_lag_days:
            raise FactorRuleValidationError(
                f"inputs[{idx}] ({dataset}.{field}) availability_lag_days={lag_days} is below governed minimum {cat_def.min_availability_lag_days}"
            )
        declared_inputs[name] = {
            "name": name,
            "dataset": dataset,
            "field": field,
            "availability_lag_days": lag_days,
        }
        if lag_minutes is not None:
            declared_inputs[name]["availability_lag_minutes"] = lag_minutes

    # Parse and validate signal expression
    signal_ast = parse_factor_expression(
        strategy_logic.get("signal"),
        allow_cross_sectional=False,
        allow_literal_constants=False,
    )
    signal_refs, all_const_params = collect_expression_refs_and_params(signal_ast)
    allowed_signal_refs = set(declared_inputs.keys()) | set(BUILT_IN_PRICE_VOLUME_FIELDS)
    unknown_signal_refs = signal_refs - allowed_signal_refs
    if unknown_signal_refs:
        raise FactorRuleValidationError(
            f"signal expression references undeclared input(s): {sorted(unknown_signal_refs)}"
        )
    if "signal" in signal_refs:
        raise FactorRuleValidationError("signal expression cannot self-reference 'signal'")
    _validate_window_And_param_constraints(signal_ast, parameters)

    # Validate entry block
    raw_entry = strategy_logic.get("entry")
    if not isinstance(raw_entry, Mapping) or set(raw_entry.keys()) != {"long_when", "short_when"}:
        raise FactorRuleValidationError(
            "factor_rule_v1 entry must be an object with keys {'long_when', 'short_when'}"
        )
    long_when_raw = raw_entry.get("long_when")
    short_when_raw = raw_entry.get("short_when")
    if long_when_raw is None and short_when_raw is None:
        raise FactorRuleValidationError(
            "factor_rule_v1 entry must specify at least one of long_when or short_when"
        )

    allowed_post_signal_refs = allowed_signal_refs | {"signal"}
    long_when_ast: dict[str, Any] | None = None
    if long_when_raw is not None:
        long_when_ast = parse_factor_expression(
            long_when_raw,
            allow_cross_sectional=False,
            allow_literal_constants=False,
        )
        lw_refs, _ = collect_expression_refs_and_params(
            long_when_ast, const_params=all_const_params
        )
        if lw_refs - allowed_post_signal_refs:
            raise FactorRuleValidationError(
                f"entry.long_when references undeclared input(s): {sorted(lw_refs - allowed_post_signal_refs)}"
            )
        _validate_window_And_param_constraints(long_when_ast, parameters)

    short_when_ast: dict[str, Any] | None = None
    if short_when_raw is not None:
        short_when_ast = parse_factor_expression(
            short_when_raw,
            allow_cross_sectional=False,
            allow_literal_constants=False,
        )
        sw_refs, _ = collect_expression_refs_and_params(
            short_when_ast, const_params=all_const_params
        )
        if sw_refs - allowed_post_signal_refs:
            raise FactorRuleValidationError(
                f"entry.short_when references undeclared input(s): {sorted(sw_refs - allowed_post_signal_refs)}"
            )
        _validate_window_And_param_constraints(short_when_ast, parameters)

    # Validate exit block
    raw_exit = strategy_logic.get("exit")
    if not isinstance(raw_exit, Mapping) or set(raw_exit.keys()) != {"max_holding_bars", "flat_when"}:
        raise FactorRuleValidationError(
            "factor_rule_v1 exit must be an object with keys {'max_holding_bars', 'flat_when'}"
        )
    max_hold_raw = raw_exit.get("max_holding_bars")
    flat_when_raw = raw_exit.get("flat_when")
    if max_hold_raw is None and flat_when_raw is None:
        raise FactorRuleValidationError(
            "factor_rule_v1 exit must specify at least one of max_holding_bars or flat_when"
        )

    max_hold_ast: dict[str, Any] | None = None
    if max_hold_raw is not None:
        if not isinstance(max_hold_raw, Mapping) or set(max_hold_raw.keys()) != {"const_param"}:
            raise FactorRuleValidationError(
                "exit.max_holding_bars must be a {'const_param': '<name>'} reference or null"
            )
        hold_param = max_hold_raw.get("const_param")
        if not isinstance(hold_param, str) or not _IDENTIFIER_RE.match(hold_param):
            raise FactorRuleValidationError(
                "exit.max_holding_bars.const_param must be a valid parameter identifier"
            )
        all_const_params.add(hold_param)
        if hold_param in parameters:
            hold_val = parameters[hold_param]
            if not _is_int(hold_val) or int(hold_val) < 1:
                raise FactorRuleValidationError(
                    f"exit.max_holding_bars parameter '{hold_param}' must be a positive integer, got {hold_val!r}"
                )
        max_hold_ast = {"const_param": hold_param}

    flat_when_ast: dict[str, Any] | None = None
    if flat_when_raw is not None:
        flat_when_ast = parse_factor_expression(
            flat_when_raw,
            allow_cross_sectional=False,
            allow_literal_constants=False,
        )
        fw_refs, _ = collect_expression_refs_and_params(
            flat_when_ast, const_params=all_const_params
        )
        if fw_refs - allowed_post_signal_refs:
            raise FactorRuleValidationError(
                f"exit.flat_when references undeclared input(s): {sorted(fw_refs - allowed_post_signal_refs)}"
            )
        _validate_window_And_param_constraints(flat_when_ast, parameters)

    # Validate strength expression
    strength_ast = parse_factor_expression(
        strategy_logic.get("strength"),
        allow_cross_sectional=False,
        allow_literal_constants=False,
    )
    st_refs, _ = collect_expression_refs_and_params(
        strength_ast, const_params=all_const_params
    )
    if st_refs - allowed_post_signal_refs:
        raise FactorRuleValidationError(
            f"strength expression references undeclared input(s): {sorted(st_refs - allowed_post_signal_refs)}"
        )
    _validate_window_And_param_constraints(strength_ast, parameters)

    # Every referenced const_param must exist in parameters as a finite number
    # and must be a declared mutable control in mutation_surface.controls.
    for param_name in sorted(all_const_params):
        if param_name not in parameters:
            raise FactorRuleValidationError(
                f"factor_rule_v1 const_param '{param_name}' is missing from parameters"
            )
        param_val = parameters[param_name]
        if not _is_number(param_val):
            raise FactorRuleValidationError(
                f"factor_rule_v1 const_param '{param_name}' must have a numeric parameter value, got {param_val!r}"
            )

    if mutation_surface is not None:
        controls = mutation_surface.get("controls")
        immutable = set(mutation_surface.get("immutable_parameters") or ())
        control_map: dict[str, Mapping[str, Any]] = {}
        if isinstance(controls, Sequence) and not isinstance(controls, (str, bytes)):
            for ctrl in controls:
                if isinstance(ctrl, Mapping):
                    key = ctrl.get("parameter_key") or ctrl.get("parameter")
                    if isinstance(key, str) and key:
                        control_map[key] = ctrl

        for param_name in sorted(all_const_params):
            if param_name in immutable:
                raise FactorRuleValidationError(
                    f"factor_rule_v1 numeric constant '{param_name}' must be a mutable control, not in immutable_parameters"
                )
            if param_name not in control_map:
                raise FactorRuleValidationError(
                    f"factor_rule_v1 numeric constant '{param_name}' must be declared in mutation_surface.controls with an allowed_range and step"
                )
            ctrl = control_map[param_name]
            allowed_range = ctrl.get("allowed_range")
            step = ctrl.get("step")
            valid_range = False
            if isinstance(allowed_range, Mapping):
                r_min = allowed_range.get("min")
                r_max = allowed_range.get("max")
                valid_range = (
                    _is_number(r_min)
                    and _is_number(r_max)
                    and float(r_min) <= float(r_max)
                )
            elif isinstance(allowed_range, Sequence) and not isinstance(allowed_range, (str, bytes)) and len(allowed_range) == 2:
                valid_range = (
                    _is_number(allowed_range[0])
                    and _is_number(allowed_range[1])
                    and float(allowed_range[0]) <= float(allowed_range[1])
                )
            if not valid_range or not _is_number(step) or float(step) <= 0.0:
                raise FactorRuleValidationError(
                    f"factor_rule_v1 control '{param_name}' must declare a valid allowed_range and positive step"
                )

    return {
        "kind": "factor_rule_v1",
        "inputs": [declared_inputs[item["name"]] for item in raw_inputs],
        "signal": signal_ast,
        "entry": {
            "long_when": long_when_ast,
            "short_when": short_when_ast,
        },
        "exit": {
            "max_holding_bars": max_hold_ast,
            "flat_when": flat_when_ast,
        },
        "strength": strength_ast,
    }


def _parse_timestamp_utc(value: Any, *, is_decision_time: bool = False) -> datetime:
    """Parse an ISO-8601 timestamp, date string, or datetime into an aware UTC datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        t = time(23, 59, 59, 999999) if is_decision_time else time(0, 0, 0)
        return datetime.combine(value, t, tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise FactorRuleValidationError(f"invalid timestamp value: {value!r}")
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError as exc:
            raise FactorRuleValidationError(f"invalid ISO date: {value!r}") from exc
        t = time(23, 59, 59, 999999) if is_decision_time else time(0, 0, 0)
        return datetime.combine(parsed_date, t, tzinfo=timezone.utc)

    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FactorRuleValidationError(f"invalid ISO timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_TAIPEI_TZ = timezone(timedelta(hours=8))


def _filter_point_in_time_series(
    raw_series: Any,
    *,
    input_name: str,
    availability_lag_days: int,
    decision_time: str | datetime | date | None,
    lag_unit: str = "days",
    session_reset: bool = False,
) -> tuple[float, ...]:
    """Filter a feature series so only observations available at or before ``decision_time`` are visible.

    ``lag_unit="minutes"`` applies the lag in minutes (intraday features).  With
    ``session_reset`` only observations of the latest visible Asia/Taipei session
    are returned, so a window never spans the overnight gap (SD §6.3).
    """
    lag_delta = (
        timedelta(minutes=max(0, int(availability_lag_days)))
        if lag_unit == "minutes"
        else timedelta(days=max(0, int(availability_lag_days)))
    )
    session_days: list[date | None] = []
    if not isinstance(raw_series, Sequence) or isinstance(raw_series, (str, bytes)):
        raise FactorRuleValidationError(
            f"feature input '{input_name}' must be a sequence of observations, got {type(raw_series).__name__}"
        )
    if len(raw_series) == 0:
        return ()

    decision_dt: datetime | None = (
        _parse_timestamp_utc(decision_time, is_decision_time=True)
        if decision_time is not None
        else None
    )

    visible_values: list[float] = []
    for idx, item in enumerate(raw_series):
        if _is_number(item):
            visible_values.append(float(item))
            session_days.append(None)
            continue

        if isinstance(item, Mapping):
            raw_val = item.get("value")
            if not _is_number(raw_val):
                raise FactorRuleValidationError(
                    f"feature input '{input_name}[{idx}].value' must be a finite number"
                )
            avail_raw = item.get("available_time")
            event_raw = item.get("event_time", item.get("date", item.get("as_of")))

            if avail_raw is None and event_raw is None:
                visible_values.append(float(raw_val))
                session_days.append(None)
                continue

            if decision_dt is None:
                raise FactorRuleValidationError(
                    f"decision_time is required when evaluating point-in-time timestamped feature input '{input_name}'"
                )

            effective_avail: datetime | None = None
            event_dt: datetime | None = None
            if avail_raw is not None:
                effective_avail = _parse_timestamp_utc(avail_raw, is_decision_time=False)
            if event_raw is not None:
                event_dt = _parse_timestamp_utc(event_raw, is_decision_time=False)
                lagged_dt = event_dt + lag_delta
                if effective_avail is None or lagged_dt > effective_avail:
                    effective_avail = lagged_dt

            assert effective_avail is not None
            if effective_avail <= decision_dt:
                visible_values.append(float(raw_val))
                session_days.append((event_dt or effective_avail).astimezone(_TAIPEI_TZ).date())
            continue

        if isinstance(item, (tuple, list)) and len(item) == 2:
            ts_raw, raw_val = item
            if not _is_number(raw_val):
                raise FactorRuleValidationError(
                    f"feature input '{input_name}[{idx}][1]' must be a finite number"
                )
            if decision_dt is None:
                raise FactorRuleValidationError(
                    f"decision_time is required when evaluating point-in-time timestamped feature input '{input_name}'"
                )
            avail_dt = _parse_timestamp_utc(ts_raw, is_decision_time=False)
            if avail_dt + lag_delta <= decision_dt:
                visible_values.append(float(raw_val))
                session_days.append(avail_dt.astimezone(_TAIPEI_TZ).date())
            continue

        raise FactorRuleValidationError(
            f"feature input '{input_name}[{idx}]' must be a finite number or point-in-time observation mapping"
        )

    if session_reset and session_days and session_days[-1] is not None:
        last_day = session_days[-1]
        return tuple(v for v, d in zip(visible_values, session_days) if d == last_day)
    return tuple(visible_values)


def _lookup_raw_feature(
    features: Mapping[str, Any],
    *,
    name: str,
    dataset: str,
    field: str,
) -> Any | None:
    """Locate a feature series in ``features`` by input name, ``dataset.field``, nested mapping, or field."""
    if name in features and features[name] is not None:
        return features[name]
    qualified_dot = f"{dataset}.{field}"
    if qualified_dot in features and features[qualified_dot] is not None:
        return features[qualified_dot]
    qualified_colon = f"{dataset}:{field}"
    if qualified_colon in features and features[qualified_colon] is not None:
        return features[qualified_colon]
    ds_entry = features.get(dataset)
    if isinstance(ds_entry, Mapping) and field in ds_entry and ds_entry[field] is not None:
        return ds_entry[field]
    if field in features and features[field] is not None:
        return features[field]
    return None


def _derive_vwap_series(
    features: Mapping[str, Any],
    close_series: tuple[float, ...],
    *,
    decision_time: str | datetime | date | None,
) -> tuple[float, ...]:
    """Derive ``vwap`` from ``TradeValue / TradeVolume``, ``(high + low + close) / 3``, or ``close``."""
    trade_val_raw = features.get("TradeValue", features.get("trade_value"))
    trade_vol_raw = features.get("TradeVolume", features.get("trade_volume", features.get("volume")))
    if trade_val_raw is not None and trade_vol_raw is not None:
        val_series = _filter_point_in_time_series(
            trade_val_raw,
            input_name="TradeValue",
            availability_lag_days=0,
            decision_time=decision_time,
        )
        vol_series = _filter_point_in_time_series(
            trade_vol_raw,
            input_name="TradeVolume",
            availability_lag_days=0,
            decision_time=decision_time,
        )
        n = min(len(val_series), len(vol_series))
        if n > 0:
            fallback_closes = close_series[-n:] if len(close_series) >= n else (0.0,) * n
            return tuple(
                (val_series[-n + i] / vol_series[-n + i])
                if abs(vol_series[-n + i]) > _EPSILON
                else fallback_closes[i]
                for i in range(n)
            )

    high_raw = features.get("high")
    low_raw = features.get("low")
    if high_raw is not None and low_raw is not None and len(close_series) > 0:
        high_s = _filter_point_in_time_series(
            high_raw,
            input_name="high",
            availability_lag_days=0,
            decision_time=decision_time,
        )
        low_s = _filter_point_in_time_series(
            low_raw,
            input_name="low",
            availability_lag_days=0,
            decision_time=decision_time,
        )
        n = min(len(high_s), len(low_s), len(close_series))
        if n > 0:
            return tuple(
                (high_s[-n + i] + low_s[-n + i] + close_series[-n + i]) / 3.0
                for i in range(n)
            )

    return close_series


def _latest_visible_versions(
    raw_series: Any,
    *,
    availability_lag_days: int,
    decision_time: str | datetime | date | None,
    lag_unit: str = "days",
) -> Any:
    """Governed read for catalogued non-price features.

    Keeps exactly the latest *visible* version per (entity, event_time) so a late
    revision replaces, rather than adds, a daily observation and replayed rows collapse.
    Catalogue lag and any row-level ``availability_lag_days`` are applied; ordering is
    by event time. Series without timestamped mapping rows pass through unchanged.
    """
    if (
        not isinstance(raw_series, Sequence)
        or isinstance(raw_series, (str, bytes))
        or decision_time is None
        or not all(
            isinstance(o, Mapping) and o.get("event_time") and o.get("available_time")
            for o in raw_series
        )
    ):
        return raw_series
    decision_dt = _parse_timestamp_utc(decision_time, is_decision_time=True)
    latest: dict[tuple[Any, datetime], tuple[tuple[datetime, str], Mapping[str, Any]]] = {}
    for obs in raw_series:
        event_dt = _parse_timestamp_utc(obs["event_time"])
        avail_dt = _parse_timestamp_utc(obs["available_time"])
        row_lag = obs.get("availability_lag_days")
        lag = max(
            int(availability_lag_days),
            row_lag if isinstance(row_lag, int) and not isinstance(row_lag, bool) else 0,
        )
        lag_delta = timedelta(minutes=lag) if lag_unit == "minutes" else timedelta(days=lag)
        if avail_dt > decision_dt or event_dt + lag_delta > decision_dt:
            continue
        key = (obs.get("entity"), event_dt)
        rank = (avail_dt, str(obs.get("ingest_time") or ""))
        if key not in latest or rank >= latest[key][0]:
            latest[key] = (rank, obs)
    ordered = sorted(latest.items(), key=lambda kv: (kv[0][1], str(kv[0][0] or "")))
    return [entry[1][1] for entry in ordered]


def resolve_point_in_time_inputs(
    strategy_logic: Mapping[str, Any],
    closes: Sequence[float] | None,
    *,
    features: Mapping[str, Any] | None = None,
    decision_time: str | datetime | date | None = None,
) -> dict[str, tuple[float, ...]]:
    """Resolve and point-in-time filter all inputs required by ``strategy_logic``."""
    feature_map: Mapping[str, Any] = features if isinstance(features, Mapping) else {}

    close_series: tuple[float, ...] = ()
    if closes is not None and len(closes) > 0:
        close_series = _filter_point_in_time_series(
            closes,
            input_name="close",
            availability_lag_days=0,
            decision_time=decision_time,
        )
    elif "close" in feature_map and feature_map["close"] is not None:
        close_series = _filter_point_in_time_series(
            feature_map["close"],
            input_name="close",
            availability_lag_days=0,
            decision_time=decision_time,
        )

    env: dict[str, tuple[float, ...]] = {}
    if close_series:
        env["close"] = close_series

    # Populate built-in price-volume fields if available in features
    for pv_field in BUILT_IN_PRICE_VOLUME_FIELDS:
        if pv_field == "close":
            continue
        raw_pv = feature_map.get(pv_field)
        if raw_pv is not None:
            env[pv_field] = _filter_point_in_time_series(
                raw_pv,
                input_name=pv_field,
                availability_lag_days=0,
                decision_time=decision_time,
            )
        elif pv_field == "vwap" and close_series:
            env["vwap"] = _derive_vwap_series(
                feature_map, close_series, decision_time=decision_time
            )

    # Resolve declared inputs[]
    for item in strategy_logic.get("inputs", ()):
        if not isinstance(item, Mapping):
            continue
        name = str(item["name"])
        dataset = str(item["dataset"])
        field = str(item["field"])
        lag_days = int(item.get("availability_lag_days", 0))
        lag_unit = "days"
        session_reset = False
        cat_def = get_feature_definition(dataset, field)
        if cat_def is not None and cat_def.availability_lag_unit == "minutes":
            # Minute features lag in minutes beyond bar close; windows reset per session.
            lag_unit = "minutes"
            lag_days = max(int(item.get("availability_lag_minutes", 0)), int(cat_def.min_availability_lag or 0))
            session_reset = not cat_def.cross_session
        elif cat_def is not None:
            lag_days = max(lag_days, cat_def.min_availability_lag_days)

        raw_series = _lookup_raw_feature(
            feature_map, name=name, dataset=dataset, field=field
        )
        if raw_series is not None and dataset in INTRADAY_PRICE_VOLUME_DATASETS:
            _require_minute_observations(feature_map, name=name, dataset=dataset, field=field)
        if raw_series is None and dataset in INTRADAY_PRICE_VOLUME_DATASETS:
            raise FactorRuleValidationError(
                f"missing required minute feature input '{name}' ({dataset}.{field}); "
                "daily/generic close or synthetic vwap fallback is not allowed"
            )
        if raw_series is None:
            if field == "close" and close_series:
                env[name] = close_series
                continue
            if field == "vwap" and "vwap" in env:
                env[name] = env["vwap"]
                continue
            raise FactorRuleValidationError(
                f"missing required feature input '{name}' ({dataset}.{field})"
            )

        if dataset not in PRICE_VOLUME_DATASETS:
            raw_series = _latest_visible_versions(
                raw_series,
                availability_lag_days=lag_days,
                decision_time=decision_time,
                lag_unit=lag_unit,
            )
        filtered = _filter_point_in_time_series(
            raw_series,
            input_name=name,
            availability_lag_days=lag_days,
            decision_time=decision_time,
            lag_unit=lag_unit,
            session_reset=session_reset,
        )
        if len(filtered) == 0:
            raise FactorRuleValidationError(
                f"feature input '{name}' ({dataset}.{field}) has no visible observations at decision_time={decision_time!r}"
            )
        env[name] = filtered

    return env


def required_history_bars(
    strategy_logic: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> int:
    """Return the minimum number of point-in-time observations required to evaluate ``strategy_logic``."""

    def _node_bars(node: Mapping[str, Any] | None, signal_bars: int) -> int:
        if node is None:
            return 1
        if "const_param" in node or "const" in node:
            return 1
        if "ref" in node:
            return signal_bars if node["ref"] == "signal" else 1
        op = str(node["op"])
        args = list(node["args"])
        if op in {"lag", "delta", "pct_change"}:
            base = _node_bars(args[0], signal_bars)
            k = int(_resolve_scalar_param(args[1], parameters))
            return base + max(0, k)
        if op in _WINDOW_ARG1_OPERATORS:
            base = _node_bars(args[0], signal_bars)
            d = int(_resolve_scalar_param(args[1], parameters))
            return base + max(1, d) - 1
        if op in _WINDOW_ARG2_OPERATORS:
            b0 = _node_bars(args[0], signal_bars)
            b1 = _node_bars(args[1], signal_bars)
            d = int(_resolve_scalar_param(args[2], parameters))
            return max(b0, b1) + max(1, d) - 1
        return max(_node_bars(arg, signal_bars) for arg in args)

    sig_bars = _node_bars(strategy_logic.get("signal"), 1)
    entry = strategy_logic.get("entry") or {}
    exit_block = strategy_logic.get("exit") or {}
    return max(
        sig_bars,
        _node_bars(entry.get("long_when"), sig_bars),
        _node_bars(entry.get("short_when"), sig_bars),
        _node_bars(exit_block.get("flat_when"), sig_bars),
        _node_bars(strategy_logic.get("strength"), sig_bars),
    )


def _resolve_scalar_param(node: Mapping[str, Any], parameters: Mapping[str, Any]) -> float:
    if "const_param" in node:
        key = str(node["const_param"])
        if key not in parameters or not _is_number(parameters[key]):
            raise FactorRuleValidationError(
                f"parameter '{key}' is missing or non-numeric in parameters"
            )
        return float(parameters[key])
    if "const" in node and _is_number(node["const"]):
        return float(node["const"])
    raise FactorRuleValidationError(
        f"expected const_param node for scalar parameter, got {node!r}"
    )


def _align_two_series(
    a: list[float],
    b: list[float],
    *,
    is_a_scalar: bool,
    is_b_scalar: bool,
) -> tuple[list[float], list[float]]:
    if is_a_scalar and is_b_scalar:
        return [a[-1]], [b[-1]]
    if is_a_scalar:
        return [a[-1]] * len(b), b
    if is_b_scalar:
        return a, [b[-1]] * len(a)
    n = min(len(a), len(b))
    if n == 0:
        return [], []
    return a[-n:], b[-n:]


def _window_mean_std(w: Sequence[float]) -> tuple[float, float]:
    d = len(w)
    mean = sum(w) / d
    var = sum((v - mean) ** 2 for v in w) / d
    return mean, math.sqrt(max(0.0, var))


def _window_ols(w: Sequence[float]) -> tuple[float, float, float]:
    """Return ``(slope, rsquare, latest_residual)`` of ``w`` regressed on ``t = 0 .. d - 1``."""
    d = len(w)
    if d < 2:
        return 0.0, 0.0, 0.0
    t_mean = (d - 1) / 2.0
    t_var = (d * d - 1) / 12.0
    y_mean = sum(w) / d
    cov_ty = sum((i - t_mean) * (w[i] - y_mean) for i in range(d)) / d
    y_var = sum((v - y_mean) ** 2 for v in w) / d
    if t_var <= _EPSILON:
        return 0.0, 0.0, 0.0
    slope = cov_ty / t_var
    intercept = y_mean - slope * t_mean
    fitted_last = intercept + slope * (d - 1)
    resi = w[-1] - fitted_last
    if y_var <= _EPSILON:
        rsquare = 0.0
    else:
        rsquare = min(1.0, max(0.0, (cov_ty * cov_ty) / (t_var * y_var)))
    return slope, rsquare, resi


def _eval_node_series(
    node: Mapping[str, Any],
    env: Mapping[str, tuple[float, ...]],
    parameters: Mapping[str, Any],
) -> tuple[list[float], bool]:
    """Evaluate an AST node into ``(series, is_scalar_constant)``."""
    if "const_param" in node or "const" in node:
        return [_resolve_scalar_param(node, parameters)], True

    if "ref" in node:
        ref_name = str(node["ref"])
        if ref_name not in env:
            raise FactorRuleValidationError(f"missing series for input reference '{ref_name}'")
        series = list(env[ref_name])
        if not series:
            raise FactorRuleValidationError(f"input reference '{ref_name}' has empty series")
        return series, False

    op = str(node.get("op", ""))
    if op in CROSS_SECTIONAL_OPERATORS:
        raise FactorRuleValidationError(
            f"cross-sectional operator '{op}' is not allowed in per-symbol factor_rule_v1 evaluation"
        )
    if op not in TIME_SERIES_OPERATORS:
        raise FactorRuleValidationError(f"unknown factor operator '{op}'")

    args = list(node.get("args", ()))
    expected_arity = OPERATOR_ARITY[op]
    if len(args) != expected_arity:
        raise FactorRuleValidationError(
            f"operator '{op}' requires {expected_arity} argument(s), got {len(args)}"
        )

    # Unary elementwise operators
    if op in {"abs", "sign", "clip01", "log", "not"}:
        s0, sc0 = _eval_node_series(args[0], env, parameters)
        out: list[float] = []
        for v in s0:
            if not math.isfinite(v):
                out.append(math.nan)
            elif op == "abs":
                out.append(abs(v))
            elif op == "sign":
                out.append(1.0 if v > 0.0 else (-1.0 if v < 0.0 else 0.0))
            elif op == "clip01":
                out.append(min(1.0, max(0.0, v)))
            elif op == "log":
                out.append(math.log(v) if v > _EPSILON else math.nan)
            elif op == "not":
                out.append(0.0 if v > 0.0 else 1.0)
        return out, sc0

    # Binary elementwise operators
    if op in {
        "add",
        "sub",
        "mul",
        "div",
        "ratio",
        "signed_power",
        "max2",
        "min2",
        "gt",
        "lt",
        "and",
        "or",
    }:
        s0, sc0 = _eval_node_series(args[0], env, parameters)
        s1, sc1 = _eval_node_series(args[1], env, parameters)
        a0, a1 = _align_two_series(s0, s1, is_a_scalar=sc0, is_b_scalar=sc1)
        out = []
        for x, y in zip(a0, a1):
            if not (math.isfinite(x) and math.isfinite(y)):
                out.append(math.nan)
            elif op == "add":
                out.append(x + y)
            elif op == "sub":
                out.append(x - y)
            elif op == "mul":
                out.append(x * y)
            elif op in {"div", "ratio"}:
                out.append((x / y) if abs(y) > _EPSILON else 0.0)
            elif op == "signed_power":
                if abs(x) <= _EPSILON:
                    out.append(0.0)
                else:
                    try:
                        val = math.copysign(abs(x) ** y, x)
                    except (OverflowError, ValueError):
                        val = math.nan
                    out.append(val if math.isfinite(val) else math.nan)
            elif op == "max2":
                out.append(max(x, y))
            elif op == "min2":
                out.append(min(x, y))
            elif op == "gt":
                out.append(1.0 if x > y else 0.0)
            elif op == "lt":
                out.append(1.0 if x < y else 0.0)
            elif op == "and":
                out.append(1.0 if (x > 0.0 and y > 0.0) else 0.0)
            elif op == "or":
                out.append(1.0 if (x > 0.0 or y > 0.0) else 0.0)
        return out, (sc0 and sc1)

    # Lag / difference / percent-change operators
    if op in {"lag", "delta", "pct_change"}:
        s0, _ = _eval_node_series(args[0], env, parameters)
        k_val = _resolve_scalar_param(args[1], parameters)
        if not _is_int(k_val):
            raise FactorRuleValidationError(f"operator '{op}' lag k must be an integer")
        k = int(k_val)
        min_k = 0 if op == "lag" else 1
        if k < min_k or k > MAX_FACTOR_WINDOW_BARS:
            raise FactorRuleValidationError(
                f"operator '{op}' lag k must be in [{min_k}, {MAX_FACTOR_WINDOW_BARS}], got {k}"
            )
        n = len(s0)
        out = [math.nan] * n
        for t in range(k, n):
            cur = s0[t]
            prev = s0[t - k]
            if not (math.isfinite(cur) and math.isfinite(prev)):
                continue
            if op == "lag":
                out[t] = prev
            elif op == "delta":
                out[t] = cur - prev
            elif op == "pct_change":
                out[t] = ((cur - prev) / prev) if abs(prev) > _EPSILON else 0.0
        return out, False

    # Single-series rolling window operators
    if op in {
        "ts_mean",
        "ts_std",
        "ts_zscore",
        "ts_rank",
        "ts_min",
        "ts_max",
        "ts_sum",
        "ts_slope",
        "ts_rsquare",
        "ts_resi",
        "ts_quantile",
        "ts_argmax",
        "ts_argmin",
        "ts_decay_linear",
    }:
        s0, _ = _eval_node_series(args[0], env, parameters)
        d_val = _resolve_scalar_param(args[1], parameters)
        if not _is_int(d_val):
            raise FactorRuleValidationError(f"operator '{op}' window d must be an integer")
        d = int(d_val)
        min_d = 2 if op in _MIN_WINDOW_2_OPERATORS else 1
        if d < min_d or d > MAX_FACTOR_WINDOW_BARS:
            raise FactorRuleValidationError(
                f"operator '{op}' window d must be in [{min_d}, {MAX_FACTOR_WINDOW_BARS}], got {d}"
            )
        q_param = 0.5
        if op == "ts_quantile":
            q_param = _resolve_scalar_param(args[2], parameters)
            if q_param < 0.0 or q_param > 1.0:
                raise FactorRuleValidationError(
                    f"operator 'ts_quantile' q must be in [0.0, 1.0], got {q_param}"
                )

        n = len(s0)
        out = [math.nan] * n
        denom_decay = d * (d + 1) / 2.0
        for t in range(d - 1, n):
            w = s0[t - d + 1 : t + 1]
            if not all(math.isfinite(v) for v in w):
                continue
            if op == "ts_mean":
                out[t] = sum(w) / d
            elif op == "ts_std":
                _, std = _window_mean_std(w)
                out[t] = std
            elif op == "ts_zscore":
                mean, std = _window_mean_std(w)
                out[t] = ((w[-1] - mean) / std) if std > _EPSILON else 0.0
            elif op == "ts_rank":
                if d == 1:
                    out[t] = 0.5
                else:
                    last = w[-1]
                    less = sum(1 for v in w[:-1] if v < last)
                    equal = sum(1 for v in w[:-1] if v == last)
                    out[t] = (less + 0.5 * equal) / (d - 1)
            elif op == "ts_min":
                out[t] = min(w)
            elif op == "ts_max":
                out[t] = max(w)
            elif op == "ts_sum":
                out[t] = sum(w)
            elif op in {"ts_slope", "ts_rsquare", "ts_resi"}:
                slope, rsq, resi = _window_ols(w)
                if op == "ts_slope":
                    out[t] = slope
                elif op == "ts_rsquare":
                    out[t] = rsq
                else:
                    out[t] = resi
            elif op == "ts_quantile":
                sw = sorted(w)
                if d == 1:
                    out[t] = sw[0]
                else:
                    pos = q_param * (d - 1)
                    lo = int(math.floor(pos))
                    hi = int(math.ceil(pos))
                    frac = pos - lo
                    out[t] = sw[lo] + frac * (sw[hi] - sw[lo])
            elif op == "ts_argmax":
                best_idx = 0
                best_val = w[0]
                for i in range(1, d):
                    if w[i] >= best_val:
                        best_val = w[i]
                        best_idx = i
                out[t] = float(best_idx)
            elif op == "ts_argmin":
                best_idx = 0
                best_val = w[0]
                for i in range(1, d):
                    if w[i] <= best_val:
                        best_val = w[i]
                        best_idx = i
                out[t] = float(best_idx)
            elif op == "ts_decay_linear":
                out[t] = sum((i + 1) * w[i] for i in range(d)) / denom_decay
        return out, False

    # Two-series rolling window operators (ts_corr, ts_cov)
    if op in {"ts_corr", "ts_cov"}:
        s0, sc0 = _eval_node_series(args[0], env, parameters)
        s1, sc1 = _eval_node_series(args[1], env, parameters)
        d_val = _resolve_scalar_param(args[2], parameters)
        if not _is_int(d_val):
            raise FactorRuleValidationError(f"operator '{op}' window d must be an integer")
        d = int(d_val)
        min_d = 2 if op == "ts_corr" else 1
        if d < min_d or d > MAX_FACTOR_WINDOW_BARS:
            raise FactorRuleValidationError(
                f"operator '{op}' window d must be in [{min_d}, {MAX_FACTOR_WINDOW_BARS}], got {d}"
            )
        a0, a1 = _align_two_series(s0, s1, is_a_scalar=sc0, is_b_scalar=sc1)
        n = len(a0)
        out = [math.nan] * n
        for t in range(d - 1, n):
            wx = a0[t - d + 1 : t + 1]
            wy = a1[t - d + 1 : t + 1]
            if not (all(math.isfinite(v) for v in wx) and all(math.isfinite(v) for v in wy)):
                continue
            mx, stdx = _window_mean_std(wx)
            my, stdy = _window_mean_std(wy)
            cov = sum((wx[i] - mx) * (wy[i] - my) for i in range(d)) / d
            if op == "ts_cov":
                out[t] = cov
            else:
                if stdx <= _EPSILON or stdy <= _EPSILON:
                    out[t] = 0.0
                else:
                    out[t] = min(1.0, max(-1.0, cov / (stdx * stdy)))
        return out, False

    raise FactorRuleValidationError(f"unhandled operator '{op}'")


def evaluate_factor_expression(
    node: Mapping[str, Any],
    env: Mapping[str, Sequence[float]],
    parameters: Mapping[str, Any],
) -> float:
    """Evaluate a single factor expression AST node and return its latest finite float value."""
    parsed = parse_factor_expression(
        node, allow_cross_sectional=False, allow_literal_constants=False
    )
    tuple_env = {k: tuple(float(v) for v in seq) for k, seq in env.items()}
    series, _ = _eval_node_series(parsed, tuple_env, parameters)
    if not series or not math.isfinite(series[-1]):
        raise FactorRuleValidationError(
            "factor expression requires sufficient history to produce a finite value"
        )
    return float(series[-1])


def evaluate_factor_rule_view(
    strategy_artifact: Mapping[str, Any],
    closes: Sequence[float] | None,
    *,
    features: Mapping[str, Any] | None = None,
    decision_time: str | datetime | date | None = None,
    holding_bars: int | None = None,
    current_direction: str | None = None,
) -> dict[str, Any]:
    """Evaluate a ``factor_rule_v1`` artifact into a structured directional view.

    Returns ``{"action", "direction", "signal", "momentum", "strength"}``,
    preserving full compatibility with the 2026-11-07 expression layer and
    vectorbt backtest adapter.
    """
    strategy_logic = strategy_artifact.get("strategy_logic")
    parameters = strategy_artifact.get("parameters")
    mutation_surface = strategy_artifact.get("mutation_surface")
    if not isinstance(strategy_logic, Mapping) or not isinstance(parameters, Mapping):
        raise FactorRuleValidationError(
            "strategy_artifact must include strategy_logic and parameters mappings"
        )

    validated_logic = validate_factor_rule_logic(
        strategy_logic,
        parameters,
        mutation_surface if isinstance(mutation_surface, Mapping) else None,
    )

    # Shared evaluator (research/vectorbt + paper): features may only enter through
    # the governed catalogue with timestamped point-in-time observations.
    assert_governed_feature_inputs(validated_logic)
    assert_governed_feature_observations(validated_logic, features, decision_time)

    env = resolve_point_in_time_inputs(
        validated_logic,
        closes,
        features=features,
        decision_time=decision_time,
    )

    signal_series, _ = _eval_node_series(validated_logic["signal"], env, parameters)
    if not signal_series or not math.isfinite(signal_series[-1]):
        raise FactorRuleValidationError(
            "factor_rule_v1 signal requires sufficient point-in-time history to produce a finite value"
        )

    post_env = dict(env)
    post_env["signal"] = tuple(signal_series)
    n = len(signal_series)

    long_node = validated_logic["entry"]["long_when"]
    short_node = validated_logic["entry"]["short_when"]
    flat_node = validated_logic["exit"]["flat_when"]
    max_hold_node = validated_logic["exit"]["max_holding_bars"]

    def _to_len_n(node_or_none: Mapping[str, Any] | None) -> list[float]:
        if node_or_none is None:
            return [0.0] * n
        s, is_sc = _eval_node_series(node_or_none, post_env, parameters)
        if is_sc:
            return [s[-1]] * n
        if len(s) >= n:
            return s[-n:]
        return ([math.nan] * (n - len(s))) + s

    long_series = _to_len_n(long_node)
    short_series = _to_len_n(short_node)
    flat_series = _to_len_n(flat_node)
    strength_series = _to_len_n(validated_logic["strength"])

    max_hold_bars: int | None = None
    if max_hold_node is not None:
        max_hold_bars = int(_resolve_scalar_param(max_hold_node, parameters))

    # Deterministic bar-by-bar state machine across valid signal bars so
    # max_holding_bars works identically in backtest and paper replay.
    pos_dir = "FLAT"
    bars_held = 0
    action = "HOLD"
    direction = "FLAT"

    for i in range(n):
        sig_i = signal_series[i]
        if not math.isfinite(sig_i):
            continue

        if i == n - 1:
            if current_direction in {"LONG", "SHORT", "FLAT"}:
                pos_dir = current_direction
            if holding_bars is not None:
                bars_held = int(holding_bars)
            elif pos_dir in {"LONG", "SHORT"}:
                bars_held += 1
        else:
            if pos_dir in {"LONG", "SHORT"}:
                bars_held += 1

        l_trig = math.isfinite(long_series[i]) and long_series[i] > 0.0
        s_trig = math.isfinite(short_series[i]) and short_series[i] > 0.0
        f_trig = math.isfinite(flat_series[i]) and flat_series[i] > 0.0
        mh_trig = (
            max_hold_bars is not None
            and pos_dir in {"LONG", "SHORT"}
            and bars_held >= max_hold_bars
        )

        if mh_trig:
            action = "BUY" if pos_dir == "SHORT" else "SELL"
            pos_dir = "FLAT"
            bars_held = 0
        elif s_trig:
            action = "SELL"
            if pos_dir != "SHORT":
                pos_dir = "SHORT"
                bars_held = 0
        elif f_trig:
            action = "BUY" if pos_dir == "SHORT" else "SELL"
            pos_dir = "FLAT"
            bars_held = 0
        elif l_trig:
            action = "BUY"
            if pos_dir != "LONG":
                pos_dir = "LONG"
                bars_held = 0
        else:
            action = "HOLD"

        if action == "BUY":
            direction = "LONG"
        elif action == "SELL":
            direction = "SHORT"
        else:
            direction = "FLAT"

    raw_strength = strength_series[-1]
    if not math.isfinite(raw_strength):
        raise FactorRuleValidationError(
            "factor_rule_v1 strength expression did not produce a finite value"
        )
    clamped_strength = min(1.0, max(0.0, float(raw_strength)))
    if action == "HOLD":
        clamped_strength = 0.0

    latest_signal = float(signal_series[-1])
    return {
        "action": action,
        "direction": direction,
        "signal": latest_signal,
        "momentum": latest_signal,
        "strength": clamped_strength,
    }


def evaluate_factor_rule_action(
    strategy_artifact: Mapping[str, Any],
    closes: Sequence[float] | None,
    *,
    features: Mapping[str, Any] | None = None,
    decision_time: str | datetime | date | None = None,
    holding_bars: int | None = None,
    current_direction: str | None = None,
) -> str:
    """Evaluate a ``factor_rule_v1`` artifact and return ``BUY``, ``SELL``, or ``HOLD``."""
    view = evaluate_factor_rule_view(
        strategy_artifact,
        closes,
        features=features,
        decision_time=decision_time,
        holding_bars=holding_bars,
        current_direction=current_direction,
    )
    return str(view["action"])


# ---------------------------------------------------------------------------
# Structural mutation operators (design §12.2 "Mutate", EVOLUTION-AUTO-CONSUMER-SEARCH)
# ---------------------------------------------------------------------------
# Operators act only on the bounded grammar: they never emit free code, never
# introduce literal constants (every new number is a declared const_param with
# an allowed_range and step) and only draw features from the governed catalogue.

MUTATION_OPERATORS: tuple[str, ...] = (
    "change_window",
    "swap_operator",
    "add_filter",
    "remove_filter",
    "change_exit",
)

# Same-type operator families: a swap never changes arity, window-slot shape or
# value type (boolean vs numeric).
_SWAP_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("ts_mean", "ts_min", "ts_max", "ts_sum", "ts_zscore", "ts_rank", "ts_std"),
    ("lag", "delta", "pct_change"),
    ("gt", "lt"),
    ("and", "or"),
    ("add", "sub", "mul", "div", "ratio"),
)
_ADDED_FILTER_PREFIX = "mut_filter_"
_FILTER_WINDOW_RANGE = (2, 24)
_FILTER_THRESHOLD_RANGE = (-2.0, 2.0)
_FILTER_THRESHOLD_STEP = 0.1


class FactorRuleMutationError(FactorRuleValidationError):
    """Raised when no structural mutation can be produced for a rule."""


def _control_index(controls: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(c.get("parameter_key")): c for c in controls if isinstance(c, Mapping)}


def _control_bounds(control: Mapping[str, Any]) -> tuple[float, float]:
    rng = control["allowed_range"]
    if isinstance(rng, Mapping):
        return float(rng["min"]), float(rng["max"])
    return float(rng[0]), float(rng[1])


def _walk_nodes(node: Any, path: tuple = ()):  # yields (path, node)
    if isinstance(node, Mapping):
        yield path, node
        for key, value in node.items():
            if isinstance(value, (Mapping, list)):
                yield from _walk_nodes(value, path + (key,))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            yield from _walk_nodes(value, path + (idx,))


def _logic_roots(logic: Mapping[str, Any]) -> list[tuple[str, Any]]:
    entry, exit_ = logic["entry"], logic["exit"]
    return [
        ("signal", logic["signal"]),
        ("entry.long_when", entry.get("long_when")),
        ("entry.short_when", entry.get("short_when")),
        ("exit.flat_when", exit_.get("flat_when")),
        ("strength", logic["strength"]),
    ]


def _snap(value: float, lo: float, hi: float, step: float, integer: bool) -> float | int:
    steps = round((value - lo) / step)
    snapped = lo + steps * step
    snapped = min(max(snapped, lo), hi)
    if integer:
        return int(round(snapped))
    return round(snapped, 10)


def sample_control_value(control: Mapping[str, Any], rng: Any) -> float | int:
    """Draw one on-grid value inside a control's declared ``allowed_range``."""
    lo, hi = _control_bounds(control)
    step = float(control["step"])
    n_steps = int(math.floor((hi - lo) / step + 1e-9))
    value = lo + rng.randint(0, max(n_steps, 0)) * step
    return _snap(value, lo, hi, step, control.get("value_type") == "integer")


def _set_param(
    parameters: dict[str, Any], controls: list[dict[str, Any]], key: str, value: Any
) -> None:
    parameters[key] = value
    for control in controls:
        if control.get("parameter_key") == key:
            control["current_value"] = value


def _window_param_keys(logic: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    for _, root in _logic_roots(logic):
        for _, node in _walk_nodes(root):
            op = node.get("op")
            slot = 1 if op in _WINDOW_ARG1_OPERATORS else 2 if op in _WINDOW_ARG2_OPERATORS else None
            if slot is not None:
                arg = node["args"][slot]
                if "const_param" in arg and arg["const_param"] not in keys:
                    keys.append(str(arg["const_param"]))
    return keys


def _referenced_params(logic: Mapping[str, Any]) -> set[str]:
    params: set[str] = set()
    for _, root in _logic_roots(logic):
        for _, node in _walk_nodes(root):
            if "const_param" in node:
                params.add(str(node["const_param"]))
    hold = logic["exit"].get("max_holding_bars")
    if isinstance(hold, Mapping) and "const_param" in hold:
        params.add(str(hold["const_param"]))
    return params


def _mutate_change_window(logic, parameters, controls, rng):
    index = _control_index(controls)
    keys = [k for k in _window_param_keys(logic) if k in index]
    if not keys:
        raise FactorRuleMutationError("rule has no window parameter to change")
    key = rng.choice(keys)
    old = parameters[key]
    lo, hi = _control_bounds(index[key])
    candidates = [
        v for v in (sample_control_value(index[key], rng) for _ in range(16)) if v != old
    ]
    if not candidates:
        raise FactorRuleMutationError(f"window control '{key}' has no alternative value")
    _set_param(parameters, controls, key, candidates[0])
    return {"operator": "change_window", "parameter": key, "from": old, "to": candidates[0]}


def _mutate_swap_operator(logic, parameters, controls, rng):
    sites: list[tuple[Mapping[str, Any], tuple[str, ...]]] = []
    for _, root in _logic_roots(logic):
        for _, node in _walk_nodes(root):
            op = node.get("op")
            for family in _SWAP_FAMILIES:
                if op in family and len(family) > 1:
                    sites.append((node, family))
    if not sites:
        raise FactorRuleMutationError("rule has no swappable operator")
    node, family = rng.choice(sites)
    old = node["op"]
    new = rng.choice([o for o in family if o != old])
    node["op"] = new
    return {"operator": "swap_operator", "from": old, "to": new}


def _unique_name(base: str, taken: set[str]) -> str:
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}_{n}"
    return candidate


def _catalogue_candidates(logic: Mapping[str, Any]) -> list[FeatureDefinition]:
    used = {(i["dataset"], i["field"]) for i in logic["inputs"]}
    out = [
        d
        for d in list_feature_definitions()
        if (d.dataset, d.field) not in used and d.dataset not in PRICE_VOLUME_DATASETS
    ]
    return sorted(out, key=lambda d: (d.dataset, d.field))


def _mutate_add_filter(logic, parameters, controls, rng):
    long_when = logic["entry"].get("long_when")
    if long_when is None:
        raise FactorRuleMutationError("add_filter requires an entry.long_when to extend")
    if any(i["name"].startswith(_ADDED_FILTER_PREFIX) for i in logic["inputs"]):
        raise FactorRuleMutationError("rule already carries a mutation filter")
    candidates = _catalogue_candidates(logic)
    if not candidates:
        raise FactorRuleMutationError("feature catalogue has no unused feature")
    feature = rng.choice(candidates)
    taken_inputs = {i["name"] for i in logic["inputs"]}
    name = _unique_name(f"{_ADDED_FILTER_PREFIX}{feature.field}", taken_inputs)
    win_key = _unique_name(f"{name}_window", set(parameters))
    thr_key = _unique_name(f"{name}_threshold", set(parameters) | {win_key})
    logic["inputs"].append(
        {
            "name": name,
            "dataset": feature.dataset,
            "field": feature.field,
            "availability_lag_days": feature.min_availability_lag_days,
        }
    )
    win_lo, win_hi = _FILTER_WINDOW_RANGE
    thr_lo, thr_hi = _FILTER_THRESHOLD_RANGE
    controls.append(
        {
            "parameter_key": win_key,
            "value_type": "integer",
            "current_value": 4,
            "allowed_range": {"min": win_lo, "max": win_hi},
            "step": 1,
        }
    )
    controls.append(
        {
            "parameter_key": thr_key,
            "value_type": "number",
            "current_value": 0.0,
            "allowed_range": {"min": thr_lo, "max": thr_hi},
            "step": _FILTER_THRESHOLD_STEP,
        }
    )
    parameters[win_key] = 4
    parameters[thr_key] = 0.0
    filter_node = {
        "op": "gt",
        "args": [
            {"op": "ts_zscore", "args": [{"ref": name}, {"const_param": win_key}]},
            {"const_param": thr_key},
        ],
    }
    logic["entry"]["long_when"] = {"op": "and", "args": [long_when, filter_node]}
    return {
        "operator": "add_filter",
        "input": name,
        "dataset": feature.dataset,
        "field": feature.field,
        "parameters": [win_key, thr_key],
    }


def _mutate_remove_filter(logic, parameters, controls, rng):
    long_when = logic["entry"].get("long_when")
    added = [i for i in logic["inputs"] if i["name"].startswith(_ADDED_FILTER_PREFIX)]
    if (
        not added
        or not isinstance(long_when, Mapping)
        or long_when.get("op") != "and"
    ):
        raise FactorRuleMutationError("rule has no removable filter")
    target = rng.choice(added)["name"]
    base, filt = long_when["args"]

    def _refs(node: Any) -> set[str]:
        return {n["ref"] for _, n in _walk_nodes(node) if "ref" in n}

    if target not in _refs(filt):
        base, filt = filt, base
    if target not in _refs(filt) or target in _refs(base):
        raise FactorRuleMutationError("filter structure does not match a removable filter")
    dropped = {n["const_param"] for _, n in _walk_nodes(filt) if "const_param" in n}
    logic["entry"]["long_when"] = base
    logic["inputs"] = [i for i in logic["inputs"] if i["name"] != target]
    still_used = _referenced_params(logic)
    for key in sorted(dropped - still_used):
        parameters.pop(key, None)
    controls[:] = [c for c in controls if c.get("parameter_key") not in (dropped - still_used)]
    return {"operator": "remove_filter", "input": target, "parameters": sorted(dropped)}


def _mutate_change_exit(logic, parameters, controls, rng):
    index = _control_index(controls)
    options: list[str] = []
    hold = logic["exit"].get("max_holding_bars")
    if isinstance(hold, Mapping) and hold.get("const_param") in index:
        options.append("hold")
    flat = logic["exit"].get("flat_when")
    if isinstance(flat, Mapping) and flat.get("op") in ("gt", "lt"):
        options.append("flat_comparator")
    if not options:
        raise FactorRuleMutationError("rule has no exit to change")
    choice = rng.choice(options)
    if choice == "hold":
        key = hold["const_param"]
        old = parameters[key]
        alts = [v for v in (sample_control_value(index[key], rng) for _ in range(16)) if v != old]
        if not alts:
            raise FactorRuleMutationError(f"exit control '{key}' has no alternative value")
        _set_param(parameters, controls, key, alts[0])
        return {"operator": "change_exit", "exit": "max_holding_bars", "parameter": key, "from": old, "to": alts[0]}
    old = flat["op"]
    flat["op"] = "gt" if old == "lt" else "lt"
    return {"operator": "change_exit", "exit": "flat_when", "from": old, "to": flat["op"]}


_MUTATORS = {
    "change_window": _mutate_change_window,
    "swap_operator": _mutate_swap_operator,
    "add_filter": _mutate_add_filter,
    "remove_filter": _mutate_remove_filter,
    "change_exit": _mutate_change_exit,
}


def mutate_factor_rule_structure(
    strategy_logic: Mapping[str, Any],
    parameters: Mapping[str, Any],
    controls: Sequence[Mapping[str, Any]],
    *,
    rng: Any,
    operator: str | None = None,
) -> dict[str, Any]:
    """Apply one structural mutation to a ``factor_rule_v1`` rule.

    Inputs are never modified. Returns ``strategy_logic``, ``parameters``,
    ``controls`` (deep copies) and a ``mutation`` descriptor. When ``operator``
    is None the operators are tried in a seeded-random order until one applies.
    The result is re-validated with :func:`validate_factor_rule_logic`.
    """
    import copy

    if operator is not None and operator not in _MUTATORS:
        raise FactorRuleMutationError(f"unknown mutation operator '{operator}'")
    order = [operator] if operator else rng.sample(list(MUTATION_OPERATORS), len(MUTATION_OPERATORS))
    last_error: Exception | None = None
    for name in order:
        logic = copy.deepcopy(dict(strategy_logic))
        params = copy.deepcopy(dict(parameters))
        ctrls = [copy.deepcopy(dict(c)) for c in controls]
        try:
            descriptor = _MUTATORS[name](logic, params, ctrls, rng)
            validate_factor_rule_logic(
                logic,
                params,
                {"controls": ctrls, "immutable_parameters": []},
            )
        except FactorRuleValidationError as exc:
            last_error = exc
            continue
        return {
            "strategy_logic": logic,
            "parameters": params,
            "controls": ctrls,
            "mutation": descriptor,
        }
    raise FactorRuleMutationError(f"no structural mutation applies: {last_error}")
