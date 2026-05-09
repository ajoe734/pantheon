# BFF-LUV-GAP-012 Sidecar BFF and Frontend Handoff Packet

Task ID: BFF-LUV-GAP-012-SIDECAR-BFF-HANDOFF
Parent task: BFF-LUV-GAP-012
Helper kind: bff_handoff_packet
Owner: Codex2
Reviewer: Codex
Prepared: 2026-05-09T14:18:13Z
Mutates canonical truth: no

## Scope

This is a support-only sidecar for the BFF-LUV-GAP-012 cutover-smoke parent
task. It does not update L1 canonical truth, the execute-plans contract
registry, runtime-manager behavior, governance behavior, BFF implementation,
or frontend code. The parent owner decides whether and how to absorb this
packet before running or unblocking the parent cutover smoke.

The frontend source repo for this handoff is `/home/lupin/code/execute-plans`.
Do not use the legacy `front-ai-trading-system` repo for this packet.

## Current State Snapshot

`ai-status.json` still records the parent `BFF-LUV-GAP-012` as `blocked`, with
the last note saying live cutover must wait for `BFF-LUV-SEM-001` through
`BFF-LUV-SEM-005` semantic completion tasks. That parent status is older than
the semantic-completion artifacts and live deployment evidence.

Current task/evidence readout:

| Area | Evidence | Handoff note |
|---|---|---|
| Parent task status | `BFF-LUV-GAP-012` remains `blocked` in `ai-status.json` | Parent owner should refresh status only after deciding whether the smoke can now run. |
| SEM completion | `BFF-LUV-SEM-001` through `BFF-LUV-SEM-005` artifacts record completed semantic fixes. | The original semantic blocker appears stale from the support packet perspective. |
| Live deployment | `BFF-LUV-SEM-006` artifact records lupin dev live cutover complete. | Public OpenAPI and anonymous final-contract probe passed in the archived evidence. |
| Local BFF final wiring | Focused tests pass in this worktree. | Route registration/read-model smoke is green locally. |
| Registry coverage snapshot | `execute_plans_bff_routes.json` still reports stale missing rows for `strategy-persona`. | Do not use the coverage report alone as the live-readiness source until this mismatch is reconciled. |

## Evidence Commands Run

Commands run from `/home/lupin/code/pantheon` during this sidecar refresh:

```bash
jq '.tasks[]? | select(.id == "BFF-LUV-GAP-012")' ai-status.json
sed -n '1,280p' docs/bff/execution-tasks/2026-05-08-execute-plans-gap/BFF-LUV-GAP-012-execute-plans-cutover-smoke.md
sed -n '1,220p' docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-001-session-auth-lifecycle.md
sed -n '1,240p' docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-002-command-execution-bridge.md
sed -n '1,220p' docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-003-entity-detail-read-models.md
sed -n '1,220p' docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-004-v5-loop-sentinel-runtime.md
sed -n '1,240p' docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-005-agora-extended-semantics.md
sed -n '1,220p' docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-006-lupin-dev-live-cutover.md
python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py
python3 -m pytest services/control-plane/bff/test_execute_plans_contract_registry.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/openapi.json
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/health
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/me
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/strategies
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/agora/signals
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/v5/control-room
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/v5/loop-runs
```

Observed results:

- Coverage report rendered 178 rows from the 2026-05-08 snapshot.
- Coverage report still shows `strategy-persona` as 24 missing and 1 deferred,
  plus three `execute-plans-cutover-smoke` deferred rows.
- Focused local BFF tests passed: `12 passed, 3 warnings`.
- Live lupin dev probes returned:
  - `/openapi.json` -> `200`
  - `/health` -> `200`
  - `/bff/me` -> `401`
  - `/bff/strategies` -> `401`
  - `/bff/agora/signals` -> `401`
  - `/bff/v5/control-room` -> `401`
  - `/bff/v5/loop-runs` -> `401`

Warnings in the local pytest run were pre-existing warning-class issues:
duplicate FastAPI operation id for an OpenClaw readiness route and
`datetime.utcnow()` deprecation warnings in `read_store.py`.

## BFF Query Gap Readout

There are two different "gap" views that the parent owner should not conflate.

### 1. Runtime and route-registration evidence

Local route registration and live anonymous probes now indicate the final
execute-plans BFF route surface is present on lupin dev. Auth-required routes
return `401` anonymously instead of `404` or `500`, which is the expected live
cutover gate for unauthenticated probes.

Semantic-completion artifacts record these delivered behaviors:

- SEM-001: `/bff/me`, auth refresh, logout, tenant switch, and locale mutation
  now use session lifecycle semantics instead of generic receipt payloads.
