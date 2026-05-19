# LOVABLE-STRICT-PUBLISH Audit Packet

Task: `LOVABLE-STRICT-PUBLISH`
Owner: `Codex2`
Reviewer: `Codex`
Status: review approved; Codex2 owner closeout recorded

## Purpose

SA section 2.2 requires a non-blocking follow-up for the
`execute-plans@main` Lovable build to be republished with strict build-time BFF
settings and then checked for seed/mock fallback leakage. This packet records
the Pantheon-side audit procedure and evidence fields. It does not modify the
`execute-plans` repository.

## Required Build Env

The Lovable publish must be built with the exact values in
`support/evidence/LOVABLE-STRICT-PUBLISH/required_build_env.json`:

| Key | Required value |
|---|---|
| `VITE_BFF_MODE` | `live` |
| `VITE_BFF_FALLBACK` | `strict` |
| `VITE_BFF_REAL_WRITES` | `false` |

## Audit Command

```bash
python3 scripts/audit_lovable_strict_publish.py \
  https://pantheon-dev.lovable.app/management \
  --output support/evidence/LOVABLE-STRICT-PUBLISH/strict-publish-audit.json \
  --report support/evidence/LOVABLE-STRICT-PUBLISH/strict-publish-audit.md
```

Use the target Lovable deployment URL that was republished from
`execute-plans@main` with the required build env.

## Evidence To Record

| Evidence field | Source |
|---|---|
| Deployment URL | Audit command argument |
| Check timestamp | `checked_at` in JSON output |
| Required build env | `required_flags` in JSON output |
| Strict env confirmation | `strict_env_confirmed` and `missing_flags` |
| Hosted bundle URLs | `bundle_urls` |
| Hosted bundle hashes | `bundle_hashes[*].sha256` |
| Mock or seed runtime path leaks | `forbidden_runtime_paths` |
| Final result | `passed` |

## Acceptance Mapping

| Acceptance item | Evidence |
|---|---|
| `verify_strict_publish(deployment_url)` returns an `AuditResult` dict | `scripts/audit_lovable_strict_publish.py` |
| Required `VITE_*` keys are listed with exact values | `required_build_env.json` |
| Probe fails on `/mocks/` or `seed.*` runtime paths | `bundle_verification_probe.md` and unit test failure case |
| Human-readable evidence packet exists | this file |
| One pass and one relaxed-env fail case are covered | `scripts/test_audit_lovable_strict_publish.py` |
| No `execute-plans` repo modification | scope is limited to Pantheon files in this task |

## Current Packet State

This packet is ready for an operator or CI job to run against the next strict
Lovable deployment. The generated JSON/Markdown audit result should be stored
beside this template when that deployment URL is available.

## Published Implementation

Initial implementation was published through the task PR and merged to `dev`.
Codex2 resumed ownership after chair reassignment from Gemini because Gemini
hit repeated terminal quota failures on this task.

Task PR: https://github.com/ajoe734/pantheon/pull/62
Review handoff PR: https://github.com/ajoe734/pantheon/pull/80
Review approval: Codex approved the task on 2026-05-17 after verifying the
script, required env packet, bundle probe documentation, focused tests, and
Pantheon-only scope.
Publication closeout: PR #80 merged into `dev` on 2026-05-17 after the task
branch was refreshed against `origin/dev` and the branch CI gates passed.

## Codex2 Verification

Fresh verification on 2026-05-17:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider scripts/test_audit_lovable_strict_publish.py`
- `python3 -m py_compile scripts/audit_lovable_strict_publish.py scripts/test_audit_lovable_strict_publish.py`
- `git diff --check -- scripts/audit_lovable_strict_publish.py scripts/test_audit_lovable_strict_publish.py support/evidence/LOVABLE-STRICT-PUBLISH/audit_report.md support/evidence/LOVABLE-STRICT-PUBLISH/required_build_env.json support/evidence/LOVABLE-STRICT-PUBLISH/bundle_verification_probe.md`

Result: audit infrastructure is review approved and ready for task closeout.
This task still does not modify the `execute-plans` repository.

## 2026-05-18 Codex Status Recovery

Codex found the implementation and review handoff already merged through PRs
#62 and #80, while the canonical task board still listed
`LOVABLE-STRICT-PUBLISH` as `todo`. The audit infrastructure remains unchanged
from the merged state; this recovery only records the task-board closeout.

Verification rerun on 2026-05-18:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider scripts/test_audit_lovable_strict_publish.py`
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/audit_lovable_strict_publish.py scripts/test_audit_lovable_strict_publish.py`
- `git diff --check -- scripts/audit_lovable_strict_publish.py scripts/test_audit_lovable_strict_publish.py support/evidence/LOVABLE-STRICT-PUBLISH/audit_report.md support/evidence/LOVABLE-STRICT-PUBLISH/required_build_env.json support/evidence/LOVABLE-STRICT-PUBLISH/bundle_verification_probe.md`

## 2026-05-19 Claude2 Status Recovery

Chair reassigned owner from Gemini to Claude2 after Gemini hit terminal quota
exhaustion. Implementation was already complete on the task branch. Claude2
re-verified all acceptance criteria:

- `verify_strict_publish(deployment_url)` returns a complete `AuditResult` dict
  with `strict_env_confirmed` bool and `missing_flags` list.
- `required_build_env.json` lists the three required `VITE_*` keys with exact
  values (`VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  `VITE_BFF_REAL_WRITES=false`).
