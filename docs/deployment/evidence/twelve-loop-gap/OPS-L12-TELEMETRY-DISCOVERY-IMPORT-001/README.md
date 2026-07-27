# OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 — Evidence

**Owner:** Codex2 · **Reviewer:** Codex
**Repository:** `ajoe734/pantheon`
**Review file:** `docs/deployment/evidence/twelve-loop-gap/OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001/evidence.json`
**State of this cut:** ready for independent review

## Outcome

The two telemetry test modules now load through their package-qualified sibling
imports:

- `services.telemetry.test_capture` contributes 35 tests;
- `services.telemetry.test_feedback_adapter` contributes 40 tests;
- repository-root `unittest discover` reports zero loader errors;
- neither repaired module mutates `sys.path`;
- no bare `capture` or `feedback_adapter` alias is left in `sys.modules`.

The original baseline was freshly reproduced in a detached worktree at
`71aea154b8a1ab6e652e02018f47c57f26513de0`:

```text
ran=197 failures=0 errors=2 skipped=1 elapsed=12.974s
error_ids=unittest.loader._FailedTest.services.telemetry.test_capture,
          unittest.loader._FailedTest.services.telemetry.test_feedback_adapter
```

Current repository-root discovery ran 342 tests with zero failures, zero loader
errors, and the same one pre-existing skip.

## AC2 resolution

The prior evidence cut correctly stopped at
`blocked_pending_scope_decision`: a bare interpreter cannot resolve the
top-level `services` package from a foreign cwd without `PYTHONPATH`.

Human/Ops chose the packaging option. The dependency task
`OPS-L12-PYTHON-PACKAGING-PROVISION-001` delivered:

- a root distribution with an explicit `services`, `integrations`, `scripts`
  allowlist;
- `scripts/dev/provision_python_distribution.py` as the governed developer,
  worker, and CI entry point;
- fail-closed dependency-interpreter selection;
- unconditional regressions for all four AC2 modes;
- canonical module identity without adding the repository root to `sys.path`.

Its implementation merged through PR #4232 as
`3802799f81778c93728d9dbbe4028289f153c718` and the governed task archive is
`done` with Codex2 approval bound to
`docs/deployment/evidence/twelve-loop-gap/OPS-L12-PYTHON-PACKAGING-PROVISION-001/evidence.json`.

Fresh execution from `/tmp` under `env -i` with no `PYTHONPATH`:

| Mode | Result |
|---|---|
| Dotted `unittest` on both modules | 75 tests, `OK` |
| Direct `test_capture.py` | 35 tests, `OK` |
| Direct `test_feedback_adapter.py` | 40 tests, `OK` |
| `pytest -c /dev/null --noconftest` on both files | 75 passed |
| Repository-root `unittest discover` | zero loader errors |

The same dotted command under the unprovisioned shared interpreter fails with
`No module named 'services'`. That negative control proves the positive result
comes from the governed installed distribution, not from cwd or ambient path
state.

`AC2_FEASIBILITY_PROOF.md` retains the original exhaustive import-resolution
analysis and records Option A as implemented.

## Regression and evidence gates

`services/telemetry/test_discovery_imports.py` contains 20 tests covering:

- package-qualified sibling imports across every telemetry test module;
- no `sys.path` mutation;
- zero `_FailedTest` placeholders and all 75 repaired tests present;
- repeated in-process discovery;
- foreign-cwd and hostile-environment child processes;
- all four installed-distribution AC2 modes;
- no repository-root path injection or exported top-level collisions;
- canonical, unduplicated module identity.

`scripts/test_ops_l12_telemetry_discovery_import_evidence.py` fails closed on:

- formal schema violations;
- future or non-UTC evidence timestamps;
- non-dense record-log sequences;
- wrong task owner/reviewer or unresolved AC2 admission;
- missing or unapproved packaging dependency evidence;
- out-of-range `validation.commands[N]` references;
- checksum drift or checksum-coverage drift.

## Validation

The authoritative command list and full conclusions are in
`evidence.json.validation.commands`.

| Check | Result |
|---|---|
| Pre-change detached-worktree discovery | 197 tests, 2 loader errors, 1 skip |
| `unittest services.telemetry.test_discovery_imports` | 20 tests, `OK` |
| Foreign-cwd no-`PYTHONPATH` dotted/direct/pytest modes | 75 / 35 / 40 / 75, all pass |
| Full repository-root telemetry discovery | 342 tests, `OK (skipped=1)` |
| Full hostile-environment telemetry discovery | 342 tests, 0 failures, 0 errors, 1 skip |
| Full telemetry pytest suite | 353 passed, 1 skipped, 35 subtests passed |
| Evidence gate | schema, timestamps, dependency, references, checksum pass |
| Production module diff from baseline | empty |
| `git diff --check` | exit 0 |

## Production boundary

This task does not change:

- `services/telemetry/capture.py`;
- `services/telemetry/feedback_adapter.py`;
- telemetry runtime configuration;
- live supervisor configuration;
- the packaging implementation owned by
  `OPS-L12-PYTHON-PACKAGING-PROVISION-001`.

The diff from the reproduced baseline to this branch is empty for both
production modules.

## Delivery history

| PR | Purpose | Merge |
|---|---|---|
| #4222 | Initial import repair | `55b17612ed150f52a518a4e8c4c6e75502830f6b` |
| #4225 | Schema-valid evidence and fail-closed timestamp gate | `8d1b5077996a2d27aafb83ff5756f0290d0e90bc` |
| #4226 | AC2 feasibility proof and explicit blocked state | `1cf27337e9197c8bc0840e466f55019065e3576e` |
| #4232 | Reviewed installed-distribution dependency | `3802799f81778c93728d9dbbe4028289f153c718` |

The first three are retained historical delivery, not approval of this cut.
This owner-adoption cut still requires:

1. push to the existing repair PR #4273;
2. green exact-head required checks;
3. independent Codex review bound to `evidence.json`;
4. merge to `dev`;
5. governed owner closeout.

## Integrity

`evidence.sha256` pins:

- `AC2_FEASIBILITY_PROOF.md`;
- `README.md`;
- `evidence.json`.

The manifest cannot contain its own digest, so the companion checksum is the
integrity anchor and the evidence test verifies it mechanically.
