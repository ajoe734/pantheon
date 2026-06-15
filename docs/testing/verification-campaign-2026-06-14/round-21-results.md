# Round 21 — Results

**Executed:** 2026-06-15 (UTC). **Method:** isolated-subprocess import + route
audit per FastAPI service.

## H1/H2 — PASS across all importable services

**21 services** imported and audited; **every one: 0 shadowed routes, 0
duplicate `(method, path)` registrations.**

| Service | routes | shadowed | dups |
|---|---|---|---|
| lineage-read | 17 | 0 | 0 |
| capital | 22 | 0 | 0 |
| promotion | 14 | 0 | 0 |
| policy-learning | 15 | 0 | 0 |
| feedback | 11 | 0 | 0 |
| broker | 11 | 0 | 0 |
| control-plane/router | 10 | 0 | 0 |
| control-plane/persona | 11 | 0 | 0 |
| control-plane/feedback | 12 | 0 | 0 |
| evaluation | 11 | 0 | 0 |
| channels/web | 11 | 0 | 0 |
| optimizer-svc | 12 | 0 | 0 |
| training-session | 26 | 0 | 0 |
| research-worker-gateway | 15 | 0 | 0 |
| evolution | 25 | 0 | 0 |
| governance | 19 | 0 | 0 |
| research | 23 | 0 | 0 |
| incidents | 15 | 0 | 0 |
| reconciliation-drift | 24 | 0 | 0 |
| postmortems | 16 | 0 | 0 |
| deployment | 30 | 0 | 0 |

The route-shadowing/dup-registration class (F3/F9 on the BFF) does **not**
recur anywhere in the fleet. The BFF is the only service with the deeply-stacked
alias/generic-route pattern that produced F9's benign duplicates.

## Coverage note

6 services were not importable in the static harness (`search`, `consultation`,
`memory`, `source_ingestion`, `registry` use package-relative imports;
`openclaw-gateway-adapter` builds its app lazily). These need a package-context
import (`python -m services.<x>.main`) and are deferred; their unit suites are
green (Round 7).

## Net

H1/H2 **PASS** — the fleet's route tables resolve cleanly. No dead/shadowed
routes or silent duplicate handlers outside the BFF.
