# Review: SVC-CP-RTR-001 — Wire control-plane/router into docker-compose

**Reviewer:** Claude  
**Date:** 2026-04-17  
**Verdict:** APPROVED

## Checklist

| Item | Result |
|---|---|
| Build entry with correct Dockerfile path (`services/control-plane/router`) | ✓ |
| `PORT: "8001"` env present | ✓ |
| `PERSONA_URL: http://persona:8002` matches `main.py` default | ✓ |
| `depends_on: persona` with `service_healthy` (added per reopen) | ✓ |
| `depends_on: operator-bff` with `service_healthy` (retained) | ✓ |
| `depends_on: governance` with `service_healthy` (retained) | ✓ |
| `healthcheck` on `/health` endpoint (defined in `main.py:194`) | ✓ |
| External port `18003:8001` — no conflict with bff (18001) or persona (18002) | ✓ |
| `start_period: 5s` consistent with other services | ✓ |
| No unnecessary volumes (router is stateless) | ✓ |

## Notes

- The Dockerfile CMD hardcodes `--port 8001` rather than reading `$PORT`. This is consistent with how other services are configured and the port matches, so no defect.
- The router only needs `PERSONA_URL` from the environment (confirmed by reading `main.py`); no missing env vars.
- The `operator-bff` and `governance` dependencies are defensive — the router doesn't call them directly but they must be healthy before persona can serve correctly.

## Outcome

All requirements from the original task and the reopen are satisfied. No further changes needed.
