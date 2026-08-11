# Supervisor Evidence: SUP-AI-CHECK-FILTER-20260811

## Task

- ID: `SUP-AI-CHECK-FILTER-20260811`
- Title: Classify optional PR diagnostics without blocking merges
- Owner: Codex
- Reviewer: Antigravity2

## Reproduction

- Target PR: #4741 (`task/SUP-PROVIDER-REPORT-CALL-SIGNATURE-20260811`)
- Exact target head: `231125cf1b00c5dd758927d87ab1af9255aca347`
- Failing check: `Audit signed canonical review (not required issuer) (4741)`
- Workflow: `Canonical Review Attestation Audit`
- GraphQL `isRequired(pullRequestNumber: 4741)`: `false`
- Original `mergeStateStatus`: `UNSTABLE`
- Initial `auto_integrator` output: `PR #4741 has failing checks: Audit signed canonical review (not required issuer) (4741).`

The 2026-08-11 independent GraphQL readback still returned the failed audit as
explicitly non-required at the same target head. PR #4741 has since merged via
the governed path; this source task did not directly merge it.

## Remediation

1. Enrich `gh pr view` rollups from GraphQL `isRequired` values, keyed by check
   type plus check name/context.
2. Ignore a failed or pending context only when requiredness is exactly false
   and `workflowName` identifies the known read-only diagnostic workflow.
3. Keep optional actual CI failures blocking. For example,
   `Python packaging provision` remains a failure even though GitHub currently
   reports it as non-required, because it comes from `Branch CI Gate` rather
   than the diagnostic audit workflow.
4. Treat missing, malformed, unmatched, or conflicting requiredness and missing
   or unknown workflow provenance as blocking. A duplicate identity is required
   whenever any matching node is required.
5. Accept `UNSTABLE` only after the enriched rollup is green under these rules;
   review binding, exact-head, trailer, required-check, and merge-state gates
   remain unchanged.
6. Include ignored diagnostic names in dry-run and successful merge output so
   the allow decision is auditable.

## Verification

- `python3 scripts/dev/provision_python_distribution.py`: passed.
- `.venv-pantheon/bin/python3 -m unittest scripts/git/test_auto_integrator.py`:
  30 tests passed.
- `.venv-pantheon/bin/python3 -m py_compile scripts/git/auto_integrator.py scripts/git/test_auto_integrator.py`:
  passed.
- Live PR #4744 production-shape enrichment at remote head
  `3ffa3f494fe7d092c71fa45acb7d21be121a0f75`: summary `green`, no required
  failure or pending check, and exactly one ignored diagnostic audit. The PR
  remained `BLOCKED` pending independent review, and no merge was attempted.
- `git diff --check`: passed.
- After composing `origin/dev` commit
  `7f93be09e3f40e9f11a28cf6c0766e116a54adc6` at merge commit
  `2092d52ca3901a14a2956c2f02cb1f496033f4a7`, the same 30 tests, `py_compile`,
  JSON validation, and `git diff --check origin/dev...HEAD` passed; the base
  introduced no overlapping task-artifact changes.

The final exact-head decision must be supplied by Antigravity2 through the
governed review command with `evidence.json` bound as `REVIEW_FILE`; a committed
manifest cannot contain its own commit SHA.
