# Twelve-Loop Gap Remediation Execution Tasks

Program ID: `pantheon-twelve-loop-gap-2026-07-26`

Canonical catalog: `tasks.json`

Proof-production ownership overlay: `proof-ownership.json`

Fleet assignment correction: `assignment-revision-1.json`

Source plan:
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/TWELVE_LOOP_GAP_INVENTORY_2026-07-26.md`

Planning addenda:

- `ROUND1_SPEC_RUNTIME_AUDIT.md`
- `ROUND2_IMPLEMENTATION_FAILURE_AUDIT.md`
- `ROUND3_ACCEPTANCE_EVIDENCE_AUDIT.md`
- `PARALLEL_FLEET_EXECUTION_PLAN_2026-07-26.md`

## Delivery contract

Every task must use a clean task worktree, push a unique branch, open a PR to
`dev`, pass required checks, receive review from the distinct assigned
reviewer, merge, deploy when applicable, and archive a checksummed
product-evidence manifest.

The exact catalog is authoritative. Per-task Markdown is a human-readable
mirror and cannot silently narrow catalog acceptance.

`proof-ownership.json` does not remove a catalog proof obligation or change
the immutable catalog digest. It may assign production of a proof to a
dependency-ordered descendant when the proof requires activation or hosted
verification that cannot exist before the source domain task completes.
Every delegation is bound to the exact base catalog, validated against the
DAG, attached to the active source task by a Human/Ops-only governed command,
and must be witnessed by its declared downstream task before program
closeout.

Materialization attaches an immutable catalog SHA and artifact-conflict guard
to every task. The canonical `assign` transition checks both incoming and
already-protected scopes under its task-state lock; undeclared later overlap is
rejected even if it races the batch after dry-run.

## Frontiers

| Wave | Parallel frontier |
| --- | --- |
| 0 | fleet capacity and guarded dispatch verification |
| 1 | controller, telemetry, reconciliation, source, Alpha, Agora, Consultation, Deployment |
| 2 | Distillation, Teaching, Imitation, Capital, Evolution, BFF Health, protected signoff |
| 3 | Compose activation, controller/BFF truth, separate frontend truth |
| 4 | four disjoint product drill packets |
| 5 | current hosted all-loop drill and unique closeout |

Assignment revision 1 makes Antigravity and Claude the implementation owners
for every unfinished task. The live primary capacity is four Antigravity lanes
plus the single shared Claude account lane. Codex-family workers are limited to
the inherited independent reviews whose existing verdicts must remain valid;
they are not unfinished-task owners. Antigravity2 remains fail-closed until its
live authentication probe succeeds.

The archived parallel plan records the initial Codex-family allocation and is
historical. `assignment-revision-1.json` is the reviewed successor record; it
binds the old and new catalog digests, preserves completed task contracts and
dynamic task state, and lists every corrected unfinished assignment.

## Task index

| Task | Loop(s) | Wave | Lane |
| --- | --- | ---: | --- |
| `L12-FLEET-001` | all/program | 0 | fleet-capacity |
| `L12-CTRL-001` | all | 1 | loop-controller |
| `L12-TEL-001` | telemetry/reconciliation | 1 | telemetry |
| `L12-REC-001` | telemetry/reconciliation | 1 | reconciliation |
| `L12-SRC-001` | source ingestion | 1 | source |
| `L12-ALPHA-001` | alpha replication | 1 | alpha |
| `L12-AGORA-001` | Agora evidence | 1 | agora |
| `L12-CONS-001` | consultation | 1 | consultation |
| `L12-DEP-001` | promotion/deployment | 1 | deployment |
| `L12-DIST-001` | strategy distillation | 2 | source |
| `L12-TEACH-001` | persona teaching | 2 | teaching |
| `L12-IMIT-001` | imitation/shadow | 2 | imitation |
| `L12-CAP-001` | capital execution | 2 | capital |
| `L12-EVO-001` | evolution | 2 | evolution |
| `L12-BFF-001` | BFF health | 2 | bff-health |
| `L12-SIGNOFF-001` | all/program | 2 | protected-human-ops-signoff |
| `L12-MANIFEST-001` | all | 3 | runtime-manifest |
| `L12-TRUTH-001` | all | 3 | operator-truth |
| `L12-FE-TRUTH-001` | all | 3 | frontend-truth |
| `L12-VERIFY-KNOW-001` | 1–3 | 4 | verify-knowledge |
| `L12-VERIFY-LEARN-001` | 4–7 | 4 | verify-learning |
| `L12-VERIFY-RUNTIME-001` | 8–9 | 4 | verify-runtime |
| `L12-VERIFY-OBS-001` | 10–12 | 4 | verify-observability |
| `L12-HOSTED-001` | all | 5 | hosted-drill |
| `L12-CLOSE-001` | all | 5 | program-closeout |

## Dispatch

Before materialization:

```bash
python3 scripts/dispatch_twelve_loop_gap_2026_07_26.py --validate-only

/home/lupin/pantheon/.venv/bin/python -m pytest -q -p no:cacheprovider \
  scripts/test_dispatch_twelve_loop_gap_2026_07_26.py

PANTHEON_STATUS_ROOT=/home/lupin/pantheon \
python3 scripts/dispatch_twelve_loop_gap_2026_07_26.py \
  --live-config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  --dry-run
```

After plan merge, terminal dev deploy, preliminary supervisor/provider
readiness, independent dispatcher review, and `sync-dev-root.sh` installation,
run from the exact clean installed root. Replace `<INSTALLED_DEV_ROOT_SHA>` with
the full verified SHA printed by `git rev-parse HEAD`; do not use a
task-worktree SHA. `L12-FLEET-001` then records the formal post-materialization
capacity proof:

```bash
cd /home/lupin/pantheon-ci-deploy/dev-root
PANTHEON_STATUS_ROOT=/home/lupin/pantheon \
AI_NAME=Human/Ops \
python3 scripts/dispatch_twelve_loop_gap_2026_07_26.py \
  --live-config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json \
  --command-root /home/lupin/pantheon-ci-deploy/dev-root \
  --command-sha <INSTALLED_DEV_ROOT_SHA> \
  --apply
```

Do not bulk-materialize this DAG through the Management AI DevTaskPacket
bridge. That model omits required loop/maturity/authority/evidence fields and
the repository records unresolved bulk partial-replay defects.

## Universal acceptance

- real non-seed desired state reaches authoritative terminal actual state;
- duplicate and concurrent execution are safe;
- retry, backoff, DLQ and replay are durable;
- worker, dependency, database and stack restart behavior is proved;
- failure/degraded state cannot appear green;
- RBAC, tenant, MFA, approval, environment and no-live-capital boundaries pass;
- BFF/controller truth identifies provenance and exact deployment;
- evidence is schema-valid, checksummed and formally reviewed.
- final closeout consumes a protected, non-replayable Human/Ops verdict bound
  to exact catalog, manifest and deployment identities.

## Closeout

`L12-CLOSE-001` is the only program sink and directly depends on the installed
`L12-SIGNOFF-001` guard. It cannot close on local tests, queued/submitted
receipts, old-host evidence, registry metadata, mutable signoff metadata, or a
controller process without current authoritative readback.
