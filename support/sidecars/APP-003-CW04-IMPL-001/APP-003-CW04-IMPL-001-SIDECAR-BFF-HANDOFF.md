# APP-003-CW04-IMPL-001 BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`
**Parent task:** `APP-003-CW04-IMPL-001` - implement CW-04 Red-team Memo route family
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Sidecar owner:** `Codex`
**Sidecar reviewer:** `Claude2`
**Date:** `2026-04-22`
**Last refresh:** `2026-04-22T15:00Z` — refreshed against live parent-lane state
**Mutates canonical:** `no`

> Support artifact only. This packet does not change canonical truth, runtime
> behavior, or live task-board semantics. It consolidates the current CW-04
> implementation status, the frontend truth boundary, and the minimum
> parent-lane absorption checklist for a truthful handoff.
>
> This refresh reconciles the original sidecar (authored before the parent
> lane shipped the CW-04 memo route family) with current repo truth. Three of
> the four original gaps are now resolved by the parent lane; the remaining
> gap (module-local frontend handoff bundle) and one screen-spec wording
> drift are the only follow-ups still owed.

## 1. Executive Summary

`APP-003-CW04-IMPL-001` is **route-live** in the repo. The parent lane has
shipped the ratified memo list/detail route family, the `ConsultMemo`
projection with mapping and governance-authority signals, and the CW-04
contract proof. What remains is frontend-side activation, not backend
implementation.

Current repo truth:

- the ratified CW-04 contract lives at `docs/bff/CW-04-redteam-memo.md`
- the example payload lives at `docs/examples/CW-04-redteam-memo.json`
- the screen spec lives at `docs/screens/CW-04-redteam-memo.md` but still
  contains one wording drift that conflicts with the ratified contract
  (see DRIFT-CW04-HANDOFF-005 below)
- `GET /api/v1/consult/memos` and `GET /api/v1/consult/memos/{memo_id}` are
  **live** in `services/control-plane/bff/main.py`
- the backend-owned `ConsultMemo` summary/detail projection, the
  `list_consult_memos` / `get_consult_memo` helpers, and the
  `consult_memos` read-surface dataset live in
  `services/control-plane/bff/read_store.py`
- CW-04-specific contract proof lives at
  `services/control-plane/bff/test_cw04_redteam_memo_contract.py`
- `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`
  now states "CW-04 Red-team Memo routes are now live" and lists the
  memo list/detail routes as `live`
- `docs/lovable/PANTHEON_FRONTEND_SA.md` still marks the memo list/detail
  screens as blocked shell-only — still correct because the module-local
  frontend handoff bundle has not been published yet
- no module-specific frontend handoff bundle exists under
  `docs/pantheon-handoffs/CW-04-redteam-memo/`

Practical conclusion:

- backend/BFF parent work is effectively absorbed; the remaining parent
  follow-up is publishing the CW-04 module-local frontend handoff bundle
  and correcting the screen-spec wording drift
- frontend must follow the ratified contract and wait for the module-local
  handoff bundle before starting production UI; it must not start from the
  screen spec alone because the drift remains unresolved

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes |
|---|---|---|
| Canonical BFF contract | `docs/bff/CW-04-redteam-memo.md` | Ratified. Canonical detail fields include `session_to_memo_mapping`, `evidence_refs[]`, `allowedActions.canInitiateGovernanceReview`, `meta.staleness`, and `meta.surfaces.redteam_memo.state`. |
| Example payload | `docs/examples/CW-04-redteam-memo.json` | Matches the ratified list/detail/degraded branches. |
| Screen spec | `docs/screens/CW-04-redteam-memo.md` | Published; retains one wording drift (`meta.surfaces.redteam_memo = "stale"`) that still conflicts with the ratified contract. |
| Family-level readiness gate | `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` | Now states CW-04 routes are **live** and remaining work is frontend handoff publication plus UI activation. |
| Lovable summary | `docs/lovable/PANTHEON_FRONTEND_SA.md` | Still keeps `/consultation/memos` and `/consultation/memos/:memo_id` blocked shell-only — correct until the module-local handoff bundle lands. |
| HTTP route exposure | `services/control-plane/bff/main.py` | `GET /api/v1/consult/memos` at line 7211; `GET /api/v1/consult/memos/{memo_id}` at line 7253 — **live**. |
| Read-store projection | `services/control-plane/bff/read_store.py` | `_project_consult_memo_summary`, `_project_consult_memo_detail`, `list_consult_memos`, `get_consult_memo`, and the `consult_memos` dataset are present — **live**. |
| Executable proof | `services/control-plane/bff/test_cw04_redteam_memo_contract.py` | CW-04-specific contract test is present (≈259 lines) — **live**. |
| Module-specific frontend handoff | missing | No `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md` exists yet — **still pending**. |

