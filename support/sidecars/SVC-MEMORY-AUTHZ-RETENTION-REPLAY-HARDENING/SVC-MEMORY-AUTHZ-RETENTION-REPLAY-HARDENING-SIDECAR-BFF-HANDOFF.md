# SVC-MEMORY-AUTHZ-RETENTION-REPLAY-HARDENING Sidecar BFF Handoff

Task: `SVC-MEMORY-AUTHZ-RETENTION-REPLAY-HARDENING-SIDECAR-BFF-HANDOFF`  
Parent: `SVC-MEMORY-AUTHZ-RETENTION-REPLAY-HARDENING`  
Owner: `Codex2`  
Reviewer: `Codex`
Status: review approved; closeout finalization ready
Last updated: 2026-04-30

## Scope Boundary

This is a support packet for the parent memory-hardening task. It does not
change canonical truth, BFF runtime code, memory service code, governance AuthZ
policy, or frontend code.

The packet gives the parent owner and downstream BFF/frontend implementers a
concrete handoff view of:

- current memory/BFF route posture,
- BFF query and projection gaps exposed by the hardening slice,
- operator journey expectations,
- frontend consumption rules and stop conditions.

## Source Context Read

Task-scoped context:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/svc_memory_authz_retention_replay_hardening_sidecar_bff_handoff.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`

Implementation and handoff references:

- `docs/services/memory/README.md`
- `services/memory/main.py`
- `services/memory/MEMORY_LAYER_DESIGN_NOTE.md`
- `services/governance/authz.py`
- `services/governance/main.py`
- `docs/bff/KW-01-institutional-memory.md`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_kw01_institutional_memory_contract.py`
- `services/control-plane/bff/test_read_store_service_clients.py`
- `docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md`
- `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`

## Current Posture

### Memory Service

The memory service exposes two read classes:

| Route | Audience | Current role |
|---|---|---|
| `GET /api/memory/entries` | service/BFF integration | lists institutional entries, with filters and `active_only` |
| `GET /api/memory/entries/{entry_id}` | service/BFF integration | returns one institutional entry |
| `GET /api/memory/retrieve` | session-facing retrieval facade | performs governed retrieval, ranks hits, and increments `reuse_count` |

The hardened retrieval facade now checks governance AuthZ before store access.
When governance is not configured, retrieval fails closed with
`governance_authz_unconfigured`. Authorized institutional retrieval increments
`reuse_count`.

Retention posture:

- new entries receive `expires_at` from `PANTHEON_MEMORY_RETENTION_DAYS` unless
  the writer explicitly supplies an expiration or disables automatic expiry;
- expired entries are archived with `archived_at` and `archived_reason`;
- archived and superseded entries remain persisted for lineage/replay;
- active list and retrieval calls exclude archived/superseded entries by
  default.

Replay posture:

- focused memory replay evidence covers write, store reload, governed retrieval,
  `reuse_count` persistence, and expired archive exclusion from active
  retrieval while remaining available through `active_only=false`.

### Governance AuthZ

The governance AuthZ route is:

- `POST /api/governance/authz/check`

The current narrow policy supports action `memory.retrieve` and denies all
unsupported actions. It requires:

- non-empty `actor_id`;
- non-empty `actor_roles`;
- `context.session_id`;
- valid memory scope: `institutional`, `persona`, or `both`;
- role eligibility for institutional or persona-scoped reads;
- persona session scope match for persona reads;
- consultation reads only when `resource.relevance_scope` is
  `persona_and_committee`.

### BFF Knowledge Workbench Surface

The BFF operator-facing routes are:

| Route | Audience | Current role |
|---|---|---|
| `GET /api/v1/knowledge/memory` | frontend/operator | paginated institutional-memory browse projection |
| `GET /api/v1/knowledge/memory/{entry_id}` | frontend/operator | institutional-memory detail projection |

These routes require BFF read role and project memory entries through
`ReadSurfaceStore`. Service-backed reads override local snapshot fallback when
`PANTHEON_MEMORY_API_URL` is configured. Existing focused tests verify:

- published KW-01 list/detail shape;
- service-backed reads override seeded snapshot data;
- memory service data-dir fallback works;
- unavailable service truth does not silently fall back to seeded local snapshot;
- degraded/unavailable surface states come from BFF surface state.

## BFF Query Gaps

These gaps are support observations for parent/mainline decision-making. They
are not canonical changes.

| Gap | Current behavior | Why it matters | Suggested owner |
|---|---|---|---|
| Browse routes do not expose archived-history mode | `GET /api/v1/knowledge/memory` calls `ReadSurfaceStore.list_institutional_memory_entries()`, which uses the service list path without an `active_only=false` operator switch. | Retention hardening keeps archived entries durable for lineage/replay, but the operator browse surface has no explicit archive/history lane. | Parent owner decides whether a follow-up BFF query parameter is needed. |
| BFF service client list path does not document propagated service filters beyond the KW-01 contract | KW-01 publishes `knowledge_type`, `scope`, `scope_filter`, `tags`, `page`, `page_size`; memory service supports `contributing_persona_id` and `active_only`, but these are not exposed in KW-01. | Operators may need persona/session lineage filtering during retention or replay investigations. The current UI should not invent these filters. | Parent owner and Knowledge Workbench owner. |
| Retrieval AuthZ denial is not an operator browse contract | `/api/memory/retrieve` returns fail-closed authorization errors, while KW-01 routes are BFF read-role guarded browse projections. | Frontend should not treat retrieval-facade denials as KW-01 degradation. If an operator needs to debug retrieval AuthZ, that should be a distinct admin/support surface or evidence note. | Parent owner. |
| `reuse_count` has two different meanings by surface | Retrieval increments `reuse_count`; KW-01 browse displays the current projected value without causing reuse. | Frontend must not increment or infer reuse locally. Operator copy should avoid implying that simply viewing the browse/detail screen counts as retrieval reuse. | Frontend implementer. |
| Archived/superseded lineage visibility is partial | KW-01 detail can show `lifecycle.status` and `superseded_by` for an entry it can fetch, but active list excludes non-active entries from the memory service by default. | A superseded/archived replacement chain may be hard to navigate unless the BFF can fetch historical entries by id or list archive mode. | Parent owner. |
| Persona-memory merge remains future-facing | Retrieval facade reserves `scope=persona|institutional|both`, but current hardening evidence is institutional-memory focused. | UI must not render persona-memory results or mixed retrieval history unless the BFF publishes a specific contract. | Parent owner. |

