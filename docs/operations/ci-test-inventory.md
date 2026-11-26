# CI test inventory

**Rule for task authors.** A new test file (`test_*.py` or `*_test.py`, tracked by git, outside vendored code) is run by CI by default: the `Test sweep` job in `branch-ci.yml` runs every tracked test file that no other job command names, one pytest process per file, so you do not add the file to any list. A test file that CI cannot run (needs a database, network, Docker or a dependency the job lacks) or that is red is exempt only if it is listed in `docs/operations/ci-test-inventory.json` with a `reason` and an `owner` (a task id or `domain:<name>`); an exemption is never a way to let a failing assertion pass, and no assertion may be weakened or test deleted to make the guard green.

## How it is enforced

- `scripts/ci_test_inventory.py check` (job `Test inventory guard`) fails when a test file is neither collected by a required job nor listed with a reason and owner, when an entry has no reason or owner, when an entry names a file that no longer exists, and when an entry names a file that a required job now runs (remove it then). It also fails if the sweep job is not wired with every shard `0..N-1`.
- What a job collects is read from `.github/workflows/branch-ci.yml`, `.github/workflows/stage-0-ci.yml` and `.github/pantheon-stage0-matrix.json`: each `pytest` / `unittest` / `unittest discover` command is parsed, a directory argument covers every test file below it, `--ignore` is honoured, `file::node` selections count as collected (partial), and dotted unittest modules resolve against the repo root and `.orchestrator`. The source-ingestion shard runner's `TARGETS` are read from the script. There is no second list of covered files.
- Both new jobs are legs of the required `Service suites` check, so no branch-protection change is needed.
- `python3 scripts/ci_test_inventory.py run-shard --shard I --of N` is the sweep; shards are split by a stable hash of the path. Tests of the guard: `scripts/test_ci_test_inventory.py`.

## Limits

The guard is static: it does not run `pytest --collect-only`, so a directory argument is assumed to collect every test file below it (conftest `collect_ignore` or `-k` filters are not modelled). The inventory lists tests that do not run; it does not make red tests pass. Entries are removed by the owning domain when the test is fixed.

## State at the merge base (2026-11-19)

1748 test files; 736 named by existing required job commands; 1012 named by none. Run once each on the development host (evidence: `docs/deployment/evidence/CI-EVERY-TEST-FILE-RUNS-20261119/evidence.json`): 782 passed (now run by the sweep), 230 failed or errored (listed in the inventory with failing test names and owner). Of the failures, 113 are `tests/e2e` persona tests (`domain:persona-e2e`), 66 are BFF tests (`domain:bff`), 27 are delivery scripts.
