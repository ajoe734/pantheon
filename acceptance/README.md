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

### Viewer identity and redaction

The dev acceptance stage gives release probes the read-only viewer identity in
`ACCEPTANCE_VIEWER_CLIENT_ID` and `ACCEPTANCE_VIEWER_CLIENT_SECRET` (only when the stage
declares output redaction). A probe must never print them. The stage also redacts, as
`[redacted]`, the value of `ACCEPTANCE_VIEWER_CLIENT_SECRET` and of every non-empty
environment value whose name ends in `_SECRET`, `_TOKEN` or `_PASSWORD` in all captured
check and probe output before it is clipped or written to the verdict. A probe that
needs the identity and does not receive it still exits 77 (skip).

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

## trade-journey-readable probe (`acceptance/probes/trade-journey-readable`)

Task: BFF-TRADE-JOURNEY-READABLE-20261120. Read-only release probe. Logs in at `/bff/auth/dev-login` with
`ACCEPTANCE_VIEWER_CLIENT_ID` / `ACCEPTANCE_VIEWER_CLIENT_SECRET` (never printed); exit 77 naming the missing variable
when either (or `ACCEPTANCE_BFF_URL`) is absent. Reads the first page of `GET /bff/management/trade-journeys`
(`page_size=50`) and fails when no row is read, when rows with a persona have no `persona_name`, when any row has
`persona_is_probe` true, or when `attention=recon_mismatch` returns as many rows as the unfiltered page.
Tests: `acceptance/test_trade_journey_readable_probe.py` with `fixtures/hosted_trade_journeys_20261120.json`.

## Governed paper risk and journey proof

`acceptance/probes/paper-risk-journey` belongs to `PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121`
(RC2 WP2.1 / RC6). It uses the existing policy probe to read all three trading
personas, active tenant bindings and pool policies, and requires current owner
approval of the requested profile. Both pool activation and catalog policy
targets are recognized; tenant, policy identity, version, content digest, target,
expiry, role authority and paper authorization scope must agree. A catalog
reference never falls back to nearby inline policy data. Journey details use
the served BFF contract: `identifiers.binding_id[]` and root `stage_events`.
Every inspected stage must also name the active binding before an immutable
owner event can qualify.

The journey probe requires the standard `ACCEPTANCE_BFF_URL`,
`ACCEPTANCE_FE_URL`, `ACCEPTANCE_BACKEND_SHA`, `ACCEPTANCE_FRONTEND_SHA` and
viewer credentials. Optional `ACCEPTANCE_POLICY_READER_CLIENT_ID/SECRET` are
used only for the governance approval GET. On the existing dev release runner,
the configured `DEV_DEPLOY_SSH_HOST`, `DEV_DEPLOY_SSH_USER`,
`DEV_DEPLOY_SSH_KEY_FILE` and `DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE` enable
`scripts/dev_vm_ssh.sh` owner reads. No additional owner URLs or tokens are
needed: the fixed read commands use installed paper-signal-producer service
credentials for tenant-scoped Capital policy and immutable telemetry GETs.
When no separate policy reader is configured, the existing dev risk-owner
identity is exchanged inside the BFF container solely for the approvals GET.
The transport is limited to the current dev BFF origin. Credentials stay in
the containers; returned errors omit remote exception text.

Alternatively, reference-backed pools accept `ACCEPTANCE_CAPITAL_OWNER_URL`
and `ACCEPTANCE_CAPITAL_OWNER_TOKEN` for the existing tenant-scoped
`/api/capital-pools/{id}/risk-policy?stage=paper` GET; fresh source reads accept
`ACCEPTANCE_TELEMETRY_URL` and `ACCEPTANCE_TELEMETRY_TOKEN` for
`/api/telemetry/events/{id}`. Missing authorized reads fail closed. The probe
creates no grants or policies. A public DTO alone lacks complete sizing metadata.

The exact FE/BFF pair is read before and after. `deployment.json.deployedAt`
sets the cutoff: only natural live source events strictly after it, belonging to
the active binding, persona, tenant and journey, qualify. The probe requires a
governed sizing decision for every expected persona, a pre-broker rejection
with its actual reason and required/available/limit amounts, and a fresh fill
with submission sizing and six passing broker/position/cash reconciliation
comparisons. Both historical URLs are read separately to verify recovered
insufficient-cash evidence and unavailable accounting evidence. They never
supply fresh samples. The companion browser observation and served service
version inventory remain task evidence; probe success alone does not assert
that the browser or every service was verified.

Exit 0 means all probe checks passed; 1 means failed, including unresolved policy,
identity, binding, history, source reads or a changed served pair; 77 means missing
initial release/auth configuration, or pending (`verdict: pending`, `pending: [...]`):
the findings are a non-empty subset of exactly `no_fresh_governed_decision:<persona>`,
`no_fresh_pre_broker_rejection_with_amounts` and
`no_fresh_broker_position_cash_reconciled_fill`. Any other finding alongside them exits 1. Run `timeout 245s acceptance/probes/paper-risk-journey`.
All product reads use GET; only existing dev-login token exchanges use POST.
No trades, policy activation, repair or replay are performed. Dev probes remain
post-switch and do not introduce a deploy gate.

Budget: at most 72 requests (including remote GETs and token exchanges), 240
seconds, 15 seconds per request or SSH call, 8 MiB per
response, 50 list rows and four detailed journeys per persona (three by default;
maximum ten configured personas). Budget exhaustion fails. This is a bounded
sample, not a global absence claim. The release runner owns cadence (one per
release) and its existing capped verdict; the probe writes only stdout and
adds no persistent runtime storage or daily growth. Standalone task evidence is
a fixed git snapshot, with no recurring writer or temporary product files.
Tests: `acceptance/test_paper_risk_journey.py` and
`acceptance/test_paper_risk_policy_binding.py`. Positive fresh events are
explicit synthetic fixtures, never represented as hosted acceptance.

