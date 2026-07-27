# OPS-L12-PYTHON-PACKAGING-PROVISION-001 — Evidence

**Title:** Provision installed Python package for telemetry AC2
**Owner:** Codex · **Reviewer:** Codex2 · **Phase:** Twelve-loop closure
**Repository:** `ajoe734/pantheon` · **Branch:** `task/OPS-L12-PYTHON-PACKAGING-PROVISION-001`
**Base:** merged dev tip `4cb436f80` · **Validated implementation head:** `36b3750eb`
· **Independently reviewed PR head:** `dd64fd9d4` (PR
[#4232](https://github.com/ajoe734/pantheon/pull/4232))
**Superseded cuts:** head `c72842d9d` on dev `643181a06`; head `4aab5cca4` on dev `7fedefb28`.

> **Status: independent review approved.** Codex2 independently reproduced the
> bare-system worker bootstrap, all four canonical execution modes, the
> fail-closed controls, checksum/source integrity, and the full telemetry result
> before recording the decision below. PR #4232 remains unmerged with auto-merge
> disabled; owner closeout is still required.

> **Third cut, answering an AC2 rejection.** Codex2 rejected AC2 in the real
> auto-worker dispatch, and the rejection was right. This cut answers it with an
> implementation change, not an argument. Both earlier epochs are **superseded**:
> `integrity.source_artifact_sha256_by_epoch` now carries exactly one epoch,
> because this cut changes four of the six files those epochs pinned.

> **Owner adoption:** Claude authored the implementation and the third evidence
> cut recorded below. The governed task row later reassigned final ownership to
> Codex because the original lane became unavailable. The manifest preserves
> Claude's historical record entries and adds a separate Codex adoption entry;
> Codex2 remains the independent reviewer.

The machine-readable manifest is `evidence.json`; `evidence.sha256` pins it and
this README. This README is the human summary and does not outrank the manifest.
`scripts/test_ops_l12_python_packaging_provision_evidence.py` is the fail-closed
gate over the manifest itself.

## A note on criterion numbering

Two different criteria are called "AC2" in the surrounding record, and the
distinction matters when reading this directory:

- **the predecessor's AC2** — OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001's second
  acceptance criterion, the four-mode requirement that task could not close;
- **this task's AC2** — the second criterion of *this* task's canonical list,
  about CI and worker provisioning.

The four-mode requirement is **AC3** in this manifest, matching this task's own
canonical acceptance list. Where this README says "the four modes" it means the
predecessor's AC2, which is this task's AC3. The rejection described below was
against **this task's AC2**, the provisioning criterion.

## Why this task exists

The Human/Ops in-progress audit of OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 at
`2026-07-26T22:41:34Z` rejected that task's attempt to narrow its acceptance and
required either an implementation that makes every named mode pass, or a formal
impossibility proof plus scope revision. That task produced
[`AC2_FEASIBILITY_PROOF.md`](../OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001/AC2_FEASIBILITY_PROOF.md),
which proved the requirement satisfiable by exactly one mechanism:

> `services` is a top-level name, and CPython resolves top-level names only from
> `sys.path`. `sys.path` at interpreter start is built from the script/cwd entry,
> `PYTHONPATH`, and the site directories. The criterion forbids the first two.
> An entry in `site-packages` is the only candidate left, and the enumeration is
> exhaustive.

It offered two options. Human/Ops chose **Option A — authorize packaging**, and
created this task to own it. This delivery is that mechanism.

## The rejection, and what it found

Codex2's dispatch ran the bootstrap exactly as `AI_COLLABORATION_GUIDE.md`
documented it, on a real auto-worker host:

```bash
python3 scripts/dev/provision_python_distribution.py --quiet --print-python
"$PANTHEON_PY" -m pytest ...
```

The first line exited `0`. The second died with `No module named pytest`.

The cause was a wrong assumption, not a wrong mechanism. Provisioning installs
import *paths*; the dependencies were inherited from **whichever interpreter
invoked the script**. On an auto-worker host `command -v python3` is
`/usr/bin/python3`, which has no pytest and no service dependency — so
provisioning verified the three exported names, reported success, and handed
back an environment that could not run the command the guide printed on the very
next line. It only worked in the first two cuts because the owner had run it
from the fleet `.venv`.

That is a silent-success hole in a governed entry point, and the reviewer was
right to call it AC2 rather than an environment quirk. `validation.commands[0]`
reproduces the failure at the rejected head `ad719d9c2`;
`validation.commands[1]` and `[2]` are the identical commands passing here.

## The fix: a dependency-interpreter selection contract

Provisioning no longer assumes the caller has the dependencies. It resolves a
**dependency interpreter** separately, probing each candidate for `pytest` from a
foreign cwd with no `PYTHONPATH` before accepting it:

| # | Candidate | Why |
|---|---|---|
| 1 | `--dependency-python` | explicit operator choice |
| 2 | `$PANTHEON_DEPENDENCY_PYTHON` | governed environment override |
| 3 | the interpreter running the script | the dev-CI and fleet-environment path |
| 4 | `$VIRTUAL_ENV` | the caller is inside an activated environment |
| 5 | `<checkout>/.venv` | a checkout-local environment |
| 6 | `<main worktree>/.venv` | derived from `git rev-parse --git-common-dir` |

Row 6 is the one that answers the rejection: **every auto worker runs in a linked
git worktree** whose dependencies live in the main checkout, and that derivation
is how a bare `python3` in a worktree finds them. No supervisor or worker
configuration is read to do it.

Three properties make this a contract rather than a heuristic:

- **It fails closed.** If no candidate qualifies, provisioning exits non-zero and
  prints the whole candidate table with the reason each entry was rejected, both
  remedies, and a pointer to the guide (`validation.commands[9]`). It never
  returns an interpreter that cannot run the next documented command.
- **An explicit choice is authoritative.** `--dependency-python` and
  `$PANTHEON_DEPENDENCY_PYTHON` are never silently replaced by a fallback
  (`validation.commands[10]`); a silent fallback is how the wrong environment
  gets used unnoticed.
- **The result is re-proved, not assumed.** `.venv-pantheon` is created *by* the
  selected interpreter — so its version matches the site directories it
  inherits — and every run ends by verifying that the interpreter it hands back
  can import `pytest` from a foreign cwd with no `PYTHONPATH`.

One subtlety is worth a reviewer's attention, because getting it wrong silently
reopens the defect: candidates are deduplicated on their **absolute** path, never
their resolved one. A venv interpreter is a symlink to the base interpreter that
built it, so resolving would collapse every environment on the host into a single
candidate and drop the only one that has the dependencies. That is fenced by
`TestDependencyInterpreterContract::test_venv_interpreters_are_not_deduplicated_into_their_base`.

## The four modes

Every command below ran from a foreign working directory with no `PYTHONPATH`
key in the environment at all.

| Mode | Command | Before (dev `643181a06`) | After |
|---|---|---|---|
| M1 | `pytest -q -c /dev/null --noconftest <abs>/test_capture.py <abs>/test_feedback_adapter.py` | PASS (75) | **PASS (75)** |
| M2 | `python -m unittest services.telemetry.test_capture services.telemetry.test_feedback_adapter` | FAIL — `No module named 'services'` | **PASS (75)** |
| M3 | `python <abs>/test_capture.py`, `python <abs>/test_feedback_adapter.py` | FAIL — same | **PASS (35 + 40)** |
| M4 | `python -m unittest discover -s services/telemetry -t . -p …` from the repository root | PASS (75) | **PASS (75)** |

M2 and M3 were the gap. The pass is attributable to the installed distribution
and nothing else: the same two commands under an **unprovisioned** interpreter,
in the same environment, still fail with `ModuleNotFoundError: No module named
'services'` (`validation.commands[6]` and `[7]`).

**M1 changed status in this cut.** It used to `skipTest` when the provisioned
interpreter had no pytest — which on the worker host meant the mode was
*unproven* while the run still printed `OK`. It is now a hard assertion, and
provisioning is required to fail closed rather than return such an interpreter.
`validation.commands[8]` runs the whole AC2 module under the bare
`/usr/bin/python3`: 20 tests, `OK`, and the only two skips are the two tests that
are about the *ambient* interpreter by design.

## What was delivered

| File | Role |
|---|---|
| `pyproject.toml` | The `pantheon-repo` distribution. Explicit `packages.find` allowlist exporting exactly `services`, `integrations`, `scripts`. No dependencies, no pytest config, no package data. **Unchanged by this cut.** |
| `scripts/dev/provision_python_distribution.py` | The single governed install entry point, shared by dev CI and the auto-worker test bootstrap. This cut adds the dependency-interpreter selection contract, `--dependency-python`, `--recreate`, and the closing dependency verification. |
| `scripts/dev/test_provision_python_distribution.py` | Fast static packaging contract, plus `TestDependencyInterpreterContract`: seven tests that drive the real script from a purpose-built dependency-free interpreter. 13 → 20 tests. |
| `services/telemetry/test_discovery_imports.py` | `TestAC2ModesUnderInstalledDistribution` (all four modes) and `TestInstalledDistributionIsCanonical`. This cut turns M1's pytest skip into a hard assertion. 20 tests. |
| `.github/workflows/branch-ci.yml` | `Python packaging provision` job: install `requirements.txt`, provision through the same script, run both suites. **Unchanged by this cut** — it already runs the new tests. |
| `AI_COLLABORATION_GUIDE.md` | § 3 *Python Test Environment Provisioning*, now including *Where the dependencies come from*. |

## Two design decisions a reviewer should check

**1. Why a script rather than a documented `pip install -e .`.** An editable
install writes an *absolute* mapping from the three exported names to one
checkout. Pantheon runs many checkouts at once — the supervisor root plus one
git worktree per auto-worker task — all sharing the host interpreter. A bare
`pip install -e .` into that shared interpreter silently rebinds `services` for
every other checkout on the machine, so worker A would execute worker B's code
and never know. That is precisely the duplicate-module-identity hazard this task
is required not to introduce. Two rules close it: the default target is a
checkout-scoped `.venv-pantheon`, and every run verifies from a foreign cwd with
no `PYTHONPATH` that each exported name resolves *inside this checkout*, failing
closed and naming the offending path otherwise.

Dependency inheritance does not weaken that. The inherited site directories are
written into a `.pth`, and `site.addpackage` appends the listed directories to
`sys.path` **without** recursing into their own `.pth` files — so a Pantheon
editable install belonging to a *different* checkout in the dependency
environment is inherited as neither a path entry nor a finder. Anything inside
the checkout is dropped from the inheritance list outright, and
`verify_checkout_binding` re-proves the mapping on every run regardless.

`.venv-pantheon/` matches the existing `.venv-*/` rule in `.gitignore`, so
provisioning a worker worktree does not dirty it. No `.gitignore` change was
needed and none was made.

**2. Why the fix does not buy the four modes at the cost of the other criteria.**
The crude way to close M2/M3 would be a `.pth` line containing the repository
root. That would export every root-level module — `cli.py`, `gate.py`,
`workflows.py` — as a top-level name, colliding with widely used PyPI
distributions, and would reintroduce duplicate identities. The declared-package
editable install does not. Observed under the provisioned interpreter
(`validation.commands[13]`):

- the repository root is **not** on `sys.path` — the distribution installs a
  meta-path finder with an explicit three-name mapping, so there is no
  process-global `sys.path` mutation on any code path;
- `cli`, `gate`, `workflows`, `conftest`, `mlflow_adapter` all resolve to `None`;
- the allowlist exports exactly three names.

`TestInstalledDistributionIsCanonical` fences all three, so a future switch to a
cruder mechanism fails the suite.

## Verification

Observed at head `36b3750eb` on merged dev tip `4cb436f80`.

| Run | Result |
|---|---|
| **The rejection, at the rejected head `ad719d9c2`** — documented bootstrap from `/usr/bin/python3`, then `-m pytest` | provisioning exit `0`, then **`No module named pytest`** |
| **The same commands here** | provisioning exit `0` naming `<main worktree>/.venv` as the dependency source, then **40 passed, 23 subtests** |
| Control: `/usr/bin/python3 -c 'import pytest'` | `ModuleNotFoundError` — nothing ambient is certifying the result |
| Baseline `pytest services/telemetry -q` at dev `4cb436f80` (throwaway worktree, removed after) | 348 passed, 1 skipped |
| `pytest services/telemetry -q` under the bootstrapped interpreter | **353 passed, 1 skipped** — exactly +5, matching the 15 → 20 growth of the one changed module |
| `python3 -m unittest services.telemetry.test_discovery_imports -v` under the **bare system interpreter** | Ran 20, `OK (skipped=2)`; M1 passes, the two skips are the ambient-interpreter tests |
| `pytest scripts/dev/test_provision_python_distribution.py -q` (fleet interpreter) | 20 passed, 3 subtests |
| Unprovisioned M2 / M3 controls | `FAILED (errors=1)` / `No module named 'services'` |
| Fail-closed paths: no qualifying candidate, an explicit candidate without the deps, `--mode current` on the shared interpreter | exit 1 with a specific, actionable message in all three |
| Exact-head required checks on PR #4232 (runs `30231402274` pull_request, `30231401041` push) | all four Branch CI Gate jobs **success** on both events |

The single skip is pre-existing and unrelated
(`test_l12_tel_001_durable_ingest.py:255`, needs `PANTHEON_TEST_NATS_URL`); it is
identical in the baseline and post-change runs.

## What is not claimed

- **No merge commit.** A manifest cannot contain the merge commit of the PR that
  introduces it; the governed closeout checkpoint binds that.
- **Only one source epoch.** The two earlier epochs are superseded, not dropped
  for convenience: this cut changes four of the six files they pinned, and the
  gate checks every recorded digest against the committed bytes, so carrying
  them would describe trees that no longer exist. They remain readable in this
  file's git history and in `record_log`.
- **The new packaging job is not a branch-protection required check.** Adding it
  is a repository-settings change this task does not own. Recorded as a residual
  risk.
- **Discovery is host-shaped.** The candidate list encodes where Pantheon
  dependencies live on the hosts this repository runs on. A host that keeps them
  elsewhere gets a loud failure and two authoritative overrides, not a guess.
  Recorded as a residual risk.
- **No live supervisor configuration was read or written**, and no supervisor
  process-control action was requested. `services/telemetry/capture.py` and
  `feedback_adapter.py` are byte-identical to dev.

## Review status

Codex2 approved the evidence at PR head `dd64fd9d4833766eeb32e7a18901a65a73a5df49`
on 2026-07-27 after these independent observations:

- the guide's bootstrap started from `/usr/bin/python3`, with
  `PANTHEON_DEPENDENCY_PYTHON`, `VIRTUAL_ENV`, and `PYTHONPATH` absent, selected
  `<main worktree>/.venv`, and provisioned a fresh interpreter;
- the packaging/discovery suites passed with `40 passed, 23 subtests`; the same
  bare interpreter drove the discovery module directly with `Ran 20 tests, OK
  (skipped=2)`, where both skips are the intended ambient-pytest checks;
- the full telemetry suite passed with `353 passed, 1 skipped, 35 subtests`; the
  single skip is the pre-existing NATS crash probe;
- unprovisioned M2/M3 controls still failed on `No module named 'services'`,
  while an explicit dependency interpreter without pytest and unsafe
  `--mode current` both exited 1 with their governed diagnostics;
- the finalized manifest gate passed with `10 passed, 36 subtests`,
  `evidence.sha256`
  matched both companion files, source digests matched the single implementation
  epoch, and the checkout gained no install artifact;
- push run `30267610432` and pull-request run `30267613436` each reported all
  four Branch CI Gate jobs successful at the reviewed head. Auto-merge remained
  disabled.

The review also corrected two stale residual-risk references to removed
`validation.commands[19]` and `[20]`; that historical whole-services collection
note is non-blocking and is not used to prove any acceptance criterion. The only
`.orchestrator/` PR change is this task's brief; live supervisor config and the
runtime telemetry modules remain outside the diff. GitHub currently marks the PR
behind the moving `dev` branch, so branch synchronization and merge remain owner
closeout work and are not claimed by this review.
