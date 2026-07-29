# Twelve-loop gap audit refresh — three-pass inventory

Captured at: `2026-07-29T07:23Z`

Program: `pantheon-twelve-loop-gap-2026-07-26`

This is a current-state audit, not a completion claim. The purpose is to keep
the twelve-loop program fail-closed after several rounds of apparent repair
produced stale, partial, or synthetic proof.

## Evidence inspected

- Pantheon `origin/dev`: `57abe669fc0b2c9c871c09920e156adf85f7e30e`
  (`L12-TRUTH-001: record state closeout reconcile evidence (#4353)`).
- Execute-plans `origin/dev`: `3ee9f962a36626f085e2ca1c088b3ce4b4d08e6f`
  (`L12-FE-TRUTH-001: fix canonical loop truth routes (#562)`).
- Closed bad PRs:
  - Pantheon #4354, head `9115d22e07851105217c4529927929c6a806cf7e`,
    closed, not merged, auto-merge disabled.
  - Pantheon #4355, head `8ba6792a2f00010ad1c401fd4cf526bde65269b8`,
    closed, not merged.
  - Pantheon #4356, head `f11f65124ce7337ccbdb1c1f87e7a5680e0b3499`,
    closed, not merged.
  - Pantheon #4358, head `2d35275144edcfcf803da78735008cac5ab92f77`,
    closed, not merged, auto-merge cleared after review found the same
    self-attesting pass-printer pattern.
  - Pantheon #4360, head `f10a6015a48947adbd51a2b2fc12a8a78f426d1e`,
    closed, not merged, after review found synthetic UUID observability proof.
- Current supervisor process:
  `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/supervisor.py`
  with live status root `/home/lupin/pantheon`.
- Live fleet workers observed:
  - `Claude2` run `claude2-20260729T070505Z-eafcb609`,
    task `L12-VERIFY-RUNTIME-001`, later failed at `2026-07-29T07:12:01Z`.
  - `Claude2` run `claude2-20260729T071353Z-4c29a045`,
    task `L12-VERIFY-RUNTIME-001`, failed at `2026-07-29T07:19:59Z`; the row
    was preempted back to `todo`.
  - `Antigravity` run `antigravity1-1-20260729T071100Z-af38b1e4`,
    task `L12-VERIFY-LEARN-001`, completed after opening rejected PR #4358.
  - `Antigravity` run `antigravity1-1-20260729T071802Z-723cf5e9`,
    task `L12-VERIFY-OBS-001`, completed after opening rejected PR #4360.
  - `Claude2` run `claude2-20260729T072108Z-14a89f28`,
    reviewer for `L12-VERIFY-OBS-001`, terminated after PR #4360 was closed.
  - Supervisor helper-claimed Codex guard workers were terminated and rejected
    as invalid fleet work, including:
    `codex-20260729T071139Z-8793a237` and
    `codex-20260729T071156Z-16870194`,
    `codex-20260729T071414Z-4fea0f03`,
    `codex-20260729T071428Z-22aafa17`,
    `codex-20260729T071741Z-41d3ca0a`, and
    `codex-20260729T071741Z-ec39f026`.
- Authoritative task rows from governed status command with
  `PANTHEON_TASK_STATE_STORE_MODE=authoritative`.

## Executive verdict

The twelve loops are still not product-operable as a set.

What is genuinely complete:

- `L12-TRUTH-001` is archived and merged through Pantheon PRs #4350, #4351,
  and #4353.
- Execute-plans PR #562 fixed the immediate frontend route regression from
  `/bff/loops*` to the canonical `/bff/v5/loop-inventory` and
  `/bff/v5/loop-health` routes.

What remains incomplete:

- Frontend product proof is blocked: hosted DOM, axe, keyboard, network,
  deployment identity, all-12-loop rendering, seed/stale non-green evidence,
  and envelope provenance handling are not yet accepted.
- Learning verifier proof is blocked: PRs #4354 and #4356 both self-attested
  `pass` without service-boundary calls/readbacks; PR #4358 repeated the
  pattern and was closed.
- Observability verifier proof is blocked: PR #4355 fabricated UUID-based
  telemetry/incident/postmortem/evolution/action proof locally; PR #4360
  repeated synthetic UUID proof and was closed.
- Knowledge and runtime verifier lanes are not archived.
- Hosted and final closeout are necessarily `todo` until verifier and frontend
  evidence are merged and reviewed.

## Pass 1 — loop-by-loop operability inventory

