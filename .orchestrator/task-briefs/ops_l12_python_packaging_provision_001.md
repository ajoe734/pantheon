# Task Brief: OPS-L12-PYTHON-PACKAGING-PROVISION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Provision installed Python package for telemetry AC2
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Delivery is complete and re-cut on a synced head; awaiting Codex2's independent review of the evidence manifest and checksum. PR [#4232](https://github.com/ajoe734/pantheon/pull/4232) is OPEN, CLEAN and MERGEABLE at head `4aab5cca40a6815876beb8d77972bb71d12d757d`, with all four Branch CI Gate jobs green on both the pull_request run 30228827007 and the push run 30228825268. Auto-merge is deliberately **not** enabled, because AC6 requires the independent decision to precede any merge.

## Why this task exists

The Human/Ops in-progress audit of OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 at
`2026-07-26T22:41:34Z` rejected that task's attempt to narrow its second
acceptance criterion and required either an implementation making every named
execution mode pass, or a formal impossibility proof plus a scope revision. That
task's `AC2_FEASIBILITY_PROOF.md` proved the requirement satisfiable by exactly
one mechanism — an entry in `site-packages`, because `services` is a top-level
name and the criterion forbids both the cwd entry and `PYTHONPATH`. Human/Ops
chose **Option A, authorize packaging**, and created this task to own it.

## Delivered surface

| File | Role |
|---|---|
| `pyproject.toml` | The `pantheon-repo` distribution; explicit `packages.find` allowlist exporting exactly `services`, `integrations`, `scripts`. |
| `scripts/dev/provision_python_distribution.py` | The single governed install entry point shared by dev CI and the auto-worker test bootstrap. |
| `scripts/dev/test_provision_python_distribution.py` | Static packaging contract; the allowlist stays an allowlist and the script fails closed. |
| `services/telemetry/test_discovery_imports.py` | 15 → 20 tests; the two that recorded the gap as an expected failure are replaced by unconditional four-mode and canonical-identity assertions. |
| `.github/workflows/branch-ci.yml` | New `Python packaging provision` job. |
| `AI_COLLABORATION_GUIDE.md` | § 3 *Python Test Environment Provisioning*. |
| `docs/deployment/evidence/twelve-loop-gap/OPS-L12-PYTHON-PACKAGING-PROVISION-001/` | Evidence manifest, README, checksum, plus the fail-closed gate `scripts/test_ops_l12_python_packaging_provision_evidence.py`. |

No live supervisor configuration was read or written, and
`services/telemetry/capture.py` and `feedback_adapter.py` are byte-identical to
the base dev tip.

## Evidence re-cut on a synced head

The first evidence cut was taken at head `c72842d9de8afeae46d8953174b18698bd2c10e3`
on merged dev tip `643181a067ec5c344faac0766c69de0d5cfb32eb`. `dev` then advanced
twice and the PR went `BEHIND`. Two conflict-free dev merges — `0a5f32db4`, then
`4aab5cca4` onto dev tip `7fedefb281dd416e0412e935c48e866438f56e6d` — restored a
`CLEAN` state. Between them they landed OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001
and OPS-CI-PR-TRAILER-RANGE-001; they changed **no file this task owns**, so all
six implementation artifacts are byte-identical across both epochs and
`integrity.source_artifact_sha256_by_epoch` pins both. Every result was
re-observed at `4aab5cca4`, and the required checks now carry exact-head
conclusions rather than the first cut's `pending_at_cut_time` placeholders.

## Review request for Codex2

Review target: `docs/deployment/evidence/twelve-loop-gap/OPS-L12-PYTHON-PACKAGING-PROVISION-001/evidence.json`
and its `evidence.sha256`, at head `4aab5cca40a6815876beb8d77972bb71d12d757d`.
The evidence README § *Review status* lists five specific checks, the most
load-bearing being that `validation.commands[8]` — the unprovisioned control —
still fails, so the M2/M3 passes are attributable to the distribution and not to
ambient state.

## Summary
建立可安裝的 Pantheon Python distribution 與受治理測試環境 provisioning，讓 telemetry discovery AC2 在 foreign cwd、無 PYTHONPATH 下四種執行模式全部通過；不得修改 live supervisor config。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
