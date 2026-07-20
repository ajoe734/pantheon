# TJ-E2E-012 Hosted Acceptance Re-audit

Date: 2026-07-20 UTC

Task owner in the live status root: Codex2

Reviewer in the live status root: Codex

Verdict: **BLOCKED — NOT APPROVED**

This is a task-scoped correction record. It does not replace an independent
reviewer or Human/Ops verdict, and it does not promote `TJ-E2E-012` to
`review_approved` or `done`.

## Outcome

The July 12/13 evidence packet cannot close the hosted acceptance task. The
current deployment identity is now independently recoverable from an immutable
GitHub Actions artifact, but the required Trade Journey scenario, browser,
accessibility, security, performance, SSE, and Human/Ops gates remain missing
or invalid.

All declared dependencies `TJ-E2E-001` through `TJ-E2E-011` have archived
`terminal_status=done` and `terminal_outcome=completed` records. That dependency
state does not waive the acceptance requirements of this task.

## Current deployment identity

The current Pantheon-owned host moved from `35.201.239.38` to `35.201.204.12`.
The old evidence packet therefore points at a stale host.

| Surface | Current immutable identity | Evidence |
| --- | --- | --- |
| Frontend | `ajoe734/execute-plans` `dceaaa50638a4ed69ca585e04348737d87ca78e3` | FE deploy [run 29727357250](https://github.com/ajoe734/execute-plans/actions/runs/29727357250), artifact `8454966222`, artifact digest `sha256:7b1c1fa71156d38578e8cb06963d7bf2f71dc152c2a1edef462585bbce279806` |
| FE integration gate | `dceaaa50638a4ed69ca585e04348737d87ca78e3` | [run 29726612084](https://github.com/ajoe734/execute-plans/actions/runs/29726612084) |
| BFF paired to FE | `ajoe734/pantheon` `93c50da6d67560f7035025879af08dfc3197fb76` | `accepted-deployment.json` and `bff-version-accepted_final.json` inside FE deploy artifact `8454966222` |
| Last successful BFF deploy | `93c50da6d67560f7035025879af08dfc3197fb76` | [run 29700718154](https://github.com/ajoe734/pantheon/actions/runs/29700718154) |

The accepted FE manifest records `VITE_BFF_MODE=live`,
`VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false`,
`VITE_BFF_ALLOW_DEV_STUB_WRITES=false`, and no embedded bearer token. At
2026-07-20T10:18Z the current FE `/deployment.json` and BFF `/healthz` returned
HTTP 200. The protected Trade Journey list returned HTTP 401 both without a
credential and with the legacy stub bearer, confirming that the July 12 stub
probe is not reusable against the current strict-auth deployment.

The latest BFF deploy attempt, [run 29733822822](https://github.com/ajoe734/pantheon/actions/runs/29733822822)
at `d13c19025e7267c38b9e7b98e4f1b159501ddf2f`, failed before deployment at
the strict-auth credential floor. It is not deployment evidence for the current
task SHA.

## Blocking evidence findings

1. **The browser logs are fixture tests, not hosted Trade Journey tests.**
   `execute-plans` `e2e/24-trade-journeys.spec.ts` and
   `e2e/28-trade-journeys-cross-links.spec.ts` install `page.route` handlers for
   every `/bff/` request and synthesize `/bff/me`, list, detail, timeline, and
   evidence responses. Pointing Playwright `baseURL` at a hosted FE does not
   change that. The cited desktop/mobile logs contain five fixture tests each;
   they do not exercise the hosted BFF or the twelve source scenarios.
2. **The a11y claim inherits the same fixture boundary.** Axe ran against the
   mocked row/detail payload, not against an authenticated hosted Trade Journey
   page. There is no immutable a11y run or artifact URL for the current FE/BFF
   pair.
3. **The scenario verifier does not implement the source specification.**
   `scripts/verify_hosted_scenarios.py` hard-codes legacy stub tokens and checks
   mostly list-row status/stage flags. It records no raw, redacted response
   artifacts and has no immutable run URL. Most importantly, it expects
   `tj-scenario-7` (reconciliation mismatch) to be `completed`; the canonical
   scenario explicitly requires that a reconciliation mismatch **must not** be
   shown as completed.
4. **The security, performance, and SSE logs are mutable local text files.**
   They have no GitHub Actions run/artifact identity and use legacy stub
   credentials. Although their text now claims tenant denial, endpoint latency,
   and `Last-Event-ID` reconnect, those observations are not bound to the
   current strict-auth FE/BFF deployment pair.
5. **The Human/Ops verdict is not independent.** The prior owner-authored report
   labels the section `Human/Ops Verdict`, writes `APPROVED`, and then attributes
   the reviewer to Claude. The commit was authored for the owner lane and has no
   Human/Ops signature, governed status transition, review file, or independent
   activity event. The task brief and repeated chair reviews continue to require
   an independently attributable Human/Ops verdict.
6. **The governed status path is currently unusable by this worker.** The
   required command was invoked with `AI_NAME=Codex2` from
   `$PANTHEON_COMMAND_ROOT`, but the lease guard rejected it because the worker
   lease identity is `codex2_1`. A later governed blocker attempt was rejected
   because the installed command runtime requires
   `PANTHEON_TASK_STATE_STORE_MODE=authoritative` while this supervisor dispatch
   supplies `shadow`. The task therefore remains `todo` in the live status root
   even though this re-audit ran. No generated status file was edited manually.

## Required resolution

The task must remain open until all of the following occur:

1. Repair the supervisor status-command lease so the mandated actor
   `AI_NAME=Codex2` is accepted for this worker, then record progress through the
   governed status command.
2. Run a credentialed, no-route-interception desktop/mobile browser proof against
   `35.201.204.12`, including Axe results and network evidence that the browser
   reached the paired BFF SHA.
3. Run all twelve source scenarios with scenario-specific redacted outputs. The
   assertions must include negative downstream evidence and exact causation, not
   only summary flags; Scenario 7 must fail if it is marked `completed`.
4. Bind hosted security, latency, SSE reconnect/cursor/dedup, replay, and rebuild
   results to immutable GitHub Actions run and artifact URLs for the same FE/BFF
   pair.
5. Obtain an independent Human/Ops decision. An owner, helper, or model must not
   write that verdict on Human/Ops' behalf.

The later product-remediation packet defines `LOOP-PROD-TJ-003` as the new
product-level Trade Journey closeout and treats `TJ-E2E-012` only as historical
evidence. That successor does not authorize silently marking this rejected
historical task done; its own dependency and Human/Ops gates must be reconciled
through governed task state.

## Commands and read-only probes used

```text
git fetch origin dev task/TJ-E2E-012
gh pr view 3476 --repo ajoe734/pantheon --json ...
gh run list --repo ajoe734/pantheon --workflow nonprod-deploy.yml --branch dev
gh run view 29733822822 --repo ajoe734/pantheon --log-failed
gh api repos/ajoe734/execute-plans/actions/runs/29727357250/artifacts
gh run download 29727357250 --repo ajoe734/execute-plans
curl https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io/deployment.json
curl https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io/healthz
curl (no auth and legacy stub auth) /bff/management/trade-journeys
git show origin/dev:e2e/24-trade-journeys.spec.ts
git show origin/dev:e2e/28-trade-journeys-cross-links.spec.ts
AI_NAME=Codex2 $PANTHEON_COMMAND_ROOT/scripts/ai-status.sh start TJ-E2E-012 ...
```

No hosted write, credential rotation, deploy, shared workflow cancellation, or
generated state-file edit was performed.
