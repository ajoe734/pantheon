"""BFF-ATTRIBUTION-STRATEGY-LABEL-VIEW-20261106.

Performance attribution labels strategy rows from one bounded registry label
read made only after paging; it never reads or normalizes the full strategy
registry list.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from services.control_plane.bff.agora.performance import service as perf
from services.control_plane.bff.command_adapters import base as adapter_base
from services.control_plane.bff.core import owner_reads
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)

HOSTED_LABELS = {
    "strategy-persona-298e138c136c9840b0e4": "青禾｜2330 台股 Paper 學習 R2",
    "strategy-persona-01390a4b72713295b001": "衡岳｜0050 台股 Paper 學習",
    "strategy-persona-8ae996980eeccb80d928": "澄川｜2317 台股 Paper 學習",
}


class _Store:
    """Read store whose full strategy list must never be touched."""

    def __init__(self, labels: Any = None, fail: bool = False) -> None:
        self.label_calls = 0
        self.full_list_calls = 0
        self._labels = labels if labels is not None else {
            sid: {"title": title, "name": title} for sid, title in HOSTED_LABELS.items()
        }
        self._fail = fail

    def list_strategy_specs(self, **_kw: Any) -> List[Dict[str, Any]]:
        self.full_list_calls += 1
        raise AssertionError("attribution must not call list_strategy_specs")

    def get_strategy_labels(self) -> Dict[str, Any]:
        self.label_calls += 1
        if self._fail:
            raise RuntimeError("registry down")
        return self._labels

    def dataset_source(self, _dataset: str) -> str:
        return "registry"


def _sources(store: _Store) -> Dict[str, Any]:
    runtimes = [
        {"runtime_id": f"rt-{i}", "persona_id": f"p-{i}", "status": "active",
         "strategy_id": sid, "capital_pool_id": "pool-1"}
        for i, sid in enumerate([*HOSTED_LABELS, "strategy-unknown"])
    ]
    telemetry = {
        r["runtime_id"]: {"runtime_id": r["runtime_id"], "pnl": 1.0, "total_trades": 1,
                          "collected_at": "2026-11-06T00:00:00Z"}
        for r in runtimes
    }
    sources = perf.pm12_performance_attribution_sources(read_store=store)
    sources.update(
        runtime_bindings=runtimes,
        telemetry_by_runtime_id=telemetry,
        personas_by_id={r["persona_id"]: {"persona_id": r["persona_id"], "name": r["persona_id"]} for r in runtimes},
    )
    return sources


def _response(store: _Store, dimensions: List[str], **kw: Any) -> Dict[str, Any]:
    return perf.pm12_performance_attribution_response(
        dimensions=dimensions, period="latest", page_token=None, page_size=kw.pop("page_size", 50),
        read_store=store, sources_fn=lambda _t: _sources(store),
        utc_now=lambda: "2026-11-06T00:00:00Z", **kw,
    )


def test_sources_never_call_full_strategy_list() -> None:
    store = _Store()
    sources = perf.pm12_performance_attribution_sources(read_store=store)
    assert store.full_list_calls == 0 and store.label_calls == 0
    assert sources["strategies"] == [] and sources["strategies_by_id"] == {}


def test_page_without_strategy_row_makes_zero_registry_strategy_reads() -> None:
    store = _Store()
    payload = _response(store, ["persona", "runtime"])
    assert payload["data"]["items"]
    assert store.label_calls == 0 and store.full_list_calls == 0


def test_page_with_strategy_rows_makes_exactly_one_bounded_read_and_keeps_labels() -> None:
    store = _Store()
    payload = _response(store, ["strategy"])
    assert store.label_calls == 1 and store.full_list_calls == 0
    labels = {r["dimension_key"]: r["label"] for r in payload["data"]["items"]}
    for sid, title in HOSTED_LABELS.items():
        assert labels[sid] == title
    assert labels["strategy-unknown"] == "strategy-unknown"  # id fallback
    assert payload["meta"]["surfaces"]["strategies"]["status"] == "ok"


def test_label_precedence_title_then_name_then_id() -> None:
    store = _Store(labels={
        "strategy-persona-298e138c136c9840b0e4": {"title": "T", "name": "N"},
        "strategy-persona-01390a4b72713295b001": {"name": "Only name"},
        "strategy-persona-8ae996980eeccb80d928": {},
    })
    labels = {r["dimension_key"]: r["label"] for r in _response(store, ["strategy"])["data"]["items"]}
    assert labels["strategy-persona-298e138c136c9840b0e4"] == "T"
    assert labels["strategy-persona-01390a4b72713295b001"] == "Only name"
    assert labels["strategy-persona-8ae996980eeccb80d928"] == "strategy-persona-8ae996980eeccb80d928"


def test_only_returned_page_triggers_label_read() -> None:
    store = _Store()
    # Page 1 holds only persona-dimension rows ahead of strategy rows.
    payload = _response(store, ["persona", "strategy"], page_size=2)
    assert all(r["dimension"] == "persona" for r in payload["data"]["items"])
    assert store.label_calls == 0


def test_failed_label_read_degrades_instead_of_failing() -> None:
    store = _Store(fail=True)
    payload = _response(store, ["strategy"])
    labels = {r["dimension_key"]: r["label"] for r in payload["data"]["items"]}
    assert labels == {sid: sid for sid in [*HOSTED_LABELS, "strategy-unknown"]}
    surface = payload["meta"]["surfaces"]["strategies"]
    assert surface["status"] == "degraded" and "RuntimeError" in surface["note"]
    assert store.label_calls == 1 and store.full_list_calls == 0


def test_per_persona_read_model_sources_do_not_read_strategies() -> None:
    store = _Store()
    sources = perf.pm12_performance_attribution_sources("tenant-dev", read_store=store)
    perf.pm12_performance_attribution_facts(sources)
    assert store.full_list_calls == 0 and store.label_calls == 0


# --- port level: auth / tenant forwarding, same shape as the count read -----

def _port_with(monkeypatch, fake) -> DefaultResearchKnowledgeSourcePort:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")
    monkeypatch.setattr(adapter_base, "http_request_json", fake)
    return DefaultResearchKnowledgeSourcePort()


def _row(sid, reg, version, created, spec_title=None, meta=None):
    metadata = {"persona_ids": ["p1"], **(meta or {})}
    if spec_title:
        metadata["strategy_spec"] = {"title": spec_title}
    return {"entry": {"registry_id": reg, "artifact_type": "strategy_spec", "strategy_id": sid,
                      "version": version, "owner_tenant": "tenant-dev", "created_at": created,
                      "metadata": metadata}}


def test_label_read_forwards_authorization_and_selected_tenant(monkeypatch) -> None:
    calls: list = []
    rows = [
        _row("s1", "s1-v1", "1.0.0", "2026-01-01T00:00:00Z", "Old"),
        _row("s1", "s1-v10", "1.10.0", "2026-02-01T00:00:00Z", "Current"),
        _row("s1", "s1-v2", "1.2.0", "2026-03-01T00:00:00Z", "Mid"),
        _row("s2", "s2-v1", "1.0.0", "2026-01-01T00:00:00Z", None, {"name": "Meta name"}),
        _row("s3", "s3-v1", "1.0.0", "2026-01-01T00:00:00Z"),
    ]

    def fake(url, **kwargs):
        calls.append((url, kwargs.get("auth_token"), kwargs.get("tenant_id")))
        return rows

    port = _port_with(monkeypatch, fake)
    t1 = owner_reads.authorization.set("Bearer workshop-user:operator:tenant-dev")
    t2 = owner_reads.selected_tenant.set("tenant-dev")
    try:
        labels = port.get_strategy_labels()
    finally:
        owner_reads.selected_tenant.reset(t2)
        owner_reads.authorization.reset(t1)
    assert labels == {
        "s1": {"title": "Current", "name": "Current"},
        "s2": {"title": "Meta name", "name": "Meta name"},
        "s3": {"title": "s3", "name": "s3"},
    }
    assert calls == [(
        "http://registry.internal/api/registry/strategy-specs?view=persona_binding",
        "workshop-user:operator:tenant-dev", "tenant-dev",
    )]


def test_label_read_without_authorization_or_with_forbidden_never_returns_labels(monkeypatch) -> None:
    import urllib.error
    from fastapi import HTTPException

    leak = [_row("leak", "leak-v1", "1.0.0", "2026-01-01T00:00:00Z", "other tenant")]
    port = _port_with(monkeypatch, lambda *a, **k: leak)
    with pytest.raises(HTTPException) as no_auth:
        port.get_strategy_labels()
    assert no_auth.value.status_code == 503

    def forbidden(url, **kwargs):
        raise urllib.error.HTTPError(url, 403, "x", hdrs=None, fp=None)

    port = _port_with(monkeypatch, forbidden)
    t1 = owner_reads.authorization.set("Bearer workshop-user:operator:tenant-dev")
    try:
        with pytest.raises(HTTPException) as denied:
            port.get_strategy_labels()
    finally:
        owner_reads.authorization.reset(t1)
    assert denied.value.status_code == 403


def test_label_read_rejects_invalid_payload(monkeypatch) -> None:
    from fastapi import HTTPException

    t1 = owner_reads.authorization.set("Bearer workshop-user:operator:tenant-dev")
    try:
        for bad in ({}, ["x"], [{"entry": {"strategy_id": "s1"}}]):
            port = _port_with(monkeypatch, lambda *a, _b=bad, **k: _b)
            with pytest.raises(HTTPException) as exc:
                port.get_strategy_labels()
            assert exc.value.status_code == 503
    finally:
        owner_reads.authorization.reset(t1)


def test_injected_store_labels_use_current_version() -> None:
    port = DefaultResearchKnowledgeSourcePort(strategy_specs_store={
        "s1": {"strategy_id": "s1", "title": "Spec title", "versions": [
            {"spec_version_id": "s1-v1", "spec_version": "1.0.0", "title": "Old", "created_at": "2026-01-01T00:00:00Z"},
            {"spec_version_id": "s1-v10", "spec_version": "1.10.0", "title": "Current", "created_at": "2026-02-01T00:00:00Z"},
            {"spec_version_id": "s1-v2", "spec_version": "1.2.0", "title": "Mid", "created_at": "2026-03-01T00:00:00Z"},
        ]},
        "s2": {"strategy_id": "s2", "name": "Name only"},
    })
    assert port.get_strategy_labels() == {
        "s1": {"title": "Current", "name": "Current"},
        "s2": {"title": "Name only", "name": "Name only"},
    }
