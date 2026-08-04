# L12-VERIFY-KNOW-001 — knowledge-loop product drill

Owner `Claude2` · Reviewer `Antigravity` · Branch `task/L12-VERIFY-KNOW-001` · Base `dev`

Machine-readable manifest: [`evidence.json`](evidence.json) (digests in
[`evidence.sha256`](evidence.sha256)). Raw run record:
[`drill-run.json`](drill-run.json).

## What this task delivered

`scripts/verify_twelve_loop_knowledge.py` — a repeatable `EP3` drill for loops
1–3. It starts the real `source-ingest`, `registry`, and
`research-orchestrator` services as three independent OS processes on real TCP
ports, then drives the three real loop controllers
(`source-ingestion-controller`, `strategy-distillation-controller`,
`alpha-replication-controller`) across those boundaries and asserts terminal
truth from each authority's own readback API.

```bash
# The drill imports the real service apps in child processes, so the
# interpreter needs their dependencies (fastapi, uvicorn, pydantic,
# jsonschema, asyncpg). A bare system python3 cannot start the services.
<venv>/bin/python scripts/verify_twelve_loop_knowledge.py \
  --run-dir /tmp/l12-verify-know \
  --evidence-out docs/deployment/evidence/twelve-loop-gap/L12-VERIFY-KNOW-001/drill-run.json

# Check this manifest against its archived run record (no services needed).
python3 scripts/verify_twelve_loop_knowledge.py --verify-manifest
```

`--verify-manifest` is the reviewer's integrity gate. The manifest is
hand-authored around a machine-written run record, so the two can drift. It
fails closed on any mismatch in run identity, per-check statuses, the gap set
and its `observed` values, either checksum, or the drill source itself: the run
record carries `script_sha256`, so a manifest whose evidence predates the
script in the tree is rejected rather than silently accepted.

Nothing in the evidence is a seeded fixture standing in for a loop result.
Every `SourceRecord`, `StrategySpec`, seed, and queue entry was produced by a
real controller against a real service. The one bounded input is an
allowlisted `static_records` payload on a single operator-registered
connector, which is what keeps the drill from performing an external crawl.

## Result

**11 of 16 checks pass. 4 fail and 1 is blocked. The loops-1-to-3 chain is not
product-level.** Four consecutive runs of this drill source — each on a clean
worktree at the recorded `script_sha256` — produced identical per-check statuses
and the identical gap set. Runs 1–3 ran at the recorded `git_sha`, and the third
is the archived `drill-run.json`. Run 4 re-ran after `dev` was merged into the
task branch, at `c2be9dcb`, and reproduced the same 11/4/1 split and the same
five gaps; the merge touched `services/bff/assistant` and supervisor tooling,
none of the source-ingest, registry, or research surfaces this drill exercises.
All four correlation ids are listed in `evidence.json` under
`drill.reproducibility`.

| Acceptance criterion | Verdict |
| --- | --- |
| Real Persona requirement produces SourceRecord and one mutable StrategySpec draft | partially proven (artifact path proven; terminal source truth blocked by G2) |
| Approved StrategySpec produces authoritative ExperimentRun | **not proven** (G1, G3) |
| Unapproved spec and immutable approved artifact negative gates pass | partially proven (both gates pass; approved artifact is not durable — G5) |
| Duplicate, concurrency, provider/Registry/research failure, restart | partially proven (loops 1–2 all pass; loop 3 blocked by G1, G3) |
| BFF and controller terminal truth match every authority | partially proven (loops 1–2 pass; loop 3 truth actively misleads — G4) |

### What is proven

- A persona-bound bounded pull commits exactly one normalized `SourceRecord`,
  with ingest-run provenance readable through the service API (`C02`).
- That committed record distills into exactly one **mutable** `StrategySpec`
  draft in the real Registry, with lineage bound to the committed source
  digest, and the job reaches a terminal `done` (`C03`).
- An unapproved draft produces no replication work and no `ExperimentRun`
  anywhere in the research authority (`C04`).
- A replacement controller replica with a fresh durable queue re-delivers the
  same versioned identity and records an immutable skip without rewriting the
  approved artifact (`C06`).