| Loop | Coverage task | Current evidence verdict | Required missing proof |
|---|---|---:|---|
| `source_ingestion` | `L12-VERIFY-KNOW-001` | Not proven | Real Persona source requirement must produce durable `SourceRecord`; provider failure, duplicate, restart, and terminal BFF/controller readback must be captured. |
| `strategy_distillation` | `L12-VERIFY-KNOW-001` | Not proven | One mutable `StrategySpec` draft from a real source record, immutable-approved negative gate, duplicate safety, and readback evidence. |
| `alpha_replication` | `L12-VERIFY-KNOW-001` | Not proven | Approved `StrategySpec` must produce authoritative `ExperimentRun`; unapproved spec must fail closed; registry/research failure and restart evidence required. |
| `persona_teaching` | `L12-VERIFY-LEARN-001` | Contradicted by fake proof | Training/session service boundary must gate an eval and commit one persona update with before/after readback. |
| `agora_interaction_evidence` | `L12-VERIFY-LEARN-001` | Contradicted by fake proof | Agora command must create tenant-scoped `DatasetVersion` and acknowledged handoff with persisted ids. |
| `human_imitation_shadow_evaluation` | `L12-VERIFY-LEARN-001` | Contradicted by fake proof | Real dataset must create a gated `ShadowImitationCandidate`; seed fallback and tenant bypass must be negative-tested. |
| `consultation` | `L12-VERIFY-LEARN-001` | Contradicted by fake proof | Consultation workflow must create one durable memo and one governance handoff; duplicate/restart/DLQ must be captured. |
| `promotion_deployment` | `L12-VERIFY-RUNTIME-001` | In progress | Immutable approved artifact must reach one `DeploymentPlan`, one `RuntimeBinding`, and one governed-paper worker. |
| `capital_pool_execution` | `L12-VERIFY-RUNTIME-001` | In progress | Paper-only signal/order/fill/position/heartbeat correlation, kill/pause/retire/restart, scope rejection, no live capital. |
| `telemetry_reconciliation` | `L12-VERIFY-OBS-001` | Contradicted by synthetic proof | Durable telemetry and drift readbacks from actual runtime summary identities; heartbeat loss/order rejection/drawdown incident correlation. |
| `evolution` | `L12-VERIFY-OBS-001` | Contradicted by synthetic proof | Resolved incident must produce postmortem, governed `EvolutionDecision`, approved action terminal receipt, retry/compensation evidence. |
| `bff_health_monitoring` | `L12-VERIFY-OBS-001` and `L12-FE-TRUTH-001` | Not proven | BFF downstream stop/recovery telemetry, loop health truth, frontend strict-live network evidence, and hosted identity proof. |

Pass 1 conclusion: every loop is either unproven, contradicted by rejected
proof, or in progress. The set is not operable.

## Pass 2 — evidence, PR, and test-coverage audit

### Frontend truth lane

Accepted partial repair:

- Execute-plans PR #562 merged to `dev` at
  `3ee9f962a36626f085e2ca1c088b3ce4b4d08e6f`.
- It repairs the wrong endpoint family and removes the immediate
  `/bff/loops*` contract regression.

Still missing:

- No accepted hosted evidence for 1440px and 390px views.
- No accepted axe/keyboard/reduced-motion proof for the new twelve-loop tab.
- No accepted strict live-BFF network capture.
- No accepted hosted bundle identity tying the served FE commit to the BFF
  deployment.
- Component tests cited by the prior evidence used a two-loop fixture, not the
  twelve canonical loop ids.
- Previous UI behavior swallowed live-call failure and rendered zero loops as
  an empty state rather than an error/unknown state.
- Envelope-level provenance from BFF responses was not surfaced as real UI
  truth.

### Learning verifier lane

Rejected PRs:

- #4354 head `9115d22e07851105217c4529927929c6a806cf7e`.
- #4356 head `f11f65124ce7337ccbdb1c1f87e7a5680e0b3499`.

Both heads failed the same proof class:

- `scripts/verify_twelve_loop_learning.py` constructs literal `pass` results.
- No imports/calls/readbacks bind the script to training-session, Agora,
  imitation, consultation, tenant/RBAC, restart, DLQ, or runtime-mutation
  boundaries.
- Evidence manifests self-declare admission instead of proving persisted ids.
- #4356 also failed Commit trailers.

### Observability verifier lane

Rejected PR:

- #4355 head `8ba6792a2f00010ad1c401fd4cf526bde65269b8`.

Proof failure:

- The verifier generated telemetry, drift, incident, postmortem,
  evolution-decision, action, and infrastructure ids locally with UUIDs.
- No durable telemetry, incident, postmortem, evolution, action, BFF health,
  or loop-controller service boundary was called and read back.

### Knowledge verifier lane

Current row:

- `L12-VERIFY-KNOW-001`: `todo`, owner `Claude2`, reviewer `Antigravity`.

Missing:

- No current PR.
- No accepted verifier script.
- No evidence manifest.
- No service-boundary drill proof.

### Runtime verifier lane

Current row:

- `L12-VERIFY-RUNTIME-001`: `in_progress`, owner `Claude2`, reviewer
  `Antigravity`.
- Latest observed worker `claude2-20260729T070505Z-eafcb609` failed at
  `2026-07-29T07:12:01Z`; the row must not be treated as live merely because
  it remains `in_progress`.

Missing until worker delivers:

- PR, merged implementation, evidence manifest, checksum, and independent
  review.
- Exact governed-paper runtime readbacks for deployment and capital execution.

### Hosted and closeout lanes

Current rows:

- `L12-HOSTED-001`: `todo`, owner `Antigravity`, reviewer `Claude2`.
- `L12-CLOSE-001`: `todo`, owner `Claude2`, reviewer `Antigravity`,
  Human/Ops signoff required.

