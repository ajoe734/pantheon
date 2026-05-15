# SVC-EVO-001 Acceptance Packet

**Sidecar for:** SVC-EVO-001 — Add Dockerfile and compose entry for evolution service
**Helper kind:** acceptance_packet
**Owner:** Claude
**Reviewer:** Codex2
**Generated:** 2026-04-17
**Status:** accepted (review_approved → done 2026-04-17)

---

## 1. Scope

This packet supports SVC-EVO-001 without modifying canonical truth.
It documents the acceptance checklist, dependency map, and observations for the reviewer.

---

## 2. Artifacts Delivered by SVC-EVO-001

| Artifact | Path | Status |
|---|---|---|
| Dockerfile | `services/evolution/Dockerfile` | Present |
| docker-compose entry | `docker-compose.yml` → `evolution:` stanza | Present |

---

## 3. Acceptance Checklist

### 3.1 Dockerfile

- [x] Base image: `python:3.11-slim`
- [x] WORKDIR set to `/workspace`
- [x] `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1` set
- [x] `requirements.txt` installed via pip
- [x] Source tree copied into image
- [x] Port `8093` exposed
- [x] CMD launches `uvicorn services.evolution.main:app --host 0.0.0.0 --port 8093`

### 3.2 docker-compose.yml entry

- [x] `build.context: .` with `dockerfile: services/evolution/Dockerfile`
- [x] `PORT: "8093"` env var set
- [x] `depends_on` includes `runtime-manager` (condition: `service_healthy`)
- [x] `depends_on` includes `governance` (condition: `service_healthy`)
- [x] `volumes` maps `evolution-data:/data/evolution`
- [x] `ports` maps `18093:8093`
- [x] `healthcheck` configured (10s interval, 5s timeout, 10 retries, 5s start_period)
- [x] `evolution-data` named volume declared in top-level `volumes:`

### 3.3 Health Endpoint

- [x] `GET /health` exists in `services/evolution/main.py:173`
- [x] docker-compose healthcheck calls `http://127.0.0.1:8093/health`

> **Note:** The acceptance criterion in `ai-status.json` references `/__health__`, but the actual
> endpoint implemented in `main.py` and used by the compose healthcheck is `/health`.
> The implementation is consistent with itself; the task acceptance string appears to be a
> copy-paste artifact from another service. Reviewer should confirm this is acceptable or
> update the canonical acceptance string.

---

## 4. Dependency Map

```
evolution service
  └── depends_on (service_healthy)
        ├── runtime-manager   (manages runtime sessions, persona lifecycle)
        └── governance        (evolution review queue, threshold policy)
  └── volume
        └── evolution-data    (/data/evolution — persists evolution state)
  └── external reads (at runtime, not compose-level)
        └── INCIDENT_DATA_DIR=/data/incident (shared read from incident service)
```

---

## 5. Open Items for Reviewer

| # | Item | Severity |
|---|---|---|
| 1 | Health endpoint path: task says `/__health__`, impl uses `/health` | Low — implementation is internally consistent; acceptance string needs correction |
| 2 | `INCIDENT_DATA_DIR` env var is set but there is no compose-level `depends_on` for the incident service — if incident data is absent at startup the service may silently miss data | Low — review whether a soft or hard dependency is needed |
| 3 | No explicit `network:` declaration — evolution uses the default compose network, which is consistent with all other services in this repo | Informational |

---

## 6. Handoff Note

SVC-EVO-001 is in `review` status with Codex as reviewer.
This sidecar packet is provided as supporting material only.
The reviewer of the parent task (Codex) should use this checklist as a reference;
the reviewer of this sidecar (Codex2) should confirm the packet is complete and accurate.
