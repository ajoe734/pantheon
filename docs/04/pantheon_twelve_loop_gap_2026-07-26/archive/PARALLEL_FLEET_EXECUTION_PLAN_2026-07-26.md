# Parallel Fleet Execution Plan

Program: `pantheon-twelve-loop-gap-2026-07-26`

Goal: maximize useful parallel work without assigning two workers overlapping
artifacts or allowing integration files to become merge-conflict bottlenecks.

## Capacity gate

The repository declares four Codex and four Codex2 worker slots. The current
running supervisor is healthy and idle, but the on-disk runtime config would
disable those lanes on restart. `L12-FLEET-001` must prove the reviewed repo
policy is provisioned and loaded before the eight-lane frontier is considered
available.

Do not restart or dispatch while a Pantheon nonprod deploy owns the dev
deployment lease. Wait for terminal deploy state, then recheck supervisor,
watchdog, queue, approvals, provider readiness, and exact loaded config.

## Artifact isolation strategy

Domain tasks do not claim `docker-compose.yml`,
`docs/deployment/loop-catalog.registry.json`, or final BFF/catalog integration
files. These shared surfaces are owned by later serial tasks:

- `L12-MANIFEST-001`: Compose/default-runtime activation;
- `L12-TRUTH-001`: BFF controller/catalog truth;
- `L12-CLOSE-001`: final evidence admission and maturity promotion.

Tasks sharing a service directory are explicitly ordered. Source and
Distillation are one serial lane. Controller/BFF monitor work is ordered.
Frontend work is a separate `execute-plans` repository task.

The pre-existing `PPL-ALLOC-009` remains blocked on Human/Ops and owns broad
Pantheon BFF and execute-plans source scopes. It is an explicit external
dependency of Controller, Agora, BFF Health, protected Signoff, Truth, and
Frontend Truth. Those tasks must not start until it is `done`; the dispatcher
rejects any other live overlap it discovers.

## Wave graph

### Wave 0 — program and capacity bootstrap

Executed by this planning delivery:

- merge the three-pass inventory, task catalog, dispatcher, tests, and fleet
  capacity drift guard;
- deploy/sync the exact merge;
- `L12-FLEET-001` verifies eight Codex-family slots and guarded dispatcher
  dry-run.

### Wave 1 — eight-slot foundation frontier

| Slot | Task | Owner | Reviewer | Scope |
| --- | --- | --- | --- | --- |
| 1 | `L12-CTRL-001` | Codex | Codex2 | loop-control and controller truth |
| 2 | `L12-TEL-001` | Codex2 | Codex | telemetry durability/identity |
| 3 | `L12-REC-001` | Codex | Codex2 | reconciliation durability/timeout |
| 4 | `L12-SRC-001` | Codex2 | Codex | source controller |
| 5 | `L12-ALPHA-001` | Codex | Codex2 | alpha replication |
| 6 | `L12-AGORA-001` | Codex2 | Codex | Agora extraction |
| 7 | `L12-CONS-001` | Codex | Codex2 | Consultation executor |
| 8 | `L12-DEP-001` | Codex2 | Codex | Deployment dispatcher |

`L12-FLEET-001` is a short prerequisite/runtime verification and does not
consume a long implementation lane after it closes.

While `PPL-ALLOC-009` remains blocked, Controller and Agora remain
dependency-blocked. Telemetry, Reconciliation, Source, Alpha, Consultation,
Deployment, and the Wave-2 Teaching lane provide seven immediately useful
implementation lanes; the eighth slot is reserved for reviews/finalization.
When the Human/Ops gate closes, Controller and Agora enter the frontier without
overlapping the prior owner.

### Wave 2 — seven parallel domain closures

| Task | Dependency reason |
| --- | --- |
| `L12-DIST-001` | follows Source because both own `services/source_ingestion` |
| `L12-TEACH-001` | independent training-session lane |
| `L12-IMIT-001` | independent policy-learning lane |
| `L12-CAP-001` | independent execution/capital lane |
| `L12-EVO-001` | consumes repaired telemetry contract |
| `L12-BFF-001` | consumes controller and telemetry foundations |
| `L12-SIGNOFF-001` | installs protected Human/Ops transition authority |

The remaining eighth slot is available for review/finalization. Closeout
cannot become ready merely from task metadata; it has a direct dependency on
the merged and active signoff guard.

### Wave 3 — serial shared integration

1. `L12-MANIFEST-001` activates required workers with restart/health and safe
   defaults in one Compose change.
2. `L12-TRUTH-001` integrates all controller records, BFF truth, and
   non-promoted catalog metadata.
3. `L12-FE-TRUTH-001` updates and deploys the separate `execute-plans`
   frontend against the merged BFF contract.

### Wave 4 — four parallel product drills

- `L12-VERIFY-KNOW-001`: Source, Distillation, Alpha.
- `L12-VERIFY-LEARN-001`: Teaching, Agora, Imitation, Consultation.
- `L12-VERIFY-RUNTIME-001`: Deployment and governed-paper Capital.
- `L12-VERIFY-OBS-001`: Telemetry, Reconciliation, Evolution, BFF Health.

Each drill owns a separate evidence directory and verifier script.

### Wave 5 — hosted and closeout

1. `L12-HOSTED-001` deploys the exact merged candidates, verifies FE/BFF
   identity, runs the all-service restart drill and the two cross-loop positive
   chains.
2. `L12-CLOSE-001` replays every evidence manifest, obtains final formal
   review and a protected Human/Ops verdict, and promotes only proved maturity.

## Dispatch mechanism

Use `scripts/dispatch_twelve_loop_gap_2026_07_26.py`:

1. `--validate-only` validates exact fields, DAG, repositories, task docs,
   dependencies, and artifact overlap ordering.
2. `--dry-run` projects the same authoritative task-state journal used by the
   supervisor and rejects ID/contract/live-scope collisions without mutation.
3. `--apply` runs the clean, merged, SHA-pinned `ai_status.py` from the
   installed supervisor command root, using create-only assignment and
   authoritative journal readback after every task.
4. Rerun is idempotent: exact materializations are skipped; conflicting active
   or archived IDs fail closed.

Every materialized task carries an immutable catalog-bound artifact-conflict
guard. `ai_status assign` evaluates that guard under the same canonical task
lock as the write, so a later bridge/operator assignment cannot race in an
undeclared overlapping scope. Only catalog dependency-ordered overlaps and the
explicit `PPL-ALLOC-009` external owner are admitted.

Do not bulk-dispatch the DAG through the assistant DevTaskPacket bridge. Its
task model drops program fields and the repository already records a bulk
delimiter/partial-replay prohibition.

## Fleet monitoring

For every frontier:

- supervisor/watchdog health remains current;
- no provider is silently disabled;
- each running task has one clean worktree, expected branch and declared scope;
- no two workers share a task;
- tasks advance `todo → in_progress → review → review_approved → done`;
- dependency activation occurs only after exact `done`;
- every task produces a PR/check/merge and evidence manifest;
- stalled, quota, auth, review, and deployment blockers create visible status
  and are repaired/reassigned without silently narrowing scope.

## Anti-overload rules

- no task receives a blanket `.` artifact scope;
- no parallel siblings touch the same service directory;
- no domain worker edits shared Compose/catalog files;
- no reviewer equals owner;
- no additional sidecar is spawned when the review/finalization queue consumes
  all healthy reviewer capacity;
- hosted restart tests are serialized under the dev deployment lease;
- no live broker or live capital is enabled.
