# Acceptance Packet - FE-INT-GATE-ALIGN-F15

**Sidecar Task ID:** FE-INT-GATE-ALIGN-F15-SIDECAR-ACCEPTANCE
**Parent Task:** FE-INT-GATE-ALIGN-F15 - Align `09-strict-vs-hybrid.spec.ts` to hosted Lovable DOM
**Helper Kind:** acceptance_packet
**Prepared by:** Codex2 (2026-05-14T13:49Z)
**Reviewer:** Claude2 (reassigned from Gemini2 before approval)
**Parent Owner:** Codex2
**Parent Reviewer:** Claude

---

## 1. Scope Reminder

This sidecar is support-only. It does not edit L1 canonical truth, core contract truth, runtime implementation, registry implementation, governance implementation, or execute-plans source.

The purpose is to give the parent owner/reviewer a concrete acceptance checklist and dependency map for closing `FE-INT-GATE-ALIGN-F15` once the hosted Lovable strict-mode deployment gate is satisfied.

---

## 2. Parent State

Current parent state from `ai-status.json`:

| Field | Value |
|---|---|
| Status | `blocked` |
| Owner | `Codex2` |
| Reviewer | `Claude` |
| Waiting for | `Gemini2` |
| Primary artifact repo | `/home/lupin/code/execute-plans` |
| Execute-plans branch | `bff-luv-fe-006-dev-deploy` |
| Hosted FE | `https://pantheon-dev.lovable.app` |
| Dev BFF | `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` |
| F15 spec | `e2e/09-strict-vs-hybrid.spec.ts` |
| Gap evidence | `.lovable/audits/current-run/f15-strict-product-gap.md` |

Parent blocker summary:

- The F15 strict 5xx assertion is not currently green against hosted Lovable.
- The recorded failure is a hosted strict-mode product/deployment gap, not a selector mismatch.
- The strict branch must still fail closed with typed error text and zero seed rows.
- `FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE` tracks the deploy/runtime-hook follow-up needed before the parent can pass.

---

## 3. Acceptance Checklist For Parent Closeout

Do not close `FE-INT-GATE-ALIGN-F15` until all parent criteria below are satisfied.

| # | Criterion | Required Evidence |
|---|---|---|
| 1 | Hosted Lovable dev serves a strict-capable artifact | Hosted bundle contains runtime hook literals such as `pantheon.integration.fallback`, `__PANTHEON_BFF_RUNTIME__`, `__PANTHEON_RUNTIME_CONFIG__`, or equivalent strict runtime config support |
| 2 | Dev BFF is reachable during the hosted run | `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/health` returns healthy JSON near the Playwright run |
| 3 | `PANTHEON_E2E_STRICT=1` hosted F15 run passes twice | Two separate `npx playwright test e2e/09-strict-vs-hybrid.spec.ts --trace=on` runs against `https://pantheon-dev.lovable.app`, each reporting `1 skipped, 2 passed` |
| 4 | Strict 5xx remains fail-closed | Injected `/bff/strategies` 503 renders strict typed error text and does not show `Momentum Quant Alpha`, `FALLBACK DATA`, or `serving mock data` |
| 5 | 4xx typed BFF envelope never falls back | The governed 409 path continues to show no seed fallback in strict mode |
| 6 | No acceptance weakening | Do not change F15 to accept seed fallback in strict mode |
| 7 | Closeout commit location remains correct | Any parent source/spec closeout commit belongs in `/home/lupin/code/execute-plans` on `bff-luv-fe-006-dev-deploy`, per parent task acceptance |

Expected strict run shape:

```text
F15 strict vs hybrid fallback
  - hybrid 5xx injection falls back to mock with a visible live-BFF banner
    skipped because PANTHEON_E2E_STRICT=1
  - strict 5xx injection fails closed without showing mock data
    passed
  - 4xx BffError envelope never falls back to mock
    passed

1 skipped, 2 passed
```

---

## 4. Dependency Map

| Dependency | Role | Current Status |
|---|---|---|
| `FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE` | Required deploy/runtime-hook follow-up for hosted strict behavior | `blocked` in `ai-status.json`, waiting for `Gemini` |
| Hosted Lovable dev deployment | Must serve the strict-capable build before parent rerun | Still open at 2026-05-14T13:49Z |
| `/home/lupin/code/execute-plans` | Source/spec repo for F15 | On branch `bff-luv-fe-006-dev-deploy` during read-only check |
| `7dff8fa` | Follow-up remediation commit | `FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE wire strict runtime override` |
| `104f06b` | Runtime hook foundation commit | `FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE restore dev write gate` |
| `e2e/09-strict-vs-hybrid.spec.ts` | Parent hosted gate | Installs runtime fallback override before navigation and still forbids seed rows in strict mode |
| `.lovable/audits/current-run/f15-strict-product-gap.md` | Evidence record | Records original hosted failure plus local strict runtime-hook verification |
| Dev BFF health endpoint | Required live backend target | Healthy in sidecar read-only check |

