# SVC-CP-RTR-001 Review Packet

**Sidecar Task ID**: `SVC-CP-RTR-001-SIDECAR-REVIEW`
**Parent Task**: `SVC-CP-RTR-001`
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Parent Status**: `in_progress`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `review_packet`
**Date**: `2026-04-17`

> This is a support artifact only. It does not modify canonical truth, L1 policy, or core runtime/registry/governance implementations.

## 1. Current Snapshot

Per `ai-status.json`, `SVC-CP-RTR-001` is the Phase 7 deployment slice for wiring `services/control-plane/router/` into `docker-compose.yml`.

Current parent-task state:

- `owner=Codex`
- `reviewer=Claude`
- `status=in_progress`
- latest resolved implementation handoff: router compose entry was added with `services/control-plane/router/Dockerfile`, `PERSONA_URL=http://persona:8002`, `depends_on` on `operator-bff` and `governance`, host port `18003`, and healthcheck against `GET /health`
- latest resolved review note: add `persona: service_healthy` under `router.depends_on`, because router dispatches to `PERSONA_URL=http://persona:8002` and should not become healthy before that upstream is ready

This sidecar packet packages the repo-local evidence for the compose/build/health shape only. It does not supersede the already-open parent review change request above.

Within that narrower scope, the packet highlights two additional reviewer-relevant gaps:

1. the delivered compose entry does not set a `PORT` env var even though the planning summary mentions it
2. this packet found config-level evidence, but no separate runtime artifact showing `docker compose up router` was actually executed and healthy

## 2. Parent Acceptance Surface

From `ai-status.json` and the planning-session materialization entry for `SVC-CP-RTR-001`, the parent task is expected to:

1. add the `router` service to `docker-compose.yml`
2. wire build context to `services/control-plane/router/Dockerfile`
3. add dependency ordering for `operator-bff` and `governance`
4. expose the router on compose with a healthcheck
5. per planning summary, include `PORT` env alongside the compose entry

The explicit acceptance list stored in `ai-status.json` is narrower:

1. `docker compose up router` results in a passing healthcheck
2. `router` appears in `docker-compose.yml`

Separately, the handoff log in `ai-status.json` shows an already-issued parent review change request: add `persona: service_healthy` under `router.depends_on`.

## 3. Evidence Summary

### 3.1 Compose Entry

`docker-compose.yml:311-329` now contains a `router:` service with:

- build context `services/control-plane/router`
- Dockerfile `Dockerfile`
- environment `PERSONA_URL: http://persona:8002`
- `depends_on` with `operator-bff` and `governance`, both gated by `service_healthy`
- port mapping `18003:8001`
- healthcheck polling `http://127.0.0.1:8001/health`

This is direct evidence that the parent task satisfied the compose-entry portion of the slice.

### 3.2 Router Image / Port Surface

`services/control-plane/router/Dockerfile:1-15` shows:

- base image `python:3.11-slim`
- `pip install -r requirements.txt`
- `EXPOSE 8001`
- `uvicorn main:app --host 0.0.0.0 --port 8001`

This means the container port is hard-coded to `8001` at image startup time. The compose entry is compatible with that image shape.

### 3.3 Router Health Endpoint

`services/control-plane/router/main.py:30-32` defines the app and reads `PERSONA_URL` from env.

`services/control-plane/router/main.py:194-201` exposes:

- `GET /health`
- response body with `status: "ok"`
- `service: "router"`
- `persona_url`
- `session_ttl_seconds`

This matches the compose healthcheck target at `docker-compose.yml:324-325`.

## 4. Acceptance Mapping

| Acceptance / expectation | Evidence | Status |
|---|---|---|
| `router` service appears in `docker-compose.yml` | `docker-compose.yml:311-329` | Pass |
| Build points at `services/control-plane/router/Dockerfile` | `docker-compose.yml:312-314` plus `services/control-plane/router/Dockerfile:1-15` | Pass |
| `depends_on` includes `operator-bff` and `governance` | `docker-compose.yml:317-321` | Pass |
| Parent review-requested `depends_on persona` is present | No `persona:` entry under `router.depends_on` in `docker-compose.yml:317-321` | Open parent gap |
| Healthcheck exists and targets router `/health` | `docker-compose.yml:324-325` plus `services/control-plane/router/main.py:194-201` | Pass |
| Compose entry provides router-to-persona env wiring | `docker-compose.yml:315-316` plus `services/control-plane/router/main.py:32` | Pass |
| `PORT` env is present, as stated in planning summary | No `PORT:` entry under `router.environment`; image starts uvicorn on fixed `8001` | Gap |
| `docker compose up router` was actually run and healthy | No repo-local command output or evidence artifact found in this sidecar pass | Unverified |

## 5. Reviewer Findings

| Finding | Severity | Detail |
|---|---|---|
| Router compose entry is present and structurally correct | ✅ | Build context, env, dependencies, port mapping, and healthcheck all align with the existing router service surface |
| Healthcheck target is coherent with the service implementation | ✅ | Compose probes `/health` on `8001`, and router exposes `/health` on `8001` |
| Parent task already has an unresolved `persona` dependency review request | Medium | `ai-status.json` records a prior review note asking for `persona: service_healthy`, but the current compose entry still only gates on `operator-bff` and `governance` |
| `PORT` env from the planning summary was not implemented | Medium | The planning/session summary explicitly mentions `PORT env`, but the final compose entry relies on the Dockerfile's fixed `--port 8001` instead |
| Runtime acceptance is not independently evidenced here | Medium | The acceptance text says `docker compose up router` should pass healthcheck, but this packet did not find a test log, screenshot, or command artifact proving that run |

## 6. Recommended Reviewer Disposition

For `SVC-CP-RTR-001`, the current evidence supports a config-level conclusion that the router compose wiring was added correctly.

However, this packet should be read together with the already-open parent review request to add `persona: service_healthy`.

Beyond that existing parent gap, two further review choices remain for the parent reviewer:

1. accept the fixed-port implementation as sufficient and treat the missing `PORT` env as non-blocking because the image already binds `8001`
2. request a small follow-up from the parent owner to either:
   - add the missing `PORT` env for consistency with the planning summary, or
   - explicitly justify why the fixed Dockerfile port makes `PORT` unnecessary

Separately, if the parent reviewer wants the acceptance text enforced literally, they should ask the parent owner for a concrete `docker compose up router` health-pass artifact before approving the parent task.

## 7. Handoff Briefing for Codex

This sidecar packet is ready for review as support-only material for the parent task.

Suggested sidecar approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/SVC-CP-RTR-001/SVC-CP-RTR-001-SIDECAR-REVIEW.md \
  REVIEW_NOTES_ZH="審查通過||review packet 已整理 router compose 與 health evidence；並明確標出 PORT env 未實作與 compose-up 健康證據未附兩個 reviewer 注意點" \
  bash scripts/ai-status.sh approve SVC-CP-RTR-001-SIDECAR-REVIEW \
  "Review packet verified: router compose evidence is summarized and the remaining reviewer checks are clearly called out."
```

After sidecar approval, parent-task disposition remains with parent reviewer `Claude` and parent owner `Codex`. This packet should be treated as reviewer intake support, not as an automatic approval of `SVC-CP-RTR-001`.