## 3. Gap Classification For This Handoff

### GAP-CW04-HANDOFF-001 — Read model / projection — **RESOLVED**

Backend-owned `ConsultMemo` projection now lives in
`services/control-plane/bff/read_store.py` as
`_project_consult_memo_summary` / `_project_consult_memo_detail`, and the
`consult_memos` dataset is wired through `list_consult_memos` /
`get_consult_memo`.

Residual risk for frontend:

- frontend must still **not** synthesize memo lifecycle, mapping, or
  governance authority from raw consultation session/transcript reads —
  read model truth is the backend projection only.

### GAP-CW04-HANDOFF-002 — Public route family — **RESOLVED**

`services/control-plane/bff/main.py` now exposes:

- `GET /api/v1/consult/memos` (line 7211)
- `GET /api/v1/consult/memos/{memo_id}` (line 7253)

Both routes conform to the ratified contract shape, including
`meta.staleness` and `meta.surfaces.redteam_memo.state`. The family
readiness gate in `PACKET_FAMILY.md` correctly treats the routes as
`live`.

### GAP-CW04-HANDOFF-003 — End-to-end BFF proof — **RESOLVED**

`services/control-plane/bff/test_cw04_redteam_memo_contract.py` now
provides CW-04-specific contract proof for memo list/detail payload
shape, surface state transitions, and governance-handoff authority
gating.

### GAP-CW04-HANDOFF-004 — Module-specific frontend handoff bundle — **STILL OPEN**

There is still no module-specific CW-04 handoff folder or frontend change
spec under `docs/pantheon-handoffs/CW-04-redteam-memo/`.

Impact:

- even though the route family is live, frontend activation still needs a
  canonical module-local handoff packet before Lovable or any front lane
  should implement production UI
- the `PANTHEON_FRONTEND_SA.md` blocked shell-only wording should stay as
  it is until this bundle lands

### DRIFT-CW04-HANDOFF-005 — Screen spec still treats `stale` as a surface state — **STILL OPEN**

The ratified contract says:

- freshness belongs in `meta.staleness`
- `meta.surfaces.redteam_memo.state` is `ok | degraded | unavailable`

But `docs/screens/CW-04-redteam-memo.md` still contains lines that treat
`meta.surfaces.redteam_memo = "stale"` as a valid surface state (see the
Degradation section, entries beginning `When meta.surfaces.redteam_memo =
"stale"`).

Impact:

- this is the highest-risk frontend wording drift in the current CW-04
  bundle
- the future frontend handoff packet should normalize readers to
  `meta.staleness.status` for freshness and keep
  `meta.surfaces.redteam_memo.state` for surface health only

## 4. Frontend Truth Boundary

For any future CW-04 frontend handoff, these are the safe contract rules.

| Topic | Frontend must use | Frontend must not use |
|---|---|---|
| List route | `GET /api/v1/consult/memos` | raw consultation session lists as a memo substitute |
| Detail route | `GET /api/v1/consult/memos/{memo_id}` | transcript or committee detail as a memo-detail substitute |
| Mapping source | `session_to_memo_mapping` | client-derived session/transcript joins |
| Governance CTA | `allowedActions.canInitiateGovernanceReview` | `status = "published"` alone |
| Evidence links | BFF-provided `evidence_refs[].link` | client-built evidence URLs from ids or artifact refs |
| Surface health | `meta.surfaces.redteam_memo.state` (`ok | degraded | unavailable`) | `recommendations[]` presence as proof the memo surface is healthy |
| Freshness | `meta.staleness.status` and `meta.staleness.as_of` | `meta.surfaces.redteam_memo = "stale"` |
| Readiness gate | family packet + future module-level handoff bundle | screen spec alone as production-ready approval |

The safest frontend source order is:

1. `docs/bff/CW-04-redteam-memo.md`
2. `docs/examples/CW-04-redteam-memo.json`
3. future CW-04 `FRONTEND_CHANGE_SPEC.md`
4. family-level summaries and screen spec only as navigation aids

## 5. Truthful Operator Journey

This is the operator path the parent lane has already wired through the
live route family.

