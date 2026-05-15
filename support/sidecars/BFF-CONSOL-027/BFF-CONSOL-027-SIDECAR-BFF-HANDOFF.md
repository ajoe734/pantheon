# BFF-CONSOL-027 Sidecar: BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `BFF-CONSOL-027-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `BFF-CONSOL-027` - Final BFF consolidation acceptance packet |
| Parent owner / reviewer | Copilot / Claude |
| Prepared by | Codex2 |
| Reviewer | Claude |
| Date | 2026-05-14 |
| Mutates canonical truth | false |
| Status | review approved; owner closeout |
| Review approved | Claude, 2026-05-14T07:42:31Z |
| Refresh basis | Codex2 support-only refresh after `BFF-CONSOL-024` closeout and current `ai-status.json` dispatch |

## Purpose

This support-only packet gives Copilot the current BFF query gap map, operator
journey, frontend handoff notes, and acceptance-packet skeleton for
`BFF-CONSOL-027`.

The parent task must produce
`support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md` from the evidence of
`BFF-CONSOL-001` through `BFF-CONSOL-026`, then route it to Claude for final
sign-off. This sidecar does not change L1 canonical truth, the BFF runtime,
contract truth, route manifests, registry code, governance code, or
execute-plans source.

## Current State Snapshot

Source of truth checked during this refresh:

- Active `ai-status.json` entries for `BFF-CONSOL-022`, `023`, `027`, and this
  sidecar.
- Archived task snapshots for completed tasks, including `BFF-CONSOL-024`.
- Existing evidence and sidecar files under `support/evidence/` and
  `support/sidecars/BFF-CONSOL-*`.
- Current route-diff and seed-taxonomy files already present in the repo.

Current parent blockers for final acceptance:

| Task | Current state | BFF-CONSOL-027 impact |
|---|---|---|
| `BFF-CONSOL-022` | `blocked`, owner Codex2, waiting for Gemini | Staging strict preview evidence is initialized but blocked on Lovable preview URL, staging credentials, and BFF reachability. Current durable status says the fixed elapsed-day soak gate has been removed; do not copy older "7 day" text as a hard gate without owner refresh. |
| `BFF-CONSOL-023` | `todo`, owner Gemini2 | Prod strict cutover evidence is not available. It depends on the staging verification gate from 022. |
| `BFF-CONSOL-024` | `done`, archived 2026-05-14T07:23:05Z | Old `/bff/actions/*` deprecation is accepted evidence, not provisional. Pantheon commit `5225c289` verifies deprecation headers/body markers, final `/bff/v1/commands` remains unmarked, and execute-plans default command routing landed at sibling commit `3da4830`. |
| `BFF-CONSOL-027` | `todo`, owner Copilot, reviewer Claude | Final acceptance packet has not started. |

Important correction from the previous packet: `BFF-CONSOL-016`, `019`, `020`,
`021`, `024`, `025`, and `026` are no longer pending in the current evidence
set. Use their evidence/support files below instead of leaving blank acceptance
sections.

## Evidence Inventory for ACCEPTANCE.md

Copilot should cite the narrowest durable evidence file for each section. When a
task archive is not present in this checkout, cite the evidence file and support
packet instead of inventing status.

### Contract, Vocabulary, Fixtures

