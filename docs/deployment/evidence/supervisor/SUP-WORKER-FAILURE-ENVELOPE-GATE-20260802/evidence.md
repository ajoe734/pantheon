# SUP-WORKER-FAILURE-ENVELOPE-GATE-20260802 evidence

Task: Gate worker failures on authoritative terminal envelopes

Owner: Codex · Reviewer: Human/Ops · Status: **review approved**

## Scope

This task owns the supervisor admission boundary between mixed-trust worker
logs and provider-failure classification. It does not change provider
credentials, account/quota grouping, retry thresholds, reviewer policy,
runtime-state JSON, live process signals, or product behavior.

## Delivered contract

- Plain-text matches enter failure classification only after the runner has
  published a terminal failure marker.
- Structured provider streams may independently admit only explicit terminal
  result/error/failure envelopes or rejected rate-limit control events.
- Structured user/tool-result records and all nonterminal JSON remain
  transcript content; nested quota/auth strings are never regex-scanned.
- Existing filters for source diffs, command output, search-result prefixes,
  captured orchestrator records, and allowed rate-limit notices remain in
  force after the runner gate.
- Missing processes without a runner/provider terminal envelope retain the
  `missing_process` failure kind and cannot create a quota pause.

## Acceptance evidence

- Four captured quota fixture/source forms return no detected worker failure
  without an authoritative envelope.
- Runner/provider terminal cases still classify `auth`,
  `capacity_retryable`, `quota_terminal`, and `terminal`.
- False-positive cases are exercised with a failed runner so the tests prove
  the content filters, not merely the absence of a runner marker.
- Boot reconciliation proves both sides: a Gemini nonzero terminal runner
  preserves quota classification, while a Copilot missing process with only
  quota-like log text remains `missing_process` and creates no dispatch pause.

## Verification

- `python3 scripts/dev/provision_python_distribution.py` — checkout-scoped
  interpreter provisioned and dependency imports verified.
- Focused supervisor suite — 88 passed, 392 deselected, 10 subtests passed.
- Full supervisor regression — 480 passed, 74 subtests passed in 64.33s.
- `python3 -m py_compile .orchestrator/supervisor.py .orchestrator/test_supervisor.py`
  — passed.
- `git diff --check` — passed.

## Review and delivery

Human/Ops approved PR #4482 at exact head
`2f9f77b475cf2287e44da988ccd01a02e450cef4` through the Pantheon canonical
review gate (`success`, status id `51496748158`) at
`2026-08-02T05:30:28Z`. The approved head merged to `dev` as
`4c5dcf4a55307776765bc402454e4a44c61259e2` at
`2026-08-02T05:37:23Z`.

The closeout-only evidence update records that already-governed decision and
repairs the final task commit metadata after the canonical reviewer changed
from Codex2 to Human/Ops. It does not change implementation behavior or the
configured account, reviewer-policy, or runtime layers. The closeout commit
still requires its own exact-head Human/Ops review before it may merge.
