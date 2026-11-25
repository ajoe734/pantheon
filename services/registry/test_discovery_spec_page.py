"""Keyset paged discovery read of StrategySpecs (REGISTRY-DISCOVERY-SPEC-PAGE-20261125).

``GET /api/registry/strategy-specs?view=discovery_candidates&tenant_id=<t>`` returns
caller-visible ``strategy_spec`` rows whose ``artifact_state`` is not retired/rejected,
projected to ``registry_id``/``strategy_id``/``artifact_state``/``lineage``/``metadata``,
cut at ``page_size`` (max 50) with an opaque keyset token bound to view and tenant.

The in-memory tests always run. The Postgres tests run against a real Postgres when
``TEST_DATABASE_URL`` is set, with the hosted row mix (7521 drafts, 1556 retired,
338 approved, other tenants/types, legacy JSON-string payloads, missing metadata;
about 5.6 kB per row). ``REGISTRY_DISCOVERY_EVIDENCE_OUT`` records the measurements.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import threading
import tracemalloc
from uuid import uuid4

import pytest

from . import pg_store as pg_store_module
from . import storage as storage_module
from .models import (
    BUILTIN_TENANT, ArtifactState, ArtifactType, Lineage, RegistryEntry, StorageBackend, StorageRef,
)
from .pg_store import PostgresRegistryStore
from .split_api import RegistryService
from .storage import RegistryStore
from .test_service import _bearer, _jwt, strict_client  # noqa: F401
from .test_strategy_summary_page_bounded import _realistic_strategy_spec_payload

VIEW = "/api/registry/strategy-specs?view=discovery_candidates"
FIELDS = {"registry_id", "strategy_id", "artifact_state", "lineage", "metadata"}
EVIDENCE_ENV = "REGISTRY_DISCOVERY_EVIDENCE_OUT"
TARGET_ROW_BYTES = 5600

# Hosted counts (registry group conversation 2026-11-25) plus the other shapes the
# equality proof needs; total 9940 rows.
HOSTED_MIX = (
    # (tenant, artifact_type, state, count)
    ("tenant-a", ArtifactType.STRATEGY_SPEC, ArtifactState.DRAFT, 7521),
    ("tenant-a", ArtifactType.STRATEGY_SPEC, ArtifactState.RETIRED, 1556),
    ("tenant-a", ArtifactType.STRATEGY_SPEC, ArtifactState.APPROVED, 338),
    ("tenant-a", ArtifactType.STRATEGY_SPEC, ArtifactState.CANDIDATE, 150),
    ("tenant-b", ArtifactType.STRATEGY_SPEC, ArtifactState.DRAFT, 200),
    (BUILTIN_TENANT, ArtifactType.STRATEGY_SPEC, ArtifactState.APPROVED, 10),
    ("tenant-a", ArtifactType.EXECUTION_BUNDLE, ArtifactState.DRAFT, 100),
    ("tenant-a", ArtifactType.MODEL_ARTIFACT, ArtifactState.DRAFT, 65),
)


def _record(label: str, data: dict) -> None:
    out = os.getenv(EVIDENCE_ENV, "").strip()
    if not out:
        return
    try:
        existing = json.loads(open(out).read())
    except (OSError, ValueError):
        existing = {}
    existing[label] = data
    with open(out, "w") as fh:
        json.dump(existing, fh, indent=2, sort_keys=True)


def _sized_spec(sid: str, state: ArtifactState) -> dict:
    """The realistic summary-page spec, trimmed so the stored row is about 5.6 kB."""
    spec = _realistic_strategy_spec_payload(sid, lifecycle_state=state.value, persona_ids=["persona-7"])
    spec["description"] = spec["description"][:120]
    while len(json.dumps(spec)) > TARGET_ROW_BYTES - 1150 and isinstance(spec.get("signals"), list) and spec["signals"]:
        spec["signals"].pop()
    return spec


def _entry(rid, *, tenant="tenant-a", atype=ArtifactType.STRATEGY_SPEC, state=ArtifactState.DRAFT,
           metadata="spec", bulk=True, lineage=None) -> RegistryEntry:
    sid = f"strat-{rid}"
    if metadata == "spec":
        metadata = {"strategy_spec": _sized_spec(sid, state) if bulk else {"strategy_id": sid}, "tenant_id": tenant}
    return RegistryEntry(
        registry_id=rid, artifact_type=atype, strategy_id=sid, version="1.0.0", artifact_state=state,
        lineage=lineage if lineage is not None else Lineage(source_run_ids=[f"run-{rid}"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum=f"sha256:{rid}", metadata=metadata,
        created_at="2026-11-01T00:00:00Z", updated_at="2026-11-01T00:00:00Z", owner_tenant=tenant,
        approval_evidence={"blob": "e" * 400} if state == ArtifactState.APPROVED else None,
    )


def _hosted_entries():
    """Hosted-shape population: mixed states/tenants/types, some missing metadata or lineage."""
    n = 0
    for tenant, atype, state, count in HOSTED_MIX:
        for i in range(count):
            n += 1
            rid = f"reg-{tenant.strip('_')}-{atype.value}-{state.value}-{i:05d}"
            kwargs = {}
            if i % 97 == 5:
                kwargs["metadata"] = None
            if i % 89 == 7:
                kwargs["lineage"] = Lineage()
            yield _entry(rid, tenant=tenant, atype=atype, state=state, **kwargs)


def _walk(get_page, *, max_pages=1000):
    seen, token, pages = [], None, 0
    while pages < max_pages:
        page = get_page(token)
        pages += 1
        seen.extend(page["items"])
        token = page["next_page_token"]
        if token is None:
            return seen, pages
    raise AssertionError("did not terminate")


def _expected_from_unpaged(unpaged: list[dict]) -> list[dict]:
    return sorted(
        (
            {k: row["entry"][k] for k in FIELDS}
            for row in unpaged
            if row["entry"]["artifact_state"] not in ("retired", "rejected")
        ),
        key=lambda e: e["registry_id"],
    )


# -- In-memory owner ---------------------------------------------------------


def test_http_all_pages_equal_unpaged_filtered_set_and_carry_five_fields(strict_client):  # noqa: F811
    store = storage_module.get_store()
    for e in _hosted_entries_small():
        store.put(e)
    token = _jwt(subject="disc", tenant="tenant-a")
    seen, pages = _walk(lambda t: strict_client.get(
        VIEW + "&tenant_id=tenant-a&page_size=7" + (f"&page_token={t}" if t else ""),
        headers=_bearer(token)).json())
    unpaged = strict_client.get(
        "/api/registry/strategy-specs", headers={**_bearer(token), "X-Tenant-Id": "tenant-a"}).json()
    entries = [row["entry"] for row in seen]
    assert all(set(e) == FIELDS for e in entries)
    assert entries == _expected_from_unpaged(unpaged)
    assert pages > 1
    states = {e["artifact_state"] for e in entries}
    assert states == {"draft", "candidate", "approved"}
    assert any(e["metadata"] is None for e in entries)
    owners = {r["entry"]["owner_tenant"] for r in unpaged if r["entry"]["registry_id"] in {e["registry_id"] for e in entries}}
    assert "tenant-b" not in owners


def _hosted_entries_small():
    """Same shape mix at 1/50 scale for the HTTP equality test."""
    for tenant, atype, state, count in HOSTED_MIX:
        for i in range(max(3, count // 50)):
            rid = f"reg-{tenant.strip('_')}-{atype.value}-{state.value}-{i:05d}"
            yield _entry(
                rid, tenant=tenant, atype=atype, state=state, bulk=False,
                **({"metadata": None} if i == 1 else {}),
                **({"lineage": Lineage()} if i == 2 else {}),
            )


def test_page_size_is_capped_at_fifty(strict_client):  # noqa: F811
    store = storage_module.get_store()
    for i in range(60):
        store.put(_entry(f"reg-cap-{i:03d}", bulk=False))
    token = _jwt(subject="disc", tenant="tenant-a")
    body = strict_client.get(VIEW + "&tenant_id=tenant-a&page_size=200", headers=_bearer(token)).json()
    assert len(body["items"]) == 50 and body["next_page_token"]


def test_tenant_and_auth_denials_match_the_unpaged_call(strict_client):  # noqa: F811
    token_a = _jwt(subject="disc", tenant="tenant-a")
    unpaged = "/api/registry/strategy-specs"
    # Unauthenticated caller: same status as the unpaged list.
    assert strict_client.get(VIEW + "&tenant_id=tenant-a").status_code == strict_client.get(unpaged).status_code == 401
    # Caller without a read role: same denial.
    nobody = _jwt(subject="nobody", tenant="tenant-a", roles=("nobody",))
    denied = strict_client.get(VIEW + "&tenant_id=tenant-a", headers=_bearer(nobody))
    assert denied.status_code == strict_client.get(unpaged, headers=_bearer(nobody)).status_code == 403
    # Wrong tenant: same 403 and detail as the unpaged list with that X-Tenant-Id.
    wrong = strict_client.get(VIEW + "&tenant_id=tenant-b", headers=_bearer(token_a))
    today = strict_client.get(unpaged, headers={**_bearer(token_a), "X-Tenant-Id": "tenant-b"})
    assert (wrong.status_code, wrong.json()) == (today.status_code, today.json()) == (403, today.json())
    # Header and tenant_id that disagree are refused too.
    mixed = strict_client.get(VIEW + "&tenant_id=tenant-a", headers={**_bearer(token_a), "X-Tenant-Id": "tenant-b"})
    assert mixed.status_code == 403
    # Reserved builtin tenant as scope: same denial as the header form.
    builtin = strict_client.get(VIEW + f"&tenant_id={BUILTIN_TENANT}", headers=_bearer(token_a))
    assert builtin.status_code == 403
    # tenant_id is required.
    assert strict_client.get(VIEW, headers=_bearer(token_a)).status_code == 400


def test_tokens_are_bound_to_view_and_tenant_and_carry_no_tenant_identifier(strict_client):  # noqa: F811
    store = storage_module.get_store()
    for i in range(4):
        store.put(_entry(f"reg-tok-a-{i}", bulk=False))
        store.put(_entry(f"reg-tok-b-{i}", tenant="tenant-b", bulk=False))
    a, b = _jwt(subject="a", tenant="tenant-a"), _jwt(subject="b", tenant="tenant-b")
    page_a = strict_client.get(VIEW + "&tenant_id=tenant-a&page_size=1", headers=_bearer(a)).json()
    token = page_a["next_page_token"]
    decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    assert "tenant-a" not in decoded and "tenant" not in token
    # Another tenant's token is refused.
    other = strict_client.get(VIEW + f"&tenant_id=tenant-b&page_token={token}", headers=_bearer(b))
    assert other.status_code == 400 and "Invalid page_token" in other.json()["detail"]
    # A card admission cursor (raw registry_id) or another view's token is refused.
    raw = strict_client.get(VIEW + "&tenant_id=tenant-a&page_token=reg-tok-a-0", headers=_bearer(a))
    assert raw.status_code == 400
    forged = base64.urlsafe_b64encode(json.dumps(
        {"v": "strategy_summary_page", "s": json.loads(decoded)["s"], "k": "reg-tok-a-0"}).encode()).decode()
    assert strict_client.get(VIEW + f"&tenant_id=tenant-a&page_token={forged}", headers=_bearer(a)).status_code == 400
    # The same token keeps working for its own tenant and view.
    two = strict_client.get(VIEW + "&tenant_id=tenant-a&page_size=2", headers=_bearer(a)).json()["items"]
    ok = strict_client.get(VIEW + f"&tenant_id=tenant-a&page_size=1&page_token={token}", headers=_bearer(a))
    assert ok.status_code == 200 and ok.json()["items"][0]["entry"] == two[1]["entry"]


def test_page_walk_is_stable_while_entries_are_appended_or_change_state():
    store = RegistryStore()
    for i in range(0, 40, 2):
        store.put(_entry(f"reg-st-{i:03d}", bulk=False))
    service = RegistryService(store=store)
    visible = frozenset({"tenant-a", BUILTIN_TENANT})

    def page(token):
        return service.discovery_candidate_page(
            visible_tenants=visible, tenant_id="tenant-a", page_size=5, page_token=token)

    first = page(None)
    seen = [r["entry"]["registry_id"] for r in first["items"]]
    assert seen == [f"reg-st-{i:03d}" for i in range(0, 10, 2)]
    # Mutations between pages: appends behind and ahead of the cursor, a retire ahead,
    # and a state change of an already-returned row.
    store.put(_entry("reg-st-001", bulk=False))                     # behind cursor
    store.put(_entry("reg-st-021", bulk=False))                     # ahead of cursor
    retired = store.get("reg-st-030")
    retired.artifact_state = ArtifactState.RETIRED
    store.put(retired)
    promoted = store.get("reg-st-002")
    promoted.artifact_state = ArtifactState.CANDIDATE
    store.put(promoted)
    token = first["next_page_token"]
    while token is not None:
        nxt = page(token)
        seen += [r["entry"]["registry_id"] for r in nxt["items"]]
        token = nxt["next_page_token"]
    assert len(seen) == len(set(seen))
    unchanged = {f"reg-st-{i:03d}" for i in range(0, 40, 2)} - {"reg-st-030"}
    assert unchanged <= set(seen)
    assert "reg-st-021" in seen and "reg-st-030" not in seen and "reg-st-001" not in seen
    assert seen == sorted(seen)


def test_in_memory_owner_projects_only_page_rows_of_strategy_spec(monkeypatch):
    store = RegistryStore()
    for e in _hosted_entries_small():
        store.put(e)
    projected = []
    real = storage_module.discovery_candidate_projection

    def spy(entry):
        projected.append(entry)
        return real(entry)

    monkeypatch.setattr(storage_module, "discovery_candidate_projection", spy)
    rows = store.list_discovery_candidates(visible_tenants=frozenset({"tenant-a"}), limit=6)
    assert len(projected) == len(rows) == 6
    assert all(e.artifact_type == ArtifactType.STRATEGY_SPEC for e in projected)
    assert all(e.artifact_state.value not in ("retired", "rejected") for e in projected)


def test_in_memory_hosted_scale_peak_memory_twenty_pages_and_three_readers():
    store = RegistryStore()
    for e in _hosted_entries():
        store.put(e)
    assert len(store._entries) == 9940
    service = RegistryService(store=store)
    visible = frozenset({"tenant-a", BUILTIN_TENANT})

    def walk_twenty(out):
        token, n, rows = None, 0, 0
        for _ in range(20):
            page = service.discovery_candidate_page(
                visible_tenants=visible, tenant_id="tenant-a", page_size=50, page_token=token)
            n, rows, token = n + 1, rows + len(page["items"]), page["next_page_token"]
        out.append((n, rows, len(json.dumps(page))))

    tracemalloc.start()
    tracemalloc.reset_peak()
    base = tracemalloc.get_traced_memory()[0]
    single: list = []
    walk_twenty(single)
    seq_peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.reset_peak()
    base = tracemalloc.get_traced_memory()[0]
    results: list = []
    threads = [threading.Thread(target=walk_twenty, args=(results,)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    conc_peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.reset_peak()
    base = tracemalloc.get_traced_memory()[0]
    store.list_all_entries()  # today's unpaged copy, for contrast
    unpaged_peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.stop()
    assert single[0][:2] == (20, 1000) and all(r[:2] == (20, 1000) for r in results)
    assert seq_peak < 8 * 1024 * 1024, seq_peak
    assert conc_peak < 16 * 1024 * 1024, conc_peak
    _record("in_memory_owner", {
        "rows": 9940, "twenty_pages_peak_bytes": seq_peak, "three_readers_peak_bytes": conc_peak,
        "unpaged_list_all_entries_peak_bytes": unpaged_peak, "last_page_response_bytes": single[0][2],
    })


# -- Postgres owner ----------------------------------------------------------


def test_postgres_owner_filters_and_limits_in_sql_and_projects_five_fields():
    captured = []

    class _Cursor:
        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql, args=()):
            captured.append((sql, args))
            return _Cursor()

    class _Entries:
        table = "registry_entries"

        @contextlib.contextmanager
        def _use_conn(self, conn):
            yield _Conn()

    pg = PostgresRegistryStore.__new__(PostgresRegistryStore)
    pg._entries = _Entries()
    pg.list_discovery_candidates(visible_tenants=frozenset({"tenant-a"}), limit=51, after_registry_id="reg-9")
    sql, args = captured[0]
    assert "NOT IN ('retired', 'rejected')" in sql and "LIMIT 51" in sql
    assert "ORDER BY (payload ->> 'registry_id') COLLATE \"C\" ASC" in sql
    assert "payload || '{}'::jsonb" in sql and sql.index("LIMIT 51") < sql.rindex("ORDER BY")
    assert "payload ->> 'artifact_type' = %s" in sql and "strategy_spec" in args
    assert sql.startswith("SELECT jsonb_build_object(") and "(payload)::text" not in sql
    for key in ("storage_ref", "approval_evidence", "owner_tenant", "evaluation_summary"):
        assert f"'{key}'" not in sql.split(" FROM ")[0]
    assert ["tenant-a"] in args and "reg-9" in args


@pytest.fixture(scope="module")
def hosted_pg():
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for the real Postgres proof")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    schema = f"registry_disc_{uuid4().hex[:12]}"
    store = PostgresRegistryStore(dsn=dsn, entries_table=f"{schema}.entries",
                                  receipts_table=f"{schema}.command_receipts")
    rows, sizes = [], []
    for n, entry in enumerate(_hosted_entries()):
        doc = json.dumps(entry.to_dict(), sort_keys=True)
        sizes.append(len(doc))
        # A few legacy JSON-string payloads, as the owner store still reads them.
        rows.append((entry.registry_id, json.dumps(doc) if n % 1000 == 3 else doc))
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    f'INSERT INTO "{schema}"."entries" (record_id, payload) VALUES (%s, %s::jsonb)', rows)
            conn.execute(f'ANALYZE "{schema}"."entries"')
        yield store, {"rows": len(rows), "avg_row_bytes": sum(sizes) // len(sizes),
                      "total_bytes": sum(sizes), "legacy_string_rows": sum(1 for i in range(len(rows)) if i % 1000 == 3)}
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _rss_hwm_reset() -> None:
    with contextlib.suppress(OSError):
        with open("/proc/self/clear_refs", "w") as fh:
            fh.write("5")


def _rss_kib(field: str) -> int:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith(field + ":"):
                return int(line.split()[1])
    return -1


def test_postgres_hosted_shape_equality_with_unpaged_list(hosted_pg, strict_client, monkeypatch):  # noqa: F811
    store, shape = hosted_pg
    from . import service as service_module

    monkeypatch.setattr(service_module, "get_store", lambda: store)
    token = _jwt(subject="disc", tenant="tenant-a")
    seen, pages = _walk(lambda t: strict_client.get(
        VIEW + "&tenant_id=tenant-a&page_size=50" + (f"&page_token={t}" if t else ""),
        headers=_bearer(token)).json())
    unpaged = strict_client.get(
        "/api/registry/strategy-specs", headers={**_bearer(token), "X-Tenant-Id": "tenant-a"}).json()
    entries = [r["entry"] for r in seen]
    expected = _expected_from_unpaged(unpaged)
    assert entries == expected
    assert all(set(e) == FIELDS for e in entries)
    legacy = {rid for n, rid in enumerate(e.registry_id for e in _hosted_entries()) if n % 1000 == 3}
    assert legacy & {e["registry_id"] for e in entries}
    _record("postgres_equality", {
        **shape, "pages": pages, "discovery_rows": len(entries), "unpaged_rows": len(unpaged),
        "states": sorted({e["artifact_state"] for e in entries}),
        "missing_metadata_rows": sum(1 for e in entries if e["metadata"] is None),
    })


def test_postgres_hosted_shape_peak_memory_and_rows_per_page(hosted_pg, monkeypatch):
    store, shape = hosted_pg
    service = RegistryService(store=store)
    visible = frozenset({"tenant-a", BUILTIN_TENANT})
    decoded: list[str] = []
    real = pg_store_module.discovery_candidate_from_payload

    def spy(payload):
        decoded.append(payload["registry_id"])
        return real(payload)

    monkeypatch.setattr(pg_store_module, "discovery_candidate_from_payload", spy)
    per_call: list[int] = []
    real_rows = PostgresRegistryStore._strategy_spec_keyset_rows

    def counting(self, **kw):
        rows = real_rows(self, **kw)
        per_call.append(len(rows))
        return rows

    monkeypatch.setattr(PostgresRegistryStore, "_strategy_spec_keyset_rows", counting)

    def walk_twenty(out):
        token, rows, size = None, 0, 0
        for _ in range(20):
            page = service.discovery_candidate_page(
                visible_tenants=visible, tenant_id="tenant-a", page_size=50, page_token=token)
            rows, token = rows + len(page["items"]), page["next_page_token"]
            size = max(size, len(json.dumps(page)))
        out.append((rows, size))

    import time

    rss_base = _rss_kib("VmRSS")
    _rss_hwm_reset()
    tracemalloc.start()
    tracemalloc.reset_peak()
    base = tracemalloc.get_traced_memory()[0]
    started = time.perf_counter()
    single: list = []
    walk_twenty(single)
    seq_seconds = time.perf_counter() - started
    seq_peak = tracemalloc.get_traced_memory()[1] - base
    seq_hwm = _rss_kib("VmHWM")
    tracemalloc.reset_peak()
    base = tracemalloc.get_traced_memory()[0]
    _rss_hwm_reset()
    results: list = []
    threads = [threading.Thread(target=walk_twenty, args=(results,)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    conc_peak = tracemalloc.get_traced_memory()[1] - base
    conc_hwm = _rss_kib("VmHWM")
    tracemalloc.reset_peak()
    base = tracemalloc.get_traced_memory()[0]
    _rss_hwm_reset()
    store.list_all_entries()  # today's unpaged read, for contrast
    unpaged_peak = tracemalloc.get_traced_memory()[1] - base
    unpaged_hwm = _rss_kib("VmHWM")
    tracemalloc.stop()

    assert single[0][0] == 1000 and all(r[0] == 1000 for r in results)
    assert max(per_call) <= 51 and len(per_call) == 80
    assert len(decoded) == sum(per_call)
    discovery_ids = {e.registry_id for e in _hosted_entries()
                     if e.artifact_type == ArtifactType.STRATEGY_SPEC and e.owner_tenant in visible
                     and e.artifact_state != ArtifactState.RETIRED}
    assert set(decoded) <= discovery_ids
    assert seq_peak < 16 * 1024 * 1024, seq_peak
    assert conc_peak < 32 * 1024 * 1024, conc_peak
    _record("postgres_owner", {
        **shape,
        "page_size": 50, "max_rows_fetched_per_call": max(per_call), "calls": len(per_call),
        "max_response_bytes_per_page": max(r[1] for r in results + single),
        "twenty_pages_seconds": round(seq_seconds, 3),
        "twenty_pages_python_peak_bytes": seq_peak,
        "three_readers_python_peak_bytes": conc_peak,
        "unpaged_list_all_entries_python_peak_bytes": unpaged_peak,
        "process_rss_kib_before": rss_base,
        "process_rss_hwm_kib_twenty_pages": seq_hwm,
        "process_rss_hwm_kib_three_readers": conc_hwm,
        "process_rss_hwm_kib_unpaged_contrast": unpaged_hwm,
    })


def test_postgres_card_admission_keyset_unchanged_by_shared_helper(hosted_pg):
    """view=card_admission_drafts keeps its contract on the shared keyset helper (real Postgres)."""
    store, _shape = hosted_pg
    for i in range(5):
        e = _entry(f"reg-card-pg-{i}", tenant="tenant-card", bulk=False)
        e.metadata["strategy_spec"]["metadata"] = {"card_wiring": {"status": "gate_ready"}}
        store.put(e)
    store.put(_entry("reg-card-pg-blocked", tenant="tenant-card", bulk=False))
    service = RegistryService(store=store)
    seen, token = [], None
    for _ in range(5):
        page = service.card_admission_draft_page(
            visible_tenants=frozenset({"tenant-card"}), tenant_id="tenant-card", page_size=2,
            after_registry_id=token)
        assert set(page) == {"items", "next_page_token"}
        seen += [i["entry"]["registry_id"] for i in page["items"]]
        token = page["next_page_token"]
        if token is None:
            break
    assert seen == [f"reg-card-pg-{i}" for i in range(5)]