| Section | Source tasks | Evidence to cite |
|---|---|---|
| Backend route manifest | `BFF-CONSOL-001` | `ai-task-archive/tasks/BFF-CONSOL-001.json`; backend snapshot `services/control-plane/bff/contract_snapshots/backend_routes_manifest.json`; closeout notes mention 371 routes and stable `{param}` normalization. |
| Frontend route manifest | `BFF-CONSOL-002` | `ai-task-archive/tasks/BFF-CONSOL-002.json`; frontend snapshot `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`. |
| Route diff baseline | `BFF-CONSOL-003`, `BFF-CONSOL-026` | `.github/workflows/bff-route-diff.yml`; `docs/bff/contract_snapshots/route-diff-baseline.json`; `support/evidence/BFF-CONSOL-026-closeout.md`. |
| Command envelope spec | `BFF-CONSOL-004` | `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md`; `support/evidence/BFF-CONSOL-004-closeout.md`. |
| Live status banner | `BFF-CONSOL-005` | `ai-task-archive/tasks/BFF-CONSOL-005.json`; review evidence under `support/evidence/BFF-CONSOL-005/`. |
| Role vocabulary | `BFF-CONSOL-006` | `docs/bff/role-vocabulary-mapping-2026-05-13.md`; `support/sidecars/BFF-CONSOL-006/REVIEW-claude-2026-05-13.md`. |
| Seed taxonomy | `BFF-CONSOL-007`, `015`, `025` | `docs/bff/seed-taxonomy.json`; `support/sidecars/BFF-CONSOL-015/BFF-CONSOL-015-SIDECAR-BFF-HANDOFF.md`; `support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-SIDECAR-BFF-HANDOFF.md`; `docs/bff/seed-elimination-2026-05-13.md`. |
| Fixture packs | `BFF-CONSOL-008`, `009`, `010` | `services/control-plane/bff/data/fixtures_pack_a.json`; `fixtures_pack_b.json`; `fixtures_pack_c.json`; archives for 008/009/010. |

Current seed taxonomy in this checkout has 83 helpers:

| Category | Count |
|---|---:|
| `live_required` | 62 |
| `deferred` | 15 |
| `mock_only_dev` | 4 |
| `deprecated` | 2 |

### Read Path, Auth, SSE

| Section | Source tasks | Evidence to cite |
|---|---|---|
| Pack A detail smoke | `BFF-CONSOL-016` | `support/evidence/BFF-CONSOL-016-detail-smoke-a.json`; `support/sidecars/BFF-CONSOL-016/BFF-CONSOL-016-SIDECAR-BFF-HANDOFF.md`; commit evidence in git log includes `6b59cbd2` and `72a65d78`. |
| Pack B detail smoke | `BFF-CONSOL-017` | `support/evidence/BFF-CONSOL-017-detail-smoke-b.json`; `support/sidecars/BFF-CONSOL-017/BFF-CONSOL-017-SIDECAR-BFF-HANDOFF.md`; commit evidence in git log includes `83c42310` and `aea5d8b4`. |
| Pack C detail smoke | `BFF-CONSOL-018` | `support/evidence/BFF-CONSOL-018-detail-smoke-c.json`; `ai-task-archive/tasks/BFF-CONSOL-018.json`. |
| SSE replay | `BFF-CONSOL-011` | `support/evidence/BFF-CONSOL-011-sse-replay-smoke.json`; `ai-task-archive/tasks/BFF-CONSOL-011.json`. |
| SSE backpressure | `BFF-CONSOL-012` | `support/evidence/BFF-CONSOL-012-sse-backpressure.json`; `ai-task-archive/tasks/BFF-CONSOL-012.json`. |
| Cookie-session write gate | `BFF-CONSOL-013` | `ai-task-archive/tasks/BFF-CONSOL-013.json`; sibling execute-plans commit noted there. |
| Lovable CORS + JWKS | `BFF-CONSOL-014` | `ai-task-archive/tasks/BFF-CONSOL-014.json`. |

### Write Path and Cutover