Functional dependency chain:

```text
FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE deploy/refresh
  -> hosted Lovable bundle contains strict runtime hook
  -> hosted asset gate passes
  -> run F15 strict hosted Playwright twice
  -> FE-INT-GATE-ALIGN-F15 can move from blocked to review
```

---

## 5. Current Read-Only Evidence

Sidecar read-only checks performed at 2026-05-14T13:49Z.

### Execute-Plans Branch And Commits

```bash
git -C /home/lupin/code/execute-plans branch --show-current
git -C /home/lupin/code/execute-plans log --oneline -20 -- \
  .lovable/audits/current-run/f15-strict-product-gap.md \
  e2e/09-strict-vs-hybrid.spec.ts \
  src/lib/bff-v1/runtimeEnv.ts \
  src/lib/bff-v1/liveTransport.ts \
  src/lib/bff/client.ts
```

Observed:

```text
bff-luv-fe-006-dev-deploy
7dff8fa FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE wire strict runtime override
104f06b FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE restore dev write gate
```

### Dev BFF Health

```bash
curl -fsSL https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/health
```

Observed:

```json
{"status":"ok","service":"operator-bff","version":"0.2.0","timestamp":"2026-05-14T13:49:09Z"}
```

### Hosted Asset Gate

```bash
asset=$(curl -fsSL https://pantheon-dev.lovable.app/ \
  | rg -o '/assets/index-[A-Za-z0-9_-]+\.js' \
  | head -n 1)
printf 'asset=%s\n' "$asset"
curl -fsSL "https://pantheon-dev.lovable.app${asset}" \
  | rg -o 'pantheon\.integration\.fallback|pantheon\.integration\.realWrites|__PANTHEON_BFF_RUNTIME__|__PANTHEON_RUNTIME_CONFIG__|VITE_BFF_FALLBACK|VITE_BFF_REAL_WRITES|readBffEnv' \
  | sort \
  | uniq -c
```

Observed:

```text
asset=/assets/index-CrXlErW2.js
      3 VITE_BFF_FALLBACK
      1 VITE_BFF_REAL_WRITES
```

No matches were observed for `pantheon.integration.fallback`, `pantheon.integration.realWrites`, `__PANTHEON_BFF_RUNTIME__`, `__PANTHEON_RUNTIME_CONFIG__`, or `readBffEnv`.

Interpretation: the hosted asset has changed from the older evidence asset, but the current hosted bundle still does not expose the runtime hook strings expected by the strict-capable follow-up. The parent F15 hosted rerun should wait for this asset gate or equivalent deploy evidence.

---

## 6. Recommended Parent Rerun After Asset Gate Passes

Run from `/home/lupin/code/execute-plans` after hosted Lovable serves the strict-capable artifact:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
PANTHEON_E2E_STRICT=1 \
PLAYWRIGHT_HTML_OUTPUT_DIR=.lovable/audits/current-run/f15-strict-hosted-final-report-run1 \
npx playwright test e2e/09-strict-vs-hybrid.spec.ts --trace=on \
  --output=.lovable/audits/current-run/f15-strict-hosted-final-results-run1 \
  --reporter=list

PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
PANTHEON_E2E_STRICT=1 \
PLAYWRIGHT_HTML_OUTPUT_DIR=.lovable/audits/current-run/f15-strict-hosted-final-report-run2 \
npx playwright test e2e/09-strict-vs-hybrid.spec.ts --trace=on \
  --output=.lovable/audits/current-run/f15-strict-hosted-final-results-run2 \
  --reporter=list
```

Record both result directories and the hosted asset hash in the parent closeout evidence.

---

## 7. Reviewer Notes For Claude2

Please verify:

1. This sidecar stayed inside support-only scope.
2. The parent blocker is represented as hosted deploy/runtime-hook availability, not a spec selector issue.
3. The checklist still requires strict fail-closed behavior and does not weaken F15 acceptance.
4. The dependency chain correctly routes the remaining open gate through `FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE` and hosted Lovable redeploy evidence.

If accurate, approve this sidecar so Codex2 can do formal closeout.

---

## 8. Closeout Note

Reviewer reassignment was applied by the orchestrator because the original reviewer was unavailable. Claude2 approved this support packet on 2026-05-14T14:38:19Z with the following review conclusion:

- support-only scope confirmed
- parent blocker correctly documented as hosted strict-mode deployment gap with asset gate evidence
- checklist enforces strict fail-closed behavior without weakening F15
- dependency chain routes through `FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE`

Closeout verification:

```bash
git diff --cached --check -- support/sidecars/FE-INT-GATE-ALIGN-F15/FE-INT-GATE-ALIGN-F15-SIDECAR-ACCEPTANCE.md
```
