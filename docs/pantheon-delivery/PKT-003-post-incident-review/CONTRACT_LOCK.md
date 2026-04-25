# PKT-003-post-incident-review — Contract Lock

Locked at: 2026-04-16
Locked by: Codex (pantheon-bff-worker)

This file captures the exact BFF response shapes after resolving the blocking
`items[].resolved_at` gap for the Post-Incident Review Console.

---

## Endpoint 1: `GET /api/v1/incidents?status=resolved` (list panel)

### Observed BFF shape

```json
{
  "items": [
    {
      "incident_id": "inc-20260409-002",
      "title": "Deployment plan plan-F-042 stalled at paper stage",
      "severity": "sev2",
      "status": "resolved",
      "artifact_id": "artifact-042",
      "opened_at": "2026-04-09T08:00:00Z",
      "resolved_at": "2026-04-09T10:30:00Z"
    }
  ],
  "page_info": {
    "next_page_token": null
  },
  "meta": {
    "snapshot_at": "2026-04-16T...",
    "surfaces": {
      "incident_list": {
        "status": "ok",
        "source": "local_snapshot"
      }
    }
  }
}
```

### Contract status

- Required field `resolved_at` is present per resolved incident row
- Top-level envelope structure (`items`, `page_info`, `meta.snapshot_at`) remains aligned
- Extra compatibility field `opened_at` is still present and is non-blocking

---

## Endpoint 2: `GET /api/v1/operator/post-incident-review/{incident_id}` (composed detail)

### Observed BFF shape

```json
{
  "data": {
    "incident": {
      "incident_id": "inc-20260409-002",
      "title": "Deployment plan plan-F-042 stalled at paper stage",
      "status": "resolved",
      "artifact_id": "artifact-042",
      "artifact_version": "v2.1.0",
      "runtime_id": "runtime-042",
      "trace_id": "trace-inc-20260409-002"
    },
    "postmortem": {
      "postmortem_id": "pm-20260409-002",
      "status": "published",
      "root_cause": "Promotion gate timeout was set too low (30s) for artifact validation under load.",
      "action_items": [
        "Increase promotion gate timeout to 120s",
        "Add queue-depth alerting for promotion gate"
      ]
    },
    "evolution_decisions": [
      {
        "id": "evo-dec-001",
        "action_type": "retrain",
        "risk_level": "medium",
        "status": "approved",
        "incident_ref": "inc-20260410-001",
        "artifact_id": "artifact-042"
      }
    ],
    "lineage_edges": [
      {
        "id": "ln-edge-002",
        "from_artifact_id": "artifact-042",
        "to_artifact_id": "artifact-043",
        "relationship": "promoted_to",
        "created_at": "2026-04-10T00:00:00Z"
      }
    ],
    "telemetry_performance": {
      "artifact_id": "artifact-042",
      "artifact_version": "v2.1.0",
      "window": "24h",
      "summary": {
        "total_pnl": -0.12,
        "max_drawdown": 0.125,
        "sharpe_ratio": -0.8,
        "total_trades": 47,
        "fill_rate": 0.94,
        "avg_slippage_bps": 3.2
      },
      "collected_at": "2026-04-10T15:00:00Z"
    }
  },
  "meta": {
    "snapshot_at": "2026-04-16T...",
    "surfaces": {
      "postmortem": { "status": "ok", "source": "local_snapshot" },
      "evolution_decisions": { "status": "ok", "source": "local_snapshot" },
      "lineage": { "status": "ok", "source": "local_snapshot" },
      "telemetry_performance": { "status": "ok", "source": "local_snapshot" }
    }
  }
}
```

### Contract status

No blocking gaps. All required fields from the BFF contract are present.

Implementation note: `meta.surfaces.*` values are objects with a `status` key, not
plain strings. Frontend code must use `surfaces.postmortem.status` for gating checks.

---

## Endpoint 3: `GET /api/v1/postmortems` (navigation only)

### Observed BFF shape

```json
{
  "data": [
    {
      "postmortem_id": "pm-20260409-002",
      "incident_id": "inc-20260409-002",
      "title": "Postmortem: Deployment plan F-042 promotion timeout",
      "status": "published",
      "root_cause": "Promotion gate timeout was set too low..."
    }
  ],
  "meta": {
    "total": 1,
    "staleness": null
  }
}
```

### Contract status

Used for navigation only (not primary data source). No blocking gap for the
Post-Incident Review Console UI.

---

## Blocking threshold

No blocking BFF gaps remain for this screen.