| Section | Source tasks | Evidence to cite |
|---|---|---|
| Backend actions-to-command adapter | `BFF-CONSOL-019` | `support/sidecars/BFF-CONSOL-019/BFF-CONSOL-019-SIDECAR-BFF-HANDOFF.md`; implementation commit `34fa7aec`; `services/control-plane/bff/tests/test_actions_to_commands_adapter.py`. |
| Frontend command client migration | `BFF-CONSOL-020` | `support/evidence/BFF-CONSOL-020-closeout.md`; `support/sidecars/BFF-CONSOL-020/BFF-CONSOL-020-SIDECAR-BFF-HANDOFF.md`; sibling execute-plans commit `30b4ed394bcb036cb9f18b63c99f2910a657916e`. |
| Receipt replay/conflict/idempotency | `BFF-CONSOL-021` | `support/evidence/BFF-CONSOL-021-dual-write-soak.json`; `ai-task-archive/tasks/BFF-CONSOL-021.json`. Fixed elapsed-day soak was removed; regression follow-up is non-blocking. |
| Staging strict preview | `BFF-CONSOL-022` | `support/evidence/BFF-CONSOL-022-staging-strict-soak.md`; active task is blocked on Gemini for preview URL, credentials, and reachability. |
| Prod strict cutover | `BFF-CONSOL-023` | Expected `support/evidence/BFF-CONSOL-023-prod-strict-soak.md`; not present/started yet. |
| Old action receipt deprecation | `BFF-CONSOL-024` | `ai-task-archive/tasks/BFF-CONSOL-024.json`; Pantheon commit `5225c289`; support handoff `support/sidecars/BFF-CONSOL-024/BFF-CONSOL-024-SIDECAR-BFF-HANDOFF.md`. Accepted evidence: legacy `/bff/actions/*` receipts keep compatibility while returning `Deprecation`, `Sunset`, `Link`, and `Warning` headers plus `data`, `receipt`, and `meta` deprecation markers; final `/bff/v1/commands` remains unmarked. |
| Seed-only surface elimination | `BFF-CONSOL-025` | `docs/bff/seed-elimination-2026-05-13.md`; `support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-SIDECAR-BFF-HANDOFF.md`; Pantheon commit `f37b1099`; sibling execute-plans commit `226d7e4`. |
| CI route diff fail-hard | `BFF-CONSOL-026` | `support/evidence/BFF-CONSOL-026-closeout.md`; `support/sidecars/BFF-CONSOL-026/BFF-CONSOL-026-SIDECAR-BFF-HANDOFF.md`; workflow and baseline files listed above. |

## BFF Query Coverage Summary

### Verified read routes

The following read journeys have evidence and should be treated as closed for
the final packet, subject to 022/023 environment cutover verification:

| Family | Representative routes | Evidence |
|---|---|---|
| Pack A strategy/persona/deployment/runtime | `/bff/strategies`, `/bff/strategies/{id}`, strategy related routes, `/bff/personas/{id}`, `/bff/deployments/{id}`, `/bff/runtimes/{id}` | `BFF-CONSOL-016-detail-smoke-a.json` |
| Pack B evolution/research/v5/agora/artifacts | `/bff/evolution-programs/{id}`, `/bff/research-experiments/{id}`, `/bff/research-analyses/{id}`, `/bff/v5/interventions/{id}`, `/bff/agora/sessions/{id}`, `/bff/agora/sessions/{id}/messages`, `/bff/artifacts/{id}`, `/api/v1/lineage/inspiration/{artifact_id}` | `BFF-CONSOL-017-detail-smoke-b.json` |
| Pack C incident/approval/rebalance/job/audit | `/api/v1/operator/incident-response/{id}`, `/bff/approvals/{id}`, `/bff/deployments/{id}`, `/bff/rebalances/{id}`, `/bff/jobs/{id}`, `/bff/audit`, `/bff/audit/entities/{entity_type}/{entity_id}` | `BFF-CONSOL-018-detail-smoke-c.json` |
| SSE approval channel | `/bff/events/stream?channel=approval` | `BFF-CONSOL-011-sse-replay-smoke.json`, `BFF-CONSOL-012-sse-backpressure.json` |
| Session/auth gates | `/bff/me`, `/bff/auth/refresh`, `/bff/logout` | 013/014 archives |

Known degraded-path behavior verified by evidence:

- Pack A/B detail phantom IDs return typed `OBJECT_NOT_FOUND` 404.
- Pack C incident/approval/rebalance/job phantom IDs return typed 404; audit
  entity trails may return 200 with an empty list because the audit trail is a
  list-only surface.
- SSE missing replay cursor returns 409 with resync-route guidance.
- The live SSE mock generator is closed in live mode.

### Write routes and command receipts

Current write-path truth for the final packet:

| Surface | State |
|---|---|
| `POST /bff/v1/commands` | Backend final command admission exists and is covered by 019/021 tests. |
| `POST /bff/actions/{entityType}/{entityId}/{actionId}` | Backend legacy adapter routes through final command admission, records final admission/source route, and keeps `live_capital_side_effects=false`. |
| Frontend direct command caller | `BFF-CONSOL-020` closeout points to sibling execute-plans commit `30b4ed3` with `commandClient.ts` and command-route tests. |
| Replay/conflict/preconditions | `BFF-CONSOL-021-dual-write-soak.json` records legacy and direct receipt samples, stable replay, 409 conflict, `CONFIRM_TOKEN_REQUIRED`, and `APPROVAL_REQUIRED`. |
| Old action deprecation | Accepted in `BFF-CONSOL-024` done archive from 2026-05-14T07:23:05Z. Verified markers include `Deprecation`, `Sunset`, `Link`, `Warning`, `meta.deprecated`, and `data.receipt.deprecated`; final `/bff/v1/commands` receipts stay free of deprecation markers. |
| Live writes in strict preview | Must remain blocked with `VITE_BFF_REAL_WRITES=false` for 022/023 cutover evidence. |

Do not claim live capital execution is enabled. The command path evidence proves
admission, receipt, idempotency, and audit/foundation behavior, not production
capital side effects.

### Route diff gate

`BFF-CONSOL-026` makes route diff fail-hard by default. The current baseline is
intentionally still in fail status because it locks grandfathered backend-only
coverage debt:

| Metric | Current value |
|---|---:|
| Backend routes in snapshot | 371 |
| Frontend routes in snapshot | 178 |
| Fail-hard baseline failures | 209 |
| Backend active routes missing frontend rows | 209 |
| Frontend active routes missing backend rows | 0 |
| Naming/family mismatches | 0 |
| Warnings | 0 |

Final acceptance should say that the fail-hard gate locks the current failure
surface and prevents silent growth. It should not say the BFF/frontend route
manifests are fully parity-clean.

## Operator Journey

### Strict live read journey

```text
Operator opens execute-plans with VITE_BFF_MODE=live and VITE_BFF_FALLBACK=strict
  -> UI calls /bff/me to establish session and write eligibility
  -> list pages call Pack A/B/C BFF list routes
  -> operator opens details for strategy, persona, deployment, runtime,
     evolution, research, v5 intervention, agora session, artifact,
     incident, approval, rebalance, job, or audit trail
  -> detail routes return 2xx fixture-backed data or typed OBJECT_NOT_FOUND
  -> UI must render a live-backed detail, explicit not-found, or explicit
     unavailable/degraded state; it must not silently substitute seed data
  -> SSE approval feed opens, replays by cursor, and advertises resync routes
```

### Strict live write journey

```text
Operator attempts a governed write
  -> frontend checks authenticated session and VITE_BFF_REAL_WRITES
  -> in 022/023 strict cutover evidence, VITE_BFF_REAL_WRITES=false blocks fetch
  -> when writes are enabled in a controlled environment:
       preferred path: POST /bff/v1/commands
       compatibility path: POST /bff/actions/{entityType}/{entityId}/{actionId}
  -> BFF derives actor, validates role/policy/preconditions, records
     idempotency/audit/foundation context, and returns CommandResponse
  -> exact retry with same idempotency key returns the same receipt
  -> changed retry with same idempotency key returns 409 IDEMPOTENCY_CONFLICT
  -> missing confirm/approval evidence returns typed non-2xx errors
```

### Cutover journey

```text
Staging preview owner deploys isolated Lovable preview env
  -> preview uses VITE_BFF_MODE=live, VITE_BFF_FALLBACK=strict,
     VITE_BFF_REAL_WRITES=false
  -> remote smoke records Pack A/B/C reads, detail routes, SSE, and no fallback
  -> production strict cutover waits for staging verification
  -> final packet records prod smoke/regression evidence before Claude sign-off
```

The current 022 evidence file still contains older "7 day" wording, while the
active task next message says the fixed elapsed-day soak gate has been removed.
Copilot should ask the 022 owner/reviewer to refresh that evidence before
copying it into final acceptance.

## Frontend Handoff Notes

Use the sidecar packets for frontend absorption details. The final acceptance
packet should keep only the acceptance-critical facts and link to sidecars for
longer implementation guidance.

