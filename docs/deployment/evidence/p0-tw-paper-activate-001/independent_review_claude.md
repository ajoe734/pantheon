# P0-TW-PAPER-ACTIVATE-001 — Independent Reviewer Verdict (Claude)

Reviewer: Claude · Owner: Antigravity · Date: 2026-07-26
Verdict: **REOPEN — acceptance not met**

Reviewed commits:

- pantheon `9133106cf` (`main.py` perf_delta + Python regression test)
- pantheon `e59641425` (Track C go/no-go packet)
- pantheon `7a57c4a6f` (evidence manifest)
- execute-plans `a3c1c29c` (`personaFleetLinks.ts` link gate)

## Reproduction command used for live verification

```bash
/home/lupin/pantheon/.venv/bin/python -c "
import sys, os, json
sys.path.insert(0, os.path.abspath('services/control-plane/bff'))
import main
p = main._persona_fleet_slim_list_payload(
    snapshot_at='2026-07-26T00:00:00Z', state=None, health=None,
    deployment_stage=None, market_scope=None, q=None,
    page_token=None, page_size=50)
for r in p['data']['items']:
    print(json.dumps({k: r.get(k) for k in
        ('persona_id','runtime_id','runtime_binding_id','perf_delta','perfDelta','mode')}))
"
```

Output on the reviewed worktree (post-fix):

```
{"persona_id": "persona-crypto",    "runtime_id": "runtime-crypto-paper",    "runtime_binding_id": "runtime-crypto-paper",    "perf_delta": null, "perfDelta": null, "mode": "paper"}
{"persona_id": "persona-us-equity", "runtime_id": "runtime-us-equity-paper", "runtime_binding_id": "runtime-us-equity-paper", "perf_delta": null, "perfDelta": null, "mode": "paper"}
{"persona_id": "persona-tw-equity", "runtime_id": "runtime-tw-equity-paper", "runtime_binding_id": "runtime-tw-equity-paper", "perf_delta": null, "perfDelta": null, "mode": "paper"}
```

Owner's Python test does pass, but only with the repo venv
(`/home/lupin/pantheon/.venv/bin/python`); bare `python3` has no `fastapi`.

## Blocking findings

### R1 — The reported operator symptom is NOT fixed (acceptance 1, brief A2)

`personaFleetPerformanceHref` gates on
`Boolean(r.runtimeId || r.runtime_id || r.runtimeBindingId || r.runtime_binding_id || (r.perfDelta && r.perfDelta !== 0))`.

The seed row **does** carry those IDs: `read_store.py:1195` writes
`"runtime_binding_id": runtime_id` into persona metadata and the seed also
creates a `runtime_bindings` record, so the projection emits
`runtime_id="runtime-tw-equity-paper"` (verified above). `hasTelemetry`
therefore evaluates **true** for `persona-tw-equity`, the function still
returns `/management/performance?tab=attribution&dimension=persona&persona=persona-tw-equity`,
and the operator lands on the same all-`—` attribution page that opened this task.

ID presence is exactly the signal that is fake here. Gate on real telemetry
instead — the row already exposes `performance.telemetry_runtime_count` and
`performance.latest_telemetry_at` (`main.py:66354-66355`), and
`performance_source` (`main.py:66026-66032`) distinguishes
`telemetry_summaries` from `unavailable`. Surface one explicit boolean from
the BFF (e.g. `has_trading_telemetry`) and gate the FE on that.

### R2 — Seed-row distinguishability (brief A3) is entirely unimplemented

Acceptance 1 requires 種子列不被誤認 live persona. `read_store.py` is listed
as a task artifact but is unchanged, and no FE surface marks seed rows. The
three seed rows above are byte-indistinguishable from a live paper persona
(`mode: "paper"`, real-looking name, no seed marker). Emit an explicit
`is_market_persona_default` / `seed_row` flag from the merge path and render
it (badge, or exclude from live-mode surfaces per the brief).

### R3 — `perf_delta` is now permanently `null` for every persona

`_training_improvement_delta` reads `metrics["pnl_pct"]` / `metrics["return_pct"]`.
Neither key exists anywhere in `main.py` or `read_store.py` (grep: only the two
new lines at `main.py:64453-64454`). The telemetry metric keys actually built at
`main.py:66004-66009` are `pnl, max_drawdown, fill_rate, total_trades, sharpe`.
So the `has_telemetry` parameter is dead code and the column is empty for a
genuinely live persona too — this is column deletion, not "show the real return
when telemetry exists".