## Daily paper risk journey check (`acceptance/paper_risk_journey_day.py`)

Task: PAPER-RISK-JOURNEY-DAILY-RUNS-20261125 (after PAPER-RISK-JOURNEY-PENDING-AND-DAILY-20261125), run by
`.github/workflows/paper-risk-journey-daily.yml`. Reuses the release probe's `check` unchanged (its pending/failed
split stays) and adds the deadline. Trading sessions are the distinct trade days of decisions released for the
configured personas, read from `GET /bff/management/trade-journeys` (list and detail) and the immutable
`trade_decision` event through the existing telemetry owner read; there is no calendar route and no weekday
arithmetic. The workflow prepares the same pinned SSH transport as the release stage with the existing dev
secrets and variables and removes it afterwards. Only decisions whose `decision_time` is after the cutoff are
judged; older parked decisions released later are ignored and listed (`ignored_before_cutoff`).

Operator HOLD rule (2026-11-25):

- a decision the strategy itself held (`flat_view`, `already_at_target`, no directional view) is neither pass nor failure;
- a directional view (LONG/SHORT) without `pre_trade_sizing` (or sizing not bound to the persona's policy), or a HOLD with
  `candidates_unavailable` / `budget_unavailable`, fails at once, naming persona, decision and reason;
- five trading days after the cutoff without any judgeable (sized) decision for a persona fail;
- five trading days without a refusal or reconciled fill fail (unchanged);
- seven calendar days without any released decision, or with unreadable journey reads, fail; before that they are pending.

Exit 0 passed or pending, 1 failed, 77 missing configuration. Reads: at most 72 + 3 x 13 + 2 requests, 280 seconds.
Tests: `acceptance/test_paper_risk_journey_day.py` (fixed clock, recorded hosted shapes, route/read registration) and
`scripts/test_dev_acceptance_stage.py`. Accepted only after the coordinator runs the workflow once against hosted.

## mgmt-strategy-meeting-research-readable probe (`acceptance/probes/mgmt-strategy-meeting-research-readable`)

Task: BFF-STRATEGY-SYNTHETIC-STUBS-20261120. Read-only release probe. Logs in at `/bff/auth/dev-login` with
`ACCEPTANCE_VIEWER_CLIENT_ID` / `ACCEPTANCE_VIEWER_CLIENT_SECRET` (never printed); exit 77 naming the missing variable
when either (or `ACCEPTANCE_BFF_URL`) is absent. GET only. Fails when `GET /bff/strategies?exclude_state=draft` (all pages)
lists a `SIMULATION paper-only`, `TW official` / `strat-tw-official` row or a retired/unbound `Pantheon Dev Paper Release`
row, or lacks `meta.hidden_unbound_stub_strategy_count` / `meta.hidden_test_persona_strategy_count`; when
`GET /bff/meetings?status=upcoming` lists a dead_letter meeting; or when `GET /bff/research-tasks` lists an `is_non_real`
row or lacks `meta.hidden_non_real_count`. Tests: `acceptance/test_mgmt_strategy_meeting_research_readable.py` with
`fixtures/hosted_mgmt_strategy_meeting_research_20261120.json`.

## meeting-action-followthrough probe (`acceptance/probes/meeting-action-followthrough`)

Task: MEETING-ACTION-TWO-MEETING-PROOF-20261123; the natural hosted observation stays owned by
MEETING-HOSTED-ACCEPTANCE-20261115 (no second job or gate). Read-only (GET only) with the same viewer login as the
other probes. `ACCEPTANCE_MEETING_ACTION_M1` / `ACCEPTANCE_MEETING_ACTION_M2` name the recorded meeting and the natural
next meeting of the same series. Without them the observation is **pending**: it prints `pending` and exits 77, never 0.
Fails on: the same meeting twice, legacy W47 (`mtg-0f7fa4993625ca47`) as proof, tenant/type/subject mismatch, an
action without its canonical Research ticket or with another meeting/series origin, evidence that is nonexistent,
checksum-drifted, or a non-owner reference (generic closed demand, inbox/membership ack), submitter equal to verifier
or either missing, a prior action silently dropped at M2, and a submitted item M2 neither reviews nor cites. Bounded:
two meeting reads plus at most 24 ticket reads, 8 MiB per response; no writer or storage. Pure checker and
falsification tests: `acceptance/test_meeting_action_followthrough.py`. Real-owner regression (needs an isolated
`RESEARCH_MEETING_TEST_DSN`, skipped otherwise): `services/consultation/tests/test_persona_meeting_action_loop.py`.

### trading-pulse-operator consistent read

Heartbeats move `latest_report_at` and `pnl_at` every ~15 s, so comparing the
pulse with raw telemetry read many seconds later was unverifiable on three
releases. The probe now reads the pulse and then every raw telemetry summary
back to back (nothing else in between) and repeats that pair at most 6 times
until `latest_report_at` and `pnl_at` agree for every operator runtime. Every
existing comparison runs on the accepted pair; no tolerance is added. A pair
that never agrees exits 77 with `source_changed_during_read:attempts=6`. If the
raw summaries are identical between two consecutive attempts yet the pulse still
disagrees, the pulse is stale and the probe fails
(`pulse_stale_against_unchanged_source`). The roster bookend check is unchanged.
Budget (unchanged contract): read-only GETs, at most 200 runtimes, 5 MiB and
30 s per request, retries stop after 240 s inside the stage's 300 s timeout;
stdout only, no files, no new writer.
