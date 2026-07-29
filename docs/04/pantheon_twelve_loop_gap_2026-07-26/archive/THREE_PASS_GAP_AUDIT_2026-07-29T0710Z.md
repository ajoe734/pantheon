# Twelve-loop gap audit refresh — three-pass inventory

Captured at: `2026-07-29T07:23Z`

Live-rescue addendum captured at: `2026-07-29T08:30Z`

Review correction captured at: `2026-07-29T08:35Z`

Fleet correction captured at: `2026-07-29T08:40Z`

Dispatch-priority correction captured at: `2026-07-29T08:48Z`

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

## Live-rescue addendum — three-pass recheck after 08:20Z fleet repair

This addendum repeats the audit after the 08:20Z live repair because several
failures were runtime/control-plane failures rather than product verifier
failures. It is still not a completion claim.

### Addendum pass 1 — current loop/task state

Current supervisor heartbeat inspected at `2026-07-29T08:28:25Z` showed the
supervisor alive with `execution.running=4`.

Verified supervisor/auto-worker dispatches, not Codex subagents:

- `L12-VERIFY-KNOW-001` was auto-started on `Claude2` as
  `claude2-20260729T082322Z-0b3d4613`.
- `L12-VERIFY-OBS-001` was auto-started on `Antigravity` as
  `antigravity1-1-20260729T082732Z-560e0361`; the task subsequently reached
  `review` at `2026-07-29T08:28:16Z` with next evidence pointing at
  `docs/deployment/evidence/twelve-loop-gap/L12-VERIFY-OBS-001/evidence.json`
  and anchor commit `65aa15358` on `task/L12-VERIFY-OBS-001`.
- At `2026-07-29T08:31:49Z`, `Claude2` rejected that OBS review: the diff from
  rejected head `f10a6015a..65aa15358` changed only evidence JSON timestamps
  and UUIDs, while `scripts/verify_twelve_loop_observability.py` remained
  byte-identical to the already rejected self-attesting generator. OBS returned
  to `in_progress` and was redispatched to `Antigravity` as
  `antigravity1-1-20260729T083305Z-e86d7488`.
- `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729` was handled by `Antigravity`
  and moved to `review` at `2026-07-29T08:26:02Z`; a `Codex2` reviewer worker
  was then dispatched because the task row names `Codex2` as reviewer.
- `L12-VERIFY-RUNTIME-001` remains `todo`; it cannot dispatch while the single
  `Claude2` account quota is occupied by `L12-VERIFY-KNOW-001`.

Current loop proof state after this rescue:

| Lane | State after 08:30Z recheck | Remaining proof gap |
|---|---|---|
| KNOW (`source_ingestion`, `strategy_distillation`, `alpha_replication`) | `in_progress` on real `Claude2` auto worker | Still no terminal evidence, PR, review approval, or merged manifest. |
| LEARN (`persona_teaching`, `agora`, `imitation`, `consultation`) | `blocked` by rejected fake verifier heads | Needs full service-boundary rewrite; no current valid worker result. |
| RUNTIME (`promotion_deployment`, `capital_pool_execution`) | `todo`, waiting for `Claude2` quota | Still lacks governed-paper runtime proof and restart/DLQ evidence. |
| OBS (`telemetry_reconciliation`, `evolution`, `bff_health_monitoring`) | Rejected by `Claude2`; redispatched to `Antigravity` | Must replace byte-identical synthetic verifier with service-backed telemetry/incident/postmortem/evolution/BFF readbacks. |
| FE/HOSTED/CLOSE | Not proven | Still dependent on accepted verifier and hosted identity evidence. |

### Addendum pass 2 — newly confirmed development gaps

The live repair exposed these additional development gaps that were not
explicit enough in the 07:23Z audit:

1. **Task-brief synchronization drift.** The live status root held stale
   `.orchestrator/task-briefs/l12_verify_{know,runtime,obs}_001.md` files with
   old owner/reviewer values `Codex2`/`Codex`, while the dev-root generated
   briefs had the corrected `Claude2`/`Antigravity` or `Antigravity`/`Claude2`
   assignment. A worker copied the stale status-root brief into its worktree
   even though the wake prompt had the correct owner. This can make a correct
   supervisor dispatch execute with wrong task context.
