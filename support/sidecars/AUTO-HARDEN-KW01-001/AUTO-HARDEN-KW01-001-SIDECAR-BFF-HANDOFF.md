# AUTO-HARDEN-KW01-001 BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`  
**Parent task:** `AUTO-HARDEN-KW01-001` - Wire KW-01 institutional memory to service owned truth  
**Parent owner:** `Codex2`  
**Parent reviewer:** `Codex`  
**Parent status:** `done` (archived 2026-04-20)  
**Sidecar task:** `AUTO-HARDEN-KW01-001-SIDECAR-BFF-HANDOFF`  
**Prepared by:** `Codex`  
**Reviewer:** `Claude`  
**Date:** `2026-04-20`  
**Mutates canonical:** `no`

> Support artifact only. This packet does not change canonical truth, L1 policy, or core runtime implementation. It packages the post-hardening KW-01 reality into a reviewer-ready BFF and frontend handoff.

---

## 1. Purpose

This sidecar exists because `KW-01-FOUNDATION-001` already published the
frontend-facing contract and screen specs, but `AUTO-HARDEN-KW01-001` changed
the backend truth source behind those same routes.

The important change is not new route shape. The important change is that the
KW-01 BFF now prefers service-owned institutional memory records and only falls
back to seeded local snapshot data when the service-backed dataset is not
available.

Use this packet when reviewing or briefing downstream frontend work so that
"KW-01 is live" is understood as "live against service-owned truth when that
truth is present", not merely "served from an example-backed browse shell".

---

## 2. Current Slice State

| Item | Value |
|---|---|
| Feature / module | `KW-01 Institutional Memory` |
| Frontend routes | `/knowledge/memory`, `/knowledge/memory/:entry_id` |
| BFF routes | `GET /api/v1/knowledge/memory`, `GET /api/v1/knowledge/memory/{entry_id}` |
| Overview route | `GET /api/v1/workbench/knowledge` |
| Canonical contract | `docs/bff/KW-01-institutional-memory.md` |
| Frontend handoff | `docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md` |
| Hardening delta | BFF read path now resolves service-owned memory store before local fallback |
| Primary proof | `services/control-plane/bff/test_kw01_institutional_memory_contract.py` |

Current conclusion:

- the BFF query gap for KW-01 is closed
- the route family is unchanged from the published contract
- the hardening work was backend truth-source alignment, not frontend contract redesign

---

## 3. Source References

| Source | Why it matters |
|---|---|
| `ai-task-archive/tasks/AUTO-HARDEN-KW01-001.json` | Parent completion record and reviewed acceptance summary |
| `services/control-plane/bff/main.py:6404-6521` | Live list/detail route behavior and surface-state wiring |
| `services/control-plane/bff/read_store.py:364-369` | Institutional memory dataset discovery config |
| `services/control-plane/bff/read_store.py:3971-4025` | Summary/detail projection plus service-first lookup |
| `services/control-plane/bff/test_kw01_institutional_memory_contract.py:172-267` | Executable proof for contract shape, service override, and memory data-dir discovery |
| `services/memory/main.py:19-29` | Canonical memory-service store path resolution |
| `docs/bff/KW-01-institutional-memory.md` | Published route shape remains authoritative |
| `docs/screens/KW-01-institutional-memory.md` | Current frontend rendering constraints |
| `docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md` | Existing frontend delivery packet that should still be used |
| `support/sidecars/KW-01-FOUNDATION-001/KW-01-FOUNDATION-001-SIDECAR-BFF-HANDOFF.md` | Prior foundation-stage handoff baseline |

---

## 4. Landed BFF Behavior

### 4.1 Live endpoints

