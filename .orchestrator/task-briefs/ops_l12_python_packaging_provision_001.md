# Task Brief: OPS-L12-PYTHON-PACKAGING-PROVISION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Provision installed Python package for telemetry AC2
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Codex adopted the already-validated repair after the governed owner reassignment from Claude. The evidence authority metadata and checksum are refreshed for the current owner; PR [#4232](https://github.com/ajoe734/pantheon/pull/4232) remains OPEN, its prior evidence head `6773c64ee` has all four Branch CI Gate jobs green on both pull_request and push runs, and auto-merge remains disabled pending Codex2's independent review of the narrow owner-adoption follow-up.

## Summary
建立可安裝的 Pantheon Python distribution 與受治理測試環境 provisioning，讓 telemetry discovery AC2 在 foreign cwd、無 PYTHONPATH 下四種執行模式全部通過；不得修改 live supervisor config。

## Why this task exists

The Human/Ops in-progress audit of OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 at
`2026-07-26T22:41:34Z` rejected that task's attempt to narrow its second
acceptance criterion and required either an implementation making every named
execution mode pass, or a formal impossibility proof plus a scope revision. That
task's `AC2_FEASIBILITY_PROOF.md` proved the requirement satisfiable by exactly
one mechanism — an entry in `site-packages`, because `services` is a top-level
name and the criterion forbids both the cwd entry and `PYTHONPATH`. Human/Ops
chose **Option A, authorize packaging**, and created this task to own it.

## The AC2 rejection, and what this cut changes

Codex2 rejected AC2 in the real auto-worker dispatch. The rejection was correct.
The guide documented the bootstrap as plain
`python3 scripts/dev/provision_python_distribution.py`, and on an auto-worker
host `command -v python3` is `/usr/bin/python3`, which has no pytest.
Provisioning installs import *paths* and inherited its dependencies from
whichever interpreter invoked it, so it verified the three exported names, exited
`0`, and returned an environment whose very next documented command died with
`No module named pytest`. It had only ever been exercised from the fleet `.venv`.

The repair is a **dependency-interpreter selection contract** in the provisioning
script. Dependencies now come from a separately resolved interpreter, chosen from
an ordered candidate list in which every candidate is probed for `pytest` from a
foreign cwd with no `PYTHONPATH`: `--dependency-python`,
`$PANTHEON_DEPENDENCY_PYTHON`, the invoking interpreter, `$VIRTUAL_ENV`,
`<checkout>/.venv`, and `<main worktree>/.venv` derived from
`git rev-parse --git-common-dir`. That last candidate is the one an auto worker
needs, because every worker runs in a linked worktree whose dependencies live in
the main checkout. `.venv-pantheon` is created *by* the selected interpreter, so
its version matches the site directories it inherits, and every run closes by
re-proving that the interpreter it hands back can import `pytest`. Where no
candidate qualifies the script exits non-zero with the whole candidate table and
both remedies; an explicitly named interpreter is authoritative and is never
silently replaced by a fallback.

Two further changes follow from it: `test_m1_pytest_from_foreign_cwd` no longer
skips when the provisioned interpreter lacks pytest — that skip is what let M1 go
unproven on the worker host while the run still read `OK` — and
`scripts/dev/test_provision_python_distribution.py` gains
`TestDependencyInterpreterContract`, seven tests that drive the real script from
a purpose-built dependency-free interpreter.

## Delivered surface

| File | Role |
|---|---|
| `pyproject.toml` | The `pantheon-repo` distribution; explicit `packages.find` allowlist exporting exactly `services`, `integrations`, `scripts`. Unchanged by this cut. |
| `scripts/dev/provision_python_distribution.py` | The single governed install entry point shared by dev CI and the auto-worker test bootstrap; now carries the dependency-interpreter selection contract. |
| `scripts/dev/test_provision_python_distribution.py` | Static packaging contract plus the dependency-free bootstrap regression; 13 → 20 tests. |
| `services/telemetry/test_discovery_imports.py` | 20 tests; the four-mode and canonical-identity assertions, with M1 now unconditional. |
| `.github/workflows/branch-ci.yml` | `Python packaging provision` job. Unchanged by this cut — it already runs both modules. |
| `AI_COLLABORATION_GUIDE.md` | § 3 *Python Test Environment Provisioning*, now including *Where the dependencies come from*. |
| `docs/deployment/evidence/twelve-loop-gap/OPS-L12-PYTHON-PACKAGING-PROVISION-001/` | Evidence manifest, README, checksum, plus the fail-closed gate `scripts/test_ops_l12_python_packaging_provision_evidence.py`. |

No live supervisor configuration was read or written, and
`services/telemetry/capture.py` and `feedback_adapter.py` are byte-identical to
the validated dev base.

## Evidence epochs

`integrity.source_artifact_sha256_by_epoch` carries exactly one implementation
epoch, `36b3750eb`. The two earlier cuts — `c72842d9d` on dev `643181a06`, and
`4aab5cca4` on dev `7fedefb28` — are superseded, because the third cut changes
four of the six implementation files they pinned and the evidence gate checks
every recorded digest against the committed bytes. Their shas remain in
`record_log` and in the git history of the manifest.

The governed owner reassignment does not rewrite those historical epochs:
Claude remains the recorded implementation and evidence-cut actor, while Codex
is the current owner responsible for review handoff and finalization.

## Review request for Codex2

Review target: `docs/deployment/evidence/twelve-loop-gap/OPS-L12-PYTHON-PACKAGING-PROVISION-001/evidence.json`
and its `evidence.sha256`, at PR head `6773c64ee` plus the current owner's narrow
evidence-authority adoption commit. The evidence README § *Review status* lists
six specific checks. The load-bearing one is the first: run the guide's own
command from the worker default interpreter, with no
`PANTHEON_DEPENDENCY_PYTHON` and no `PYTHONPATH`, and confirm
`"$PANTHEON_PY" -m pytest` now runs — that is the exact sequence that failed in
the previous dispatch.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
