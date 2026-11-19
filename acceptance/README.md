# Dev acceptance stage

After a dev release has been switched and verified (`deployed`), the
`dev-acceptance-stage` job of `.github/workflows/nonprod-deploy.yml` runs
`scripts/dev_acceptance_stage.py`. It never rolls back and never fails the run.
Its result is the **acceptance verdict**: `pending`, `passed` or `failed`.
A release is `accepted` only when `acceptance=passed`; `deploymentState` of the
hosted manifest keeps its meaning (the deployed pair of record).

## Release probes only

The release acceptance stage judges **the release itself**. It runs only probes that can be
answered from the release right after the switch (identity of the deployed pair, a read of
the just-deployed `/bff/version` or a feature route). A probe that asks about daily system
state (for example "did the personas finish yesterday's cycle?") is **not** a release probe:
the answer does not depend on this release, so it would mark every later release `failed`
until that state recovers. Such daily state probes live outside `acceptance/probes/`, run from
their own daily workflow and report their verdict there. The persona-day probe
(`acceptance/persona_day.py`, workflow `persona-day-acceptance.yml`) is a daily state probe;
it is not discovered by the stage.

### Accepted baseline of record

A release becomes the accepted baseline of record only when its acceptance verdict is `passed`
(`acceptance/latest.json`, see Verdict). A `failed` or `pending` verdict does not advance it, so
the baseline stays on the last release whose stage passed until the next release passes.
The deploy itself, its compensation and the restore of a captured baseline are unchanged:
acceptance never gates or rolls back a deploy.

## Feature acceptance probes

One executable, **read-only** probe per feature chain, placed directly in
`acceptance/probes/` (no subdirectories, no dotfiles, no symlinks, no `.md`/`.json`).

| Item | Contract |
|---|---|
| Owner | One of the first 20 lines must be `# acceptance-owner: <TASK-ID or chain-id>` (`//` also accepted). A probe without it is recorded as failed. |
| Input (env) | `ACCEPTANCE_BACKEND_SHA`, `ACCEPTANCE_FRONTEND_SHA`, `ACCEPTANCE_FE_URL`, `ACCEPTANCE_BFF_URL` |
| Exit `0` | passed |
| Exit `77` | skipped (not applicable to this pair; not a failure) |
| Any other exit, timeout (300 s), non-executable | failed |
| Output | stdout/stderr, last 2000 characters, are kept as the result `detail` |
| Mutation | A probe must not mutate hosted state. State-mutating work is a declared check (below). |

Example (a probe file such as `acceptance/probes/<chain>-bff-version`):

```bash
#!/usr/bin/env bash
# acceptance-owner: SOME-TASK-OR-CHAIN-ID
set -euo pipefail
curl --fail --silent --show-error --max-time 30 "${ACCEPTANCE_BFF_URL%/}/bff/version" >/dev/null
```

The directory may be empty or absent; the stage then runs only the built-in check.
Each feature task adds its own probe (remediation plan RC2); the directory ships `persona-pool-member-rules` (below).

## Declared checks

`acceptance/checks.json` (optional; absent means none): `{"checks": [{"id", "owner", "argv": [...], "mutates": false, "timeout": 300}]}`.
A check with `"mutates": true` runs only while the job holds the qualification
lease; otherwise it is `skipped` with `lease_required` and the verdict stays
`pending` (a required check that did not run is not a pass).

## Verdict

`acceptance-verdict.json` (schema `pantheon.dev-acceptance-verdict.v1`):
`acceptance`, `deployment` (backend/frontend SHA), `evidenceRun` (run id, attempt, URL),
`checks[]` (every result) and `failed[]` (each failing check or probe with its
`owner`, so a coordinator can open or reopen that task or chain).

- Hosted: the file replaces `acceptance/latest.json` beside the release store on the dev VM
  (one document, replaced per release; nothing is appended). It is a sibling of the FE
  `deployment.json`, not a field inside it, because the FE manifest and its release
  directory are digest-pinned by the exact-artifact capture and verify path.
- Run evidence: artifact `dev-acceptance-verdict-<run>-<attempt>`, 30-day retention.

## Persona-day probe (`acceptance/persona_day.py`) — daily workflow only

Task: PERSONA-DAY-ACCEPTANCE-PROBE-TRUTHFUL-20261118. Workflow `.github/workflows/persona-day-acceptance.yml`.

- **Green only on verdict `passed`.** The step runs under `pipefail`, so the probe exit code reaches the job:
  `failed`, an unreadable BFF or a missing credential all make the run red. A test
  (`scripts/test_acceptance_workflows_truthful.py`) fails if an acceptance workflow pipes a probe without `pipefail`.
- **Reads the BFF as the read-only viewer.** The probe exchanges the existing dev-login viewer identity
  (`DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID` var / `DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET` dev secret, passed as
  `ACCEPTANCE_VIEWER_CLIENT_ID/SECRET`) at `/bff/auth/dev-login`; the token lives in memory only and is never
  printed or written. No new secret. GET only apart from that exchange.
- **Failure reasons carry the status:** `auth_failed:401`, `read_failed:503`, `credential_missing:viewer_client`.
  Exit 77 is only for an unset `ACCEPTANCE_BFF_URL`.
- **Cycle day is UTC.** The persona cycle fires at 10:00 UTC; a day is judged once its window ended (10:45 UTC),
  earlier clocks judge the previous UTC day. Reports state `cycle_day` and `cycle_due_at_utc`, so a late-started run
  still judges the stated day. The workflow cron is `30 11 * * 1-5` (one run per weekday, no hosted writer).
- Artifact `persona-day.json`: a few KB (one line per persona report), 30-day retention.

## persona-pool-member-rules probe (`acceptance/probes/persona-pool-member-rules`)

Task: BFF-PERSONA-STRATEGY-VISIBILITY-20261119. Read-only release probe. Logs in at `/bff/auth/dev-login` with
`ACCEPTANCE_VIEWER_CLIENT_ID` / `ACCEPTANCE_VIEWER_CLIENT_SECRET` (never printed); exit 77 naming the missing variable
when either (or `ACCEPTANCE_BFF_URL`) is absent. Reads every page of `GET /bff/personas` (`page_size=200`, following `next_page_token`). For each non-retired, non-archived persona
whose `strategy_pool` surface is ok it fails when a current member has no ok `memberRules` row, or when `pool_health`
reports `cycle_pool_snapshot_mismatch` although the members of the version named by `observed_pool_version` equal the
current members. No cycle observation is not a failure. A run that checks no persona exits 1 with `reason: no_persona_checked`; the JSON report lists
each checked persona id with `ok`/`failed` plus `read`/`skipped` counts (never a token or body). Task:
PERSONA-POOL-PROBE-CHECKS-LIVE-PERSONAS-20261119. Tests: `acceptance/test_persona_pool_member_rules.py` with
`fixtures/hosted_persona_pool_20261119.json` and `fixtures/hosted_strategy_rules_20261119.json`.
