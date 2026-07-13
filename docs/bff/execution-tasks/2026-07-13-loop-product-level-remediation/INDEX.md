# Loop Product-Level Remediation Execution Packet — 2026-07-13

Status: ready for fleet dispatch after merge

Primary planning baseline:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

Machine task catalog: [tasks.json](tasks.json)

Dispatcher:
`scripts/dispatch_loop_product_level_remediation_2026-07-13.py`

Dispatcher tests:
`scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py`

## Product contract

This packet contains 36 primary execution tasks. It is a build-and-proof DAG,
not a checklist that can be closed from component tests. The program remains
active until the twelve canonical L1 loops plus the Per-Persona OODA composite
overlay have default runtime ownership, real canonical effects or explicit
terminal failure, restart-safe recovery, authoritative operator truth, and the
hosted product evidence required by the master plan.

Only `done` satisfies a dependency. A task that is blocked, cancelled,
superseded, submitted, merged-but-not-deployed, registry-only, fixture-only, or
missing its terminal downstream readback does not open the next frontier.

## Audited baseline

The 2026-07-13 audit established these starting facts:

- 37 archived `LOOP-AUTO-*` tasks were done, while live loop health exposed
  zero controller records, zero live loops, and zero reconciled loops.
- Eleven canonical rows were still reported `api-only`; Capital Pool
  Execution was `manual`.
- deployment consumed receipts without a real apply callback, paper execution
  lacked a first-class signal producer, teaching could pass on stub data,
  consultation could manufacture a successful memo, evolution could manufacture
  `SUBMITTED`, and loop-run/Journey truth depended on manual/backfill paths.
- the hosted execute-plans release used unsafe write flags; a failed deploy
  switched the live symlink before probes and did not roll back.
- the BFF did not expose an exact git SHA/image identity, hosted auth still
  admitted a broad development bearer posture, and several frontend features
  were not on the exact deployed branch.
- Strategy Workshop still had six intentionally fail-closed 501 operations.
  Honest unavailability is preferable to a fake success, but it is not product
  completion.

Every task must re-audit current `origin/dev` before editing because active
PPL, TJ, PINT, EVOCHAIN, SSE, and reconciliation work may advance after this
baseline.

## Program gates

| Gate | Fail-closed admission |
| --- | --- |
| G0 | 12 canonical loops + OODA overlay, unique IDs, valid one-repo routing, acyclic dependencies, explicit existing-task convergence |
| G1 | strict scoped dev auth, no browser credential, safe writes false, exact-SHA gate-before-deploy, candidate probe, rollback, FE/BFF build identities |
| G2 | default deployment owner, durable trigger, real canonical effect, terminal target readback |
| G3 | duplicate/lease/timeout/DLQ/replay and worker/BFF/DB/full-stack recovery; controller truth, not registry metadata |
| G4 | Knowledge, Execution, Human Interaction, and Management Repair target-dev paths |
| G5 | authenticated desktop/mobile, strict performance, accessibility, SSE recovery, degraded/error, RBAC/tenant/MFA/two-person matrix |
| G6 | checksummed machine evidence, exact PR/merge/deploy identities, independent review, evidence-derived maturity, zero blocking risk |

## Primary DAG

