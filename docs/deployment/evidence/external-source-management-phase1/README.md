# External Data Source Management Phase-1 Hosted Acceptance

Status: **blocked / not accepted**

This directory is the task-scoped acceptance workspace for
`SRCM-P1-HOSTED-ACCEPTANCE-20260824` (SD-SRCM-08). It does not currently
contain accepted hosted browser evidence. The previous ten 1×1 PNG files and
hand-written HAR summaries were placeholders and have been removed; they must
not be used as delivery evidence.

## Verified deployment prerequisites

| Component | Repository | Live commit | Evidence |
|---|---|---|---|
| BFF | `ajoe734/pantheon` | `63353e4b4de5df80ea9c9975e002ba95266a4bb8` | `GET /bff/version` |
| Frontend | `ajoe734/execute-plans` | `c21df2cfdaf1781cdf6db517a57dc6c718e0e0f9` | `GET /deployment.json` |
| Source definitions | `ajoe734/pantheon` | `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` | connector-definition readback |

The frontend manifest and live BFF have zero SHA drift. The normal frontend
profile is `read-only`, with `VITE_BFF_REAL_WRITES=false`. These facts are
necessary prerequisites; they do not prove that the ten hosted journeys ran.

## Open blockers

- No workflow artifact contains all ten unmocked Playwright journeys.
- No sanitized checksum-bound HAR is present.
- No real, non-placeholder screenshot is present for any journey.
- The served read-only profile cannot execute the mutating journey matrix.
- On 2026-08-26 the dev VM's `source-ingest-scheduler` was observed running as
  a long-lived `reconcile_only` controller with `MAX_TICKS=0`; acceptance
  requires an explicitly bounded manual one-shot (`MAX_TICKS=1`, restart
  policy `no`) and must never enable `reconcile_and_pull` daemon behavior.

`browser-evidence.json` is intentionally a pending-capture manifest. The
remaining JSON files are unaccepted candidate inputs retained for comparison;
they do not become evidence until a real HAR binds every receipt exchange and
the complete verifier passes.

## Fail-closed evidence contract

The verifier now requires all of the following:

1. browser evidence schema v2 with Playwright hosted workflow/run/head
   provenance;
2. zero route interception and a bounded write-proof capture profile;
3. a real sanitized HAR file with a SHA-256 binding;
4. one unique concrete HAR entry matching each journey receipt's method, URL,
   and response status;
5. one unique PNG screenshot per journey, at least 640×360, with a SHA-256
   binding and an observed DOM checkpoint;
6. exact FE/BFF identity, restored read-only defaults, and manual one-shot
   `reconcile_only` Source Ingestion posture; and
7. a root `evidence.json` status of `passed` with checksum bindings for every
   required JSON artifact.

Until those artifacts exist, both offline and live verification must exit
non-zero:

```bash
python3 scripts/verify_external_source_management_acceptance.py --offline-only
python3 scripts/verify_external_source_management_acceptance.py
```