- Three scheduled pulls and three distillation ticks converge on one source
  version, one seed, and one draft (`C07`).
- Two independent OS processes contending for one shared ledger both do work,
  create exactly one draft per admitted job, and leave nothing degraded
  (`C08`).
- A bounded provider outage dead-letters durably and an operator replay
  commits exactly one `SourceRecord` (`C09`).
- A real Registry outage parks a durable retry with stage `registry_sync`, and
  recovery replays to exactly one draft and one committed version (`C10`).
- Restarting all three services plus the controller reloads durable state
  under a fresh runtime identity without duplicating any work (`C12`).
- The `source_ingestion` and `strategy_distillation` controller records
  conform to the shared loop-control contract and project BFF truth that
  matches the authorities' own readbacks (`C13`, `C14`).

### Gaps found

Five reproducible product-level gaps. Each is recorded in `evidence.json` with
its root cause, owning surface, and the check that found it. **None of them was
repaired here**: this task's artifact scope is the drill and its evidence, and
the fixes belong to the lanes that own those surfaces.

| Gap | Severity | Summary |
| --- | --- | --- |
| **G1** | blocking | `alpha_replication` discovery reads `source_id` from the distillation seed store and queries `/api/registry/strategies/{strategy_id}/strategy-specs`. Distillation registers the spec under `strat-<source_id>-<digest12>`, and the seed carries no registry or strategy identity, so the keys can never match. An approved StrategySpec is never admitted as replication desired state. |
| **G3** | blocking | `_queue_payload_from_registry_entry` requires a tenant binding on the approved entry, but the distillation controller never writes one. Even with G1 repaired, every approved distilled spec is rejected at queue admission. |
| **G5** | blocking | `services/registry/storage.RegistryStore` is a process-local in-memory dict, and the deployed registry mounts no volume for entries. Approved, immutable, review-decided StrategySpecs are lost on restart — the authority both the DIST immutability guarantee and the ALPHA approved-only gate rest on. |
| **G2** | high | A persona requirement bound to an operator-managed connector takes the `verified_existing_custom_connector` path, which never writes the `persona_source_reconciliation` desired-state marker. The controller's own terminal readback guard then fails closed forever, so loop 1 can only reach terminal reconciled truth through the two built-in TW live providers. |
| **G4** | high | Because of G1, the alpha controller records a *successful* tick with `approved_spec_count: 0`, zero backlog, and no evidence refs. The projected BFF truth reads "healthy", so an operator cannot see that an approved artifact is stranded. |

### Corroboration outside the drill

- The running `pantheon-alpha-replication-worker-1` container's durable
  controller state shows 23 197 ticks and 22 980 successes with
  `approved_spec_count: 0` and no created `ExperimentRun` ids — exactly the
  shape G1 and G4 predict.
- `docker-compose.yml` points both the distillation controller and the alpha
  replication worker at the same `STRATEGY_SPEC_SEED_STORE_PATH`, so the
  deployed wiring carries the same discovery break the drill reproduces.

## Scope boundaries

- No hosted-runtime, shared-deployment, or live-capital claim is made.
- Loop-control rows are exercised through the real `LoopControllerWriter` SDK
  and the real record validation (JSON Schema plus
  `assert_controller_record_conforms`) with the durable Postgres write
  captured rather than executed, so the drill never mutates shared
  loop-control state. The Postgres-backed BFF read path is owned by
  `L12-TRUTH-001`.
- The drill is safe to re-run: it builds its own isolated data root and its own
  service processes, and touches no shared deployment.

## Re-drilling after repairs

Each failing check is written to pass once its gap is closed, so the same
script is the acceptance gate for the follow-up repairs. `C11`
(research-authority outage) is recorded as **blocked** rather than failed
because it cannot be exercised at all until G1 and G3 are fixed; it will begin
running as soon as an approved spec can reach the replication queue.

A re-cut must replace `drill-run.json`, both checksums, and every run-specific
value in `evidence.json` in the same commit. Run `--verify-manifest` before
committing: it is what catches a manifest left describing an older run.
