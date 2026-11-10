"""ALPHA-TEMPLATE-LIBRARY-20261110 tests: schema, Alpha158 reference parity, Alpha101, registry."""
from __future__ import annotations

import importlib.util
import json
import math
import random
from pathlib import Path

import pytest

from services.registry import alpha_template as at
from services.registry.factor_rule import FactorRuleValidationError, evaluate_factor_expression
from services.registry.models import ArtifactType
from services.registry.split_api import RegistryService

LIB_DIR = at.ALPHA_TEMPLATE_LIBRARY_DIR


def _load_generator():
    spec = importlib.util.spec_from_file_location("alpha_template_generate", LIB_DIR / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def library():
    return {t["template_id"]: t for t in at.load_library_templates()}


def _fixture(n: int = 140):
    rnd = random.Random(20261110)
    o, h, l, c, v, vw = [], [], [], [], [], []
    px = 100.0
    for _ in range(n):
        op_ = px * (1 + rnd.uniform(-0.01, 0.01))
        cl = op_ * (1 + rnd.uniform(-0.03, 0.03))
        hi = max(op_, cl) * (1 + rnd.uniform(0.0005, 0.02))
        lo = min(op_, cl) * (1 - rnd.uniform(0.0005, 0.02))
        vo = rnd.uniform(1e5, 5e6)
        o.append(op_); h.append(hi); l.append(lo); c.append(cl); v.append(vo)
        vw.append((hi + lo + cl) / 3 * (1 + rnd.uniform(-0.002, 0.002)))
        px = cl
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "vwap": vw}


# ------------------------------------------------ independent reference (Qlib definitions)
def _mean(x): return sum(x) / len(x)
def _sstd(x):
    m = _mean(x); return math.sqrt(sum((a - m) ** 2 for a in x) / (len(x) - 1))
def _quantile(x, q):
    s = sorted(x); pos = q * (len(s) - 1); lo = int(pos); hi = min(lo + 1, len(s) - 1)
    return s[lo] + (pos - lo) * (s[hi] - s[lo])
def _corr(a, b):
    ma, mb = _mean(a), _mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den
def _ols(y):
    d = len(y); xs = list(range(1, d + 1)); xm, ym = _mean(xs), _mean(y)
    sxx = sum((x - xm) ** 2 for x in xs)
    b = sum((x - xm) * (v - ym) for x, v in zip(xs, y)) / sxx
    a = ym - b * xm
    res = [v - (a + b * x) for x, v in zip(xs, y)]
    ss_res = sum(r * r for r in res); ss_tot = sum((v - ym) ** 2 for v in y)
    return b, 1 - ss_res / ss_tot, res[-1]