- SEM-002: final command routes write command-store-backed receipts with
  idempotent replay/conflict behavior and no live-capital side effects.
- SEM-003: final `{id}` detail aliases now project real DTOs, 404 for unknown
  records when the source exists, or explicit degraded DTOs when a source is
  unavailable.
- SEM-004: v5 control-room, loop-runs, sentinel findings, persona health, and
  strategy health are backed by runtime/read-store semantics with source-aware
  degraded metadata.
- SEM-005: extended Agora surfaces are store-backed; `POST /bff/agora/ask`
  persists session/message state and returns a command receipt without making
  an LLM call in the route handler.
- SEM-006: lupin dev public BFF returned OpenAPI 200 with 338 paths and an
  anonymous final-contract probe with zero 404/500 failures.

### 2. Checked-in registry snapshot evidence

The route registry snapshot remains older than the semantic-completion work.
The current report still lists:

- `strategy-persona`: 24 `missing`, 1 `deferred_with_task`
- `execute-plans-cutover-smoke`: 3 `deferred_with_task`

The `strategy-persona` rows conflict with current implementation evidence:
`main.py` registers the strategy/persona/search/types routes, the
`BFF-LUV-GAP-002` artifact records focused tests and review approval, and
`test_execute_plans_final_live_wiring_contract.py` includes strategy/persona
list/detail coverage.

Parent-owner implication: before claiming registry-based route closure, either
refresh `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`
in a registry-owned task, or explicitly treat the coverage report as a stale
snapshot and rely on final wiring/live probe evidence for the cutover smoke.
This sidecar intentionally does not update the registry.

## Operator Journey Handoff

### Session entry

1. Operator opens execute-plans against the Pantheon BFF base URL.
2. Frontend calls `/bff/me` or session mutation routes through `src/lib/bff/*`.
3. Anonymous browser probes should receive `401`; authenticated sessions should
   receive the BFF session DTO with selected tenant, locale, refresh state, and
   logout state.
4. The older execute-plans UI copy that says v5 control room is on a mock
   session until `/bff/me` lands should be revised when the frontend owner
   absorbs this packet.

### Management read flow

1. Strategy/persona/capital/rebalance/deployment/runtime/risk/incident pages
   should read through BFF list/detail routes, not direct service calls.
2. Final detail routes should render read-model DTOs for seeded records.
3. Missing ids should surface typed 404 behavior when the source exists.
4. Missing data sources should render explicit degraded/unavailable metadata,
   not invented frontend-only state.

### Agora read/write flow

1. Agora daily/signals/inbox/journal/ask/evaluation/persona-lab routes should
   continue to use BFF clients.
2. Signal feedback and ask submission produce record-backed or command-backed
   receipts; they are not evidence of LLM execution inside the route handler.
3. Empty long-tail datasets should preserve source/degraded metadata so the UI
   can distinguish "healthy but empty" from "source unavailable".

### v5 control-room flow

1. Control room composes loop-runs, interventions, sentinel findings, persona
   health, and strategy health from the same child read models.
2. Loop/sentinel routes now exist on live lupin dev as auth-required routes.
3. Frontend should show degraded source metadata where runtime/sentinel sources
   are absent, rather than falling back silently to mock status.

### Command and high-risk action flow

1. Browser commands still go through BFF only.
2. Use the `Idempotency-Key` header for final command routes.
3. Same key plus same payload should replay the original receipt.
4. Same key plus changed payload should render the final `409` conflict envelope.
5. Accepted command receipts are not proof of live broker impact; SEM-002 keeps
   `liveCapitalSideEffects: false`.
6. UI should poll/read back the owning read route before showing domain state as
   changed.

## Frontend Handoff Materials

Frontend repo: `/home/lupin/code/execute-plans`.

Recommended environment for lupin dev final-contract verification:

```env
VITE_BFF_MODE=real
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
VITE_BFF_REAL_WRITES=false
```

Handoff notes:

- SEM-006 records the lupin dev target as "live-mode safe" for the final
  execute-plans BFF contract. In the current execute-plans transport code, the
  accepted env values are `mock`, `hybrid`, and `real`; use
  `VITE_BFF_MODE=real` for strict BFF mode.
- Do not set `VITE_BFF_MODE=live` unless the frontend transport first adds that
  alias. With the current code, an unrecognized mode plus a base URL falls back
  to `hybrid`.
- Keep `hybrid` available when testing UI surfaces that still depend on
  historical Lovable/mock-only routes outside the final contract.
- Keep `VITE_BFF_REAL_WRITES=false` until the frontend owner explicitly verifies
  idempotency, command receipts, audit copy, and readback refresh behavior in
  the browser. BFF command routes are command-store backed; they still do not
  enable live-capital side effects.