2. **Worker runtime dependency provisioning is not reproducible.** The live
   `Claude2` KNOW worker hit missing Python service dependencies (`fastapi`;
   then shared `/tmp/l12-alpha-pydeps` lacked `uvicorn`). A temporary runtime
   rescue installed `fastapi`, `uvicorn`, `httpx`, `pytest`, and related
   packages into `/tmp/l12-alpha-pydeps`, but this is not a durable repo change
   or worker image guarantee.
3. **Stale failure streaks preempted L12 with Codex2 chair triage.** Five old
   guardrail records (`tool_auth`, `missing_process`, and `context canceled`)
   caused `chair_review:reassignment_triage` to bypass cooldown and repeatedly
   queue `Codex2` chair reviews before L12 dispatch. Runtime-state-only repair
   cleared the stale records after verifying `gh auth` and clean command-root,
   but there is no merged regression yet.
4. **Provider-first guard is not durable until #4362 merges.** PR #4362 adds a
   provider-first helper-claim guard and tests, but branch protection still
   blocks it behind Human/Ops contexts. Live command-root cannot carry the
   unmerged patch because `worker_runner.py` refuses dirty executable/import
   files and mismatched runtime SHAs.
5. **Parallelism is bounded by provider account quota.** The live config allows
   multiple total execution workers, but `Claude2` and `Antigravity` each have
   effective account concurrency `1/1`. Running Codex workers do not consume
   those quotas, but they can distract chair/ops flow. The latest 08:40Z state
   shows `L12-VERIFY-RUNTIME-001` did start on `Claude2`, while
   `L12-VERIFY-KNOW-001` returned to `todo` after a failed/preempted Claude2
   run. OBS is back on `Antigravity` after a second Claude2 rejection.

### Addendum pass 3 — new tests and validation still missing

The following tests/validations must be added before the fleet can be called
healthy:

- Regression that task-brief materialization in the status root and worker
  worktree always reflects the authoritative task-state row, including
  owner/reviewer, status, last update, and `next`.
- Worker bootstrap/provisioning test that verifies the Python dependencies
  needed for the L12 service-boundary verifiers are available without manual
  `/tmp` repair.
- Supervisor regression that stale chair/failure-loop records cannot bypass
  L12 owner dispatch when no viable reassignment target exists or when the
  failure cause has been cleared.
- Runtime admission test that a dirty/unmerged command root is refused with a
  clear recovery path, and that the accepted recovery path is a merged runtime
  SHA rather than a live patched checkout.
- Fleet health smoke that proves `Claude2` and `Antigravity` can each complete
  one short governed worker task, update status through
  `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh`, and exit terminal without
  leaving stale queue leases.

Addendum verdict: the supervisor/fleet dispatch path is functioning again for
at least `Claude2` and `Antigravity`, but the twelve-loop product goal remains
incomplete and the newly exposed runtime-control gaps must be closed through
PRs, not live-only repair.

### Review correction — OBS remained fake after redispatch

The `Claude2` review at `2026-07-29T08:31:49Z` is decisive evidence that the
OBS lane is still not repaired:

- `generate_observability_proof_record()` constructs the whole chain with
  `uuid.uuid4()` and one shared timestamp.
- `verify_observability_chain()` asserts that same in-memory record against
  itself.
- Grep for real boundary mechanisms (`requests`, `httpx`, `urllib`,
  `psycopg`, `sqlalchemy`, `subprocess`, `socket`, `aiohttp`) across the
  verifier found zero matches.
- Manifest records shared a single timestamp, proving one-process construction
  rather than durable boundary readback.
- No open PR or merged evidence exists: PRs #4355 and #4360 are both closed
  with `mergedAt=null`.

Required OBS repair is therefore implementation work, not reviewer paperwork:
drive the telemetry, incident, postmortem, governance/evolution, downstream
health, and loop-health services; read back persisted records by id; prove
duplicate rejection and restart/replay idempotency against the store; remove
the false `governed_by=Claude2` claim; then open a fresh task PR to `dev`.

### Fleet correction — 08:40Z live state changed again

The 08:40Z live state check adds four material corrections to the addendum:

