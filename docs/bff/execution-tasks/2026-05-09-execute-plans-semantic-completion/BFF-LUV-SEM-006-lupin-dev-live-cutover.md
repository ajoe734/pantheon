# BFF-LUV-SEM-006 — Lupin Dev Live Cutover

Date: 2026-05-09
Owner lane: deployment / worker ops
Reviewer lane: integration acceptance

## Problem

Local BFF route coverage is fixed, but the public lupin dev target still runs the old BFF:

- `/openapi.json` returns 500
- most final `/bff/*` paths still return 404

The frontend cannot switch from mock to live until the patched BFF is deployed and verified on the actual public target.

## Scope

- Build and deploy the BFF patch to the lupin dev target.
- Run the anonymous probe against `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`.
- Confirm `/openapi.json` returns 200 and includes final contract paths.
- Confirm all final contract paths are registered on live: anonymous routes should be 2xx or 401, not 404 or 500.
- Publish probe evidence under `documents/` or `docs/bff/evidence/`.

## Non-Scope

- Do not claim frontend live cutover complete until the public target probe passes.
- Do not switch Lovable to live mode if live BFF still has 404/500 contract failures.

## Acceptance

- Public lupin dev `/openapi.json` returns 200.
- Public lupin dev final BFF anonymous probe has zero 404 and zero 500.
- The probe evidence records exact date, target URL, command, and per-route statuses.
- Lovable/frontend handoff explicitly says whether `VITE_BFF_MODE=live` is safe.

## Live Cutover Result

Status: complete on 2026-05-09T11:31:36Z.

Target:

- `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`

Evidence:

- `docs/bff/evidence/BFF-LUV-SEM-006-lupin-dev-live-probe-20260509T113136Z.json`

Commands run:

```bash
python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_final_command_execution_bridge.py services/control-plane/bff/test_bff_agora_core_contract.py services/control-plane/bff/test_bff_agora_extended_contract.py services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py -q
COMPOSE_BAKE=false PANTHEON_ENV=dev PANTHEON_LIVE_BROKER_ENABLED=false PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app docker compose -p pantheon -f docker-compose.yml up -d --build operator-bff
curl -sS http://127.0.0.1:18001/health
curl -sS http://127.0.0.1:18001/readyz
curl -sS https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/openapi.json
python3 inline urllib anonymous probe over FINAL_CONTRACT_METHOD_PATHS from services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py
```

Results:

- Focused pre-deploy suite: 63 passed, 6 pre-existing warning-class messages.
- Dev stack health: `operator-bff` healthy after rebuild. The compose command was broader than intended because it omitted `--no-deps`, so dependent dev services were also rebuilt/restarted; post-command `docker compose ps` showed the stack healthy.
- Public `/openapi.json`: 200 with 338 paths.
- Final contract anonymous probe: 113 concrete routes, status distribution 2x 200 and 111x 401.
- Final contract 404/500 count: 0.

Lovable/frontend handoff:

- `VITE_BFF_MODE=live` is safe for the final execute-plans BFF contract on lupin dev.
- Use `VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`.
- The older support-only sidecar packet was prepared before SEM-002 and SEM-004 reached `done`; its pending/blocker language is now superseded by the archived done snapshots and this live evidence.
