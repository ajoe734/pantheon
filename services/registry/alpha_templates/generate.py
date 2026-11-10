"""Deterministic generator for the checked-in AlphaTemplate seed library.

Run:  python3 services/registry/alpha_templates/generate.py

Writes ``qlib_alpha158.v1.json``, ``alpha101_time_series.v1.json`` and
``alpha101_cross_sectional.v1.json`` next to this file.  Tests regenerate the
documents and require byte equality with the committed files.

Provenance
* Qlib Alpha158: formulas of ``qlib/contrib/data/handler.py`` /
  ``loader.py`` (microsoft/qlib, MIT).  Only the formula definitions are
  re-expressed in factor_rule_v1 syntax; no Qlib code is copied or imported.
* Alpha101: formulas of Z. Kakushadze, "101 Formulaic Alphas", arXiv:1601.00991,
  Appendix A, implemented from the paper text.  The paper is not redistributed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DATASET = "tw_price_volume"

QLIB_LICENCE = "MIT"
QLIB_ATTRIBUTION = (
    "Formula definition from Microsoft Qlib Alpha158 (https://github.com/microsoft/qlib, "
    "qlib/contrib/data/handler.py), Copyright (c) Microsoft Corporation, MIT License. "
    "Re-expressed in factor_rule_v1; no Qlib source code is included."
)
A101_LICENCE = "arXiv-1601.00991 formulas (paper text not redistributed); independent implementation"
A101_ATTRIBUTION = (
    "Formula from Z. Kakushadze, '101 Formulaic Alphas', Wilmott 2016(84), arXiv:1601.00991, "
    "Appendix A. Implemented from the published formula; the paper itself is not redistributed."
)


class B:
    """Tiny expression builder that records inputs and parameters."""

    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.params: dict[str, float] = {}

    def ref(self, name: str) -> dict:
        if name not in self.inputs:
            self.inputs.append(name)
        return {"ref": name}

    def p(self, name: str, value: float) -> dict:
        if name in self.params and self.params[name] != value:
            raise ValueError(f"parameter {name} redefined")
        self.params[name] = value
        return {"const_param": name}

    @staticmethod
    def c(value: float) -> dict:
        return {"const": float(value)}

    @staticmethod
    def op(name: str, *args: dict) -> dict:
        return {"op": name, "args": list(args)}


def _finish(b: B, tid: str, name: str, family: str, scope: str, expr: dict, library: str,
            ref: str, licence: str, attribution: str, notes: str | None = None) -> dict:
    order = ["open", "high", "low", "close", "volume", "vwap"]
    used: set[str] = set()

    def walk(node: dict) -> None:
        if "ref" in node:
            used.add(node["ref"])
        for a in node.get("args", ()):
            walk(a)

    walk(expr)
    inputs = [
        {"name": n, "dataset": DATASET, "field": n, "availability_lag_days": 0}
        for n in sorted(used, key=order.index)
    ]
    origin: dict[str, Any] = {"library": library, "ref": ref}
    if notes:
        origin["notes"] = notes
    return {
        "schema_version": "alpha_template.v1",
        "template_id": tid,
        "name": name,
        "family": family,
        "expression": expr,
        "inputs": inputs,
        "parameters": dict(sorted(b.params.items())),
        "scope": scope,
        "origin": origin,
        "licence": licence,
        "attribution": attribution,
        "status": "draft",
        "evaluation_ref": None,
    }


# ---------------------------------------------------------------- Alpha158
EPS = 1e-12
WINDOWS = (5, 10, 20, 30, 60)

ALPHA158_NOTES = (
    "Qlib Std/Quantile use sample std (ddof=1): std_scale=sqrt(d/(d-1)) is applied to ts_std. "
    "Qlib Rank is the scipy 'rank' percentile of the last value: (ts_rank*(d-1)+1)/d. "
    "IMAX/IMIN are 1-based argmax/argmin positions / d (ties resolve to the latest bar)."
)


def _alpha158() -> list[dict]:
    out: list[dict] = []

    def add(key: str, family: str, build) -> None:
        b = B()
        expr = build(b)
        out.append(_finish(
            b, f"qlib_alpha158:{key}", f"Qlib Alpha158 {key}", family, "time_series", expr,
            "qlib_alpha158", key, QLIB_LICENCE, QLIB_ATTRIBUTION,
            ALPHA158_NOTES if key[-1].isdigit() and key not in ("OPEN0", "HIGH0", "LOW0", "VWAP0") else None,
        ))

    def kbar(b, kind):
        o, h, l, c = b.ref("open"), b.ref("high"), b.ref("low"), b.ref("close")
        op = b.op
        eps = b.c(EPS)
        rng = op("sub", h, l)
        if kind == "KMID":
            return op("div", op("sub", c, o), o)
        if kind == "KLEN":
            return op("div", rng, o)
        if kind == "KMID2":
            return op("div", op("sub", c, o), op("add", rng, eps))
        if kind == "KUP":
            return op("div", op("sub", h, op("max2", o, c)), o)
        if kind == "KUP2":
            return op("div", op("sub", h, op("max2", o, c)), op("add", rng, eps))
        if kind == "KLOW":
            return op("div", op("sub", op("min2", o, c), l), o)
        if kind == "KLOW2":
            return op("div", op("sub", op("min2", o, c), l), op("add", rng, eps))
        num = op("sub", op("sub", op("mul", b.c(2), c), h), l)
        if kind == "KSFT":
            return op("div", num, o)
        if kind == "KSFT2":
            return op("div", num, op("add", rng, eps))
        raise KeyError(kind)

    for k in ("KMID", "KLEN", "KMID2", "KUP", "KUP2", "KLOW", "KLOW2", "KSFT", "KSFT2"):
        add(k, "kbar", lambda b, k=k: kbar(b, k))
    for f in ("open", "high", "low", "vwap"):
        add(f.upper() + "0", "kbar", lambda b, f=f: b.op("div", b.ref(f), b.ref("close")))

    def rolling(b, kind, d):
        op, c_ = b.op, b.c
        o, h, l, c, v = (b.ref(n) for n in ("open", "high", "low", "close", "volume"))
        D = b.p("d", d)
        eps = c_(EPS)
        one = lambda: b.p("lag1", 1)

        def std(x):
            return op("mul", op("ts_std", x, D), b.p("std_scale", math.sqrt(d / (d - 1))))

        def gain(x):  # Greater(x - Ref(x,1), 0)
            return op("max2", op("delta", x, one()), c_(0))

        def loss(x):  # Greater(Ref(x,1) - x, 0)
            return op("max2", op("sub", op("lag", x, one()), x), c_(0))

        def absd(x):
            return op("abs", op("delta", x, one()))

        def sums(x):
            return (op("ts_sum", gain(x), D), op("ts_sum", loss(x), D),
                    op("add", op("ts_sum", absd(x), D), eps))

        if kind == "ROC":
            return op("div", op("lag", c, D), c)
        if kind == "MA":
            return op("div", op("ts_mean", c, D), c)
        if kind == "STD":
            return op("div", std(c), c)
        if kind == "BETA":
            return op("div", op("ts_slope", c, D), c)
        if kind == "RSQR":
            return op("ts_rsquare", c, D)
        if kind == "RESI":
            return op("div", op("ts_resi", c, D), c)
        if kind == "MAX":
            return op("div", op("ts_max", h, D), c)
        if kind == "MIN":
            return op("div", op("ts_min", l, D), c)
        if kind == "QTLU":
            return op("div", op("ts_quantile", c, D, b.p("q_upper", 0.8)), c)
        if kind == "QTLD":
            return op("div", op("ts_quantile", c, D, b.p("q_lower", 0.2)), c)
        if kind == "RANK":
            return op("div", op("add", op("mul", op("ts_rank", c, D), b.p("rank_span", d - 1)), c_(1)), D)
        if kind == "RSV":
            lo, hi = op("ts_min", l, D), op("ts_max", h, D)
            return op("div", op("sub", c, lo), op("add", op("sub", hi, lo), eps))
        if kind == "IMAX":
            return op("div", op("add", op("ts_argmax", h, D), c_(1)), D)
        if kind == "IMIN":
            return op("div", op("add", op("ts_argmin", l, D), c_(1)), D)
        if kind == "IMXD":
            return op("div", op("sub", op("ts_argmax", h, D), op("ts_argmin", l, D)), D)
        if kind == "CORR":
            return op("ts_corr", c, op("log", op("add", v, c_(1))), D)
        if kind == "CORD":
            r1 = op("div", c, op("lag", c, one()))
            r2 = op("log", op("add", op("div", v, op("lag", v, one())), c_(1)))
            return op("ts_corr", r1, r2, D)
        if kind == "CNTP":
            return op("ts_mean", op("gt", c, op("lag", c, one())), D)
        if kind == "CNTN":
            return op("ts_mean", op("lt", c, op("lag", c, one())), D)
        if kind == "CNTD":
            return op("sub", op("ts_mean", op("gt", c, op("lag", c, one())), D),
                      op("ts_mean", op("lt", c, op("lag", c, one())), D))
        if kind in ("SUMP", "SUMN", "SUMD"):
            g, ls, tot = sums(c)
            return {"SUMP": op("div", g, tot), "SUMN": op("div", ls, tot),
                    "SUMD": op("div", op("sub", g, ls), tot)}[kind]
        if kind == "VMA":
            return op("div", op("ts_mean", v, D), op("add", v, eps))
        if kind == "VSTD":
            return op("div", std(v), op("add", v, eps))
        if kind == "WVMA":
            w = op("mul", op("abs", op("sub", op("div", c, op("lag", c, one())), c_(1))), v)
            return op("div", std(w), op("add", op("ts_mean", w, D), eps))
        if kind in ("VSUMP", "VSUMN", "VSUMD"):
            g, ls, tot = sums(v)
            return {"VSUMP": op("div", g, tot), "VSUMN": op("div", ls, tot),
                    "VSUMD": op("div", op("sub", g, ls), tot)}[kind]
        raise KeyError(kind)

    families = {
        "ROC": "momentum", "MA": "momentum", "STD": "volatility", "BETA": "momentum",
        "RSQR": "momentum", "RESI": "reversal", "MAX": "reversal", "MIN": "reversal",
        "QTLU": "reversal", "QTLD": "reversal", "RANK": "reversal", "RSV": "reversal",
        "IMAX": "momentum", "IMIN": "momentum", "IMXD": "momentum", "CORR": "volume",
        "CORD": "volume", "CNTP": "momentum", "CNTN": "momentum", "CNTD": "momentum",
        "SUMP": "momentum", "SUMN": "momentum", "SUMD": "momentum", "VMA": "volume",
        "VSTD": "volume", "WVMA": "volume", "VSUMP": "volume", "VSUMN": "volume",
        "VSUMD": "volume",
    }
    for kind, fam in families.items():
        for d in WINDOWS:
            add(f"{kind}{d}", fam, lambda b, kind=kind, d=d: rolling(b, kind, d))
    return out


# ---------------------------------------------------------------- Alpha101
def _a101() -> tuple[list[dict], list[dict]]:
    ts: list[dict] = []
    cs: list[dict] = []

    def add(num: int, family: str, scope: str, build, note: str | None = None) -> None:
        b = B()
        expr = build(b)
        (ts if scope == "time_series" else cs).append(_finish(
            b, f"alpha101:#{num}", f"Alpha#{num}", family, scope, expr,
            "alpha101", f"#{num}", A101_LICENCE, A101_ATTRIBUTION, note,
        ))

    NORM = ("Ts_Rank is the normalised percentile (rank of the last value among earlier values in "
            "the window, in [0,1]).")

    def cond(b, c, a, other):
        """c ? a : other  where c is 0/1."""
        return b.op("add", b.op("mul", c, a), b.op("mul", b.op("sub", b.c(1), c), other))

    def adv20(b):
        return b.op("ts_mean", b.ref("volume"), b.p("d_adv", 20))

    def returns(b):
        return b.op("pct_change", b.ref("close"), b.p("k1", 1))

    def neg(b, x):
        return b.op("mul", b.c(-1), x)

    # --- time series subset
    add(6, "volume", "time_series",
        lambda b: neg(b, b.op("ts_corr", b.ref("open"), b.ref("volume"), b.p("d", 10))))

    def a7(b):
        d7 = b.op("delta", b.ref("close"), b.p("k7", 7))
        val = b.op("mul", neg(b, b.op("ts_rank", b.op("abs", d7), b.p("d60", 60))), b.op("sign", d7))
        return cond(b, b.op("lt", adv20(b), b.ref("volume")), val, b.c(-1))
    add(7, "reversal", "time_series", a7, NORM)

    def a9(b):
        d1 = lambda: b.op("delta", b.ref("close"), b.p("k1", 1))
        c1 = b.op("gt", b.op("ts_min", d1(), b.p("d5", 5)), b.c(0))
        c2 = b.op("lt", b.op("ts_max", d1(), b.p("d5", 5)), b.c(0))
        return cond(b, c1, d1(), cond(b, c2, d1(), neg(b, d1())))
    add(9, "momentum", "time_series", a9)

    add(12, "reversal", "time_series", lambda b: b.op(
        "mul", b.op("sign", b.op("delta", b.ref("volume"), b.p("k1", 1))),
        neg(b, b.op("delta", b.ref("close"), b.p("k1", 1)))))

    def a23(b):
        c = b.op("lt", b.op("div", b.op("ts_sum", b.ref("high"), b.p("d20", 20)), b.c(20)), b.ref("high"))
        return b.op("mul", c, neg(b, b.op("delta", b.ref("high"), b.p("k2", 2))))
    add(23, "reversal", "time_series", a23)

    add(26, "volume", "time_series", lambda b: neg(b, b.op(
        "ts_max", b.op("ts_corr", b.op("ts_rank", b.ref("volume"), b.p("d5", 5)),
                       b.op("ts_rank", b.ref("high"), b.p("d5", 5)), b.p("d5", 5)), b.p("d3", 3))), NORM)

    def a35(b):
        r1 = b.op("ts_rank", b.ref("volume"), b.p("d32", 32))
        r2 = b.op("sub", b.c(1), b.op("ts_rank", b.op("sub", b.op("add", b.ref("close"), b.ref("high")),
                                                       b.ref("low")), b.p("d16", 16)))
        r3 = b.op("sub", b.c(1), b.op("ts_rank", returns(b), b.p("d32", 32)))
        return b.op("mul", b.op("mul", r1, r2), r3)
    add(35, "volume", "time_series", a35, NORM)

    add(41, "kbar", "time_series", lambda b: b.op(
        "sub", b.op("signed_power", b.op("mul", b.ref("high"), b.ref("low")), b.c(0.5)), b.ref("vwap")))

    add(43, "volume", "time_series", lambda b: b.op(
        "mul", b.op("ts_rank", b.op("div", b.ref("volume"), adv20(b)), b.p("d20", 20)),
        b.op("ts_rank", neg(b, b.op("delta", b.ref("close"), b.p("k7", 7))), b.p("d8", 8))), NORM)

    def a46(b):
        c = b.ref("close")
        x = b.op("sub",
                 b.op("div", b.op("sub", b.op("lag", c, b.p("k20", 20)), b.op("lag", b.ref("close"), b.p("k10", 10))), b.c(10)),
                 b.op("div", b.op("sub", b.op("lag", b.ref("close"), b.p("k10", 10)), b.ref("close")), b.c(10)))
        c1 = b.op("lt", b.c(0.25), x)
        c2 = b.op("lt", x, b.c(0))
        fall = neg(b, b.op("sub", b.ref("close"), b.op("lag", b.ref("close"), b.p("k1", 1))))
        return cond(b, c1, b.c(-1), cond(b, c2, b.c(1), fall))
    add(46, "reversal", "time_series", a46)

    def a53(b):
        c, l, h = b.ref("close"), b.ref("low"), b.ref("high")
        inner = b.op("div", b.op("sub", b.op("sub", c, l), b.op("sub", h, b.ref("close"))),
                     b.op("sub", b.ref("close"), b.ref("low")))
        return neg(b, b.op("delta", inner, b.p("k9", 9)))
    add(53, "reversal", "time_series", a53)

    add(54, "kbar", "time_series", lambda b: b.op(
        "div",
        neg(b, b.op("mul", b.op("sub", b.ref("low"), b.ref("close")), b.op("signed_power", b.ref("open"), b.c(5)))),
        b.op("mul", b.op("sub", b.ref("low"), b.ref("high")), b.op("signed_power", b.ref("close"), b.c(5)))))

    add(101, "kbar", "time_series", lambda b: b.op(
        "div", b.op("sub", b.ref("close"), b.ref("open")),
        b.op("add", b.op("sub", b.ref("high"), b.ref("low")), b.c(0.001))))

    # --- cross-sectional (stored; refused by the per-symbol evaluator)
    csr = lambda b, x: b.op("cs_rank", x)
    add(2, "volume", "cross_sectional", lambda b: neg(b, b.op(
        "ts_corr", csr(b, b.op("delta", b.op("log", b.ref("volume")), b.p("k2", 2))),
        csr(b, b.op("div", b.op("sub", b.ref("close"), b.ref("open")), b.ref("open"))), b.p("d6", 6))))
    add(3, "volume", "cross_sectional", lambda b: neg(b, b.op(
        "ts_corr", csr(b, b.ref("open")), csr(b, b.ref("volume")), b.p("d10", 10))))
    add(4, "reversal", "cross_sectional", lambda b: neg(b, b.op(
        "ts_rank", csr(b, b.ref("low")), b.p("d9", 9))), NORM)
    add(13, "volume", "cross_sectional", lambda b: neg(b, csr(b, b.op(
        "ts_cov", csr(b, b.ref("close")), csr(b, b.ref("volume")), b.p("d5", 5)))))
    add(14, "volume", "cross_sectional", lambda b: b.op(
        "mul", neg(b, csr(b, b.op("delta", returns(b), b.p("k3", 3)))),
        b.op("ts_corr", b.ref("open"), b.ref("volume"), b.p("d10", 10))))

    def a28(b):
        corr = b.op("ts_corr", adv20(b), b.ref("low"), b.p("d5", 5))
        mid = b.op("div", b.op("add", b.ref("high"), b.ref("low")), b.c(2))
        return b.op("cs_scale", b.op("sub", b.op("add", corr, mid), b.ref("close")))
    add(28, "volume", "cross_sectional", a28)

    def a32(b):
        t1 = b.op("cs_scale", b.op("sub", b.op("div", b.op("ts_sum", b.ref("close"), b.p("d7", 7)), b.c(7)), b.ref("close")))
        t2 = b.op("cs_scale", b.op("ts_corr", b.ref("vwap"), b.op("lag", b.ref("close"), b.p("k5", 5)), b.p("d230", 230)))
        return b.op("add", t1, b.op("mul", b.c(20), t2))
    add(32, "reversal", "cross_sectional", a32)

    add(33, "kbar", "cross_sectional", lambda b: csr(b, neg(b, b.op(
        "sub", b.c(1), b.op("div", b.ref("open"), b.ref("close"))))))
    add(40, "volume", "cross_sectional", lambda b: b.op(
        "mul", neg(b, csr(b, b.op("ts_std", b.ref("high"), b.p("d10", 10)))),
        b.op("ts_corr", b.ref("high"), b.ref("volume"), b.p("d10", 10))))
    add(44, "volume", "cross_sectional", lambda b: neg(b, b.op(
        "ts_corr", b.ref("high"), csr(b, b.ref("volume")), b.p("d5", 5))))

    def a55(b):
        lo, hi = b.op("ts_min", b.ref("low"), b.p("d12", 12)), b.op("ts_max", b.ref("high"), b.p("d12", 12))
        r = b.op("div", b.op("sub", b.ref("close"), lo), b.op("sub", hi, b.op("ts_min", b.ref("low"), b.p("d12", 12))))
        return neg(b, b.op("ts_corr", csr(b, r), csr(b, b.ref("volume")), b.p("d6", 6)))
    add(55, "volume", "cross_sectional", a55)
    return ts, cs


def build_documents() -> dict[str, dict]:
    a158 = _alpha158()
    ts, cs = _a101()
    return {
        "qlib_alpha158.v1.json": {"library": "qlib_alpha158", "templates": a158},
        "alpha101_time_series.v1.json": {"library": "alpha101", "templates": ts},
        "alpha101_cross_sectional.v1.json": {"library": "alpha101", "templates": cs},
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    for name, doc in build_documents().items():
        (HERE / name).write_text(render(doc), encoding="utf-8")
        print(name, len(doc["templates"]))
