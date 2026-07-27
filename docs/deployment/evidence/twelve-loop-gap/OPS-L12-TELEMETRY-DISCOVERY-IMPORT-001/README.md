# OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 — Evidence

**Title:** Eliminate telemetry unittest discovery loader errors
**Owner:** Claude · **Reviewer:** Codex2 · **Phase:** Twelve-loop verification hardening
**Repository:** `ajoe734/pantheon` · **Branch:** `task/OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001`
**Delivery:** PR [#4222](https://github.com/ajoe734/pantheon/pull/4222) merged as `55b17612e` · PR [#4225](https://github.com/ajoe734/pantheon/pull/4225) merged as `8d1b50779` · this cut at head `b91c845e4`

> **Status: `blocked_pending_scope_decision`.** The import repair is delivered and
> verified (285 tests / 0 errors / 1 skip, 75 previously-unrun tests recovered, no
> production change). Canonical acceptance criterion 2 is **not met as written**
> and is not claimed as narrowed: see *AC2 status* below and
> [`AC2_FEASIBILITY_PROOF.md`](AC2_FEASIBILITY_PROOF.md). A Human/Ops scope
> decision is required.

The machine-readable manifest is `evidence.json`; `evidence.sha256` pins both
files. This README is the human summary and does not outrank the manifest.
`scripts/test_ops_l12_telemetry_discovery_import_evidence.py` is the fail-closed
gate over the manifest itself.

## Review status

Reviewer Codex2 **rejected** the first cut at `2026-07-26T22:15:44Z`. Their
independent positives stand: the base baseline reproduced, the merged tree
discovered 280/0/1, the regression module passed, production modules were
unchanged. Five fixes were required, and this cut answers all five:

| # | Required fix | Answer |
|---|---|---|
| 1 | Acceptance 2 was false as written — the recorded `env -i` run injected `PYTHONPATH`, and with no `PYTHONPATH` the dotted/direct forms fail from a foreign cwd | Three new **genuinely** no-`PYTHONPATH` regressions that pass, plus two assertions that make the remaining gap machine-checked. The narrowing first offered here was **rejected by Human/Ops** and has been withdrawn — AC2 is now recorded as `blocked_pending_scope_decision` (see *AC2 status*) |
| 2 | Re-cut evidence with actually observed times; bind the follow-up head and PR/checks/merge facts, not just the anchor | Every timestamp is an observed time (`21:52:40`, `21:54:05`, `21:55:32`, `21:58:37`, `22:15:44`, `22:37:15`); PR #4222 is bound in full with its three check runs; `validated_head_sha` is the re-cut head `408d6d9a5` |
| 3 | Make `evidence.json` validate against `schemas/product-evidence.schema.json` | Manifest restructured; validates under `jsonschema` 4.26.0 and under the gate's own validator |
| 4 | Add the fail-closed check rejecting future `task.evidence_cut_at`, `validation.validated_at`, and `record_log` timestamps | `scripts/test_ops_l12_telemetry_discovery_import_evidence.py` (10 tests), proven to reject the rejected manifest |
| 5 | Preserve no production/config changes | Task diff is test modules, task brief, and evidence artifacts only |

PR #4222 merged at `21:55:32Z`, six minutes **before** the `21:58:37Z` owner
handoff, and carried no reviews. It is retained historical delivery, not
approval. PR #4225 carried fixes 2–5 and the first answer to fix 1; it merged at
`23:01:39Z` as `8d1b50779`.

## AC2 status

Human/Ops audited the in-progress head `408d6d9a5` at `2026-07-26T22:41:34Z` and
**rejected** the acceptance-scope narrowing: AC2 requires *every* named mode to
pass, and recording foreign-cwd dotted and direct-file failure as an accepted
boundary proves a weaker contract. That is correct, and the narrowing is
withdrawn. The manifest now records AC2 and `task.overall_admission` as
`blocked_pending_scope_decision`, and the two boundary tests are labelled in
source as recording an **unresolved** gap.

[`AC2_FEASIBILITY_PROOF.md`](AC2_FEASIBILITY_PROOF.md) is the formal answer the
audit asked for. In short:

- `sys.path` in the foreign-cwd, no-`PYTHONPATH` environment is
  `["", stdlib…, site-packages]` — nothing points at the repository;
- the repaired file is **never executed** in that mode: asking for a module that
  does not exist at all produces the byte-identical `No module named 'services'`,
  so resolution stops at the `services` package. Code that never runs cannot
  repair anything, so no edit to the repaired files can close it;
- the mechanisms that could put the repository root on `sys.path` are exactly
  four — `PYTHONPATH`, cwd, runtime `sys.path` mutation, or a `site-packages`
  entry. AC2 forbids the first three, leaving only an installed distribution;
- **demonstrated constructively:** a single `.pth` line in a throwaway venv's
  `site-packages` — exactly what `pip install -e .` writes — makes dotted
  unittest (75 tests OK) and direct file execution (35 and 40 OK) pass from
  `/tmp` with no `PYTHONPATH` and **no change of any kind to the repaired
  files**. Removing the `.pth` fails again.

So AC2 is achievable, by packaging only. That means a root `pyproject.toml` (the
repository has never had packaging metadata anywhere) **and** a rule that every
test environment installs it — a config change plus a process change, both
outside this task's scope and both excluded by the audit's own "No config/process
change" line. The proof document states the two options and recommends handling
Option A as its own build/CI-lane task; the decision is Human/Ops'.

## Defect

`services/telemetry/test_capture.py` and
`services/telemetry/test_feedback_adapter.py` imported their package siblings by
bare module name:

```python
from capture import TelemetryCapture, ExecutionMode, ...        # test_capture.py:14
from feedback_adapter import FeedbackStoreAdapter               # test_feedback_adapter.py:11
from capture import ExecutionMode                               # test_feedback_adapter.py:12
```

A bare `capture` only resolves when `sys.path[0]` is `services/telemetry`
itself. Under repository-root discovery the interpreter's first path entry is
the repository root, so both modules raised a loader `ModuleNotFoundError` and
were replaced by `unittest.loader._FailedTest` placeholders. The consequence was
not only 2 red loader errors — **75 telemetry tests never ran at all** and the
suite still reported a total.

`OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001` recorded these two errors as an
out-of-scope residual risk on merged dev `f9b6760d6`. This task closes them.

The root `conftest.py` puts a service module's own directory on `sys.path`
before collecting it, so bare sibling imports still resolve under `pytest`.
That is why the defect was invisible to `pytest` and surfaced only under
repository-root `unittest discover` — and why the regression fence asserts the
import contract by AST rather than trusting either runner.

## Fix

Test-loading surface only. Both modules now import through the package path:

```python
from services.telemetry.capture import (...)
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from services.telemetry.capture import ExecutionMode
```

No `sys.path` bootstrap was added — `services/` and `services/telemetry/` both
carry `__init__.py`, so the package path is sufficient. Avoiding a `sys.path`
insert also avoids a process-global side effect that would leak into every other
module loaded in the same discovery run.

`services/telemetry/capture.py` and `services/telemetry/feedback_adapter.py`
were not touched; the pre-existing `sys.path` bootstrap inside
`feedback_adapter.py` (for `feedback.store`) stays owned by that production
module. No configuration file was edited.

## Repository-root resolution contract

`services.telemetry.<module>` resolves only when the repository root is on
`sys.path`. That is Python import resolution, not a property of these modules,
so the claim is stated exactly. **Every row below runs under `env -i` with no
`PYTHONPATH` key at all.**

| From | Command | Result |
|---|---|---|
| foreign cwd (`/tmp`) | `pytest -q <abs paths>` | **75 passed** — pytest walks the `__init__.py` chain to the repository root and puts it on `sys.path` itself |
| foreign cwd (`/tmp`) | `pytest -q -c /dev/null --noconftest <abs paths>` | **75 passed** — so the pass does not depend on this repository's `pytest.ini` or root `conftest.py` |
| repository root | `python -m unittest services.telemetry.test_capture services.telemetry.test_feedback_adapter` | **75 tests OK** — the interpreter puts the cwd on `sys.path` |
| repository root | `python -m unittest discover -s services/telemetry -t . -p …` | **35 / 40 tests OK** |
| foreign cwd (`/tmp`) | `python -m unittest services.telemetry.test_capture` | **fails: `No module named 'services'`** — the repository root is unreachable |
| foreign cwd (`/tmp`) | `python <abs>/services/telemetry/test_capture.py` | **fails: `No module named 'services'`** — `sys.path[0]` is `services/telemetry` |

The last two rows are the **unresolved AC2 gap**, not an accepted boundary.
Closing them inside the modules would need a process-global `sys.path` (or
`sys.modules`) mutation — which acceptance 2 itself forbids,
`test_repaired_modules_do_not_mutate_process_global_sys_path` rejects, and which
would reintroduce the duplicate-module-identity hazard this task closed — and in
the dotted case it is impossible outright because the file never executes. What
the regression module asserts there is the invariant that does hold in every
environment: **a failure may name `services`, never `capture` or
`feedback_adapter`.** That is exactly the defect this task removed. The gap is
tracked as a blocking residual risk at severity `high`, not folded into a claim
of success.

## Regression coverage

`services/telemetry/test_discovery_imports.py` (**15 tests**) fences the
contract rather than the two specific call sites, so a newly added telemetry
test module that reintroduces a bare sibling import fails here:

| Test | Proves |
|---|---|
| `test_no_telemetry_test_module_imports_a_bare_package_sibling` | AST scan of **every** `services/telemetry/test_*.py`: no import whose root name is a telemetry package sibling. Explicit relative imports remain allowed. |
| `test_repaired_modules_import_siblings_through_the_package_path` | The two repaired modules import via `services.telemetry.*`. |
| `test_repaired_modules_do_not_mutate_process_global_sys_path` | AST scan: neither repaired module touches `sys.path`. |
| `test_repaired_modules_discover_with_zero_loader_errors` | In-process `TestLoader().discover(top_level_dir=repo root)` yields zero `_FailedTest` and exactly 75 tests. |
| `test_repeated_discovery_in_one_process_is_stable` | Repeated discovery in one interpreter gives identical, error-free results — no import-cache leakage. |
| `test_importing_repaired_modules_leaves_no_bare_sibling_in_sys_modules` | No bare `capture` / `feedback_adapter` alias is registered, so the duplicate-module-identity hazard is gone too. |
| `test_repo_root_discovery_of_repaired_modules_reports_no_loader_error` | Subprocess `unittest discover -s services/telemetry -t .` with a scrubbed environment. |
| `test_repaired_modules_run_from_a_foreign_working_directory` | Both modules run with cwd set to a temporary directory. |
| `test_repaired_modules_import_under_hostile_ambient_environment` | Import succeeds with hostile `PANTHEON_RUNTIME_MANAGER_*`, `INCIDENTS_DATA_DIR`, and a nonexistent `PYTHONPATH` prefix; no sibling alias leaks. |
| `test_repaired_modules_collect_under_pytest_from_a_foreign_cwd` | `pytest --collect-only` from a foreign cwd collects all 75 tests. Skips when pytest is absent. |
| **new** `test_pytest_passes_from_foreign_cwd_with_no_pythonpath_or_repo_config` | `pytest` passes from a foreign cwd with **no `PYTHONPATH`**, `-c /dev/null` and `--noconftest`. |
| **new** `test_dotted_unittest_from_repo_root_cwd_needs_no_pythonpath` | Dotted `unittest` passes from the repository root with **no `PYTHONPATH`**. |
| **new** `test_repo_root_discovery_needs_no_pythonpath` | Repository-root `unittest discover` passes with **no `PYTHONPATH`**. |
| **new** `test_dotted_unittest_from_foreign_cwd_without_pythonpath_fails_only_on_services` | Unresolved AC2 gap, machine-checked: any failure there names `services`, never a bare sibling. |
| **new** `test_direct_file_execution_from_foreign_cwd_fails_only_on_services` | Same unresolved gap for `python <abs file>`. |

Child interpreters carry `PANTHEON_TELEMETRY_DISCOVERY_IMPORT_CHILD=1`; the
subprocess-spawning classes skip when that sentinel is set, so a widened
discovery pattern cannot fork recursively.

## Evidence gate

`scripts/test_ops_l12_telemetry_discovery_import_evidence.py` (**10 tests**) is
fail-closed over this task's own manifest:

- validates `evidence.json` against `schemas/product-evidence.schema.json` using
  `jsonschema` **and** a dependency-free draft-07 subset validator, so a bare
  interpreter without the dependency still gets a verdict instead of a skip;
- rejects a future `task.evidence_cut_at`, `validation.validated_at`,
  `record_log[].recorded_at`, or `pull_request(s).merged_at`;
- requires `record_log` sequences to be dense and 1-based and no entry to
  postdate the cut that ships it;
- verifies `evidence.sha256` against the committed bytes and against the
  coverage the manifest declares;
- proves it is not vacuous: mutating this manifest the way the rejected one was
  malformed must be rejected.

Run against the **rejected** `28e62b8f2` manifest it reports
`FAILED (failures=2, errors=4)` with **76** schema violations — the same
families the reviewer listed. Run against this manifest: `Ran 10 tests … OK`.

## Counts

Interpreter: `/home/lupin/pantheon/.venv/bin/python3` (Python 3.12.3).
Command: `env -u PANTHEON_RUNTIME_MANAGER_URL … -m unittest discover -s services/telemetry -p 'test_*.py' -t .`

| Run | Result |
|---|---|
| Baseline (branch base `71aea154b`, files byte-identical to `f9b6760d6`) | **197 tests, 2 errors, 1 skip** — `FAILED (errors=2, skipped=1)` |
| Merged PR #4222 tree (`55b17612e`, 10-test regression module) | 280 tests, 0 errors, 1 skip |
| Re-cut head `408d6d9a5`, first run | **285 tests, 0 errors, 1 skip** — `OK (skipped=1)`, 21.376s |
| Re-cut head, second consecutive run | 285 tests — `OK (skipped=1)`, 31.194s |
| Re-cut head, hostile ambient environment | 285 tests — `OK (skipped=1)`, 28.991s |

Arithmetic: the baseline's 197 included 2 `_FailedTest` placeholders, so 195
real tests ran. `195 + 35 (test_capture) + 40 (test_feedback_adapter) + 15
(test_discovery_imports) = 285`. **No test was lost; 75 previously-unrun tests
were recovered.** The 280 → 285 delta is exactly the five new no-`PYTHONPATH`
regressions.

## Negative control

The two repaired files were reverted to their pre-change bare imports and
`services.telemetry.test_discovery_imports` was re-run against them:
`Ran 15 tests … FAILED (failures=20)` across 10 distinct test methods, including
all three discriminating no-`PYTHONPATH` additions. The two boundary tests pass
in both directions by design — they assert that no failure is ever attributable
to a bare sibling, which is true of the pre-change source only because its bare
imports resolved when `sys.path[0]` was `services/telemetry`. The fixed files
were then restored and re-verified (`Ran 15 tests … OK`, `git diff --stat`
empty).

## Validation commands

All run from the repository root of the task worktree unless noted, with
`/home/lupin/pantheon/.venv/bin/python3`. `evidence.json`'s
`validation.commands` array is the authoritative list; this is the summary.

| # | Command | Result |
|---|---|---|
| 1 | `… -m unittest discover -s services/telemetry -p 'test_*.py' -t .` (pre-change base) | 197 tests, `FAILED (errors=2, skipped=1)` |
| 2–4 | same, re-cut head: twice consecutively, then hostile ambient env | 285 tests, `OK (skipped=1)` each |
| 5–7 | `… -m unittest services.telemetry.{test_capture,test_feedback_adapter,test_discovery_imports}` | 35 / 40 / 15 tests, `OK` |
| 8 | `cd /tmp && env -i PATH=… HOME=… … -m pytest -q <two abs paths>` (**no `PYTHONPATH`**) | `75 passed` |
| 9 | `cd <repo> && env -i PATH=… HOME=… … -m unittest services.telemetry.{test_capture,test_feedback_adapter}` (**no `PYTHONPATH`**) | 75 tests, `OK` |
| 10 | `cd /tmp && env -i PATH=… HOME=… … -m unittest services.telemetry.test_capture` (**no `PYTHONPATH`**) | `No module named 'services'` — recorded boundary |
| 11 | `cd /tmp && env -i … PYTHONPATH=<repo> … -m unittest` on both modules | 75 tests, `OK` |
| 12 | `cd /tmp && env -i … PYTHONPATH=<repo> … -m pytest -q` on all three modules | `90 passed, 20 subtests passed` |
| 13 | negative control (see above) | `FAILED (failures=20)` on pre-change source |
| 14 | `… -m unittest scripts.test_ops_l12_telemetry_discovery_import_evidence` against the rejected manifest | `FAILED (failures=2, errors=4)`, 76 schema violations |
| 15 | same, against this manifest | 10 tests, `OK` |
| 16 | `jsonschema.validate(evidence.json, product-evidence.schema.json)` | exit 0 |
| 17 | `sha256sum -c evidence.sha256` | `README.md: OK`, `evidence.json: OK` |
| 18 | `… -m compileall -q` on all four delivered modules | clean |
| 19 | `git diff --stat f9b6760d6 -- services/telemetry/{capture,feedback_adapter}.py` | empty — production modules unchanged |
| 20 | `git diff --check` | exit 0 |

## Scope boundary

Owned: `services/telemetry/test_capture.py`,
`services/telemetry/test_feedback_adapter.py`,
`services/telemetry/test_discovery_imports.py`,
`scripts/test_ops_l12_telemetry_discovery_import_evidence.py`, and this
evidence directory.

Not changed: `services/telemetry/capture.py`,
`services/telemetry/feedback_adapter.py`, the production `sys.path` bootstrap
inside `feedback_adapter.py`, the root `conftest.py` / `pytest.ini` harness,
`schemas/product-evidence.schema.json`, and any configuration or dependency
file.

## Delivery history

1. **PR #4219** (anchor `80ecb47e8`, evidence `bcd850aef`, base `6445eacd6`) —
   failed the required `Commit trailers` check: a 75-character anchor subject
   against a 72-character limit. Force-push recovery is not authorized for
   background workers, so the PR was closed with an explanatory comment, the
   remote task branch deleted, and the identical delivery re-cut from `dev`
   `71aea154b` with a 60-character subject. `services/telemetry` is
   byte-identical between `6445eacd6` and `71aea154b`.
2. **PR #4222** (anchor `d5c8d9a5f`, evidence `28e62b8f2`) — merged as
   `55b17612e` at `21:55:32Z` with all required checks green, but before the
   owner handoff and with no reviews. Retained historical delivery.
3. **Codex2 rejection** at `22:15:44Z` with five required fixes.
4. **PR #4225** (anchor `408d6d9a5`, evidence `3c2d88370`) — merged as
   `8d1b50779` at `23:01:39Z` with all required checks green. It carried the
   fail-closed evidence gate, the five no-`PYTHONPATH` regressions, and the
   schema-valid manifest. Note: `task_finalize.sh`'s auto-merge enable did not
   stick; the PR merged when `gh pr merge --auto --merge` was re-issued with every
   check already green.
5. **Human/Ops in-progress audit** at `22:41:34Z` — rejected head `408d6d9a5` as
   an acceptance-scope narrowing and required either an implementation covering
   every AC2 mode or a formal impossibility proof plus scope revision, with no
   config/process change and via a new repair PR.
6. **This cut** (`b91c845e4` plus these evidence bytes) on merged dev
   `8d1b50779`: AC2 narrowing withdrawn, `AC2_FEASIBILITY_PROOF.md` added,
   status `blocked_pending_scope_decision`. Each cut binds the previous pull
   request in full; the pull request that carries a cut is bound by the next one,
   because a manifest cannot contain the merge commit of the PR that introduces
   it — the same structural limit as `integrity.self_hash_omitted`.

## Residual notes

- From a foreign cwd with no `PYTHONPATH` and no other route to the repository
  root, the dotted and direct-file forms fail on `services`. **Blocking**
  residual risk at severity `high`, pending the Human/Ops AC2 scope decision —
  not a defect of these modules, and not accepted as satisfying AC2.
- The root `conftest.py` masks bare sibling imports under `pytest`, so `pytest`
  alone cannot catch a regression of this class. The AST fence can.
- `services/telemetry/test_main_routes.py` and
  `services/telemetry/test_trade_journal_contracts.py` raise loader errors under
  an interpreter lacking `flask` / `pytest` (for example bare `/usr/bin/python3`).
  Interpreter provisioning, not import resolution; out of scope.
- The single skip in every run is pre-existing and unchanged.
- The independent reviewer verdict from Codex2 on **this** cut is intentionally
  absent and is appended to `record_log` when approval occurs.
