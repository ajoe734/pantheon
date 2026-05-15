# OPS-GEM-REDEPLOY-001 BFF and Frontend Handoff Packet

Task ID: OPS-GEM-REDEPLOY-001-SIDECAR-BFF-HANDOFF
Parent task: OPS-GEM-REDEPLOY-001
Helper kind: bff_handoff_packet
Owner: Claude
Reviewer: Codex
Prepared: 2026-05-15
Mutates canonical truth: false

## Scope

Support-only handoff packet for OPS-GEM-REDEPLOY-001 (Gemini Lovable redeploy
and dev BFF credential unblock). This packet does not change L1 policy, route
truth, runtime implementation, registry state, or governance behavior.

Contents:

- summary of what OPS-GEM-REDEPLOY-001 completed vs what remains open
- BFF query surface for each downstream blocked task
- operator journey for next verification actions
- frontend handoff map for tasks that can now be re-attempted
- remaining blockers and open items for parent owner

## Parent Task Outcome Summary

Evidence recorded at `support/evidence/OPS-GEM-REDEPLOY-001.md` and
`support/evidence/OPS-GEM-REDEPLOY-001/`.

| Acceptance item | Status |
|---|---|
| Lovable dev bundle refreshed and asset hash recorded | **Met** — `index-vlevju41.js`, sha256 `8f7acc9b...` |
| Bundle contains `VITE_BFF_FALLBACK` (5 matches) | **Met** |
| Bundle contains `VITE_BFF_REAL_WRITES` (2 matches) | **Met** |
| Bundle points to dev BFF `pantheon-lupin-dev-bff.34.81.75.241.sslip.io` (7 matches) | **Met** |
| Dev BFF bearer credential documented and authenticated smoke passed | **Met** — bearer `pantheon-dev-browser:reviewer`, all 30/30 routes passed |
| Lovable preview branch URL | **Partial** — candidate `id-preview-a7067bd5--...lovable.app` exists but auth-bridges for unattended worker |
| F05 hosted E2E passes twice with new bundle | **Met** |
| F15 hosted strict E2E passes twice with new bundle | **Met** |
| ME-STARTUP hosted `/bff/me` now intercepted (was 0) | **Progress** — no longer `interceptedMeRequests=0`, but 401 path still renders hybrid text |
| BFF-CONSOL-022 Day 1 probe env usable for authenticated read smoke | **Met** |

## BFF Query Surface

### Dev BFF base URL

```text
https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
```

Health check: `GET /health` → 200, response shape `{service, status, timestamp, version}`.
OpenAPI: `GET /openapi.json` → 200.

### Dev bearer credential

```text
pantheon-dev-browser:reviewer
```

This is the dev-only browser bootstrap token. It is non-secret and embedded in
the Lovable hosted bundle. It is not a production credential. Use it for dev
smoke probes and Day 1 authenticated read paths only.

Authenticated smoke command:

```bash
PANTHEON_BFF_SMOKE_BEARER_TOKEN='pantheon-dev-browser:reviewer' \
  python3 scripts/probe_bff_authenticated_live.py \
  --base-url https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
  --output support/evidence/OPS-GEM-REDEPLOY-001/authenticated-live-dev-bff.json
```

Result: 32 total, 32 passed, 30 read probes, 0 write probes, 0 failures.

### `/bff/me` contract

Route: `GET /bff/me`
Contract marker: `BFF-LUV-GAP-009`
Source: `services/control-plane/bff/main.py`
Response envelope: `{ data, meta }`

Response `data` fields confirmed by probe (`auth_sha12: 008288ce7ac0`):

| Field | Present |
|---|---|
| `data.user` / `data.currentUser` / `data.current_user` | Yes |
| `data.roles` | Yes |
| `data.capabilities` | Yes |
| `data.tenant` / `data.tenant_id` | Yes |
| `data.locale` | Yes |
| `data.environment` | Yes |
| `data.feature_flags` | Yes |
| `data.session` | Yes |

Expected fail-closed responses:

| Case | Status | Frontend expectation |
|---|---:|---|
| No valid auth, strict backend | `401` | Render auth/error state; never fall back to mock or seed data |
| Tenant outside allowed scope | `403` | Render backend error; never fabricate tenant state |
| Caller lacks read role | `403` | Render backend error |

### `/bff/v5/interventions/{id}/remediate` (F05 write path)

Route: `POST /bff/v5/interventions/{id}/remediate`
Required bundle env: `VITE_BFF_REAL_WRITES=true` (now present in `index-vlevju41.js`)
Auth: bearer `pantheon-dev-browser:reviewer` (or valid session cookie)

The new bundle includes `VITE_BFF_REAL_WRITES` and the hosted F05 spec now
observes the POST path. No further write-gate gap in the bundle.

### `/bff/v5/strategies` (F15 strict 5xx path)

