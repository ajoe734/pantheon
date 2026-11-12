"""Mechanism stage of the replication gate (SD_DISCOVERY_DEMAND §3.2).

Runs every prediction of a hypothesis card as the fixed statistical test of its
``test_kind`` on the **in-sample window only**, Holm-corrects the p-values within the
card and records ``supported`` / ``contradicted`` / ``inconclusive`` with the statistic.

Rules that this module enforces by construction:

* No out-of-sample bar is read. Series are cut at the in-sample cutoff date *before*
  any price is converted to a number; events and observations after the cutoff are
  dropped before use.
* Nothing is synthesized. Prices come from the governed backtest dataset handed to the
  gate; observable datasets (release dates, regime bands, signal values) come from the
  caller's ``mechanism_inputs``. A prediction whose data is absent or too thin is
  recorded ``inconclusive`` with ``testable: false`` and a reason; it can never count
  as support.
* Pure Python, deterministic, no I/O.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SUPPORTED = "supported"
CONTRADICTED = "contradicted"
INCONCLUSIVE = "inconclusive"

MECHANISM_UNTESTED = "mechanism_untested"
MECHANISM_CONTRADICTED = "contradicted"
MECHANISM_TESTED = "tested"

TEST_KINDS = ("event_window", "regime_split", "dose_response", "lead_lag", "cross_symbol_sign")
MARKET_PRICE_DATASET = "tw_price_daily"


# --------------------------------------------------------------------------- statistics
def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    front = math.exp(ln_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    """Two-sided p-value of Student's t."""
    if df <= 0 or not math.isfinite(t):
        return 1.0
    return min(1.0, max(0.0, _betainc(df / 2.0, 0.5, df / (df + t * t))))


def binom_upper_tail(k: int, n: int) -> float:
    """P(X >= k) for X ~ Binomial(n, 0.5)."""
    if n <= 0:
        return 1.0
    return min(1.0, sum(math.comb(n, i) for i in range(max(0, k), n + 1)) / float(2 ** n))


def _mean_sd(xs: Sequence[float]) -> Tuple[float, float]:
    n = len(xs)
    mean = sum(xs) / n
    if n < 2:
        return mean, 0.0
    return mean, math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))


def one_sample_t(xs: Sequence[float]) -> Optional[Dict[str, float]]:
    """t-test of mean == 0. None when undefined (n < 2 or zero variance)."""
    n = len(xs)
    if n < 2:
        return None
    mean, sd = _mean_sd(xs)
    if sd <= 0.0 or not math.isfinite(sd):
        return None
    t = mean / (sd / math.sqrt(n))
    return {"n": n, "mean": mean, "statistic": t, "p_value": t_two_sided_p(t, n - 1)}


