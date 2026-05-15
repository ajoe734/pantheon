# Acceptance Packet - FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE

**Sidecar Task ID:** FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE-SIDECAR-ACCEPTANCE
**Parent Task:** FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE - Enable strict fallback selection on hosted Lovable dev build
**Helper Kind:** acceptance_packet
**Prepared by:** Codex2 (2026-05-14T13:46Z)
**Reviewer:** Codex
**Parent Owner:** Codex
**Parent Reviewer:** Codex2

---

## 1. Scope Reminder

This sidecar is a support artifact only. It does not edit L1 canonical truth, core contract truth, runtime implementation, registry implementation, governance implementation, or execute-plans source.

Its purpose is to give the parent owner/reviewer a compact acceptance checklist and dependency map for the hosted Lovable strict-mode gate.

---

## 2. Parent Task Summary

`FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE` exists because `FE-INT-GATE-ALIGN-F15` found a hosted Lovable product/deployment gap, not a selector mismatch.

The strict F15 gate covers `execute-plans/e2e/09-strict-vs-hybrid.spec.ts`.

| Surface | Target |
|---|---|
| Hosted FE | `https://pantheon-dev.lovable.app` |
| Dev BFF | `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` |
| Execute-plans branch | `bff-luv-fe-006-dev-deploy` |
| Spec | `e2e/09-strict-vs-hybrid.spec.ts` |

Current parent state from `ai-status.json`:

- Parent status: `blocked`
- Parent owner: `Codex`
- Parent reviewer: `Codex2`
- Waiting for: `Gemini`
- Current blocker: source remediation is committed and pushed in execute-plans, local live-mode strict verification passed twice, but hosted Lovable dev still serves a bundle that cannot consume the runtime strict fallback override.

---

## 3. Acceptance Checklist

### Parent Acceptance Criteria

| # | Criterion | Current Status |
|---|---|---|
| 1 | Hosted Lovable dev strict 5xx renders typed error without seed rows | Not satisfied on hosted Lovable until the deployed bundle includes the runtime fallback override hook |
| 2 | `FE-INT-GATE-ALIGN-F15` passes twice with `PANTHEON_E2E_STRICT=1` | Satisfied in local live-mode control evidence; not yet satisfied against `https://pantheon-dev.lovable.app` |
| 3 | Do not change F15 to accept seed fallback in strict mode | Satisfied so far - the spec still asserts strict typed error text and zero `Momentum Quant Alpha` seed rows |

### Sidecar Constraints

| # | Constraint | Status |
|---|---|---|
| S1 | Create support artifacts only | Satisfied - this packet is under `support/sidecars/` |
| S2 | Do not edit canonical truth or core implementation | Satisfied - this sidecar did not edit canonical docs, execute-plans source, runtime, registry, or governance code |
| S3 | Hand off packet to assigned reviewer | Satisfied by handing this packet to Codex for review after creation |

---

## 4. Test Coverage Map

`e2e/09-strict-vs-hybrid.spec.ts` has three acceptance paths:

| # | Test | Required Behavior |
|---|---|---|
| 1 | `hybrid 5xx injection falls back to mock with a visible live-BFF banner` | In auto fallback mode, injected `/bff/strategies` 503 may render the fallback banner and seed row. This branch is skipped when `PANTHEON_E2E_STRICT=1`. |
| 2 | `strict 5xx injection fails closed without showing mock data` | In strict mode, injected `/bff/strategies` 503 must render strict typed error text and must not render `Momentum Quant Alpha` or fallback banner text. |
| 3 | `4xx BffError envelope never falls back to mock` | A governed 409 typed BFF envelope must not activate seed fallback in either strict or auto mode. |

The parent gate is specifically the strict hosted branch. A hosted pass must show `1 skipped, 2 passed` for the strict run, with the hybrid 5xx branch skipped by `PANTHEON_E2E_STRICT=1`.

---

## 5. Dependency Map

| Dependency | Type | Status |
|---|---|---|
| `/home/lupin/code/execute-plans` | Frontend source repo | On branch `bff-luv-fe-006-dev-deploy` during this sidecar check |
| `7dff8fa` | Parent remediation commit | `FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE wire strict runtime override` |
| `104f06b` | Runtime hook foundation commit | Adds dev/Lovable runtime env reading used by both F05 and F15 gates |
| `src/lib/bff-v1/runtimeEnv.ts` | Runtime config hook | Reads `__PANTHEON_BFF_RUNTIME__`, `__PANTHEON_RUNTIME_CONFIG__`, `pantheon.integration.fallback`, and `pantheon.e2e.fallback` on Lovable/dev hosts |
| `src/lib/bff-v1/liveTransport.ts` | Strict transport behavior | Uses `readBffEnv()` so `VITE_BFF_FALLBACK=strict` fails closed instead of falling back to mock data |
| `src/lib/bff/client.ts` | Management mode detection | Uses `readBffEnv()` so management surfaces classify strict mode as `real` |
| `e2e/09-strict-vs-hybrid.spec.ts` | Hosted gate spec | Installs runtime fallback override before navigation; strict test still forbids seed rows |
| Hosted Lovable dev deploy | External deployment gate | Still open; must serve an artifact that contains the runtime hook/string literals |
| Lovable auth/session | Deployment trigger prerequisite | Parent status says stored auth state is expired; Gemini/Lovable deploy owner must refresh/redeploy |

