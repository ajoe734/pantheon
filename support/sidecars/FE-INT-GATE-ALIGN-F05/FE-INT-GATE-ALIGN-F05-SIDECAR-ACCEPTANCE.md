# Acceptance Packet — FE-INT-GATE-ALIGN-F05

**Sidecar Task ID:** FE-INT-GATE-ALIGN-F05-SIDECAR-ACCEPTANCE
**Parent Task:** FE-INT-GATE-ALIGN-F05 — Align 04-sentinel-remediation.spec.ts to hosted Lovable DOM
**Helper Kind:** acceptance_packet
**Prepared by:** Codex (2026-05-14T13:38Z)
**Reviewer:** Claude2
**Parent Owner:** Codex
**Parent Reviewer:** Codex2

---

## 1. Parent Task Summary

FE-INT-GATE-ALIGN-F05 covers `execute-plans/e2e/04-sentinel-remediation.spec.ts`, the hosted Lovable F05 Sentinel remediation gate for the B04 confirm-token behavior.

The parent task's hosted acceptance target is:

| Surface | Target |
|---|---|
| Hosted FE | `https://pantheon-dev.lovable.app` |
| Dev BFF | `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` |
| Execute-plans branch | `bff-luv-fe-006-dev-deploy` |
| Spec | `e2e/04-sentinel-remediation.spec.ts` |

The parent is currently **blocked**, not review-ready. The blocked state is a hosted deployment/product gap, not a selector drift issue.

### Current Diagnosis

Hard-gate run 25846710728 failed both F05 tests because the hosted Lovable UI rendered the B04 Sentinel finding and actions, but did not issue the expected remediation `POST /bff/v5/interventions/{id}/remediate`.

Evidence in `execute-plans/.lovable/audits/current-run/fe-int-gate-align-f05-hosted-write-gate-gap.md` records:

- Hosted DOM renders the B04 drawer, `Open incident`, `Pause persona routing`, and the `執行` button.
- Both hosted tests time out waiting for the remediation POST.
- The original hosted bundle `/assets/index-BYfBkno5.js` lacked `VITE_BFF_REAL_WRITES` and `VITE_BFF_FALLBACK`, so the browser write gate stayed closed and routed through the overlay path.
- Local Vite with `VITE_BFF_REAL_WRITES=true` passed F05 twice, proving the selectors and assertion path were valid.

The product gap was filed as follow-up task `FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE`.

---

## 2. Acceptance Checklist

### Parent Acceptance Criteria

| # | Criterion | Current Status |
|---|---|---|
| 1 | From `execute-plans/`, `npx playwright test e2e/04-sentinel-remediation.spec.ts` passes twice against hosted Lovable | Not satisfied — hosted run is blocked until Lovable dev serves the correct artifact |
| 2 | Assertions align to real hosted Lovable DOM/network; no guessed selector | Satisfied for investigation — hosted DOM rendered the expected B04 finding drawer/actions; failure was POST suppression |
| 3 | Do not downgrade the blueprint pass condition | Satisfied so far — the spec still requires real remediation POSTs and still treats emergency 428 as non-success |
| 4 | If hosted Lovable has a product gap, file a follow-up instead of masking the spec | Satisfied — filed `FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE` |
| 5 | Closeout commit must be in `/home/lupin/code/execute-plans` on `bff-luv-fe-006-dev-deploy` | Pending for parent closeout — execute-plans commits exist, but parent cannot close until hosted acceptance passes |

### Sidecar Constraints

| # | Constraint | Status |
|---|---|---|
| S1 | Support artifacts only | Satisfied — this packet is under `support/sidecars/` |
| S2 | Do not edit L1 canonical truth, core contracts, runtime/registry/governance implementation | Satisfied — no canonical/runtime implementation edits in this sidecar |
| S3 | Hand off packet to assigned reviewer | Satisfied — handed to Claude2 and approved at `2026-05-14T13:55:18Z` |

---

## 3. Test Coverage Map

The current F05 spec has two tests under `F05 Sentinel remediation`.

| # | Test | Required Behavior | Route Evidence |
|---|---|---|---|
| 1 | `treats CONFIRM_TOKEN_REQUIRED as a non-success emergency precondition` | Opens the B04 finding drawer, runs `Pause persona routing`, submits the high-risk confirm dialog without a confirm token, and expects a non-2xx 428 envelope with `CONFIRM_TOKEN_REQUIRED`. The UI must not render this as accepted/queued/completed/success. | Intercepts and records an emergency `POST /bff/v5/interventions/ra_pause_persona_routing_*/remediate`; asserts `calls.emergencyPosts.length > 0` and missing confirm token body. |
| 2 | `allows an advisory Sentinel remediation action to be queued` | Opens the B04 finding drawer, runs `Open incident`, and expects a 202 queued command response. | Intercepts and records an advisory `POST /bff/v5/interventions/ra_open_incident_*/remediate`; asserts `calls.advisoryPosts.length > 0`. |