### Wave 0 — Safety, identity, truth, and evidence enforcement

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-000](LOOP-PROD-000.md) | Codex / Codex2 | `pantheon` | none | Canonical loop inventory and OODA overlay truth |
| [LOOP-PROD-001](LOOP-PROD-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-000` | Durable controller truth substrate |
| [LOOP-PROD-002](LOOP-PROD-002.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-000`<br>`LOOP-PROD-001` | Product evidence schema and anti-false-close gate |
| [LOOP-PROD-AUTH-001](LOOP-PROD-AUTH-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-002` | Strict dev auth cutover and exact BFF build identity |
| [LOOP-PROD-FE-001](LOOP-PROD-FE-001.md) | Codex / Codex2 | `execute-plans` | `LOOP-PROD-002`<br>`LOOP-PROD-AUTH-001` | Gate-before-deploy safe execute-plans release |
| [LOOP-PROD-REC-001](LOOP-PROD-REC-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002` | Full-stack loop recovery and fault-injection harness |

### Wave 1 — Canonical loop owners and real side effects

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-SRC-001](LOOP-PROD-SRC-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-AUTH-001` | Source requirement reconciler and default scheduler |
| [LOOP-PROD-DIST-001](LOOP-PROD-DIST-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-SRC-001` | Durable Strategy Distillation event consumer |
| [LOOP-PROD-ALPHA-001](LOOP-PROD-ALPHA-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-DIST-001` | Durable Alpha Replication and revalidation worker |
| [LOOP-PROD-TEACH-001](LOOP-PROD-TEACH-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-SRC-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-ALPHA-001` | Fail-closed Persona Teaching on authoritative data |
| [LOOP-PROD-AGORA-001](LOOP-PROD-AGORA-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-TEACH-001`<br>`LOOP-PROD-AUTH-001`<br>`AG-GAP-014` | Durable Agora evidence, dataset, and handoff worker |
| [LOOP-PROD-CONS-001](LOOP-PROD-CONS-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-AGORA-001` | Real-participant Consultation workflow |
| [LOOP-PROD-AGORA-002](LOOP-PROD-AGORA-002.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-CONS-001`<br>`LOOP-PROD-ALPHA-001`<br>`AG-GAP-005` | Implement six deferred Strategy Workshop operations |
| [LOOP-PROD-AGORA-003](LOOP-PROD-AGORA-003.md) | Codex2 / Codex | `execute-plans` | `LOOP-PROD-AGORA-002`<br>`LOOP-PROD-FE-001`<br>`AG-GAP-013`<br>`AG-GAP-014`<br>`OPS-EP-DEV-MAIN-RECONCILE-001` | Hosted Strategy Workshop generated client and actions |
| [LOOP-PROD-IMIT-001](LOOP-PROD-IMIT-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-AGORA-001`<br>`LOOP-PROD-TEACH-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-CONS-001`<br>`LOOP-PROD-AGORA-002` | Default Human Imitation and shadow evaluation chain |
| [LOOP-PROD-DEP-001](LOOP-PROD-DEP-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-IMIT-001` | Canonical deployment dispatcher and RuntimeBinding readback |
| [LOOP-PROD-CAP-001](LOOP-PROD-CAP-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-DEP-001`<br>`LOOP-PROD-REC-001`<br>`TJ-E2E-014` | First-class bounded paper DecisionSignalProducer |
| [LOOP-PROD-TEL-001](LOOP-PROD-TEL-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-CAP-001` | Default telemetry reconciliation and incident chain |
| [LOOP-PROD-TEL-002](LOOP-PROD-TEL-002.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-CAP-001`<br>`LOOP-PROD-TEL-001`<br>`TJ-E2E-014` | Canonical loop-run and Trade Journey lifecycle projector |
| [LOOP-PROD-EVO-001](LOOP-PROD-EVO-001.md) | Codex2 / Codex | `pantheon` | `EVOCHAIN-011`<br>`LOOP-PROD-DEP-001`<br>`LOOP-PROD-TEL-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-TEL-002` | Real Evolution target-plane dispatcher |
| [LOOP-PROD-BFF-001](LOOP-PROD-BFF-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-TEL-001`<br>`LOOP-PROD-AUTH-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-EVO-001` | Authoritative BFF health monitoring and loop-health projection |
| [LOOP-PROD-OODA-001](LOOP-PROD-OODA-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-000`<br>`LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`OPENCLAW-CRON-WRITE-SCOPE`<br>`OPENCLAW-PERSONA-CRON-BACKFILL`<br>`OPENCLAW-OODA-PACKET-CLOSURE`<br>`LOOP-PROD-BFF-001` | Per-Persona OODA schedule reconciliation and product proof |

### Wave 2 — Cross-loop product paths and target-dev verifiers

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-PER-001](LOOP-PROD-PER-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-DEP-001`<br>`LOOP-PROD-CAP-001`<br>`LOOP-PROD-OODA-001`<br>`PPL-ALLOC-010`<br>`PPL-ALLOC-011` | Persona provisioning through binding and first-evaluation readback |
| [LOOP-PROD-TJ-001](LOOP-PROD-TJ-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-DEP-001`<br>`LOOP-PROD-CAP-001`<br>`LOOP-PROD-TEL-002`<br>`LOOP-PROD-EVO-001`<br>`LOOP-PROD-AUTH-001`<br>`TJ-E2E-014`<br>`LOOP-PROD-PER-001` | Canonical Trade Journey governed action backend |
| [LOOP-PROD-TJ-002](LOOP-PROD-TJ-002.md) | Codex / Codex2 | `execute-plans` | `LOOP-PROD-TJ-001`<br>`LOOP-PROD-FE-001`<br>`MGMT-SSE-001`<br>`OPS-EP-DEV-MAIN-RECONCILE-001`<br>`LOOP-PROD-AGORA-003` | Hosted Trade Journey action controls |
| [LOOP-PROD-MAI-001](LOOP-PROD-MAI-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-AUTH-001`<br>`LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-TJ-001` | Hosted Management AI repair and dev-bridge backend proof |
| [LOOP-PROD-MAI-002](LOOP-PROD-MAI-002.md) | Codex / Codex2 | `execute-plans` | `LOOP-PROD-MAI-001`<br>`LOOP-PROD-FE-001`<br>`MGMT-SSE-001`<br>`OPS-EP-DEV-MAIN-RECONCILE-001`<br>`LOOP-PROD-TJ-002` | Hosted Management AI repair product UI |
| [LOOP-PROD-VERIFY-KNOW-001](LOOP-PROD-VERIFY-KNOW-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-SRC-001`<br>`LOOP-PROD-DIST-001`<br>`LOOP-PROD-ALPHA-001`<br>`LOOP-PROD-TEACH-001`<br>`LOOP-PROD-AGORA-001`<br>`LOOP-PROD-AGORA-002`<br>`LOOP-PROD-AGORA-003`<br>`LOOP-PROD-CONS-001`<br>`LOOP-PROD-IMIT-001`<br>`LOOP-PROD-BFF-001` | Target-dev Knowledge spine product verifier |
| [LOOP-PROD-VERIFY-EXEC-001](LOOP-PROD-VERIFY-EXEC-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-PER-001`<br>`LOOP-PROD-DEP-001`<br>`LOOP-PROD-CAP-001`<br>`LOOP-PROD-TEL-001`<br>`LOOP-PROD-TEL-002`<br>`LOOP-PROD-EVO-001`<br>`LOOP-PROD-BFF-001`<br>`PPL-ALLOC-012` | Target-dev Execution spine product verifier |
| [LOOP-PROD-VERIFY-HUMAN-001](LOOP-PROD-VERIFY-HUMAN-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-TEACH-001`<br>`LOOP-PROD-AGORA-001`<br>`LOOP-PROD-AGORA-003`<br>`LOOP-PROD-CONS-001`<br>`LOOP-PROD-IMIT-001`<br>`PINT-010-R2` | Target-dev Human interaction and learning verifier |
| [LOOP-PROD-VERIFY-OODA-001](LOOP-PROD-VERIFY-OODA-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-OODA-001`<br>`LOOP-PROD-VERIFY-KNOW-001`<br>`LOOP-PROD-VERIFY-EXEC-001`<br>`LOOP-PROD-MAI-001` | Multi-persona OODA overlay product verifier |

### Wave 3 — Existing product-program convergence

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-PPL-001](LOOP-PROD-PPL-001.md) | Codex2 / Codex | `pantheon` | `PPL-ALLOC-009`<br>`PPL-ALLOC-010`<br>`PPL-ALLOC-011`<br>`PPL-ALLOC-012`<br>`PPL-ALLOC-013`<br>`LOOP-PROD-PER-001`<br>`LOOP-PROD-VERIFY-EXEC-001`<br>`LOOP-PROD-FE-001` | Persona promotion and allocation product closeout |
| [LOOP-PROD-TJ-003](LOOP-PROD-TJ-003.md) | Codex / Codex2 | `pantheon` | `TJ-E2E-012`<br>`TJ-E2E-014`<br>`LOOP-PROD-TJ-001`<br>`LOOP-PROD-TJ-002`<br>`LOOP-PROD-VERIFY-EXEC-001` | Trade Journey superseding product closeout |
| [LOOP-PROD-PINT-001](LOOP-PROD-PINT-001.md) | Codex2 / Codex | `pantheon` | `OPS-EP-DEV-MAIN-RECONCILE-001`<br>`PINT-010-R2`<br>`LOOP-PROD-AGORA-003`<br>`LOOP-PROD-VERIFY-HUMAN-001`<br>`LOOP-PROD-FE-001` | Persona Interaction reconciled hosted product closeout |
| [LOOP-PROD-MAI-003](LOOP-PROD-MAI-003.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-MAI-001`<br>`LOOP-PROD-MAI-002`<br>`LOOP-PROD-VERIFY-OODA-001` | Management AI/OpenClaw product closeout |

### Wave 4 — Global product closeout

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-CLOSE-001](LOOP-PROD-CLOSE-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-002`<br>`LOOP-PROD-AUTH-001`<br>`LOOP-PROD-FE-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-VERIFY-KNOW-001`<br>`LOOP-PROD-VERIFY-EXEC-001`<br>`LOOP-PROD-VERIFY-HUMAN-001`<br>`LOOP-PROD-VERIFY-OODA-001`<br>`LOOP-PROD-PPL-001`<br>`LOOP-PROD-TJ-003`<br>`LOOP-PROD-PINT-001`<br>`LOOP-PROD-MAI-003` | Global 12-loop plus OODA product closeout |

Wave 1 deliberately carries additional delivery dependencies between tasks
that share the root `docker-compose.yml` integration surface. Those edges
serialize canonical deployment ownership while unrelated execute-plans and
contract work can still proceed in parallel; they are not claims that one
domain algorithm semantically depends on another.

## Existing work consumed, not recreated

The catalog validates these external dependency IDs against active supervisor
state or the archive at live dispatch time:

- `AG-GAP-005`
- `AG-GAP-013`
- `AG-GAP-014`
- `EVOCHAIN-011`
- `MGMT-SSE-001`
- `OPENCLAW-CRON-WRITE-SCOPE`
- `OPENCLAW-PERSONA-CRON-BACKFILL`
- `OPENCLAW-OODA-PACKET-CLOSURE`
- `OPS-EP-DEV-MAIN-RECONCILE-001`
- `PINT-010-R2`
- `PPL-ALLOC-009`
- `PPL-ALLOC-010`
- `PPL-ALLOC-011`
- `PPL-ALLOC-012`
- `PPL-ALLOC-013`
- `TJ-E2E-012`
- `TJ-E2E-014`

In particular:

- `PPL-ALLOC-009..013` converge through `LOOP-PROD-PPL-001`.
- `TJ-E2E-012` remains historical closeout evidence; `TJ-E2E-014` and the
  new producer/projector/action work converge through `LOOP-PROD-TJ-003`.
- `OPS-EP-DEV-MAIN-RECONCILE-001` and `PINT-010-R2` remain the sole PINT
  implementation convergence path.
- `EVOCHAIN-011` must complete before the new target-plane evolution
  dispatcher can start.
- completed OpenClaw cron/write-scope/packet tasks are substrate for the OODA
  overlay task, not product-level restart and controller-health proof.
- `AG-GAP-005` preserved the six unavailable operations honestly; the new
  Agora tasks implement them. `AG-GAP-013/014` evidence is consumed rather
  than re-dispatched.

## Repository routing

Every task owns exactly one repository:

- Pantheon artifacts are ordinary repo-relative paths.
- execute-plans artifacts use the exact `execute-plans/` slash prefix and
  merge to execute-plans `dev`.
- no task mixes Pantheon and execute-plans artifacts. Backend, frontend, and
  evidence-aggregation closeout are separate tasks.

Do not use the legacy `execute-plans:` colon form; it routes completion to
the wrong repository and can create phantom-done evidence.

## Dispatch safety

The dispatcher must be merged before live use. It:

- validates every required field, task document, owner/reviewer, loop metadata,
  one-repo routing, dependency existence, and DAG acyclicity;
- rejects a frozen planning wave;
- serializes dispatcher instances with an exclusive lock;
- checks both active and archived task IDs;
- never resurrects an archived terminal ID;
- preserves an existing active task record in full;
- performs an atomic fsync + replace of `ai-status.json`;
- appends assignment events only for newly inserted tasks;
- supports catalog validation and mutation-free dry run.

Validate locally:

```sh
python3 scripts/dispatch_loop_product_level_remediation_2026-07-13.py --validate-only
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/dispatch_loop_product_level_remediation_2026-07-13.py --dry-run

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py
```

After this PR is merged, dispatch once into the canonical live status root and
refresh the generated supervisor views:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/dispatch_loop_product_level_remediation_2026-07-13.py

PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/ai_status.py sync
```

Do not bulk-materialize this DAG through the Management AI DevTaskPacket bridge
until its multi-value delimiter and partial-failure replay defects are fixed.

## Universal closeout rule

Every primary task must archive a redacted, append-only, checksummed
`evidence.json` and the reviewer must set `review_file` to it. Evidence must
identify the exact branch, PR, required checks, merge SHA, deployment SHA/image,
request/receipt/post-state correlation, duplicate/recovery/rollback/security
results, and residual risks. User-visible tasks additionally require hosted
1440px desktop and 390px mobile DOM/network evidence, axe serious/critical
zero, keyboard/focus/reduced-motion, `FE_INT_GATE_PERF_STRICT=1`, and no
unexpected console, CORS, chunk, or BFF errors.

The final `LOOP-PROD-CLOSE-001` task cannot close without an independent
Human/Ops verdict and zero unresolved blocking product risk.