def _ranks(xs: Sequence[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def correlation_test(xs: Sequence[float], ys: Sequence[float], *, rank: bool) -> Optional[Dict[str, float]]:
    if rank:
        xs, ys = _ranks(xs), _ranks(ys)
    rho = pearson(xs, ys)
    n = len(xs)
    if rho is None or n < 4:
        return None
    rho = max(-0.999999999, min(0.999999999, rho))
    t = rho * math.sqrt((n - 2) / (1.0 - rho * rho))
    return {"n": n, "rho": rho, "statistic": t, "p_value": t_two_sided_p(t, n - 2)}


def holm_adjust(p_values: Sequence[float]) -> List[float]:
    """Holm step-down adjusted p-values, returned in the input order."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


# ------------------------------------------------------------------- in-sample market view
def _norm_symbol(value: Any) -> str:
    return str(value or "").strip().upper().split(".")[0]


def _date_of(value: Any) -> Optional[str]:
    text = str(value or "")
    return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else None


class InSampleMarket:
    """Closing-price series per symbol, cut at the protocol's in-sample window before any price is read.

    Mirrors the backtest protocol (``vectorbt_adapter`` replication protocol): ``split_idx`` is
    ``int(min target-instrument bar count * in_sample_fraction)`` and EVERY instrument (targets,
    benchmark, others) keeps only its first ``split_idx`` sessions. Hence no bar the protocol
    treats as out-of-sample can enter, whatever the lengths or start dates of the instruments.
    """

    def __init__(self, dataset: Mapping[str, Any], in_sample_fraction: float,
                 target_symbols: Sequence[str] = ()):
        by_symbol: Dict[str, Dict[str, Any]] = {}
        records = dataset.get("records")
        if isinstance(records, Sequence) and not isinstance(records, (str, bytes)) and records:
            for rec in records:
                if not isinstance(rec, Mapping):
                    continue
                date = _date_of(rec.get("date") or rec.get("event_time"))
                sym = _norm_symbol(rec.get("instrument") or rec.get("symbol"))
                if date and sym:
                    by_symbol.setdefault(sym, {})[date] = rec
        else:
            pool = dataset.get("candidate_pool_market")
            symbols = pool.get("symbols") if isinstance(pool, Mapping) else None
            for sym, payload in (symbols or {}).items():
                for bar in (payload or {}).get("bars", []) if isinstance(payload, Mapping) else []:
                    date = _date_of(bar.get("event_time") if isinstance(bar, Mapping) else None)
                    if date:
                        by_symbol.setdefault(_norm_symbol(sym), {})[date] = bar
        self.cutoff: Optional[str] = None  # latest in-sample session over all instruments
        self.cutoffs: Dict[str, str] = {}
        self.split_idx = 0
        self.series: Dict[str, List[Tuple[str, float]]] = {}
        if not by_symbol:
            return
        wanted = {_norm_symbol(s) for s in target_symbols if _norm_symbol(s)}
        targets = [s for s in by_symbol if s in wanted] or list(by_symbol)
        self.split_idx = int(min(len(by_symbol[s]) for s in targets) * in_sample_fraction)
        for sym, by_date in by_symbol.items():
            rows = sorted(by_date.items(), key=lambda r: r[0])
            kept = rows[: self.split_idx]  # cut BEFORE reading a price
            if not kept:
                continue
            self.cutoffs[sym] = kept[-1][0]
            closes: List[Tuple[str, float]] = []
            for d, rec in kept:
                try:
                    close = float(rec.get("close"))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(close) and close > 0.0:
                    closes.append((d, close))
            if closes:
                self.series[sym] = closes
        if self.cutoffs:
            self.cutoff = max(self.cutoffs.values())

    def symbols(self) -> List[str]:
        return sorted(self.series)

    def daily_returns(self, sym: str) -> List[Tuple[str, float]]:
        s = self.series.get(sym, [])
        return [(s[i][0], s[i][1] / s[i - 1][1] - 1.0) for i in range(1, len(s))]


def _first_index_on_or_after(dates: Sequence[str], date: str) -> Optional[int]:
    lo, hi = 0, len(dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if dates[mid] < date:
            lo = mid + 1
        else:
            hi = mid
    return lo if lo < len(dates) else None


def _input_rows(inputs: Mapping[str, Any], dataset: str) -> List[Mapping[str, Any]]:
    rows = inputs.get(dataset) if isinstance(inputs, Mapping) else None
    return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) else []


def _row_date(row: Mapping[str, Any], *, availability_required: bool) -> Optional[str]:
    available = _date_of(row.get("available_time"))
    if available:
        return available
    return None if availability_required else _date_of(row.get("event_time"))


# ------------------------------------------------------------------------ one test per kind
# Every test returns {"testable": bool, "reason": str|None, "direction": "+"|"-"|None,
# "p_value": float|None, "statistic": ..., "n": ..., ...detail}. ``direction`` is the sign of
# the measured effect that ``p_value`` speaks to (p is the probability of an effect at least
# this extreme under "no effect").
def _untestable(reason: str, **extra: Any) -> Dict[str, Any]:
    return {"testable": False, "reason": reason, "direction": None, "p_value": None, **extra}


def _direction(x: float) -> Optional[str]:
    return "+" if x > 0 else "-" if x < 0 else None


def _target_symbols(market: InSampleMarket, targets: Sequence[str], benchmark: str) -> List[str]:
    wanted = [_norm_symbol(s) for s in targets if _norm_symbol(s)]
    syms = [s for s in wanted if s in market.series] if wanted else [
        s for s in market.symbols() if s != benchmark
    ]
    return syms


def _event_window(pred, market, inputs, targets, cfg):
    rows = _input_rows(inputs, pred["observable"]["dataset"])
    if not rows:
        return _untestable("observable_data_missing")
    before, after = int(pred["window"]["before"]), int(pred["window"]["after"])
    wlen = before + after + 1
    abnormal: List[float] = []
    for row in rows:
        sym = _norm_symbol(row.get("symbol") or row.get("instrument"))
        date = _row_date(row, availability_required=False)
        if sym not in market.series or not date:
            continue
        rets = market.daily_returns(sym)
        if not rets:
            continue
        i0 = _first_index_on_or_after([d for d, _ in rets], date)
        if i0 is None or i0 - before < 0 or i0 + after >= len(rets):
            continue  # window not fully inside the in-sample span
        window = sum(r for _, r in rets[i0 - before : i0 + after + 1])
        base = sum(r for _, r in rets) / len(rets)
        abnormal.append(window - wlen * base)
    if len(abnormal) < cfg.mechanism_min_events:
        return _untestable("too_few_in_sample_events", n=len(abnormal), minimum=cfg.mechanism_min_events)
    res = one_sample_t(abnormal)
    if res is None:
        return _untestable("zero_variance", n=len(abnormal))
    return {"testable": True, "reason": None, "direction": _direction(res["mean"]),
            "p_value": res["p_value"], "statistic": res["statistic"], "n": res["n"],
            "mean_abnormal_window_return": res["mean"]}


def _regime_labels(pred, market, inputs, cfg) -> Optional[Dict[str, str]]:
    """date -> regime label, using only information available strictly before that date."""
    dataset = pred["observable"]["dataset"]
    rows = _input_rows(inputs, dataset)
    field = pred["observable"]["field"]
    labels: Dict[str, str] = {}
    if rows:
        points = sorted(
            (d, str(r.get(field, r.get("band")))) for r in rows
            if (d := _row_date(r, availability_required=True)) and r.get(field, r.get("band")) is not None
        )
        if not points:
            return None
        bench = market.series.get(cfg.mechanism_benchmark_symbol) or next(iter(market.series.values()), [])
        for date, _ in bench:
            known = [band for d, band in points if d < date]
            if known:
                labels[date] = known[-1]
        return labels
    if dataset != MARKET_PRICE_DATASET:
        return None
    bench = market.series.get(cfg.mechanism_benchmark_symbol)
    window = cfg.mechanism_regime_ma_sessions
    if not bench or len(bench) <= window:
        return None
    for i in range(window, len(bench)):
        mean = sum(c for _, c in bench[i - window : i]) / window  # prior sessions only
        labels[bench[i][0]] = "above_ma" if bench[i - 1][1] > mean else "below_ma"
    return labels


def _regime_split(pred, market, inputs, targets, cfg):
    syms = _target_symbols(market, targets, cfg.mechanism_benchmark_symbol)
    labels = _regime_labels(pred, market, inputs, cfg)
    if not syms:
        return _untestable("target_prices_missing")
    if labels is None:
        return _untestable("regime_data_missing")
    pooled: Dict[str, List[float]] = {}
    for sym in syms:
        for d, r in market.daily_returns(sym):
            pooled.setdefault(d, []).append(r)
    by_regime: Dict[str, List[float]] = {}
    for d in sorted(pooled):
        if d in labels:
            by_regime.setdefault(labels[d], []).append(sum(pooled[d]) / len(pooled[d]))
    usable = {k: v for k, v in by_regime.items() if len(v) >= cfg.mechanism_min_observations}
    if len(usable) < 2:
        return _untestable("fewer_than_two_regimes_with_enough_observations",
                           regime_observations={k: len(v) for k, v in by_regime.items()})
    per_regime: Dict[str, Dict[str, Any]] = {}
    for label, xs in sorted(usable.items()):
        res = one_sample_t(xs)
        if res is None:
            return _untestable("zero_variance", regime=label)
        per_regime[label] = {"n": res["n"], "mean_daily_return": res["mean"],
                             "statistic": res["statistic"], "p_value": res["p_value"],
                             "direction": _direction(res["mean"])}
    expected = pred["expected_sign"]
    opposite = {"+": "-", "-": "+"}.get(expected)
    k = len(per_regime)
    against = [r for r in per_regime.values() if opposite and r["direction"] == opposite]
    if against:  # any regime moving the other way: p for that is Bonferroni over regimes
        p = min(1.0, k * min(r["p_value"] for r in against))
        direction = opposite
    else:
        p = max(r["p_value"] for r in per_regime.values())  # sign must hold in EVERY regime
        direction = expected if expected in ("+", "-") else _direction(
            sum(r["mean_daily_return"] for r in per_regime.values()))
    return {"testable": True, "reason": None, "direction": direction, "p_value": p,
            "statistic": min(abs(r["statistic"]) for r in per_regime.values()), "n": sum(r["n"] for r in per_regime.values()),
            "regimes": per_regime}


def _observations(pred, market, inputs, horizon):
    """(symbol, value, forward_return) for every in-sample row with a full forward window."""
    field = pred["observable"]["field"]
    out = []
    for row in _input_rows(inputs, pred["observable"]["dataset"]):
        sym = _norm_symbol(row.get("symbol") or row.get("instrument"))
        date = _row_date(row, availability_required=True)
        try:
            value = float(row.get(field))
        except (TypeError, ValueError):
            continue
        s = market.series.get(sym)
        if not s or not date or not math.isfinite(value):
            continue
        i0 = _first_index_on_or_after([d for d, _ in s], date)
        if i0 is None or i0 + horizon >= len(s):
            continue
        out.append((sym, value, s[i0 + horizon][1] / s[i0][1] - 1.0))
    return out


def _dose_response(pred, market, inputs, targets, cfg):
    if not _input_rows(inputs, pred["observable"]["dataset"]):
        return _untestable("observable_data_missing")
    obs = _observations(pred, market, inputs, max(1, int(pred["window"]["after"])))
    if len(obs) < cfg.mechanism_min_observations:
        return _untestable("too_few_in_sample_observations", n=len(obs), minimum=cfg.mechanism_min_observations)
    res = correlation_test([o[1] for o in obs], [o[2] for o in obs], rank=True)
    if res is None:
        return _untestable("zero_variance", n=len(obs))
    return {"testable": True, "reason": None, "direction": _direction(res["rho"]), "p_value": res["p_value"],
            "statistic": res["statistic"], "n": res["n"], "spearman_rho": res["rho"]}


def _cross_symbol_sign(pred, market, inputs, targets, cfg):
    if not _input_rows(inputs, pred["observable"]["dataset"]):
        return _untestable("observable_data_missing")
    horizon = max(1, int(pred["window"]["after"]))
    per_symbol: Dict[str, Dict[str, Any]] = {}
    grouped: Dict[str, List[Tuple[float, float]]] = {}
    for sym, value, fwd in _observations(pred, market, inputs, horizon):
        grouped.setdefault(sym, []).append((value, fwd))
    for sym, pairs in sorted(grouped.items()):
        if len(pairs) < cfg.mechanism_min_observations_per_symbol:
            continue
        res = correlation_test([p[0] for p in pairs], [p[1] for p in pairs], rank=True)
        if res is not None and _direction(res["rho"]):
            per_symbol[sym] = {"n": res["n"], "spearman_rho": res["rho"], "direction": _direction(res["rho"])}
    k = len(per_symbol)
    if k < cfg.mechanism_min_symbols:
        return _untestable("too_few_symbols_with_enough_observations", symbols_tested=k, minimum=cfg.mechanism_min_symbols)
    plus = sum(1 for r in per_symbol.values() if r["direction"] == "+")
    minus = k - plus
    expected = pred["expected_sign"]
    direction = "+" if plus > minus else "-" if minus > plus else None
    if direction is None:
        return {"testable": True, "reason": None, "direction": None, "p_value": 1.0,
                "statistic": plus / k, "n": k, "per_symbol": per_symbol}
    agree = max(plus, minus)
    return {"testable": True, "reason": None, "direction": direction, "p_value": binom_upper_tail(agree, k),
            "statistic": agree / k, "n": k, "per_symbol": per_symbol}


def _lead_lag(pred, market, inputs, targets, cfg):
    lag = max(1, int(pred["window"]["after"]))
    field = pred["observable"]["field"]
    rows = _input_rows(inputs, pred["observable"]["dataset"])
    if not rows:
        return _untestable("observable_data_missing")
    lead_x: List[float] = []
    lead_y: List[float] = []
    rev_x: List[float] = []
    rev_y: List[float] = []
    for sym in sorted({_norm_symbol(r.get("symbol") or r.get("instrument")) for r in rows}):
        s = market.series.get(sym)
        if not s:
            continue
        points = sorted(
            (d, float(r[field])) for r in rows
            if _norm_symbol(r.get("symbol") or r.get("instrument")) == sym
            and (d := _row_date(r, availability_required=True)) and isinstance(r.get(field), (int, float))
        )
        if not points:
            continue
        dates = [d for d, _ in s]
        x_at: List[Optional[float]] = []
        j, last = 0, None
        for d in dates:  # as-of alignment: latest value available on or before the session
            while j < len(points) and points[j][0] <= d:
                last = points[j][1]
                j += 1
            x_at.append(last)
        ret = [None] + [s[i][1] / s[i - 1][1] - 1.0 for i in range(1, len(s))]
        for t in range(1, len(s) - lag):
            if x_at[t] is not None and ret[t + lag] is not None:
                lead_x.append(x_at[t]); lead_y.append(ret[t + lag])  # input now -> return later
            if x_at[t + lag] is not None and ret[t] is not None:
                rev_x.append(x_at[t + lag]); rev_y.append(ret[t])    # return now -> input later
    if len(lead_x) < cfg.mechanism_min_observations:
        return _untestable("too_few_in_sample_observations", n=len(lead_x), minimum=cfg.mechanism_min_observations)
    lead = correlation_test(lead_x, lead_y, rank=False)
    rev = correlation_test(rev_x, rev_y, rank=False) if len(rev_x) >= cfg.mechanism_min_observations else None
    if lead is None:
        return _untestable("zero_variance", n=len(lead_x))
    direction = _direction(lead["rho"])
    out = {"testable": True, "reason": None, "direction": direction, "p_value": lead["p_value"],
           "statistic": lead["statistic"], "n": lead["n"], "lead_rho": lead["rho"],
           "reverse_rho": rev["rho"] if rev else None}
    if rev is not None and abs(rev["rho"]) >= abs(lead["rho"]) and rev["p_value"] <= cfg.mechanism_alpha:
        out.update(direction=None, p_value=1.0, reason="reverse_direction_not_weaker")
    return out


_TESTS = {
    "event_window": _event_window,
    "regime_split": _regime_split,
    "dose_response": _dose_response,
    "lead_lag": _lead_lag,
    "cross_symbol_sign": _cross_symbol_sign,
}


# ----------------------------------------------------------------------------- the stage
def _valid_prediction(pred: Any) -> bool:
    try:
        return (
            isinstance(pred, Mapping)
            and pred["test_kind"] in TEST_KINDS
            and pred["expected_sign"] in ("+", "-", "0")
            and isinstance(pred["observable"]["dataset"], str)
            and isinstance(pred["observable"]["field"], str)
            and int(pred["window"]["before"]) >= 0
            and int(pred["window"]["after"]) >= 0
        )
    except (KeyError, TypeError, ValueError):
        return False


def run_mechanism_stage(
    predictions: Optional[Sequence[Mapping[str, Any]]],
    dataset: Optional[Mapping[str, Any]],
    inputs: Optional[Mapping[str, Any]],
    *,
    config: Any,
    target_symbols: Sequence[str] = (),
) -> Dict[str, Any]:
    """Run the card's predictions on the in-sample window; return the mechanism record.

    ``mechanism_status`` is ``contradicted`` when any prediction is contradicted,
    ``mechanism_untested`` when no prediction was testable (or there are none), else
    ``tested``. ``mechanism_support`` = supported / testable, or None when nothing was
    testable.
    """
    preds = list(predictions or [])
    market: Optional[InSampleMarket] = None
    if isinstance(dataset, Mapping):
        market = InSampleMarket(dataset, config.in_sample_ratio, target_symbols)
        if not market.series:
            market = None
    results: List[Dict[str, Any]] = []
    for index, pred in enumerate(preds):
        base = {
            "index": index,
            "statement": pred.get("statement") if isinstance(pred, Mapping) else None,
            "test_kind": pred.get("test_kind") if isinstance(pred, Mapping) else None,
            "expected_sign": pred.get("expected_sign") if isinstance(pred, Mapping) else None,
        }
        if isinstance(pred, Mapping) and str(pred.get("testability", "testable")) != "testable":
            results.append({**base, **_untestable(str(pred.get("testability")))})
        elif not _valid_prediction(pred):
            results.append({**base, **_untestable("malformed_prediction")})
        elif market is None:
            results.append({**base, **_untestable("in_sample_market_data_missing")})
        else:
            try:
                results.append({**base, **_TESTS[pred["test_kind"]](pred, market, inputs or {}, target_symbols, config)})
            except Exception as exc:  # a broken test is never support
                results.append({**base, **_untestable(f"test_error:{type(exc).__name__}")})

    testable = [r for r in results if r["testable"]]
    adjusted = holm_adjust([r["p_value"] for r in testable])
    for r, adj in zip(testable, adjusted):
        r["holm_p_value"] = adj
        if adj <= config.mechanism_alpha and r["direction"]:
            if r["expected_sign"] == "0" or r["direction"] != r["expected_sign"]:
                r["status"] = CONTRADICTED
            else:
                r["status"] = SUPPORTED
        else:
            r["status"] = INCONCLUSIVE
    for r in results:
        if not r["testable"]:
            r["status"] = INCONCLUSIVE
            r["holm_p_value"] = None

    supported = sum(1 for r in results if r["status"] == SUPPORTED)
    contradicted = [r for r in results if r["status"] == CONTRADICTED]
    if contradicted:
        status = MECHANISM_CONTRADICTED
    elif not testable:
        status = MECHANISM_UNTESTED
    else:
        status = MECHANISM_TESTED
    return {
        "mechanism_status": status,
        "mechanism_support": (supported / len(testable)) if testable else None,
        "testable_predictions": len(testable),
        "supported_predictions": supported,
        "contradicted_predictions": len(contradicted),
        "prediction_count": len(results),
        "in_sample_cutoff": market.cutoff if market else None,
        "holm_family_size": len(testable),
        "alpha": config.mechanism_alpha,
        "predictions": results,
    }