def _ref(key: str, F, t: int) -> float:
    O, H, L, C, V, VW = (F[k] for k in ("open", "high", "low", "close", "volume", "vwap"))
    e = 1e-12
    o, h, l, c = O[t], H[t], L[t], C[t]
    simple = {
        "KMID": (c - o) / o, "KLEN": (h - l) / o, "KMID2": (c - o) / (h - l + e),
        "KUP": (h - max(o, c)) / o, "KUP2": (h - max(o, c)) / (h - l + e),
        "KLOW": (min(o, c) - l) / o, "KLOW2": (min(o, c) - l) / (h - l + e),
        "KSFT": (2 * c - h - l) / o, "KSFT2": (2 * c - h - l) / (h - l + e),
        "OPEN0": o / c, "HIGH0": h / c, "LOW0": l / c, "VWAP0": VW[t] / c,
    }
    if key in simple:
        return simple[key]
    name = key.rstrip("0123456789"); d = int(key[len(name):])
    win = lambda x: x[t - d + 1: t + 1]
    rng = range(t - d + 1, t + 1)
    up = [max(C[i] - C[i - 1], 0) for i in rng]; dn = [max(C[i - 1] - C[i], 0) for i in rng]
    ab = [abs(C[i] - C[i - 1]) for i in rng]
    vup = [max(V[i] - V[i - 1], 0) for i in rng]; vdn = [max(V[i - 1] - V[i], 0) for i in rng]
    vab = [abs(V[i] - V[i - 1]) for i in rng]
    if name == "ROC": return C[t - d] / c
    if name == "MA": return _mean(win(C)) / c
    if name == "STD": return _sstd(win(C)) / c
    if name == "BETA": return _ols(win(C))[0] / c
    if name == "RSQR": return _ols(win(C))[1]
    if name == "RESI": return _ols(win(C))[2] / c
    if name == "MAX": return max(win(H)) / c
    if name == "MIN": return min(win(L)) / c
    if name == "QTLU": return _quantile(win(C), 0.8) / c
    if name == "QTLD": return _quantile(win(C), 0.2) / c
    if name == "RANK":
        w = win(C); left = sum(1 for x in w if x < c); right = sum(1 for x in w if x <= c)
        return (left + right + (1 if right > left else 0)) / (2 * d)
    if name == "RSV": return (c - min(win(L))) / (max(win(H)) - min(win(L)) + e)
    if name == "IMAX": return (win(H).index(max(win(H))) + 1) / d
    if name == "IMIN": return (win(L).index(min(win(L))) + 1) / d
    if name == "IMXD": return (win(H).index(max(win(H))) - win(L).index(min(win(L)))) / d
    if name == "CORR": return _corr(win(C), [math.log(x + 1) for x in win(V)])
    if name == "CORD":
        return _corr([C[i] / C[i - 1] for i in rng], [math.log(V[i] / V[i - 1] + 1) for i in rng])
    cp = sum(1 for i in rng if C[i] > C[i - 1]) / d
    cn = sum(1 for i in rng if C[i] < C[i - 1]) / d
    if name == "CNTP": return cp
    if name == "CNTN": return cn
    if name == "CNTD": return cp - cn
    if name == "SUMP": return sum(up) / (sum(ab) + e)
    if name == "SUMN": return sum(dn) / (sum(ab) + e)
    if name == "SUMD": return (sum(up) - sum(dn)) / (sum(ab) + e)
    if name == "VMA": return _mean(win(V)) / (V[t] + e)
    if name == "VSTD": return _sstd(win(V)) / (V[t] + e)
    if name == "WVMA":
        w = [abs(C[i] / C[i - 1] - 1) * V[i] for i in rng]
        return _sstd(w) / (_mean(w) + e)
    if name == "VSUMP": return sum(vup) / (sum(vab) + e)
    if name == "VSUMN": return sum(vdn) / (sum(vab) + e)
    if name == "VSUMD": return (sum(vup) - sum(vdn)) / (sum(vab) + e)
    raise KeyError(key)


# ------------------------------------------------ tests
def test_schema_file_is_valid_draft7():
    from jsonschema import Draft7Validator
    Draft7Validator.check_schema(json.loads(at.ALPHA_TEMPLATE_SCHEMA_PATH.read_text()))


def test_committed_library_matches_generator():
    gen = _load_generator()
    for name, doc in gen.build_documents().items():
        assert (LIB_DIR / name).read_text(encoding="utf-8") == gen.render(doc), name


def test_every_template_validates_and_carries_provenance(library):
    assert len(library) == len(set(library))
    for tid, t in library.items():
        rec = at.validate_alpha_template(t)
        assert rec["origin"]["library"] in {"qlib_alpha158", "alpha101"}
        assert rec["licence"] and rec["attribution"]
        assert rec["status"] == "draft" and rec["evaluation_ref"] is None
        if rec["origin"]["library"] == "qlib_alpha158":
            assert rec["licence"] == "MIT" and "Microsoft" in rec["attribution"]
        else:
            assert "arXiv:1601.00991" in rec["attribution"]


def test_no_gpl_or_noncommercial_material(library):
    blob = json.dumps(list(library.values())).lower()
    for banned in ("gpl", "opensourceap", "crosssection", "jkp", "cc by-nc", "wrds"):
        assert banned not in blob