1. Enter the memo list from `/consultation/memos`.
2. Call `GET /api/v1/consult/memos` with optional `status` filter.
3. Render each row from backend-owned memo summary fields such as `memo_id`,
   `status`, `linked_request_id`, `author_ref`, `recommendation_count`, and
   `published_at`.
4. Open one memo detail via `/consultation/memos/{memo_id}` and call
   `GET /api/v1/consult/memos/{memo_id}`.
5. Render summary, recommendations, evidence drawer, and
   `session_to_memo_mapping` from the backend-owned detail payload.
6. Show the governance handoff CTA only when
   `allowedActions.canInitiateGovernanceReview = true`.
7. If `meta.surfaces.redteam_memo.state = "degraded"`, show the last-known
   memo with a degradation banner and hide the governance CTA.
8. If `meta.surfaces.redteam_memo.state = "unavailable"`, show the canonical
   unavailable state with no memo content.

## 6. Parent Absorption Checklist

Because the route family, projection, and executable proof are already in
the repo, the remaining parent-lane absorption work is narrowed to the
frontend handoff bundle plus truth-surface sync.

### 6.1 Remaining parent work

| File or area | Parent-lane action |
|---|---|
| `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md` | Publish a canonical frontend change spec aligned to the live routes; this is the gate that the Lovable summary and screen spec still reference implicitly. |
| `docs/screens/CW-04-redteam-memo.md` | Remove the `meta.surfaces.redteam_memo = "stale"` wording so the screen spec matches the ratified `ok | degraded | unavailable` surface grammar and directs freshness reads to `meta.staleness.status`. |

### 6.2 Already absorbed by the parent lane

| File | Status |
|---|---|
| `services/control-plane/bff/read_store.py` | `ConsultMemo` list/detail projection, `session_to_memo_mapping`, evidence link objects, degradation semantics, and governance-handoff authority signal are wired. |
| `services/control-plane/bff/main.py` | `GET /api/v1/consult/memos` and `GET /api/v1/consult/memos/{memo_id}` mounted on the ratified contract shape. |
| `services/control-plane/bff/test_cw04_redteam_memo_contract.py` | Executable contract proof for list/detail payloads, surface state transitions, and `canInitiateGovernanceReview` gating. |

### 6.3 Recommended frontend packet contents once the module-local bundle lands

| File to create | Purpose |
|---|---|
| `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md` | Production frontend contract for memo list/detail, evidence drawer, mapping panel, and governance handoff CTA. |
| `.coordination/requests/CW-04-redteam-memo-bff-gap.example.yaml` | Stop-work template if required fields diverge from the live route. |
| `.coordination/requests/CW-04-redteam-memo-ui-done.example.yaml` | Frontend completion handoff template. |
| `.coordination/responses/CW-04-redteam-memo-contract-ready.yaml` | Canonical contract-ready coordination packet after route verification. |
| `.coordination/responses/CW-04-redteam-memo-lovable-ui-task.yaml` | Front-lane dispatch packet once the live route and frontend spec are both settled. |

### 6.4 Truth sync to perform with the frontend bundle publication

| Truth surface | What to sync |
|---|---|
| `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` | Promote CW-04 from `frontend handoff pending` to `frontend-ready` once the module-local bundle exists and screen-spec drift is cleared. |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | Replace blocked shell-only wording once the module-local frontend packet exists. |
| `docs/screens/CW-04-redteam-memo.md` | Remove the stale-vs-surface-state wording drift so the screen spec no longer conflicts with the ratified contract. |

## 7. Reviewer Focus

For `Claude2` reviewing this sidecar:

1. Confirm the packet stays support-only and does not mutate canonical
   truth.
2. Confirm the refreshed status claims (routes, projection, and contract
   test are live; module-local handoff bundle and screen-spec wording
   drift remain open) match the files listed in section 2.
3. Confirm the packet does not overclaim CW-04 as frontend-ready: the
   module-local handoff bundle and the screen-spec wording drift are
   explicitly called out as still pending.
4. Confirm the frontend truth boundary still preserves the ratified memo
   contract, especially the `meta.staleness` vs
   `meta.surfaces.redteam_memo.state` separation.

## 8. References

- `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md`
- `docs/bff/CW-04-redteam-memo.md`
- `docs/examples/CW-04-redteam-memo.json`
- `docs/screens/CW-04-redteam-memo.md`
- `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`
- `docs/lovable/PANTHEON_FRONTEND_SA.md`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_cw04_redteam_memo_contract.py`
- `.orchestrator/task-briefs/app_003_cw04_impl_001.md`