Route: `GET /bff/v5/strategies`
Required bundle env: `VITE_BFF_FALLBACK=strict` (now present in `index-vlevju41.js`)
Strict behaviour: 5xx injection causes typed error without seed rows (not hybrid)

The new bundle includes `VITE_BFF_FALLBACK` and F15 strict spec passed twice.

### SSE stream

Route: `GET /bff/events/stream?lastEventId=...`
Observed in hosted browser probe: `200` response confirmed.
The hosted bundle now connects SSE from the correct dev BFF URL.

## Operator Journey

### Journey 1 — BFF-CONSOL-022 Day 1 Authenticated Soak (Partially Unblocked)

What is available now:

```text
Codex (BFF-CONSOL-022 owner):
  1. Configure preview-strict.env with:
     VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
     VITE_BFF_FALLBACK=strict
     VITE_BFF_REAL_WRITES=false
  2. Run authenticated BFF read smoke against dev BFF:
     PANTHEON_BFF_SMOKE_BEARER_TOKEN='pantheon-dev-browser:reviewer' \
       python3 scripts/probe_bff_authenticated_live.py \
       --base-url https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
  3. Run Pack A/B/C + detail pytest as documented in BFF-CONSOL-022 brief.
```

What still needs Gemini or human action:

```text
BFF-CONSOL-022 strict preview Day 1 hosted E2E soak:
  - Candidate preview URL: https://id-preview-a7067bd5--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app
  - Problem: unattended workers hit a Lovable auth-bridge redirect for this URL.
  - Required: either (a) a public-access Lovable preview URL without auth-bridge,
    or (b) an authenticated Lovable browser session that can navigate the preview.
  - Gemini should either provide a directly accessible preview URL, or confirm
    that the authenticated browser context in the Lovable project can be reused
    to run probe_bff_authenticated_live.py against the preview URL.
```

### Journey 2 — FE Hosted Blockers (F05, F15 Cleared; ME-STARTUP Narrowed)

```text
F05 (FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE, FE-INT-GATE-ALIGN-F05):
  Status: UNBLOCKED — new bundle includes VITE_BFF_REAL_WRITES, F05 passed twice.
  Action: Codex should re-run hosted acceptance and close F05 tasks.

F15 (FE-INT-GATE-ALIGN-F15, FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE):
  Status: UNBLOCKED — new bundle includes VITE_BFF_FALLBACK=strict, F15 passed twice.
  Action: Codex2/Codex should re-run hosted strict acceptance and close F15 tasks.

ME-STARTUP (FE-INT-GATE-FOLLOWUP-ME-STARTUP, FE-INT-GATE-ALIGN-F01):
  Status: PARTIALLY UNBLOCKED — /bff/me is now intercepted (interceptedMeRequests > 0).
  Remaining blocker: 401 path still renders hybrid-mode text "HYBRID / live / seed fallback armed"
    instead of an auth/error state without seed rows.
  Evidence: support/evidence/OPS-GEM-REDEPLOY-001/me-startup-hosted-test-results/
  This is a frontend product gap in the new bundle, not a stale-bundle issue.
  Action: Codex2 (ME-STARTUP owner) must investigate why the 401 path still
    renders hybrid seed status in the new bundle and fix or file a product-gap follow-up.
```

### Journey 3 — Lovable Preview URL Resolution

```text
Required for: BFF-CONSOL-022 Day 1 strict preview soak

Options (in preference order):
  A. Gemini provides a Lovable preview URL that does not require Lovable auth-bridge.
     Verify: curl -L <url> responds 200 without redirect to lovable.dev/auth-bridge.
  B. Gemini documents how to run probe_bff_authenticated_live.py inside an
     authenticated Lovable browser context (e.g., using Lovable CLI or cookies).
  C. Gemini confirms the main dev deployment (pantheon-dev.lovable.app) is
     sufficient for the strict preview soak, and closes the preview URL requirement.

If option C: BFF-CONSOL-022 Day 1 soak should proceed against
  https://pantheon-dev.lovable.app with strict env overrides and
  PANTHEON_BFF_SMOKE_BEARER_TOKEN='pantheon-dev-browser:reviewer'.
```

## Frontend Handoff Map

Tasks that can be re-attempted with the new bundle:

| Task | Files | Status change |
|---|---|---|
| FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE | `e2e/04-sentinel-remediation.spec.ts` | UNBLOCKED: run hosted twice |
| FE-INT-GATE-ALIGN-F05 | `e2e/04-sentinel-remediation.spec.ts` | UNBLOCKED: run hosted twice |
| FE-INT-GATE-ALIGN-F15 | `e2e/09-strict-vs-hybrid.spec.ts` | UNBLOCKED: run hosted twice |
| FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE | `e2e/09-strict-vs-hybrid.spec.ts` | UNBLOCKED: run hosted twice |