The KW-01 route family is unchanged and live:

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/v1/workbench/knowledge` | Knowledge overview shell; still the entrypoint for the workbench |
| `GET` | `/api/v1/knowledge/memory` | Paginated institutional memory list with backend-applied filtering |
| `GET` | `/api/v1/knowledge/memory/{entry_id}` | Full institutional memory detail or `404 entry_not_found` |

All three routes require a read-capable bearer identity through
`_require_read_role(...)`.

### 4.2 Query semantics that frontend should assume

`GET /api/v1/knowledge/memory` accepts the already-published filters:

- `knowledge_type`
- `scope`
- `scope_filter`
- `tags`
- `page`
- `page_size`

Important behavior details from `main.py`:

- filtering is server-side; the UI must not re-filter the returned page locally
- tag filtering is set intersection against the backend-provided `tags[]`
- pagination metadata is backend-owned
- when the memory list surface becomes `unavailable`, the BFF returns an empty
  list with zeroed counts instead of pretending data is healthy

`GET /api/v1/knowledge/memory/{entry_id}`:

- returns the projected detail object when the record exists
- returns `404` with `{ "error": "entry_not_found", "entry_id": ... }` when the
  record does not exist
- computes `meta.surfaces.entry_detail` and `meta.surfaces.source_context`
  independently

### 4.3 Projection rules that now matter more than the examples

The read-store projection remains backend-owned:

- `route_href` is always projected as `/knowledge/memory/{entry_id}`
- list rows expose `headline`, `scope`, `scope_filter`, `reuse_count`, and
  `is_superseded` from the stored record
- detail responses preserve `content`, `source_event`, `scope`, `lifecycle`,
  `usage`, and `contributing_persona_ids` as structured objects

Frontend should still consume the response as the complete projection truth,
even though the current `route_href` happens to be deterministic.

---

## 5. Truth Source Resolution

This is the actual hardening delta from the parent task.

### 5.1 Service-owned data wins

`ReadSurfaceStore` now discovers the institutional memory dataset using this
order:

1. explicit BFF store path via `PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE`
2. memory-service data directory via `PANTHEON_MEMORY_DATA_DIR` and canonical
   filenames such as `institutional_memory_entries.json`
3. local fallback snapshot embedded in the BFF read store

`get_institutional_memory_entry(...)` explicitly checks the service dataset
first and falls back to the local snapshot only when that dataset is not
available.

### 5.2 What surface states mean now

Hardening makes `meta.surfaces.*` materially useful:

| Situation | Expected surface state |
|---|---|
| service-owned store is present and readable | `ok` |
| BFF is serving local fallback snapshot | typically `degraded` |
| no usable data is available | `unavailable` |

This means the same route family can be healthy or degraded without any
frontend contract change. The UI must respect the returned surface state instead
of assuming "live route" always means `ok`.

### 5.3 Memory service alignment

`services/memory/main.py` resolves the canonical store path as:

- `PANTHEON_MEMORY_STORE` when explicitly set
- otherwise `${PANTHEON_MEMORY_DATA_DIR}/institutional_memory_entries.json`

The BFF test coverage now proves that KW-01 can discover the same data-dir based
store path, so the control-plane read surface and memory service are aligned on
where service-owned truth lives.

---

## 6. Query Gap Matrix

### 6.1 Closed gaps

These are no longer open for KW-01:

- missing list route
- missing detail route
- example-only browse path with no service-backed truth option
- ambiguity around whether BFF can discover the canonical memory-service data
  directory

### 6.2 Remaining non-goals for this slice

These remain outside this task and must stay out of frontend scope:

| Area | Status |
|---|---|
| `KW-02` to `KW-05` module read routes | still separate work, not unlocked by this hardening slice |
| write actions for institutional memory | out of scope; KW-01 remains read-only |
| new frontend components or route additions in this repo | out of scope for this sidecar |

---

## 7. Operator Journey

The truthful post-hardening operator flow is now:

```text
Operator opens /knowledge
    |
    v
GET /api/v1/workbench/knowledge
    |
    v
Operator navigates into KW-01 Institutional Memory
    |
    v
GET /api/v1/knowledge/memory
    |
    +-- meta.surfaces.memory_list = ok
    |      Render normal list view backed by service-owned truth
    |
    +-- meta.surfaces.memory_list = degraded
    |      Render list plus non-dismissable degraded banner
    |      Data may be local fallback; do not describe it as authoritative fresh truth
    |
    +-- meta.surfaces.memory_list = unavailable
           Render unavailable state; do not show empty-library messaging as if it were authoritative
    |
    v
Operator opens an entry using route_href
    |
    v
GET /api/v1/knowledge/memory/{entry_id}
    |
    +-- 200 with entry_detail/source_context surfaces
    |      Render detail, source-event panel, lifecycle, usage
    |
    +-- 404 entry_not_found
           Render explicit not-found state; do not redirect to a fake placeholder detail
```

Practical frontend implication:

- the browse and detail journey is now valid against service-backed truth
- degraded mode still exists and must remain visible to operators
- there is still no mutation or authoring journey for KW-01

---

## 8. Frontend Handoff Delta

Use the existing frontend packet:

- `docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md`

This sidecar only adds the following post-hardening clarifications.

### 8.1 What frontend can now safely assume

- the published KW-01 list/detail routes are not just planned; they are
  implemented and tested
- service-backed truth is preferred automatically when the backing dataset is
  present
- `meta.surfaces.memory_list`, `entry_detail`, and `source_context` are the
  canonical freshness/health signal for the screen
- the module remains read-only

### 8.2 What frontend must not assume

- do not assume `meta.surfaces.* = ok` just because the route exists
- do not treat a degraded list/detail payload as equivalent to a fresh service
  read
- do not reconstruct a detail route from raw ids when `route_href` is returned
- do not hide `404 entry_not_found` behind a generic empty state
- do not extend KW-01 hardening into KW-02 to KW-05 readiness claims

### 8.3 Downstream handoff message

If this slice is briefed to the frontend lane, the correct message is:

> Build against the existing KW-01 handoff/spec bundle. The route family and
> payload shape are stable. The new part is that runtime data may now come from
> the service-owned institutional memory store, with `meta.surfaces.*`
> indicating whether the screen is healthy, degraded, or unavailable.

---

## 9. Verification Snapshot

Repo evidence checked for this handoff:

| Check | Evidence | Result |
|---|---|---|
| KW-01 list route exists | `services/control-plane/bff/main.py` | PASS |
| KW-01 detail route exists | `services/control-plane/bff/main.py` | PASS |
| Service-backed store overrides local fallback | `test_kw01_service_backed_reads_override_seeded_snapshot` | PASS |
| BFF can discover memory-service data-dir store | `test_kw01_reads_memory_service_store_via_data_dir` | PASS |
| Seeded fallback still satisfies published contract | `test_kw01_institutional_memory_*_returns_published_contract_shape` | PASS |
| Memory service import/store path is package-correct | `services/memory/main.py` + parent archive note | PASS |

Recommended verification command:

```bash
pytest services/control-plane/bff/test_kw01_institutional_memory_contract.py
```

---

## 10. Reviewer Checklist

Review against these claims:

- this packet only documents support-level BFF/frontend handoff facts
- the hardening delta is truth-source alignment, not a new KW-01 route family
- service-backed store precedence and memory data-dir discovery are both backed
  by tests in repo
- the existing KW-01 frontend spec remains the right downstream packet
- no canonical document or runtime code was modified by this sidecar
