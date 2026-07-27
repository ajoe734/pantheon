# OPS-L12-PYTHON-PACKAGING-PROVISION-001 — Evidence

**Title:** Provision installed Python package for telemetry AC2
**Owner:** Claude · **Reviewer:** Codex2 · **Phase:** Twelve-loop closure
**Repository:** `ajoe734/pantheon` · **Branch:** `task/OPS-L12-PYTHON-PACKAGING-PROVISION-001`
**Base:** merged dev tip `7fedefb28` · **Validated head:** `4aab5cca4` (PR
[#4232](https://github.com/ajoe734/pantheon/pull/4232))
**First cut:** dev tip `643181a06`, head `c72842d9d` — superseded by the re-cut below.

> **Status: owner-asserted pass, pending independent review.** All four canonical
> execution modes now pass from a foreign working directory with no `PYTHONPATH`.
> The reviewer verdict is **not** asserted here — see *Review status* below.

> **Re-cut on a synced head.** After the first cut, `dev` advanced twice and PR #4232
> went `BEHIND`. Two conflict-free dev merges — `0a5f32db4`, then `4aab5cca4` onto
> dev tip `7fedefb28` — brought the branch back to `CLEAN`. Between them they landed
> OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001 and OPS-CI-PR-TRAILER-RANGE-001, which is
> why the full telemetry count moved; **they changed no file this task owns**, so all
> six implementation artifacts are byte-identical across both epochs.
> `integrity.source_artifact_sha256_by_epoch` now pins both epochs so a reviewer can
> check that mechanically. Every result below was re-observed at `4aab5cca4`.

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
predecessor's AC2, which is this task's AC3.

## Why this task exists

The Human/Ops in-progress audit of OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 at
`2026-07-26T22:41:34Z` rejected that task's attempt to narrow its acceptance and
required either an implementation that makes every named mode pass, or a formal
impossibility proof plus a scope revision. That task produced
[`AC2_FEASIBILITY_PROOF.md`](../OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001/AC2_FEASIBILITY_PROOF.md),
which proved the requirement satisfiable by exactly one mechanism:

> `services` is a top-level name, and CPython resolves top-level names only from
> `sys.path`. `sys.path` at interpreter start is built from the script/cwd entry,
> `PYTHONPATH`, and the site directories. The criterion forbids the first two.
> An entry in `site-packages` is the only candidate left, and the enumeration is
> exhaustive.

It offered two options. Human/Ops chose **Option A — authorize packaging**, and
created this task to own it. This delivery is that mechanism.

## The four modes

Every command below ran from a foreign working directory under `env -i`, so no
`PYTHONPATH` key existed in the environment at all.

| Mode | Command | Before (dev `643181a06`) | After |
|---|---|---|---|
| M1 | `pytest -q -c /dev/null --noconftest <abs>/test_capture.py <abs>/test_feedback_adapter.py` | PASS (75) | **PASS (75)** |
| M2 | `python -m unittest services.telemetry.test_capture services.telemetry.test_feedback_adapter` | FAIL — `No module named 'services'` | **PASS (75)** |
| M3 | `python <abs>/test_capture.py`, `python <abs>/test_feedback_adapter.py` | FAIL — same | **PASS (35 + 40)** |
| M4 | `python -m unittest discover -s services/telemetry -t . -p …` from the repository root | PASS (75) | **PASS (75)** |

M2 and M3 were the gap. The pass is attributable to the installed distribution
and nothing else: the same two commands under an **unprovisioned** interpreter,
in the same environment, still fail with `ModuleNotFoundError: No module named
'services'` (`validation.commands[8]`).

## What was delivered

| File | Role |
|---|---|
| `pyproject.toml` | The `pantheon-repo` distribution. Explicit `packages.find` allowlist exporting exactly `services`, `integrations`, `scripts`. No dependencies, no pytest config, no package data. |
| `scripts/dev/provision_python_distribution.py` | The single governed install entry point, shared by dev CI and the auto-worker test bootstrap. |
| `scripts/dev/test_provision_python_distribution.py` | Fast static packaging contract: the allowlist stays an allowlist, the config boundaries hold, the exported names agree across files, the script fails closed. |
| `services/telemetry/test_discovery_imports.py` | The two tests that recorded M2/M3 as an expected failure are replaced by `TestAC2ModesUnderInstalledDistribution` (all four modes, unconditional pass) and `TestInstalledDistributionIsCanonical`. 15 → 20 tests. |
| `.github/workflows/branch-ci.yml` | New `Python packaging provision` job: install `requirements.txt`, provision through the same script, run both suites. |
| `AI_COLLABORATION_GUIDE.md` | § 3 *Python Test Environment Provisioning* — the worker bootstrap and its rules. |

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
closed and naming the offending path otherwise (`validation.commands[12]`).

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
- `cli`, `gate`, `workflows`, `conftest`, `mlflow_adapter`, `main`, `capture`,
  and `telemetry` all resolve to `None`;
- `top_level.txt` contains exactly three lines.

`TestInstalledDistributionIsCanonical` fences all three, so a future switch to a
cruder mechanism fails the suite.

## Verification

| Run | Result |
|---|---|
| Baseline `pytest services/telemetry -q` at dev `643181a06` (throwaway worktree, removed after) | 296 passed, 1 skipped |
| `pytest services/telemetry -q` at head `c72842d9d` | **301 passed, 1 skipped** — exactly +5, matching the 15 → 20 growth of the one changed module |
| `pytest services/telemetry/test_discovery_imports.py -q` | 20 passed, **0 skipped** |
| `pytest scripts/dev/test_provision_python_distribution.py -q` | 13 passed |
| CI rehearsal: the packaging job's exact step sequence in a from-scratch virtualenv | 33 passed |
| Fail-closed paths: unprovisioned `--check-only`, `--mode current` on the shared interpreter, cross-checkout binding | exit 1 with a specific message in all three |
| Rootdir regression | `rootdir: <repo>`, `configfile: pytest.ini` — unchanged |

Re-observed at the synced head `4aab5cca4`:

| Run | Result |
|---|---|
| `pytest services/telemetry/test_discovery_imports.py -q` — the four modes and the unprovisioned control | 20 passed, 20 subtests, 0 skipped |
| `pytest scripts/test_ops_l12_python_packaging_provision_evidence.py scripts/dev/test_provision_python_distribution.py -q` | 23 passed, 13 subtests |
| `pytest services/telemetry -q` | 353 passed, 1 skipped |
| Exact-head required checks on PR #4232 (runs `30228827007` pull_request, `30228825268` push) | all four Branch CI Gate jobs **success** on both events |
| Implementation digests at `4aab5cca4` vs the `c72842d9d` epoch | identical for all six files |

The telemetry count rose from 301 to 353 because the sync brought in
OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001's `test_infrastructure_health_ingest.py`
and its siblings, not because this task changed: none of its six files differ
between the two epochs. The single skip is pre-existing and unrelated
(`test_l12_tel_001_durable_ingest.py:255`, needs `PANTHEON_TEST_NATS_URL`); it is
identical in the baseline, post-change, and synced-head runs.

## What is not claimed

- **No reviewer verdict.** Codex2's independent decision must be appended to
  `record_log` before `done`. Auto-merge is deliberately **not** enabled on PR
  #4232 so that no merge can precede that decision.
- **No merge commit.** PR #4232 is open, `CLEAN` and `MERGEABLE` at the time of
  this re-cut. A manifest cannot contain the merge commit of the PR that
  introduces it; the governed closeout checkpoint binds that.
- **The new packaging job is not a branch-protection required check.** Adding it
  is a repository-settings change this task does not own. Recorded as a residual
  risk.
- **No live supervisor configuration was read or written**, and no supervisor
  process-control action was requested. `services/telemetry/capture.py` and
  `feedback_adapter.py` are byte-identical to dev `643181a06`.

## Review status

Awaiting Codex2. The reviewer's independent check should target, at minimum:

1. that `validation.commands[8]` — the unprovisioned control — actually fails,
   so the M2/M3 passes are attributable to the distribution;
2. that the allowlist exports three names and no more, on the current tree;
3. that `evidence.sha256` matches the committed bytes, and that
   `integrity.source_artifact_sha256_by_epoch` matches the implementation files
   at **both** epochs, `c72842d9d` and `4aab5cca4` (the gate checks both
   mechanically, and their agreement is what makes the sync a no-op for review);
4. that nothing under `.orchestrator/` or any live supervisor config is in the
   diff;
5. that the two sync merges really are confined to other lanes' files —
   `git diff --stat 7bf1fd5f5..4aab5cca4 -- pyproject.toml scripts/dev
   .github/workflows/branch-ci.yml AI_COLLABORATION_GUIDE.md
   services/telemetry/test_discovery_imports.py` is empty.