Tasks with narrowed (not cleared) blockers:

| Task | Remaining blocker | Evidence |
|---|---|---|
| FE-INT-GATE-FOLLOWUP-ME-STARTUP | 401 path renders hybrid seed text despite `/bff/me` being intercepted | `support/evidence/OPS-GEM-REDEPLOY-001/me-startup-hosted-test-results/` |
| FE-INT-GATE-ALIGN-F01 | Depends on ME-STARTUP resolution | Inherits above |
| BFF-CONSOL-022 | Lovable preview URL that does not auth-bridge | `support/evidence/OPS-GEM-REDEPLOY-001.md` preview URL section |

Hosted acceptance commands for unblocked tasks:

```bash
# F05 hosted — run twice
cd /home/lupin/code/execute-plans
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict VITE_BFF_REAL_WRITES=true \
  npx playwright test e2e/04-sentinel-remediation.spec.ts \
  --trace=on --reporter=list \
  --output=/tmp/f05-hosted-acceptance-rerun

# F15 hosted strict — run twice
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict PANTHEON_E2E_STRICT=1 \
  npx playwright test e2e/09-strict-vs-hybrid.spec.ts \
  --trace=on --reporter=list \
  --output=/tmp/f15-hosted-acceptance-rerun
```

## Remaining Open Items For Parent Owner

These items must be resolved or explicitly accepted before OPS-GEM-REDEPLOY-001
can close:

1. **Lovable preview URL** — Candidate recorded but auth-bridges for unattended
   workers. Parent owner (Codex) must either obtain a public preview URL from
   Gemini or agree with reviewer that the main dev deployment is sufficient.

2. **ME-STARTUP 401 hybrid text** — The new bundle now calls `/bff/me` on
   startup (progress), but the 401 path still renders hybrid-mode seed fallback
   text. This is a product gap in the frontend source, not in the bundle
   deployment. Codex2 (ME-STARTUP owner) should investigate in the new bundle
   source or file a targeted follow-up before ME-STARTUP and F01 can close.

3. **BFF-CONSOL-022 strict preview Day 1 soak** — Cannot fully start until
   Lovable preview URL is accessible or main dev deployment is accepted as the
   soak target.

## Acceptance And Verification Evidence

All evidence is in `support/evidence/OPS-GEM-REDEPLOY-001/`:

| Artifact | Description |
|---|---|
| `OPS-GEM-REDEPLOY-001.md` | Main evidence file: bundle hash, credential, probe results, acceptance mapping |
| `authenticated-live-dev-bff.json` | Full authenticated BFF probe output (30/30 routes pass) |
| `hosted-browser-bff-probe-2026-05-15.md` | Browser probe: asset hash, /bff/v5/control-room 200, SSE observed |
| `f05-hosted-test-results/` | F05 E2E run 1 (passed) |
| `f05-hosted-test-results-run2/` | F05 E2E run 2 (passed) |
| `f15-hosted-test-results/` | F15 strict E2E run 1 (2 passed, 1 skipped) |
| `f15-hosted-test-results-run2/` | F15 strict E2E run 2 (2 passed, 1 skipped) |
| `me-startup-hosted-test-results/` | ME-STARTUP 401 path (1 failed — hybrid text blocker) |

## Parent Absorption Notes

- The F05/F15 clearance is durable: parent owner can reference the evidence
  directories above in BFF-CONSOL-022 Day 1 soak artifacts.
- Do not promote this packet's BFF contract table to L1 route truth; it is an
  operational summary derived from the probe output and existing BFF source.
- The ME-STARTUP 401 hybrid text gap is a new clean blocker. Record it as a
  task-level blocker in FE-INT-GATE-FOLLOWUP-ME-STARTUP before closing
  OPS-GEM-REDEPLOY-001.
- Lovable preview URL remains the only acceptance item where OPS-GEM-REDEPLOY-001
  is only partially satisfied. The parent owner must decide whether to accept
  the candidate URL with human authentication or request Gemini to provide a
  public URL.

## Reviewer Checklist

Codex should verify:

| Check | Expected |
|---|---|
| Support-only scope | Only this sidecar artifact authored by the task, plus status updates |
| No canonical mutation | No L1 policy, route truth, registry, runtime, or governance implementation changed |
| Evidence traceability | All evidence refs point to files under `support/evidence/OPS-GEM-REDEPLOY-001/` |
| Acceptance mapping accuracy | Table matches current parent task acceptance in `ai-status.json` |
| Remaining blocker clarity | Packet clearly distinguishes what is met vs what is still open |
| No archived sidecar IDs reused | Task ID `OPS-GEM-REDEPLOY-001-SIDECAR-BFF-HANDOFF` is fresh |

## Handoff

This packet is ready for Codex review. The sidecar deliverable is complete once
the task is moved to review with this artifact attached.