## Operator Journey

### Normal Browse

1. Operator opens `/knowledge/memory`.
2. Frontend calls `GET /api/v1/knowledge/memory` with only published KW-01
   filters.
3. BFF returns paginated entries plus `meta.surfaces.memory_list`.
4. Frontend renders entries in BFF order and does not sort locally.
5. Operator opens a row using `route_href`.
6. Frontend calls `GET /api/v1/knowledge/memory/{entry_id}`.
7. Detail renders source event, lifecycle, scope, usage, and surface states from
   the BFF response.

### Retention / Archive Investigation

Current route support is read-only and active-first:

1. Operator can inspect active entries and detail lifecycle status.
2. Operator can follow `lifecycle.superseded_by` when the replacement entry is
   available through the BFF detail route.
3. Operator cannot currently browse all archived entries from the KW-01 list
   route.
4. If archive/history browsing is needed, the parent should decide whether to
   add an explicit BFF query such as `active_only=false` or a separate support
   route. The frontend should not synthesize archived lists from local cache.

### Retrieval AuthZ Failure Investigation

The retrieval facade is session-facing. A denied retrieval means the memory
service rejected a `memory.retrieve` authorization check, not that the KW-01
browse route is unavailable.

Expected support flow:

1. Inspect the calling service/session context: `actor_id`, `actor_roles`,
   `session_id`, `scope`, `persona_id`, and `session_persona_id`.
2. Confirm governance AuthZ endpoint configuration:
   `PANTHEON_GOVERNANCE_AUTHZ_URL`, `PANTHEON_GOVERNANCE_API_URL`, or
   `PANTHEON_GOVERNANCE_SERVICE_URL`.
3. Treat `governance_authz_unconfigured` as fail-closed production
   misconfiguration.
4. Treat `governance_authz_unavailable` as downstream service unavailability.
5. Do not route the operator frontend directly to `/api/memory/retrieve` unless
   a separate BFF support contract is published.

## Frontend Handoff Rules

Build only against the BFF KW-01 routes:

- `GET /api/v1/knowledge/memory`
- `GET /api/v1/knowledge/memory/{entry_id}`

Do:

- render list/detail fields exactly from the BFF shape in
  `docs/bff/KW-01-institutional-memory.md`;
- preserve BFF ordering;
- use `meta.surfaces.memory_list`, `meta.surfaces.entry_detail`, and
  `meta.surfaces.source_context` for degradation state;
- show `lifecycle.status`, `lifecycle.superseded_by`, and `usage.reuse_count`
  as backend-owned values;
- use `route_href` for navigation.

Do not:

- call `/api/memory/retrieve` from the browser for KW-01 browse/detail;
- locally rank memory entries;
- invent archive/history filters not published by the BFF;
- infer retrieval AuthZ state from KW-01 browse degradation;
- mutate `reuse_count` or lifecycle state client-side;
- substitute seeded/demo memory if the BFF marks the surface unavailable.

Stop and file a BFF gap if:

- KW-01 payload shape diverges from the published contract;
- retention/archive investigation requires a list of archived entries;
- a frontend workflow needs `contributing_persona_id`, `active_only=false`, or
  persona-memory mixed retrieval;
- detail navigation to a superseded or archived entry returns 404 but the
  operator journey requires lineage replay;
- support users need first-class visibility into `memory.retrieve` AuthZ denial
  reasons.

## Parent Absorption Checklist

Parent/mainline owner can absorb this packet by deciding:

- whether KW-01 remains active-only for this wave;
- whether an archive/history browse query is needed now or should be deferred;
- whether retrieval AuthZ denial evidence belongs in a support/admin BFF route;
- whether additional BFF tests should assert active-only retention behavior
  against memory service data containing archived entries;
- whether the frontend handoff should be republished after any new BFF query
  contract is accepted.

## Suggested Focused Verification

Already relevant test targets for the parent or reviewer:

```bash
python3 services/memory/smoke_test_institutional_memory.py
python3 -m pytest services/memory/test_main.py services/governance/test_governance_api.py -q
python3 -m pytest services/control-plane/bff/test_kw01_institutional_memory_contract.py services/control-plane/bff/test_read_store_service_clients.py -q
```

This sidecar packet itself only adds support documentation. No runtime tests
were required to generate it.

## Closeout Note

Reviewer approval was recorded by Codex on 2026-04-30 with no blocking
findings. Finalization verification for the support-only packet:

```bash
test -f support/sidecars/SVC-MEMORY-AUTHZ-RETENTION-REPLAY-HARDENING/SVC-MEMORY-AUTHZ-RETENTION-REPLAY-HARDENING-SIDECAR-BFF-HANDOFF.md
git diff --check -- support/sidecars/SVC-MEMORY-AUTHZ-RETENTION-REPLAY-HARDENING/SVC-MEMORY-AUTHZ-RETENTION-REPLAY-HARDENING-SIDECAR-BFF-HANDOFF.md
```
