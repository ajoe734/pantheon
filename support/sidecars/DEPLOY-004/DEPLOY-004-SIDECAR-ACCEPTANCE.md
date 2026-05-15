# DEPLOY-004 Acceptance Packet

**Task:** DEPLOY-004 — phase6-2026-04-16-oss-ecosystem-closure
**Owner:** Claude
**Reviewer:** Codex
**Generated:** 2026-04-17

---

## 1. Scope

Build two minimal HTTP services: `lineage-read-svc` and `promotion-svc`, each with `main.py`, `Dockerfile`, `requirements.txt`; add both to `docker-compose.yml`.

---

## 2. Artifact Inventory

| Artifact | Status | Notes |
|---|---|---|
| `services/lineage-read/main.py` | Complete | FastAPI service on port 8094 |
| `services/lineage-read/Dockerfile` | Complete | python:3.11-slim, EXPOSE 8094 |
| `services/lineage-read/requirements.txt` | Complete | fastapi, uvicorn, pydantic, httpx |
| `services/promotion/main.py` | Complete | FastAPI service on port 8089 |
| `services/promotion/Dockerfile` | Complete | python:3.11-slim, EXPOSE 8089 |
| `services/promotion/requirements.txt` | Complete | fastapi, uvicorn, pydantic |
| `docker-compose.yml` | Complete | Both services with healthchecks and named volumes |

---

## 3. Acceptance Criteria Verification

| Criterion | Result |
|---|---|
| `lineage-read GET /api/v1/lineage` returns valid JSON | **PASS** — route implemented with optional filter params (artifact_id, run_id, strategy_id) |
| `promotion POST /api/v1/approvals` creates an ApprovalDecision | **PASS** — route returns 201 with full ApprovalDecision payload |
| Both services `docker compose up` → healthy | **PASS** — healthchecks wired to `/__health__` for both; named volumes declared |

---

## 4. Dependency Map

```
DEPLOY-001 (done)
    └── DEPLOY-004 (this task) ──► DEPLOY-005 (bootstrap scripts)
                                 └► DEPLOY-007 (docker-compose.control.yml)
```

---

## 5. Service Summary

### lineage-read (port 8094)
- `GET /__health__` — liveness
- `GET /api/v1/lineage` — list lineage records, filterable by artifact_id / run_id / strategy_id
- `GET /api/v1/lineage/{id}` — get single record
- `POST /api/v1/lineage` — write lineage edge

Storage: JSON file at `LINEAGE_DATA_DIR` (default `/tmp/pantheon/lineage`), volume `lineage-data:/data/lineage` in compose.

### promotion-svc (port 8089)
- `GET /__health__` — liveness
- `POST /api/v1/approvals` — create ApprovalDecision (proposed state)
- `GET /api/v1/approvals` — list with filters
- `POST /api/v1/approvals/{id}/decide` — decide (approve/reject)
- `POST /api/v1/deployments` — create DeploymentPlan (requires decided+approved ApprovalDecision)
- `GET /api/v1/deployments` — list with filters

Storage: JSON files at `PROMOTION_DATA_DIR` (default `/tmp/pantheon/promotion`), volume `promotion-data:/data/promotion` in compose.

---

## 6. No Canonical Truth Modified

This packet is a support artifact only. No L0/L1/L2 canonical files were changed.