- Probe correctly detects `/mocks/` and `seed.*` runtime paths.
- Two tests cover the pass and relaxed-flag fail cases.
- No `execute-plans` repo modification.

Verification commands run on 2026-05-19:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider scripts/test_audit_lovable_strict_publish.py`
  → `2 passed in 0.33s`
- `python3 -m py_compile scripts/audit_lovable_strict_publish.py scripts/test_audit_lovable_strict_publish.py`
  → clean

## 2026-05-19 Claude2 Owner Finalization

Owner finalization: Claude2 completed review_approved → done closeout after
Gemini2 (reviewer) approved the task on 2026-05-19.

Final verification commands run on 2026-05-19:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider scripts/test_audit_lovable_strict_publish.py`
  → `2 passed in 0.31s`
- `python3 -m py_compile scripts/audit_lovable_strict_publish.py scripts/test_audit_lovable_strict_publish.py`
  → clean

All acceptance criteria confirmed:
- `verify_strict_publish(deployment_url)` returns `AuditResult` with `strict_env_confirmed` and `missing_flags`.
- `required_build_env.json` lists three required `VITE_*` keys with exact values.
- Probe correctly detects `/mocks/` and `seed.*` runtime path violations.
- Two unit tests cover the pass and relaxed-flag fail cases; pytest exit 0.
- No `execute-plans` repo modification; scope is Pantheon-only.

Status: `LOVABLE-STRICT-PUBLISH` closed to `done` by Claude2 (owner).

## 2026-05-19 Codex Status Reconciliation

Codex was dispatched after the task board drifted back to `todo` even though the
implementation and prior closeout evidence were already merged. Codex confirmed
the task branch is already represented on `origin/dev` through PR #170
(`e90430eb`) and did not modify the `execute-plans` repository.

Focused verification rerun on 2026-05-19:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider scripts/test_audit_lovable_strict_publish.py`
  → `2 passed in 0.39s`
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/audit_lovable_strict_publish.py scripts/test_audit_lovable_strict_publish.py`
  → clean
- `git diff --check -- scripts/audit_lovable_strict_publish.py scripts/test_audit_lovable_strict_publish.py support/evidence/LOVABLE-STRICT-PUBLISH/audit_report.md support/evidence/LOVABLE-STRICT-PUBLISH/required_build_env.json support/evidence/LOVABLE-STRICT-PUBLISH/bundle_verification_probe.md .orchestrator/task-briefs/lovable_strict_publish.md`
  → clean

Closeout action: Codex is completing the owner finalization path after Gemini2's
2026-05-19 review approval. The final status archive is handled through
`scripts/ai-status.sh done` after the task-scoped closeout commit is merged.

PR base refresh: branch refreshed against `origin/dev` at `a6e4e19f` before
final `done` archival.

## 2026-05-19 Claude Owner Recovery (Current)

Chair reassigned owner from Gemini to Claude after Gemini hit repeated terminal
429 quota failures. Implementation was already complete and verified on branch.
Claude re-verified all acceptance criteria:

- `verify_strict_publish(deployment_url)` returns a complete `AuditResult` dict
  with `strict_env_confirmed` bool and `missing_flags` list.
- `required_build_env.json` lists the three required `VITE_*` keys with exact
  values (`VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  `VITE_BFF_REAL_WRITES=false`).
- Probe correctly detects `/mocks/` and `seed.*` runtime paths.
- Two unit tests cover the pass and relaxed-flag fail cases; pytest exit 0.
- No `execute-plans` repo modification; scope is Pantheon-only.

Verification commands run on 2026-05-19:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider scripts/test_audit_lovable_strict_publish.py`
  → `2 passed in 0.35s`
- `python3 -m py_compile scripts/audit_lovable_strict_publish.py scripts/test_audit_lovable_strict_publish.py`
  → clean