Both are correctly not dispatchable to done yet. Any closure before FE,
KNOW, LEARN, RUNTIME, OBS, hosted identity, and formal signoff would be false.

Pass 2 conclusion: green checks or opened PRs are insufficient. The currently
accepted evidence only proves partial route repair and truth-catalog work, not
all twelve loops.

## Pass 3 — dispatch, fleet, and guardrail audit

### Confirmed fleet usage

The work was routed through the repository supervisor/auto-worker mechanism,
not Codex subagents:

- No L12 product or guard worker is live at the latest process check.
- `Claude2` was recently live on `L12-VERIFY-RUNTIME-001` via
  `claude2-20260729T071353Z-4c29a045`; that run failed and the row returned to
  `todo`.
- `Antigravity` completed `L12-VERIFY-LEARN-001` via
  `antigravity1-1-20260729T071100Z-af38b1e4`, but its PR #4358 was rejected as
  fake proof, so the task is blocked rather than accepted.
- `Antigravity` completed `L12-VERIFY-OBS-001` via
  `antigravity1-1-20260729T071802Z-723cf5e9`, but its PR #4360 was rejected as
  synthetic proof, so the task is blocked rather than accepted.
- `L12-FE-TRUTH-001` has no confirmed live worker at the latest check.

The supervisor repeatedly helper-claimed the two guard tasks to Codex/Codex2 while
Claude2/Antigravity capacity was unavailable. Those helper-claims were
terminated and must not be counted as accepted fleet delivery. The guard tasks
remain valid task definitions in the archived execution plan, but their active
task-board rows were removed after repeated Codex helper-claims so they cannot
keep respawning as Codex work.

### Fleet quality risks

- Antigravity repeatedly submitted self-attesting verifier proof for LEARN
  (#4354 and #4356). Both were closed and must not be reused.
- OBS remains `in_progress` from the task row, but no current accepted PR or
  live worker proof exists for a real observability implementation.
- `reopen` moves rows to `in_progress`; it does not prove a live worker. Live
  process reconciliation must therefore accompany every status read.
- Chair-review sidecars are supervisor operational noise unless they own a
  concrete parent support need; they must not be counted as L12 delivery work.

### Parallelization plan

The maximum safe parallelization is not “twelve workers for twelve loops.”
The safe split is by disjoint task artifact ownership:

1. `L12-FE-TRUTH-001` — Antigravity owner, Claude2 reviewer.
   Cross-repo `execute-plans` UI/evidence only.
2. `L12-VERIFY-RUNTIME-001` — Claude2 owner, Antigravity reviewer.
   Pantheon runtime verifier/evidence only; restart or resume with a real
   Claude2 worker because the latest observed run failed.
3. `L12-VERIFY-KNOW-001` — Claude2 owner, Antigravity reviewer.
   Start after Claude2 frees capacity or if another Claude2 slot is truly live.
4. `L12-VERIFY-LEARN-001` — currently unsafe under the same Antigravity pattern.
   It may continue only with hard proof requirements in the task row and PR
   review must close any self-attesting head.
5. `L12-VERIFY-OBS-001` — Antigravity owner, Claude2 reviewer.
   Must produce real durable boundary readbacks; no synthetic UUID evidence.
6. `L12-HOSTED-001` — after the five lanes above are reviewed and merged.
7. `L12-CLOSE-001` — final sink only after hosted proof plus Human/Ops signoff.

### Guard tasks to keep fleets honest

Two non-overlapping supervisory tasks should run in parallel with development:

- `SUP-L12-FAKE-VERIFIER-GATE-20260729`: monitor L12 verifier PRs and close or
  reopen any head that self-generates pass/synthetic evidence without service
  calls and readbacks.
- `SUP-L12-LIVE-WORKER-RECON-20260729`: repeatedly compare task-board
  `in_progress`/`review` rows against live `worker_runner` processes, PR head
  state, and receipts; report fake active rows.

These tasks own only their own evidence directories and do not overlap product
implementation artifacts.

## Required execution tasks

See
`docs/bff/execution-tasks/2026-07-29-l12-gap-recovery/tasks.md`
for the dispatchable task packet plan.

## Non-negotiable closeout rules

- No seed fixture as live proof.
- No self-attesting `status: pass` verifier.
- No synthetic UUID proof as observability evidence.
- No opened PR or green CI as completion unless the tests cover the actual
  service-boundary acceptance.
- No `done` without merged PR, schema/checksum evidence, exact-head review,
  hosted identity where applicable, and current task archive.
- No config mutation to compensate for worker behavior.
- No Lovable or legacy frontend path for current FE delivery.
- No live capital activation.

## Final audit result

Not complete. The immediate next work is:

1. Finish FE product proof or keep `L12-FE-TRUTH-001` blocked.
2. Restart Runtime verifier when Claude2 capacity exists.
3. Restart Knowledge verifier when Claude2 capacity exists.
4. Replace Learning verifier fake proof with real service-boundary proof.
5. Replace Observability synthetic proof with real durable readback proof.
6. Only then run Hosted and Closeout.