- `L12-VERIFY-OBS-001` was rejected again by `Claude2` at
  `2026-07-29T08:38:37Z`. The follow-up anchor `ac87dc46a` still had
  `scripts/verify_twelve_loop_observability.py` byte-identical to the rejected
  #4360 head and changed only regenerated UUIDs/timestamps in
  `evidence.json`. OBS is therefore not merely pending review; it is back in
  implementation repair on `Antigravity` run
  `antigravity1-1-20260729T083928Z-4bc23525`.
- `L12-VERIFY-KNOW-001` is no longer a healthy active Claude2 run. The latest
  run `claude2-20260729T083251Z-33cfa39f` failed and the supervisor returned
  the row to `todo` to free Claude2 for higher-priority review/runtime work.
  This exposes a missing durable worker-bootstrap/preemption recovery test.
- `L12-VERIFY-RUNTIME-001` did dispatch to `Claude2` as
  `claude2-20260729T083911Z-b350d04f`. The earlier “waiting for Claude2 quota”
  statement is stale as of 08:40Z.
- `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729` reached review from
  `Antigravity`, but the supervisor auto-reassigned the reviewer from `Codex2`
  to `Codex` after repeated Codex2 terminal failures. That is not a Codex
  subagent created by this session; it is the repository supervisor's
  auto-worker fallback. It still violates the intended Claude/Antigravity-first
  posture until #4362 merges and is validated in the live command root.

### Dispatch-priority correction — 08:48Z OBS review was not dispatched

The 08:48Z live state check adds another fleet-control gap:

- `Antigravity` opened OBS PR #4364 at head
  `ffd90cab757ee3939cbf7c4e5e5c7956f29f0bdd`; visible checks were green, but
  merge state remained `BLOCKED` and the canonical task row stayed `review`
  awaiting `Claude2`.
- `L12-VERIFY-RUNTIME-001` run `claude2-20260729T083911Z-b350d04f` was
  superseded and returned to `todo`; `L12-VERIFY-KNOW-001` also remained
  `todo`.
- The only `Claude2` quota slot was then dispatched to non-L12
  `OPS-PROMOTE-PR-CI-TRIGGER-001` instead of OBS review. A live priority
  interruption sent SIGTERM to
  `claude2-20260729T084333Z-7b4dc4c8` and it exited with code 143, but
  foreground `ai-status blocker` was rejected by the status-command lease
  guard. The supervisor then redispatched the same non-L12 OPS review as
  `claude2-20260729T084759Z-b06bd849`.
- Therefore the remaining fleet blocker is not Claude2 auth or quota; it is
  dispatch ordering and queue lease selection. A durable fix must make fresh
  L12 `review` tasks with green PR evidence outrank non-L12 OPS review tasks
  sharing the same provider quota, and prevent immediate redispatch of an
  interrupted non-L12 event ahead of the waiting L12 review.

## Pass 3 — dispatch, fleet, and guardrail audit

### Confirmed fleet usage

The work was routed through the repository supervisor/auto-worker mechanism,
not Codex subagents:

- Latest 08:48Z process check shows no Claude2 L12 worker live. OBS is in
  `review` on PR #4364/head `ffd90cab757ee3939cbf7c4e5e5c7956f29f0bdd`, but
  Claude2 is instead running non-L12 `OPS-PROMOTE-PR-CI-TRIGGER-001` as
  `claude2-20260729T084759Z-b06bd849`.
- `L12-VERIFY-RUNTIME-001` is not currently delivered; the latest Claude2
  owner worker was superseded and the row is `todo`.
- `L12-VERIFY-KNOW-001` is not currently delivered; the latest Claude2 owner
  worker failed/preempted and the row is `todo`.
- `Antigravity` completed `L12-VERIFY-LEARN-001` via
  `antigravity1-1-20260729T071100Z-af38b1e4`, but its PR #4358 was rejected as
  fake proof, so the task is blocked rather than accepted.
- `Antigravity` completed prior `L12-VERIFY-OBS-001` attempts; PR #4360 and
  subsequent anchors `65aa15358` and `ac87dc46a` were rejected as
  synthetic/self-attesting proof. PR #4364 is the current candidate and still
  requires exact Claude2 review.
- `L12-FE-TRUTH-001` has no confirmed live worker at the latest check.
- A Codex chair review and a Codex reviewer for
  `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729` were also live; those are
  supervisor auto-workers, not Codex collaboration subagents, but they must not
  be counted as Claude/Antigravity-first L12 delivery.

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
