# System Architecture — Development Tooling Four-Gap Closure

## 1. Objective

Close the four residual development-tooling gaps with the minimum number of
mechanisms while preserving the currently healthy supervisor/worker path.

Functional success means:

- every changed tooling path selects its applicable CI gate;
- a verified merged delivery is consumed once per exact task generation and
  delivery identity;
- `supervisor.py` and `ai_status.py` become thin composition/CLI boundaries;
- historical evidence is retained or removed by proof, not filename; and
- the V1 archive is either recovered exactly or remains explicitly unavailable
  without contaminating V2 runtime truth.

## 2. Non-goals

- No product feature, twelve-loop, Management, BFF, or frontend change.
- No new security hardening program.
- No second scheduler, queue, TaskStore, merge runner, worker authority, or
  product API bridge.
- No generic task amendment event.
- No parent/sidecar scheduler revival.
- No automatic Human/Ops finalization.
- No synthetic legacy archive.

## 3. Invariants

1. V2 TaskStore is the only canonical development-task state.
2. `ai-status` is the only mutation interface used by tooling callers.
3. Supervisor remains one singleton process and `run_once` remains the sole
   cycle coordinator.
4. Auto-integrator remains the only merge owner.
5. Workers and PR helpers never merge.
6. Runtime `state.json` remains a bounded cache, not delivery truth.
7. Product runtime never imports development tooling.
8. A cleanup wave moves callers and deletes the replaced implementation in the
   same wave; forwarding facades are temporary and have explicit removal gates.

## 4. Target architecture

```text
assistant packet / Human-Ops command
                 |
                 v
       ai-status CLI composition
                 |
      V2 TaskStore canonical state
                 |
                 v
        singleton supervisor cycle
       /        |          |       \
 dispatch   worker IO   recovery   bridge drain
 policy     workspace   lifecycle  existing package
       \        |          |       /
          bounded runtime cache
                 |
                 v
        canonical auto-integrator
                 |
      exact integration receipt
                 |
     owner/Human-Ops finalization
```

The boxes are responsibility boundaries, not additional processes.

## 5. Architecture decisions

### ADR-01 — archive recovery stays offline

The legacy archive has no service, daemon, fallback reader, or startup gate.
Existing `verify_task_state_store.py --verify-archive --archive-path` is the
only verifier. Recovery work produces a custody manifest and verifier output,
not runtime code.

If exact bytes cannot be recovered, the anchor remains immutable and the
offline audit remains red with `historical_archive_unavailable`.

### ADR-02 — CI uses independent touched-domain predicates

`component_boundary.py` remains the one classifier. It gains independent
touched booleans; it does not decide approval or deployment.

```text
development_tooling_touched -> core tooling tests + integration authority tests
product_touched             -> product smoke
delivery_touched            -> delivery-focused tests
mixed diff                  -> union of applicable gates
```

`tooling_only` may remain temporarily for callers, but branch CI must stop
using it as the exclusive selector.

### ADR-03 — integration receipt is canonical task evidence

The receipt is a narrow V2 task field, written by a dedicated `ai-status`
command invoked only by the canonical integrator.

Proposed schema:

```json
{
  "version": 1,
  "result": "merged",
  "task_generation": 4,
  "repository": "ajoe734/pantheon",
  "target_branch": "dev",
  "pr": 5411,
  "head_sha": "254d2e7b05096dad3f6c7512db089ae2cbd8fe08",
  "merge_commit_sha": "8f8383b507b1fb631d44422031f01ebea5024d5e",
  "observed_at": "2026-08-29T23:05:12Z",
  "source": "canonical_auto_integrator"
}
```

The governed command validates its arguments against the current delivery
binding before appending the TaskStore transition. Candidate discovery treats
only an exact receipt match as consumed. A stale receipt is evidence, not an
eligibility veto.

The auto-integrator lock remains outermost; the status command acquires the
existing task-state/audit locks in their normal order. No runtime-state lock is
introduced.

### ADR-04 — extract to owners, not to facades

The two monoliths retain their public entrypoints, argument parsing, dependency
construction, and cycle ordering. Pure policy and domain behavior move into the
existing owner modules listed in GAP_AUDIT.md.

Permitted new modules are narrow physical boundaries where no owner exists,
for example a worker-workspace filesystem module or status projection renderer.
Names such as `Manager`, `Facade`, `Utils`, `Common2`, or generic registries are
not acceptable extraction targets.

### ADR-05 — historical evidence is not executable architecture

Historical briefs do not count as a second scheduler. They remain tracked when
their paths are part of review, delivery, task archive, or incident evidence.
New task creation must not use retired sidecar task classes, but old evidence is
not rewritten merely to make the tree look smaller.

## 6. Ownership after decomposition

| Concern | Target owner | Entry-point responsibility left behind |
|---|---|---|
| cycle order and singleton | `supervisor.py` | parse config, acquire singleton, invoke ordered phases |
| pure dispatch decision | existing `dispatch_policy.py` / `rewrite/dispatch_admission.py` | provide snapshots and execute returned plan |
| worker process state | existing lifecycle/recovery modules | perform bounded external IO requested by transition |
| worktree filesystem operations | narrow `worker_workspace.py` | call operation and record result |
| provider probe projection | existing provider-health owner | schedule probes and persist returned bounded facts |
| bridge admission/materialization | existing `development_bridge/` | schedule drain only |
| status projection files | narrow projection module | invoke after canonical commit |
| task lifecycle commands | existing task machine plus command handlers | CLI routing and transaction boundary |
| delivery/review evidence validation | existing rewrite contract plus Git review gate | external probe orchestration |
| merge | `scripts/git/auto_integrator.py` | unchanged sole owner |

## 7. Failure semantics

| Failure | Required outcome |
|---|---|
| archive candidate absent | offline audit unavailable; supervisor stays healthy |
| archive digest/size mismatch | reject candidate; do not alter anchor |
| CI classifier cannot classify changed path | report unknown and run conservative applicable smoke; never silently skip tooling for a tooling path |
| integration receipt write fails after merge | task remains review_approved; next run safely reverifies and retries receipt |
| receipt exists but identity differs | ignore receipt and evaluate current delivery |
| receipt exists and target no longer contains merge commit | fail closed and report integrity drift |
| extracted module raises | supervisor preserves existing phase error/recovery semantics |
| evidence file has unresolved references | KEEP; do not delete |

## 8. Acceptance architecture

Closure requires all of the following:

- independent CI predicates have characterization tests for tooling-only,
  product-only, delivery-only, mixed, docs-only, and unknown paths;
- the full tooling authority suite is a required check for any tooling-touched
  diff;
- an exact merged receipt suppresses repeated GitHub evaluation while leaving
  owner/Human-Ops finalization untouched;
- receipt invalidation tests cover generation, PR, branch, repository, head,
  and merge commit changes;
- one supervisor process, one auto-integrator lock, and one V2 store remain;
- extracted modules contain the moved implementation and callers no longer
  route through duplicate wrappers;
- container/runtime launch and command-root tests pass unchanged;
- evidence inventory gives every candidate KEEP, MIGRATE, REMOVE, or VERIFY
  with machine-readable reasons; and
- archive verification either passes exact bytes or remains honestly
  unavailable with an approved data-loss record.