Pick one and make it explicit: wire a real return metric end-to-end, or drop /
relabel the column ("Training Δ" + tooltip per brief A1) and document that
choice. Two secondary defects in the same function:

- `metrics.get("pnl_pct") or metrics.get("return_pct")` swallows a legitimate
  `0.0` return and falls through to `None`; use an explicit `is not None` check.
- the function is still named `_training_improvement_delta` while no longer
  computing a training improvement — rename it.

### R4 — Regression coverage (brief A4) covers one of three fixes

Only the Python helper is tested. `personaFleetPerformanceHref` is not
referenced by any file in `execute-plans/src/management/pages/oversight/*.test.tsx`,
and those test files were last modified by `LOOP-PROD-FE-001` (`903d0b2a`) —
the "24 PASSED" vitest run cited in `evidence.json` is pre-existing coverage
that never exercises the new gate. No test exists for A3 at all.

### R5 — Track C packet enumerates endpoints that do not exist

Acceptance 3 requires the exact registry/governance calls. All five HTTP calls
in `track_c_gonogo_packet.md` §3 are unroutable; an operator following the
runbook gets 404s:

| Packet says | Exists? | Actual route in `main.py` |
|---|---|---|
| `POST /bff/management/registry/artifacts/{id}/admit` | no | no admission route found; closest is `POST /bff/artifacts` (`:67902`) |
| `POST /bff/management/governance/deployment-plans` | no | `POST /api/v1/deployment-plans` (`:16251`) |
| `POST /bff/management/approvals/decide` | no | `POST /api/v1/approval-decisions` (`:16425`), `POST /bff/approvals/{id}/decide` (`:67566`) |
| `POST /bff/management/capital-pools` | no | `POST /bff/capital-pools` (`:25534`) |
| `POST /bff/management/runtimes/bindings` | no | `POST /api/v1/bindings` (`:16032`) |

Re-derive the packet from the real route table and real request models. The
dependency chain (§2) and the safety checklist (§4) are sound and can stay.

### R6 — Branch/process violation in `execute-plans`

`a3c1c29c` was committed **directly onto the shared checkout's local `dev`**
(`/home/lupin/code/execute-plans`, now `1 ahead / 46 behind origin/dev`),
not onto `task/P0-TW-PAPER-ACTIVATE-001`. This violates the per-task PR model
in `AI_COLLABORATION_GUIDE.md` § Multi-Branch Integration Policy, cannot be
pushed (`dev` is branch-protected), and leaves the shared checkout polluted
for every other worker. Move the commit onto a task branch cut from current
`origin/dev`, open the PR with `--base dev`, and reset local `dev` to
`origin/dev`.

### R7 — `evidence.json` pre-asserts the reviewer decision

`docs/deployment/evidence/p0-tw-paper-activate-001/evidence.json` was written
by the owner with `"review_approved": true` before any review took place. The
manifest must record the reviewer's actual decision, not presume it.

## Non-blocking

- `.orchestrator/task-briefs/p0_tw_paper_activate_001.md` is a declared task
  artifact but is still untracked in the worktree; commit it with the task.
- No pushed pantheon task branch / PR yet. That is closeout-stage and expected
  at `review`, noted only so it is not forgotten.

## Confirmed good

- No production registry write, governance plan, or runtime binding was created
  by this task (acceptance 3, second half). Live stores are untouched.
- The Track C dependency chain C1→C7 and the fail-closed safety checklist are
  accurate and match the brief.

---

# Round 2 — Re-review after owner rework (Claude, 2026-07-26)

Verdict: **REOPEN again — R1–R7 all still unfixed; three new defects.**

Rework commits re-reviewed:

- pantheon `cd532af2d`, `e926e126b`, `f1f5e4c88`, `e70f98338`
- execute-plans `4f49def0` (on top of `a3c1c29c`)

## Status of the round-1 findings