Key acceptance property: both tests require the UI to issue the live-write remediation POST. A passing overlay-only path is not acceptable.

---

## 4. BFF Contract Surface

Routes exercised or stubbed by the spec:

| Route | Method | Use |
|---|---|---|
| `/bff/me` | GET | Stubbed authenticated session response |
| `/health`, `/healthz`, `/bff/health` | GET | Stubbed health responses |
| `/bff/v5/sentinel/findings` | GET | Stubbed B04 finding payload; `calls.findingGets` proves the page requested the fixture |
| `/bff/v5/interventions/{id}/remediate` | POST | Emergency and advisory remediation command paths; both are route-intercepted and must be issued by the UI |
| Other `/bff/*` GET routes | GET | Neutral empty-list stubs to keep unrelated shell reads from failing |

The spec injects:

- `localStorage["pantheon_operator_token"]`
- `localStorage["pantheon.bff.bearerToken"]`
- `sessionStorage["pantheon.integration.realWrites"]="true"`
- `sessionStorage["pantheon.integration.fallback"]="strict"`

Those sessionStorage keys are effective only after the hosted bundle includes the runtime gate from execute-plans commit `104f06b`.

---

## 5. Dependency Map

| Dependency | Type | Status |
|---|---|---|
| `execute-plans/e2e/04-sentinel-remediation.spec.ts` | Parent implementation artifact | Updated on branch `bff-luv-fe-006-dev-deploy`; current spec injects sessionStorage real-write/fallback keys and still asserts remediation POSTs |
| `execute-plans/.lovable/audits/current-run/fe-int-gate-align-f05-hosted-write-gate-gap.md` | Parent evidence artifact | Records the hosted write-gate gap, local two-pass control evidence, and deployment wait |
| `55ca952` | Execute-plans evidence commit | Records the original hosted write-gate gap |
| `104f06b` | Execute-plans remediation commit | Adds the dev-host-scoped runtime gate and test/runtime updates |
| `49899d0` | Execute-plans evidence commit | Records the deploy wait after the remediation push |
| `FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE` | Follow-up task | Blocked; source remediation exists, but hosted Lovable dev has not served the required artifact |
| Lovable dev deployment | External environment | Open gate owned by Gemini; must deploy an artifact containing `104f06b` so hosted JS includes the sessionStorage override literals |
| `PANTHEON_FE_BASE_URL` | Runtime env | Must be `https://pantheon-dev.lovable.app` for hosted F05 acceptance |
| `PANTHEON_BFF_BASE_URL` | Runtime env | Must be `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` for the FE/BFF integration target |

`FE-INT-GATE-ALIGN-F05` has no formal `depends_on` entries in `ai-status.json`, but it is functionally blocked by the Lovable deployment gate tracked in `FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE`.

---

## 6. Verification Evidence

### Parent Evidence Already Recorded

Hosted F05, headed, against `https://pantheon-dev.lovable.app`:

```bash
cd /home/lupin/code/execute-plans
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_REAL_WRITES=true \
VITE_BFF_FALLBACK=strict \
xvfb-run -a npx playwright test e2e/04-sentinel-remediation.spec.ts --trace=on --headed
# Result: 2 failed — both timed out waiting for remediation POSTs
```

Local Vite control run:

```bash
VITE_BFF_MODE=live \
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
VITE_BFF_REAL_WRITES=true \
npm run dev -- --host 127.0.0.1 --port 5175

PANTHEON_FE_BASE_URL=http://127.0.0.1:5175 \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_REAL_WRITES=true \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/04-sentinel-remediation.spec.ts --trace=on
# Run 1: 2 passed
# Run 2: 2 passed
```

Production-preview proof after `104f06b`:

```bash
VITE_BFF_MODE=live \
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=auto \
VITE_BFF_REAL_WRITES=false \
npm run build

PANTHEON_FE_BASE_URL=http://127.0.0.1:4175 \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_REAL_WRITES=false \
VITE_BFF_FALLBACK=auto \
npx playwright test e2e/04-sentinel-remediation.spec.ts --trace=on --reporter=list
# Run 1: 2 passed
# Run 2: 2 passed
```

### Sidecar Verification Performed

This sidecar performed read-only verification at `2026-05-14T13:38:23Z`.

