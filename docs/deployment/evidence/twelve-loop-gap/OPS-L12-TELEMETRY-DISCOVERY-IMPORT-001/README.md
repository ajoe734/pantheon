# OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 — Evidence

**Title:** Eliminate telemetry unittest discovery loader errors
**Owner:** Claude · **Reviewer:** Codex2 · **Phase:** Twelve-loop verification hardening
**Repository:** `ajoe734/pantheon` · **Branch:** `task/OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001`
**Base:** `dev` @ `6445eacd603f0bcfb8893508fbffe341a67dd309`

The machine-readable manifest is `evidence.json`; `evidence.sha256` pins both
files. This README is the human summary and does not outrank the manifest.

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

## Fix

Test-loading surface only. Both modules now import through the package path:

```python
from services.telemetry.capture import (...)
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from services.telemetry.capture import ExecutionMode
```

No `sys.path` bootstrap was added — `services/` and `services/telemetry/` both
carry `__init__.py`, so the package path is sufficient under repository-root
`unittest discover`, under `python -m unittest services.telemetry.<module>`, and
under `pytest` prepend import mode. Avoiding a `sys.path` insert also avoids a
process-global side effect that would leak into every other module loaded in the
same discovery run.

`services/telemetry/capture.py` and `services/telemetry/feedback_adapter.py`
were not touched; the pre-existing `sys.path` bootstrap inside
`feedback_adapter.py` (for `feedback.store`) stays owned by that production
module. No configuration file was edited.

## Regression coverage

New module `services/telemetry/test_discovery_imports.py` (10 tests) fences the
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

Child interpreters carry `PANTHEON_TELEMETRY_DISCOVERY_IMPORT_CHILD=1`; the
subprocess-spawning class skips when that sentinel is set, so a widened
discovery pattern cannot fork recursively.

## Counts

Interpreter: `/home/lupin/pantheon/.venv/bin/python3` (Python 3.12.3).
Command: `env -u PANTHEON_RUNTIME_MANAGER_URL … -m unittest discover -s services/telemetry -p 'test_*.py' -t .`

| Run | Result |
|---|---|
| Baseline (branch base `6445eacd6`, files byte-identical to `f9b6760d6`) | **197 tests, 2 errors, 1 skip** — `FAILED (errors=2, skipped=1)` |
| Post-change | **280 tests, 0 errors, 1 skip** — `OK (skipped=1)` |
| Post-change, repeated | 280 tests — `OK (skipped=1)` |
| Post-change, hostile ambient environment | 280 tests — `OK (skipped=1)` |

Arithmetic: the baseline's 197 included 2 `_FailedTest` placeholders, so 195
real tests ran. `195 + 35 (test_capture) + 40 (test_feedback_adapter) + 10
(test_discovery_imports) = 280`. **No test was lost; 75 previously-unrun tests
were recovered.**

## Negative control

The two repaired files were reverted to their pre-change bare imports and
`services.telemetry.test_discovery_imports` was re-run against them:
`Ran 10 tests … FAILED (failures=15)` — all 10 test methods failed. The fixed
files were then restored and re-verified. The regression module therefore fails
on the defect it fences rather than passing vacuously.

## Validation commands

All run from the repository root of the task worktree unless noted, with
`/home/lupin/pantheon/.venv/bin/python3`:

| # | Command | Result |
|---|---|---|
| 1 | `… -m unittest discover -s services/telemetry -p 'test_*.py' -t .` (pre-change) | 197 tests, `FAILED (errors=2, skipped=1)` — baseline reproduced |
| 2 | `… -m unittest discover -s services/telemetry -p 'test_*.py' -t .` | 280 tests, `OK (skipped=1)` |
| 3 | same as 2, run again | 280 tests, `OK (skipped=1)` |
| 4 | same as 2 with hostile `PANTHEON_RUNTIME_MANAGER_URL/TOKEN/TOKEN_FILE/BINDING_STORE_PATH` and `INCIDENTS_DATA_DIR` | 280 tests, `OK (skipped=1)` |
| 5 | `… -m unittest services.telemetry.test_capture` | 35 tests, `OK` |
| 6 | `… -m unittest services.telemetry.test_feedback_adapter` | 40 tests, `OK` |
| 7 | `… -m unittest services.telemetry.test_discovery_imports` | 10 tests, `OK` |
| 8 | `… -m compileall -q` on all three files | clean |
| 9 | `cd /tmp && … -m pytest <abs paths to all three files> -q` | `85 passed, 14 subtests passed` |
| 10 | `cd /tmp && env -i PATH=… HOME=… PYTHONPATH=<repo root> … -m unittest services.telemetry.test_capture services.telemetry.test_feedback_adapter` | 75 tests, `OK` |
| 11 | `git diff --stat f9b6760d6 -- services/telemetry/capture.py services/telemetry/feedback_adapter.py` | empty — production modules unchanged |
| 12 | negative control (see above) | `FAILED (failures=15)` on pre-change source |

Command 10 uses `env -i`: the only inherited variables are `PATH`, `HOME`, and
`PYTHONPATH=<repo root>`. That is the direct proof of no cwd dependence and no
reliance on any ambient Pantheon variable.

## Scope boundary

Owned: `services/telemetry/test_capture.py`,
`services/telemetry/test_feedback_adapter.py`,
`services/telemetry/test_discovery_imports.py` (new).

Not changed: `services/telemetry/capture.py`,
`services/telemetry/feedback_adapter.py`, any configuration file, and the
production `sys.path` bootstrap inside `feedback_adapter.py`.

`git diff --stat <merge-base with origin/dev> -- .` lists exactly those three
files, 332 insertions / 3 deletions.

## Residual notes

- `services/telemetry/test_main_routes.py` and
  `services/telemetry/test_trade_journal_contracts.py` raise loader errors under
  an interpreter lacking `flask` / `pytest` (for example bare `/usr/bin/python3`).
  That is an interpreter-provisioning gap, not an import-path defect: under the
  provisioned `.venv` interpreter used for every count above, discovery is clean.
  Out of scope for this task, which does not touch dependency configuration.
- The single skip in every run is pre-existing and unchanged.
- Independent reviewer verdict from Codex2 is intentionally absent from this cut
  and is appended to `record_log` when approval occurs.