def test_alpha158_has_all_158(library):
    ids = [i for i in library if i.startswith("qlib_alpha158:")]
    assert len(ids) == 158
    assert all(library[i]["scope"] == "time_series" for i in ids)


@pytest.mark.parametrize("t", [139, 100, 70])
def test_alpha158_matches_reference_on_fixture(library, t):
    F = _fixture()
    env = {k: v[: t + 1] for k, v in F.items()}
    checked = 0
    for tid, tpl in library.items():
        if not tid.startswith("qlib_alpha158:"):
            continue
        key = tid.split(":", 1)[1]
        got = at.evaluate_alpha_template(tpl, {k: env[k] for k in (i["name"] for i in tpl["inputs"])})
        want = _ref(key, F, t)
        assert got == pytest.approx(want, rel=1e-8, abs=1e-9), (key, got, want)
        checked += 1
    assert checked == 158


def test_alpha101_time_series_subset(library):
    ts = [t for i, t in library.items() if i.startswith("alpha101:") and t["scope"] == "time_series"]
    assert {t["origin"]["ref"] for t in ts} == {"#6", "#7", "#9", "#12", "#23", "#26", "#35", "#41", "#43", "#46", "#53", "#54", "#101"}
    F = _fixture(); t = len(F["close"]) - 1
    O, H, L, C, V, VW = (F[k] for k in ("open", "high", "low", "close", "volume", "vwap"))
    adv20 = _mean(V[-20:])
    want = {
        "#101": (C[t] - O[t]) / (H[t] - L[t] + 0.001),
        "#12": (1 if V[t] > V[t - 1] else -1 if V[t] < V[t - 1] else 0) * (-(C[t] - C[t - 1])),
        "#41": math.sqrt(H[t] * L[t]) - VW[t],
        "#54": -((L[t] - C[t]) * O[t] ** 5) / ((L[t] - H[t]) * C[t] ** 5),
        "#6": -_corr(O[-10:], V[-10:]),
        "#23": (-(H[t] - H[t - 2]) if sum(H[-20:]) / 20 < H[t] else 0.0),
        "#53": -(((C[t] - L[t]) - (H[t] - C[t])) / (C[t] - L[t])
                 - ((C[t - 9] - L[t - 9]) - (H[t - 9] - C[t - 9])) / (C[t - 9] - L[t - 9])),
    }
    d1 = [C[i] - C[i - 1] for i in range(t - 4, t + 1)]
    want["#9"] = d1[-1] if (min(d1) > 0 or max(d1) < 0) else -d1[-1]
    x = (C[t - 20] - C[t - 10]) / 10 - (C[t - 10] - C[t]) / 10
    want["#46"] = -1.0 if 0.25 < x else (1.0 if x < 0 else -(C[t] - C[t - 1]))
    by_ref = {tpl["origin"]["ref"]: tpl for tpl in ts}
    for ref, w in want.items():
        tpl = by_ref[ref]
        got = at.evaluate_alpha_template(tpl, {i["name"]: F[i["name"]] for i in tpl["inputs"]})
        assert got == pytest.approx(w, rel=1e-8, abs=1e-10), ref
    # rank-based ones: finite and bounded sanity (#7 uses adv20 vs volume)
    for ref in ("#7", "#26", "#35", "#43"):
        tpl = by_ref[ref]
        val = at.evaluate_alpha_template(tpl, {i["name"]: F[i["name"]] for i in tpl["inputs"]})
        assert math.isfinite(val)
    assert adv20 > 0