| Area | Handoff to carry forward |
|---|---|
| Pack A detail UI | Add/verify path builders and adapters for strategy related tabs, persona route policy/activity/evaluations, deployment runtime binding, runtime detail, and typed 404 UI. See 016 sidecar. |
| Pack B detail UI | Use `/bff/research-experiments/{id}` and `/bff/research-analyses/{id}` for research proof; add agora session/messages and artifact inspiration adapters; render v5 `remediation_skeleton`. See 017 sidecar. |
| Pack C detail UI | Incident, approval, rebalance, job, and audit list-only behavior are verified; ensure UI does not display `undefined` for missing jobs and disables audit detail drawers with list-only copy. See 018 evidence. |
| Mock/seed state | `mock_only_dev` and `deferred` helpers must show explicit badge/empty state in live-like modes and must not return seed rows as live truth. See 015/025 packets and `docs/bff/seed-taxonomy.json`. |
| Commands | Prefer `/bff/v1/commands`; preserve idempotency headers, trace headers, confirm token, approval/two-man evidence, and typed non-2xx error handling. See 019/020/021 packets. |
| Legacy action deprecation | Use accepted 024 evidence. Final packet should include a sample legacy receipt with deprecation headers/body markers and note the 2026-06-15 sunset floor. |
| Route manifests | Route changes must update backend/frontend snapshots or explicitly mark rows non-blocking. The current fail-hard baseline has 209 locked backend-only rows and 0 frontend-only rows. |

## ACCEPTANCE.md Skeleton for Copilot

Create `support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md` with this structure:

```markdown
# BFF Consolidation Final Acceptance Packet

Task: BFF-CONSOL-027
Owner: Copilot
Reviewer: Claude
Generated: <date>

## 1. Scope and Source List
- State that the packet aggregates BFF-CONSOL-001..026.
- Link this sidecar as a support index, not as canonical truth.
- List unresolved blockers for 022/023 if still open, and record 024 as done.

## 2. Contract Diff Baseline
- Backend manifest: BFF-CONSOL-001.
- Frontend manifest: BFF-CONSOL-002.
- Fail-but-warn baseline: BFF-CONSOL-003.
- Fail-hard cutover: BFF-CONSOL-026.
- Include current route-diff metrics: 371 backend routes, 178 frontend routes,
  209 locked backend-only failures, 0 frontend-only failures, 0 naming mismatches.

## 3. Role Vocabulary and Seed Taxonomy
- Role vocabulary from BFF-CONSOL-006.
- Seed taxonomy from BFF-CONSOL-007 plus 015/025 post-state.
- Current taxonomy count: 62 live_required, 15 deferred, 4 mock_only_dev, 2 deprecated.

## 4. Fixture Pack Summary
- Pack A, B, C fixture file paths and task closeouts.
- Record stable fixture IDs used in read smoke evidence.

## 5. Live Smoke - Read Path
- Pack A: cite BFF-CONSOL-016 evidence.
- Pack B: cite BFF-CONSOL-017 evidence.
- Pack C: cite BFF-CONSOL-018 evidence.
- Include typed 404/list-only degraded-path table.

## 6. SSE Evidence
- Cite BFF-CONSOL-011 and BFF-CONSOL-012.
- Include bearer/cookie open, cursor replay, 409 resync, bounded buffer,
  disconnect reclamation, and no mock generator in live mode.

## 7. Auth and Session Gates
- Cite BFF-CONSOL-013 and BFF-CONSOL-014.
- Include /bff/me write gate, CORS allowlist, and JWKS strict verification.

## 8. Live Smoke - Write Path
- Cite BFF-CONSOL-019, 020, 021.
- Record direct /bff/v1/commands and legacy /bff/actions/* compatibility.
- Include idempotency replay/conflict and typed precondition errors.
- Do not claim live capital side effects.

## 9. Command Receipt Sample
- Paste compact samples from BFF-CONSOL-021-dual-write-soak.json.
- Add legacy deprecation headers/body markers from the BFF-CONSOL-024 archive
  and commit `5225c289`.

## 10. Staging Strict Cutover
- Cite BFF-CONSOL-022 evidence.
- If still blocked, mark BLOCKED with missing preview URL, credentials, and reachability.
- Do not preserve stale fixed-day soak wording if the owner has removed that gate.

## 11. Prod Strict Cutover
- Cite BFF-CONSOL-023 evidence when available.
- If still todo, mark PENDING and explain dependency on 022.

## 12. Seed.ts Post-State
- Cite BFF-CONSOL-015 and 025.
- Record strict live seed-gating behavior and current taxonomy counts.

## 13. CI Fail-Hard Status
- Cite BFF-CONSOL-026.
- Record fail-hard baseline metrics and workflow command.

## 14. Open Follow-Ups and Non-Gates
- List route parity backlog, legacy audit/replay tooling migration, and any
  UI-visible deprecation banner follow-up if the parent keeps it non-gating.
- Separate blockers from non-blocking regression follow-up.

## 15. Final Sign-Off
- Claude review result and approval timestamp.
```

