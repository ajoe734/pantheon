# Dev acceptance stage

After a dev release has been switched and verified (`deployed`), the
`dev-acceptance-stage` job of `.github/workflows/nonprod-deploy.yml` runs
`scripts/dev_acceptance_stage.py`. It never rolls back and never fails the run.
Its result is the **acceptance verdict**: `pending`, `passed` or `failed`.
A release is `accepted` only when `acceptance=passed`; `deploymentState` of the
hosted manifest keeps its meaning (the deployed pair of record).

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
Each feature task adds its own probe (remediation plan RC2); this task ships none.

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