def test_cross_sectional_templates_stored_and_refused(library):
    cs = [t for i, t in library.items() if i.startswith("alpha101:") and t["scope"] == "cross_sectional"]
    assert len(cs) == 11
    F = _fixture()
    for tpl in cs:
        at.validate_alpha_template(tpl)
        assert at._expression_has_cross_sectional_op(tpl["expression"])
        with pytest.raises(at.AlphaTemplateValidationError, match="refuses cross-sectional"):
            at.evaluate_alpha_template(tpl, {i["name"]: F[i["name"]] for i in tpl["inputs"]})
    # the factor_rule per-symbol expression parser also refuses the same AST
    with pytest.raises(FactorRuleValidationError):
        evaluate_factor_expression(cs[0]["expression"], {i["name"]: F[i["name"]] for i in cs[0]["inputs"]}, cs[0]["parameters"])
    # a template mislabelled time_series that uses a cs operator is rejected at validation
    bad = json.loads(json.dumps(cs[0])); bad["scope"] = "time_series"
    with pytest.raises(at.AlphaTemplateValidationError):
        at.validate_alpha_template(bad)


def test_validation_rejects_bad_records(library):
    base = library["qlib_alpha158:KMID"]
    for mutate in (
        lambda t: t.pop("licence"),
        lambda t: t.update(family="nope"),
        lambda t: t.update(inputs=[]),
        lambda t: t["inputs"][0].update(field="nonexistent_field"),
        lambda t: t.update(parameters={"x": 1}),
    ):
        t = json.loads(json.dumps(base)); mutate(t)
        with pytest.raises(at.AlphaTemplateValidationError):
            at.validate_alpha_template(t)


def test_registry_store_and_list(library):
    from services.registry.storage import reset_store, get_store
    reset_store()
    svc = RegistryService(get_store())
    views = at.ensure_builtin_alpha_templates(svc)
    assert len(views) == len(library) == 182
    again = at.ensure_builtin_alpha_templates(svc)  # idempotent
    assert [v.entry.registry_id for v in again] == [v.entry.registry_id for v in views]
    listed = [v for v in svc.list_all_entries() if v.entry.artifact_type == ArtifactType.ALPHA_TEMPLATE]
    assert len(listed) == 182
    one = svc.get(at.alpha_template_registry_id("qlib_alpha158:KMID"))
    assert one.entry.metadata["alpha_template"]["template_id"] == "qlib_alpha158:KMID"
    assert one.entry.artifact_type == ArtifactType.ALPHA_TEMPLATE


def test_http_store_list_and_generic_route_rejection():
    from fastapi.testclient import TestClient
    from services.registry.service import app
    from services.registry.storage import reset_store
    reset_store()
    with TestClient(app, headers={"Authorization": "Bearer test-operator:operator"}) as client:
        r = client.get("/api/registry/alpha-templates")
        assert r.status_code == 200, r.text
        assert len(r.json()) == 182
        cs = client.get("/api/registry/alpha-templates", params={"scope": "cross_sectional"}).json()
        assert len(cs) == 11
        kb = client.get("/api/registry/alpha-templates", params={"library": "qlib_alpha158"}).json()
        assert len(kb) == 158
        rid = at.alpha_template_registry_id("alpha101:#12")
        got = client.get(f"/api/registry/alpha-templates/{rid}")
        assert got.status_code == 200 and got.json()["entry"]["strategy_id"] == "alpha101:#12"
        bad = client.post("/api/registry/entries", json={
            "artifact_type": "alpha_template", "strategy_id": "x", "version": "1.0.0"})
        assert bad.status_code == 400
        invalid = client.post("/api/registry/alpha-templates", json={"alpha_template": {"template_id": "x"}})
        assert invalid.status_code == 400
        tpl = json.loads(json.dumps(at.load_library_templates()[0]))
        tpl["template_id"] = "custom:MyKmid"; tpl["origin"] = {"library": "custom", "ref": "MyKmid"}
        ok = client.post("/api/registry/alpha-templates", json={"alpha_template": tpl})
        assert ok.status_code == 200, ok.text
        again = client.post("/api/registry/alpha-templates", json={"alpha_template": tpl})
        assert again.status_code == 200
        assert len(client.get("/api/registry/alpha-templates").json()) == 183
    reset_store()