- Update `README.md` in the frontend repo when the parent owner absorbs this:
  it still says only `/health`, `/bff/approvals`, `/bff/actions`, and
  `/bff/v5/interventions` are live and calls out `/bff/strategies` and
  `/bff/personas` as unavailable.
- Update session/transitional copy that still says the v5 control room uses a
  mock session until `/bff/me` lands.
- Continue enforcing the "BFF boundary only" rule in execute-plans: page
  components should not call real service APIs directly.

Important frontend files to inspect before parent smoke:

| File | Parent-smoke use |
|---|---|
| `/home/lupin/code/execute-plans/README.md` | Environment mode and stale hybrid/live guidance. |
| `/home/lupin/code/execute-plans/src/lib/bff/transport.ts` | Live/hybrid mode resolution, base URL, auth headers, health probe. |
| `/home/lupin/code/execute-plans/src/lib/bff/client.ts` | Read facade and current live-vs-mock fallback behavior. |
| `/home/lupin/code/execute-plans/src/lib/bff/mutations.ts` | Write facade, audit/realtime handling, real-write gate. |
| `/home/lupin/code/execute-plans/src/lib/bff/realtime.ts` | Realtime fallback and SSE readiness checks. |
| `/home/lupin/code/execute-plans/src/lib/bff/v5.ts` | v5 intervention/control-room assumptions and session copy. |
| `/home/lupin/code/execute-plans/src/i18n/locales/en-US.ts` | Transitional mock-session copy. |
| `/home/lupin/code/execute-plans/src/i18n/locales/zh-TW.ts` | Transitional mock-session copy. |

## Suggested Parent Smoke Sequence

The parent owner can use this sequence after deciding to unblock
`BFF-LUV-GAP-012`:

```bash
cd /home/lupin/code/execute-plans
npm run test
npm run build
```

Then, from `/home/lupin/code/pantheon`:

```bash
python3 -m pytest services/control-plane/bff/test_execute_plans_contract_registry.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/openapi.json
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/me
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/strategies
curl -sS -o /dev/null -w '%{http_code}' https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/v5/control-room
```

Expected public probe shape:

- `/openapi.json` -> `200`
- public `/health` -> `200`
- auth-required `/bff/*` routes -> `401`, not `404` or `500`

If the parent owner needs route-family proof stronger than anonymous `401`,
run an authenticated/stub-auth BFF smoke locally or against a dev target with a
valid operator token. Do not treat anonymous `401` as DTO-shape proof.

## Parent Absorption Checklist

1. Decide whether `BFF-LUV-GAP-012` can be unblocked now that SEM-006 evidence
   says lupin dev live final-contract probe passed.
2. Decide whether stale registry rows must be refreshed before parent smoke, or
   whether final-wiring/live evidence is enough for this cutover run.
3. Run execute-plans `npm run test` and `npm run build`.
4. Record exact public BFF target URL and probe results in the parent artifact.
5. Update frontend README/copy in the frontend repo only through a frontend-owned
   or parent-approved task.
6. Keep `VITE_BFF_REAL_WRITES=false` unless command receipt UX, audit copy, and
   readback refresh have been explicitly verified.
7. Keep any remaining mock fallback documented as intentional `deferred_with_task`
   or `mock-only historical route`, not accidental silent fallback.

## Reviewer Handoff

Reviewer should verify that this packet:

- stays support-only and does not mutate canonical truth;
- accurately distinguishes stale registry snapshot gaps from current live route
  evidence;
- does not claim authenticated DTO correctness from anonymous `401` probes;
- uses the current lupin dev target
  `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`;
- gives the parent owner enough concrete commands to decide whether to unblock
  and run the BFF-LUV-GAP-012 smoke.

Trace sources used for this packet:

- `.orchestrator/task-briefs/bff_luv_gap_012_sidecar_bff_handoff.md`
- `ai-status.json`
- `docs/bff/execution-tasks/2026-05-08-execute-plans-gap/INDEX.md`
- `docs/bff/execution-tasks/2026-05-08-execute-plans-gap/BFF-LUV-GAP-012-execute-plans-cutover-smoke.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-001-session-auth-lifecycle.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-002-command-execution-bridge.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-003-entity-detail-read-models.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-004-v5-loop-sentinel-runtime.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-005-agora-extended-semantics.md`
- `docs/bff/execution-tasks/2026-05-09-execute-plans-semantic-completion/BFF-LUV-SEM-006-lupin-dev-live-cutover.md`
- `docs/bff/evidence/BFF-LUV-SEM-006-lupin-dev-live-probe-20260509T113136Z.json`
- `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`
- `services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py`
- `services/control-plane/bff/test_execute_plans_contract_registry.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
- `/home/lupin/code/execute-plans/README.md`