Functional dependency chain:

```text
FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE
  -> Lovable dev redeploy/refresh
  -> hosted asset includes runtime strict override hook
  -> hosted strict F15 Playwright run passes twice
  -> FE-INT-GATE-ALIGN-F15 can be unblocked without weakening strict acceptance
```

---

## 6. Verification Evidence

### Parent Evidence Already Recorded

Initial hosted failure evidence is recorded in:

- `/home/lupin/code/execute-plans/.lovable/audits/current-run/f15-strict-product-gap.md`

Observed hosted failure before remediation:

- `/bff/strategies` 503 was injected.
- Hosted Lovable rendered `live BFF unavailable Injected F15 5xx - serving mock data`.
- Hosted Lovable showed the `FALLBACK DATA` badge.
- Hosted Lovable still displayed the seed row `Momentum Quant Alpha`.

That evidence proves the original F15 failure was a real strict-mode product/deployment gap.

The same artifact records follow-up local verification after the runtime hook:

```bash
cd /home/lupin/code/execute-plans
npm run test -- src/lib/bff/__tests__/liveTransportSnapshot.test.ts
npm run test -- src/lib/bff-v1/__tests__/writes.test.ts
npm run build

PANTHEON_FE_BASE_URL=http://127.0.0.1:5173 \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
PANTHEON_E2E_STRICT=1 \
npx playwright test e2e/09-strict-vs-hybrid.spec.ts --trace=on --reporter=list
# Run 1: 1 skipped, 2 passed
# Run 2: 1 skipped, 2 passed
```

### Sidecar Read-Only Checks

This sidecar performed read-only checks at `2026-05-14T13:46Z`.

```bash
git -C /home/lupin/code/execute-plans rev-parse --abbrev-ref HEAD
git -C /home/lupin/code/execute-plans log --oneline -n 12 -- \
  e2e/09-strict-vs-hybrid.spec.ts \
  .lovable/audits/current-run/f15-strict-product-gap.md \
  src/lib/bff-v1/runtimeEnv.ts \
  src/lib/bff-v1/liveTransport.ts \
  src/lib/bff/client.ts \
  src/lib/bff/__tests__/liveTransportSnapshot.test.ts
```

Observed:

```text
bff-luv-fe-006-dev-deploy
7dff8fa FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE wire strict runtime override
104f06b FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE restore dev write gate
```

Hosted asset string gate:

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

There were no matches for `pantheon.integration.fallback`, `pantheon.integration.realWrites`, `__PANTHEON_BFF_RUNTIME__`, `__PANTHEON_RUNTIME_CONFIG__`, or `readBffEnv` in the current hosted bundle. This means the current hosted asset is not yet the strict-capable runtime-hook artifact expected by the parent task.

This sidecar did not rerun full hosted Playwright because the asset gate still fails before the E2E rerun can be expected to pass.

---

## 7. Open Gate And Required Evidence

The remaining gate is deployment, not F15 assertion design.

| Gate | Owner | Required Evidence |
|---|---|---|
| Lovable dev serves the strict-capable artifact | Gemini / Lovable deploy owner | Hosted bundle includes runtime hook literals such as `pantheon.integration.fallback` or `__PANTHEON_BFF_RUNTIME__` |
| Hosted strict F15 passes twice | Parent owner after deploy | Two hosted runs of `e2e/09-strict-vs-hybrid.spec.ts` with `PANTHEON_E2E_STRICT=1`, each producing `1 skipped, 2 passed` |

Recommended acceptance rerun after the asset gate passes:

```bash
cd /home/lupin/code/execute-plans

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

Do not close the parent by accepting hybrid seed fallback in strict mode. The parent acceptance remains "typed error and no seed rows" for injected 5xx.

---

## 8. Reviewer Notes For Codex

Please verify:

1. The sidecar stayed inside support-only scope.
2. The parent blocker is represented as a hosted Lovable deploy/artifact mismatch, not a spec-selector problem.
3. The hosted asset gate evidence is current enough for the parent owner to decide whether to rerun Playwright.
4. The acceptance rerun still requires strict fail-closed behavior and does not weaken F15 acceptance.

If accurate, approve this sidecar and return it to Codex2 for final closeout.

---

## 9. Closeout Note

Owner closeout check by Codex2 at 2026-05-14T13:55Z:

- Reviewer approval is present in `ai-status.json` with reviewer `Codex`.
- Reviewer note confirms the packet stayed under `support/sidecars`, did not change canonical/runtime artifacts, and correctly represents the current hosted Lovable blocker as deploy/artifact refresh.
- No additional canonical, runtime, registry, governance, or execute-plans edits are required for this sidecar.

Focused verification commands used for closeout:

```bash
sed -n '1,260p' .orchestrator/task-briefs/fe_int_gate_followup_f15_strict_lovable_sidecar_acceptance.md
jq '.tasks[] | select(.id=="FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE-SIDECAR-ACCEPTANCE")' ai-status.json
sed -n '1,280p' support/sidecars/FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE/FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE-SIDECAR-ACCEPTANCE.md
git status --short -- support/sidecars/FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE/FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE-SIDECAR-ACCEPTANCE.md ai-status.json current-work.md ai-activity-log.jsonl ai-task-archive/tasks/FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE-SIDECAR-ACCEPTANCE.json
```