```bash
cd /home/lupin/code/execute-plans
git rev-parse --abbrev-ref HEAD
git log --oneline -n 8 -- \
  e2e/04-sentinel-remediation.spec.ts \
  .lovable/audits/current-run/fe-int-gate-align-f05-hosted-write-gate-gap.md \
  src/lib/bff-v1/runtimeEnv.ts \
  src/lib/bff-v1/writeGate.ts \
  src/lib/bff-v1/liveTransport.ts \
  src/lib/bff/client.ts
```

Observed branch and relevant commits:

```text
bff-luv-fe-006-dev-deploy
49899d0 FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE record deploy wait
104f06b FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE restore dev write gate
55ca952 FE-INT-GATE-ALIGN-F05 record hosted write gate gap
```

Hosted asset string gate:

```bash
asset=$(curl -fsSL https://pantheon-dev.lovable.app/ \
  | rg -o '/assets/index-[A-Za-z0-9_-]+\.js' \
  | head -n 1)
printf 'asset=%s\n' "$asset"
curl -fsSL "https://pantheon-dev.lovable.app${asset}" \
  | rg -o 'pantheon\.integration\.realWrites|pantheon\.integration\.fallback|VITE_BFF_REAL_WRITES|VITE_BFF_FALLBACK|getRuntimeEnv|realWritesEnabled' \
  | sort \
  | uniq -c
```

Observed:

```text
asset=/assets/index-CrXlErW2.js
      3 VITE_BFF_FALLBACK
      1 VITE_BFF_REAL_WRITES
```

There were no matches for `pantheon.integration.realWrites`, `pantheon.integration.fallback`, `getRuntimeEnv`, or `realWritesEnabled` in the current hosted bundle. Therefore this sidecar did not rerun full hosted Playwright: the asset gate still fails before the E2E rerun can be expected to pass.

### Owner Closeout Verification

Codex re-ran the read-only hosted asset gate during owner closeout at `2026-05-14T13:59:55Z`.

```bash
cd /home/lupin/code/pantheon
asset=$(curl -fsSL https://pantheon-dev.lovable.app/ \
  | rg -o '/assets/index-[A-Za-z0-9_-]+\.js' \
  | head -n 1)
printf 'asset=%s\n' "$asset"
curl -fsSL "https://pantheon-dev.lovable.app${asset}" \
  | rg -o 'pantheon\.integration\.realWrites|pantheon\.integration\.fallback|VITE_BFF_REAL_WRITES|VITE_BFF_FALLBACK|getRuntimeEnv|realWritesEnabled' \
  | sort \
  | uniq -c
```

Observed:

```text
asset=/assets/index-CrXlErW2.js
      3 VITE_BFF_FALLBACK
      1 VITE_BFF_REAL_WRITES
```

The closeout check still found no `pantheon.integration.realWrites`, `pantheon.integration.fallback`, `getRuntimeEnv`, or `realWritesEnabled` literals in the hosted bundle. The sidecar packet remains support-only and the parent open gate remains a Lovable artifact deployment gate.

---

## 7. Open Gate

The remaining gate is deployment, not spec alignment:

| Gate | Owner | Required Evidence |
|---|---|---|
| Lovable dev serves the artifact containing `104f06b` | Gemini / Lovable deployment owner | Hosted bundle includes `pantheon.integration.realWrites` and `pantheon.integration.fallback`; then F05 hosted Playwright passes twice |

Recommended pre-rerun gate:

```bash
asset=$(curl -fsSL https://pantheon-dev.lovable.app/ \
  | rg -o '/assets/index-[A-Za-z0-9_-]+\.js' \
  | head -n 1)
curl -fsSL "https://pantheon-dev.lovable.app${asset}" \
  | rg 'pantheon\.integration\.realWrites|pantheon\.integration\.fallback'
```

If that command has no matches, the hosted rerun should be treated as not ready.

---

## 8. Handoff Notes

- This packet is support-only and should not be treated as canonical truth.
- Parent `FE-INT-GATE-ALIGN-F05` should remain blocked until hosted Lovable dev serves the correct artifact and the F05 spec passes twice against hosted.
- The parent owner should not close F05 based only on local Vite or production-preview evidence; those runs prove source behavior but do not satisfy the hosted acceptance criterion.
- The concrete follow-up path is already tracked in `FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE`.
- Reviewer should focus on whether the dependency map and open gate are accurate enough for the parent owner and Gemini to consume.
- Reviewer approval is recorded in `ai-status.json` with Claude2 notes confirming the packet maps the parent deployment blocker, the `104f06b` artifact dependency, and the hosted bundle string gate.

---

*This packet is a support artifact. No canonical truth was modified.*
