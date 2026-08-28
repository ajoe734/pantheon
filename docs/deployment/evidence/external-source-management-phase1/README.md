# External Data Source Management Phase-1 Hosted Acceptance

Status: **functional closure passed / hosted proof follow-up**

This directory is the task-scoped acceptance workspace for
`SRCM-P1-HOSTED-ACCEPTANCE-20260824` (SD-SRCM-08). Human/Ops authorized
functional closure on 2026-08-28 without treating credential, security,
exact-pair authorization, or hosted external proof as blockers. This directory
still does not contain accepted hosted browser evidence. The previous ten 1×1 PNG files and
hand-written HAR summaries were placeholders and have been removed; they must
not be used as delivery evidence.

## Verified deployment prerequisites

| Component | Repository | Live commit | Evidence |
|---|---|---|---|
| BFF | `ajoe734/pantheon` | `dcb14231d29f08f1646a4ee962b83fd2d4b67560` | `GET /bff/version` and `/healthz` |
| Frontend | `ajoe734/execute-plans` | `c230fc76bef78fc297135152f2acba690314bb9d` | `GET /deployment.json` |
| Source definitions | `ajoe734/pantheon` | `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` (last observed) | authenticated identity remains follow-up |

The frontend manifest and live BFF have zero SHA drift. The normal frontend
profile is `read-only`, with `VITE_BFF_REAL_WRITES=false`. These facts are
necessary prerequisites; they do not prove that the ten hosted journeys ran.

## Functional closure

- The original bounded attempt in workflow run
  [33027147575](https://github.com/ajoe734/execute-plans/actions/runs/33027147575)
  remains immutable failure history in
  `hosted-write-proof-attempt-33027147575.json`.
- Human/Ops subsequently attested that three egress-denied DLQ entries were
  replayed through two deduplicated executions. One exclusive TWSE/TPEx manual
  pull completed with `ran=1`, `failed=0`, `excluded=92`; final readback has
  pending and unresolved DLQ counts at zero.
- Normal Source posture is restored to `PANTHEON_EXTERNAL_EGRESS=deny` and the
  durable internal owner is `reconcile_only`, `MAX_TICKS=0`, restart
  `unless-stopped`. This is not a recurring provider pull. Product command
  flags, frontend real writes, and capital writes remain disabled.
- Public FE/BFF reachability, exact FE-to-BFF manifest binding, BFF readiness,
  unauthenticated rejection, and invalid-login rejection were re-probed on
  2026-08-28. The details and canonical Human/Ops event binding are in
  `functional-closure-20260828.json`.

`browser-evidence.json` is intentionally a pending-capture manifest. The
hosted summary and journey receipt JSON files are unaccepted candidate inputs
retained for comparison; they do not become hosted evidence until a real HAR
binds every receipt exchange and the complete verifier passes.

Functional scope is independently verifiable without upgrading those claims:

```bash
python3 scripts/verify_external_source_management_acceptance.py --functional-only --offline-only
python3 scripts/verify_external_source_management_acceptance.py --functional-only
```

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
6. exact FE/BFF identity, bounded capture-window Source authority, and restored
   read-only plus durable zero-egress `reconcile_only` normal posture; and
7. a root `evidence.json` status of `passed` with checksum bindings for every
   required JSON artifact.

Until those artifacts exist, both full offline and full live hosted verification
must exit non-zero even though `--functional-only` passes:

```bash
python3 scripts/verify_external_source_management_acceptance.py --offline-only
python3 scripts/verify_external_source_management_acceptance.py
```
