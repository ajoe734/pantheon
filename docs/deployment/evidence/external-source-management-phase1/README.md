# External Data Source Management Phase-1 Hosted Acceptance

Status: **blocked / bounded hosted tick failed**

This directory is the task-scoped acceptance workspace for
`SRCM-P1-HOSTED-ACCEPTANCE-20260824` (SD-SRCM-08). It does not currently
contain accepted hosted browser evidence. The previous ten 1×1 PNG files and
hand-written HAR summaries were placeholders and have been removed; they must
not be used as delivery evidence.

## Verified deployment prerequisites

| Component | Repository | Live commit | Evidence |
|---|---|---|---|
| BFF | `ajoe734/pantheon` | `3c79a185a97d920f41005bd41675433a046b6ece` | `GET /bff/version` |
| Frontend | `ajoe734/execute-plans` | `b019b334f6810ab9c3ebc8b9b51b9b3cb3449a57` | `GET /deployment.json` |
| Source definitions | `ajoe734/pantheon` | `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` | connector-definition readback |

The frontend manifest and live BFF have zero SHA drift. The normal frontend
profile is `read-only`, with `VITE_BFF_REAL_WRITES=false`. These facts are
necessary prerequisites; they do not prove that the ten hosted journeys ran.

## Open blocker

- Human/Ops authorized one bounded `reconcile_and_pull` tick using only the
  official public TWSE/TPEx connector. Workflow run
  [33027147575](https://github.com/ajoe734/execute-plans/actions/runs/33027147575)
  reached terminal readback after that exact tick and failed closed because the
  shared Source environment already contained three unresolved DLQ entries.
  No paid broker adapter was selected. Browser capture did not start, so there
  is still no accepted HAR or per-journey screenshot.
- The workflow restored the public FE to `read-only`, BFF/source command flags
  to their effective disabled defaults, external egress to `deny`, and the
  scheduler to stopped with restart policy `no`. Its follow-up manual
  `reconcile_only` readback was itself rejected by an existing controller lease,
  so the complete manual-one-shot acceptance posture is not proven.
- The authorized provider tick has been consumed. Repeating it without a new
  Human/Ops decision would violate the exactly-one scope. The immutable failure
  summary is `hosted-write-proof-attempt-33027147575.json`.

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