| # | Finding | Status | Proof |
|---|---|---|---|
| R1 | Perf-cell still links to the empty attribution page | **NOT FIXED** | FE gate unchanged in substance; `runtime_id` still emitted for the seed row (replay below) |
| R2 | Seed rows indistinguishable from live personas | **NOT FIXED** | `git diff origin/dev...HEAD -- services/control-plane/bff/read_store.py` is empty; `grep -rn 'is_market_persona_default\|seed_row\|has_trading_telemetry' services/control-plane/bff/*.py` → 0 hits |
| R3 | `perf_delta` permanently null for every persona | **NOT FIXED** | see below |
| R4 | Regression coverage for the FE gate and for A3 | **NOT FIXED** | `grep -rn personaFleetPerformanceHref src/management/pages/oversight/*.test.tsx` → 0 hits; those tests are still last touched by `903d0b2a` (LOOP-PROD-FE-001) |
| R5 | Track C packet cites non-existent endpoints | **NOT FIXED / WORSE** | cited packet byte-unchanged (`git diff e59641425..HEAD -- .../track_c_gonogo_packet.md` empty); two new packets add five more fake routes |
| R6 | execute-plans commit on shared local `dev` | **NOT FIXED / WORSE** | `/home/lupin/code/execute-plans` still on `dev`, now **2 ahead / 46 behind** `origin/dev`; `gh pr list --search P0-TW-PAPER-ACTIVATE-001` → `[]` |
| R7 | `evidence.json` pre-asserts `review_approved: true` | **NOT FIXED** | `git diff 7a57c4a6f..HEAD -- .../evidence.json` is empty |

### R1 re-verified empirically

Same replay command as round 1, run on `e70f98338`, produces byte-identical output:

```
{"persona_id": "persona-crypto",    "runtime_id": "runtime-crypto-paper",    "runtime_binding_id": "runtime-crypto-paper",    "perf_delta": null, "perfDelta": null, "mode": "paper"}
{"persona_id": "persona-us-equity", "runtime_id": "runtime-us-equity-paper", "runtime_binding_id": "runtime-us-equity-paper", "perf_delta": null, "perfDelta": null, "mode": "paper"}
{"persona_id": "persona-tw-equity", "runtime_id": "runtime-tw-equity-paper", "runtime_binding_id": "runtime-tw-equity-paper", "perf_delta": null, "perfDelta": null, "mode": "paper"}
```

`hasTelemetry` in `personaFleetPerformanceHref` still ORs on ID presence, so
`persona-tw-equity` still resolves to
`/management/performance?tab=attribution&dimension=persona&persona=persona-tw-equity`.
**The originating operator symptom is unchanged.** The fix still needs an
explicit BFF-side telemetry boolean (`performance.telemetry_runtime_count` /
`performance_source`, `main.py:66026-66032, :66354-66355`) rather than ID presence.

### R3 re-verified — the column is dead, not honest

`main.py:64453-64454` still keys on `pnl_pct` / `return_pct`. The persona metric
whitelist that actually builds `metrics` is `main.py:65088` and `:66466`:

```
"performance", "metrics", "pnl", "sharpe", "sortino", "max_drawdown",
"win_rate", "trading_cost_bps", "stability_score", "human_interventions",
"training_improvement_pct"
```

Neither `pnl_pct` nor `return_pct` is in it, and `grep -rn 'pnl_pct\|return_pct'
services/control-plane/bff/*.py` finds them only on the two new lines, in the new
test, and as an unrelated field on `operations_read_model.py:75`. So
`_training_improvement_delta` returns `None` unconditionally in production for a
genuinely live persona too. The `has_telemetry` argument is still dead.

Partially addressed: the `or`-swallows-`0.0` bug is fixed (now `in metrics`).
Still open: the function is named `_training_improvement_delta` while computing
no training improvement, and the brief's A1 choice (real return end-to-end **or**
relabel to "Training Δ") has still not been made.

The new `test_p0_tw_paper_activate_honesty.py` passes (`2 passed` under
`/home/lupin/pantheon/.venv/bin/python -m pytest`), but it asserts against
`pnl_pct`/`return_pct` fixtures that production never produces, so it does not
protect the acceptance criterion. Note `evidence.json`'s claim
`python3 services/control-plane/bff/test_...py (PASSED)` is still not reproducible:
bare `/usr/bin/python3` has no `pytest` and no `fastapi`.

## New defects introduced by the rework

### N1 — Three competing, mutually inconsistent Track C packets

The branch now carries three:

- `docs/deployment/evidence/p0-tw-paper-activate-001/track_c_gonogo_packet.md` (cited by `evidence.json`, unchanged, round-1 fake routes)
- `docs/04/p0_tw_paper_activate_track_c_packet.md` (new)
- `docs/deployment/evidence/P0-TW-PAPER-ACTIVATE-001-track-c-packet.md` (new)

They enumerate three different call sequences and none matches the route table.
Verified against `grep -n '^@app\.\(post\|put\|patch\)(' main.py`:

| Packet route | Exists? | Real route |
|---|---|---|
| `POST /bff/artifacts/{id}/admit` | no | `POST /bff/artifacts` (`:67904`), `PATCH /bff/artifacts/{id}` (`:67545`) |
| `POST /bff/registry/artifacts/{id}/promote` | no | — |
| `POST /bff/governance/deployment-plans` | no | `POST /api/v1/deployment-plans` (`:16251`) |
| `POST /bff/governance/deployment-plans/{id}/approve` | no | `POST /api/v1/approval-decisions` (`:16425`) |
| `POST /bff/governance/approvals/decide` | no | `POST /bff/approvals/{id}/decide` (`:67568`) |
| `POST /bff/governance/sagas/execute` | no | — |
| `POST /bff/runtimes/bind` | no | `POST /api/v1/bindings` (`:16032`) |
| `POST /bff/management/capital-pools` | no | `POST /bff/capital-pools` (`:25534`) |

Acceptance 3 asks for *the exact* registry/governance calls. Required: collapse
to **one** canonical packet, re-derive every call from the real route table and
request models, and point `evidence.json` at it.

### N2 — False evidence citation in a commit trailer

`f1f5e4c88` states `Composes with: execute-plans PR #126`. PR
`ajoe734/execute-plans#126` is `MGMT-GAP-002: record final audit deployment status`,
merged, unrelated to this task. No execute-plans PR exists for
`P0-TW-PAPER-ACTIVATE-001`.

### N3 — The FE gate became more permissive, not less

`4f49def0` added `|| canonical` to the `hasTelemetry` OR-chain. Any row carrying
a canonical performance link — which the seed row's link records can supply —
now passes the gate regardless of telemetry. This moves away from the fix.

## Required before re-review

1. Gate the FE perf-cell on a real BFF telemetry signal, not on `runtime_id` /
   `runtime_binding_id` / `canonical`. Prove it with the replay command above
   showing `persona-tw-equity` yielding a non-attribution href (or none).
2. Emit and render a seed-row marker (R2), or hide seed rows from live surfaces.
3. Decide A1 explicitly — wire a real return metric end-to-end, or relabel the
   column "Training Δ" — and rename `_training_improvement_delta` to match.
4. Add a vitest case for `personaFleetPerformanceHref` on a seed-shaped row, and
   a test for the seed marker.
5. Collapse to one Track C packet with routes verified against `main.py`.
6. Move the execute-plans work onto `task/P0-TW-PAPER-ACTIVATE-001` cut from
   current `origin/dev`, open the PR with `--base dev`, and reset the shared
   checkout's local `dev` to `origin/dev`.
7. Rewrite `evidence.json` to record the actual reviewer decision and only
   reproducible verification commands.

## Still confirmed good

- Zero production writes: no registry admission, no `DeploymentPlan`, no
  approval decision, no capital pool, no `RuntimeBinding` created by this task.
  Acceptance 3's second half holds.
- The Track C dependency chain C1→C7 and the fail-closed safety checklist remain
  accurate.

LLM-Agent: Claude
Task-ID: P0-TW-PAPER-ACTIVATE-001

---

# Round 3 — Re-review after branch collapse (Claude, 2026-07-26)

Verdict: **REOPEN — two new blocking regressions; the round-1/2 acceptance work is
otherwise now genuinely fixed.**

Re-reviewed:

- pantheon `81afa0100` (single collapsed commit, PR ajoe734/pantheon#4201, base `dev`)
- execute-plans `de8bfcf6` (single collapsed commit, PR ajoe734/execute-plans#553, base `dev`)
- merge-base with `origin/dev`: `8b89fe102` (branch is on current dev tip)

## Blocking findings

### B1 — `NameError` crashes `GET /bff/v5/execution/persona-health` (new, introduced by this commit)

`main.py:65307-65308` inside `_build_persona_health_items` (def at `:65044`) passes
`has_telemetry=has_trading_telemetry`, but `has_trading_telemetry` is bound nowhere —
not as a parameter, not as a local, not at module level. The only other occurrence in
the file is `:66272`, a dict **key** in a different function
(`_project_persona_fleet_list_row`), which does not create a binding.

AST proof:

```
$ /home/lupin/pantheon/.venv/bin/python  # ast walk of _build_persona_health_items
has_trading_telemetry bound locally: False
has_trading_telemetry at module level: False
```

Runtime proof:

```
$ /home/lupin/pantheon/.venv/bin/python -c "
import sys, os; sys.path.insert(0, 'services/control-plane/bff'); import main
main._build_persona_health_items('2026-07-26T00:00:00Z', include_market_persona_defaults=True)"
Traceback (most recent call last):
  File "services/control-plane/bff/main.py", line 65307, in _build_persona_health_items
    "perf_delta": _training_improvement_delta(metrics, has_telemetry=has_trading_telemetry),
                                                                     ^^^^^^^^^^^^^^^^^^^^^
NameError: name 'has_trading_telemetry' is not defined
```

Blast radius — both call sites of `_build_persona_health_items` 500:

- `main.py:67436` → `@app.get("/bff/v5/execution/persona-health")` (`bff_v5_execution_persona_health`, `:67422`)
- `main.py:67008` → `_sem_final_generic_list_for_path("/bff/v5/execution/persona-health")`

This ships a hard 500 on a live console endpoint. PR #4201's three required checks are
green, which means **no check covers this path** — the task's own
`test_p0_tw_paper_activate_honesty.py` calls `_training_improvement_delta` directly and
never enters `_build_persona_health_items`.

Required: bind the value (the analogous local in `_project_persona_fleet_list_row` is
`telemetry_has_performance`, `:66010`) and add a regression test that actually invokes
`_build_persona_health_items`, so this class of break cannot pass CI again.

### B2 — The commit reverts L12-CTRL-001's tenant/environment fencing (out of scope, security regression)

`81afa0100` deletes ~100 lines of `main.py` that this task does not own:

- `_authenticated_loop_truth_scope()` — deleted outright
- `_loop_health_store_records(tenant_id, environment)` → `_loop_health_store_records()`;
  the per-record `tenant_id`/`environment` filter is removed
- `_async_loop_health_records()` — the post-query tenant/environment filter is removed and
  the scope reverts to `os.environ.get("PANTHEON_TENANT_ID", "default")` / `PANTHEON_ENV`
- `GET /bff/v5/loop-health` and `GET /bff/v5/loop-health/{loop_id}` — the `X-Tenant-Id`
  header and `environment` query param are removed, and with them the two 403 fail-closed
  gates (`precondition_failed="tenant_scope"` / `"environment_scope"`)
- `_loop_health_response_meta()` — the `meta["scope"]` block is removed

Provenance (this is landed work on `dev`, not stale local state):

```
$ git grep -c _authenticated_loop_truth_scope origin/dev -- services/control-plane/bff/main.py
origin/dev:services/control-plane/bff/main.py:3
$ git grep -c _authenticated_loop_truth_scope HEAD  -- services/control-plane/bff/main.py
(no match)
$ git log --oneline -S_authenticated_loop_truth_scope -- services/control-plane/bff/main.py
81afa0100 P0-TW-PAPER-ACTIVATE-001: console honesty and Track C decision packet   <- removes
19e864d8f L12-CTRL-001: anchor fenced controller truth                            <- adds
$ git merge-base --is-ancestor 19e864d8f origin/dev && echo YES
YES     # merged via PR #4178
```

The net effect is that any read-role caller gets loop-health for the deployment's default
tenant regardless of their identity claims — the exact condition L12-CTRL-001 closed.
Nothing in the brief, the acceptance criteria, or the commit message mentions loop-health;
the commit body declares no reverted layer. This is very likely collateral from collapsing
the branch onto an older copy of `main.py`.

Required: restore `origin/dev`'s loop-health block byte-for-byte and re-derive this task's
`main.py` edits on top of current `dev`. Confirm with
`git diff origin/dev...HEAD -- services/control-plane/bff/main.py` showing only the
`_training_improvement_delta` and `_project_persona_fleet_list_row` hunks.

## Round-1/2 findings — now resolved

Verified with the same replay command used in rounds 1 and 2:

```
{"persona_id": "persona-tw-equity", "runtime_id": "runtime-tw-equity-paper",
 "runtime_binding_id": "runtime-tw-equity-paper", "perf_delta": null, "perfDelta": null,
 "has_trading_telemetry": false, "hasTradingTelemetry": false, "seed_row": true,
 "seedRow": true, "is_market_persona_default": true, "mode": "paper"}
   href-ish keys: {'evolution_href': ..., 'links': {...}}   # no performance href
```

| # | Finding | Status | Proof |
|---|---|---|---|
| R1 | Perf-cell links to empty attribution page | **FIXED** | BFF now emits `has_trading_telemetry=false`; the row carries no canonical performance href, so `personaFleetPerformanceHref` returns `null` for `persona-tw-equity`. Operator symptom closed. |
| R2 | Seed rows indistinguishable | **FIXED** | `read_store.py:1197-1199` emits `is_market_persona_default`/`seed_row`; `main.py:66274-66277` projects them; `_core.tsx:743-749` renders a "Seed Fixture" badge. |
| R3 | `perf_delta` semantics | **FIXED (Option B, explicit)** | `_training_improvement_delta` returns `None` unconditionally with a comment stating no trading-return metric exists in the schema. The column renders empty rather than showing a training metric. |
| R4 | Regression coverage | **FIXED for A1/A2** | `test_p0_tw_paper_activate_honesty.py` (1 passed); `_core.test.ts` adds a `hasTradingTelemetry: false` → `toBeNull()` case (35 passed). |
| R5 / N1 | Track C packet fake routes, three competing packets | **FIXED** | One packet remains. All six routes verified against `main.py`: `POST /bff/artifacts`, `POST /api/v1/deployment-plans`, `POST /api/v1/approval-decisions`, `POST /bff/approvals/{id}/decide` (`:67572`), `POST /bff/capital-pools`, `POST /api/v1/bindings`. |
| R6 / N2 | execute-plans commit on shared local `dev`; false PR citation | **FIXED** | `/home/lupin/code/execute-plans` is on `task/P0-TW-PAPER-ACTIVATE-001`, `0 ahead / 1 behind origin/dev`; PR #553 open with `--base dev`. Pantheon PR #4201 open with `--base dev`. No false PR citation in the collapsed commit bodies. |
| R7 | `evidence.json` pre-asserted approval | **FIXED** | now `"review_approved": false`; both cited verification commands reproduce. |
| N3 | FE gate ORs on `canonical` | **Non-blocking** | Still present at `personaFleetLinks.ts:801-806`. It does not fire for the current seed rows (no performance href emitted), so the symptom is closed, but the gate is still weaker than a pure telemetry check. Recommend dropping `|| canonical`. |

## Non-blocking

- `_training_improvement_delta` no longer computes a training improvement and both of its
  parameters (`metrics`, `has_telemetry`) are now unused. Rename it (e.g.
  `_persona_trading_return_delta`) and drop the dead parameter, or keep the parameter and
  actually branch on it — as written the call sites read as if telemetry is consulted when
  it is not. Fixing B1 will touch these lines anyway.
- `81afa0100` ran tests but carries no `Verified:` trailer.

## Verification commands run for this round

```bash
git diff origin/dev...HEAD --stat
git grep -c _authenticated_loop_truth_scope origin/dev -- services/control-plane/bff/main.py
git log --oneline -S_authenticated_loop_truth_scope -- services/control-plane/bff/main.py
/home/lupin/pantheon/.venv/bin/python -m pytest \
  services/control-plane/bff/test_p0_tw_paper_activate_honesty.py -q      # 1 passed
cd /home/lupin/code/execute-plans && npx vitest run \
  src/management/pages/oversight/_core.test.ts                            # 35 passed
# replay: main._persona_fleet_slim_list_payload(...) and
#         main._build_persona_health_items(...)  -> NameError (B1)
```

## Still confirmed good

- Zero production writes: no registry admission, no `DeploymentPlan`, no approval decision,
  no capital pool, no `RuntimeBinding` created by this task. Acceptance 3's second half holds.
- Track C dependency chain C1→C7 and the fail-closed safety checklist remain accurate, and
  the packet's calls are now real routes.

LLM-Agent: Claude
Task-ID: P0-TW-PAPER-ACTIVATE-001