## Reviewer Checklist for Claude

- Confirm the substantive task artifact is limited to
  `support/sidecars/BFF-CONSOL-027/BFF-CONSOL-027-SIDECAR-BFF-HANDOFF.md`;
  L0 state files may change only through `scripts/ai-status.sh` handoff/status
  updates.
- Confirm no L1 canonical truth, BFF runtime, contract truth, route snapshot,
  registry, governance, or execute-plans implementation file changed here.
- Confirm pending states are accurate: 022 blocked, 023 todo, and 024 archived
  done at 2026-05-14T07:23:05Z.
- Confirm final acceptance does not claim fixed-day soak gates after the sprint
  state removed them.
- Confirm 024 deprecation content is treated as accepted evidence, not
  provisional review material.
- Confirm route diff wording says "fail-hard baseline locked", not "route parity
  clean".

## Sidecar Verification

Focused checks used for this refresh:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show BFF-CONSOL-027
jq '{task_id,archived_at,terminal_status,next:.task.next,commit:.task.delivery.commit}' ai-task-archive/tasks/BFF-CONSOL-024.json
jq '{source,total:(.helpers|length),categories:(.helpers|group_by(.category)|map({category:.[0].category,count:length}))}' docs/bff/seed-taxonomy.json
python3 scripts/bff_route_diff.py --dump | jq '{summary,frontend_missing_backend_count:(.failures.frontend_missing_backend|length),naming_mismatches_count:(.failures.naming_mismatches|length)}'
jq '{task_id,summary,verification_commands}' support/evidence/BFF-CONSOL-016-detail-smoke-a.json support/evidence/BFF-CONSOL-017-detail-smoke-b.json support/evidence/BFF-CONSOL-018-detail-smoke-c.json
jq '{task_id,summary,assertions}' support/evidence/BFF-CONSOL-011-sse-replay-smoke.json support/evidence/BFF-CONSOL-012-sse-backpressure.json
jq '{task_id,status,receipt_samples,regression_checks,regression_follow_up}' support/evidence/BFF-CONSOL-021-dual-write-soak.json
jq '.tasks[] | select(.id=="BFF-CONSOL-022" or .id=="BFF-CONSOL-023" or .id=="BFF-CONSOL-027" or .id=="BFF-CONSOL-027-SIDECAR-BFF-HANDOFF") | {id,status,owner,reviewer,waiting_for,next,last_update}' ai-status.json
git diff --check -- support/sidecars/BFF-CONSOL-027/BFF-CONSOL-027-SIDECAR-BFF-HANDOFF.md
```

Observed summary:

- Parent `BFF-CONSOL-027` is active `todo`, owner Copilot, reviewer Claude, and
  depends on `BFF-CONSOL-001` through `BFF-CONSOL-026`.
- `BFF-CONSOL-024` is archived `done` at 2026-05-14T07:23:05Z with Pantheon
  commit `5225c289`; its deprecation evidence is accepted, not provisional.
- Current taxonomy has 83 helpers: 62 `live_required`, 15 `deferred`,
  4 `mock_only_dev`, and 2 `deprecated`.
- Route diff fail-hard baseline reports 371 backend routes, 178 frontend routes,
  209 locked backend-only failures, 0 frontend-only failures, and 0 naming
  mismatches.
- Read path, SSE, and command receipt evidence JSON files are present and
  parseable.
- No canonical truth or runtime implementation was modified by this sidecar.

## Owner Closeout Note

Codex2 finalization scope is limited to this support artifact and generated L0
status/archive updates from `scripts/ai-status.sh done`. The reviewer approval
from Claude accepted this packet as support-only, accurate for current
022/023/024 states, and free of canonical truth or runtime implementation
changes.
