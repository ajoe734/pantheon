# PKT-003-post-incident-review — Delivery Note

## Status

**Delivered** — the blocking incident-list contract gap is resolved. The
Post-Incident Review Console can resume its Lovable UI cycle against the
existing Pantheon endpoints.

## Delivery Summary

Delivered by: Codex (pantheon-bff-worker)
Delivered at: 2026-04-16
Source payload: `.coordination/requests/PKT-003-post-incident-review-bff-gap.yaml`

Three endpoints are required for the Post-Incident Review Console:

| Endpoint | Status |
|---|---|
| `GET /api/v1/incidents?status=resolved` | **Contract-ready** — `resolved_at` present per row |
| `GET /api/v1/operator/post-incident-review/{incident_id}` | **Contract-ready** — all required fields present |
| `GET /api/v1/postmortems` | **Navigation-only** — no blocking gap |

---

## Fixed BFF Gap

### Applied change

`services/control-plane/bff/main.py` now projects:

```python
"resolved_at": incident.get("resolved_at")
```

inside `_project_incident_home_item()`.

### Why this clears the blocker

- The list panel now receives `items[].resolved_at` for resolved incidents
- The UI no longer needs to infer close timestamps or block on a missing field
- Existing compatibility field `opened_at` remains available and is non-blocking

---

## Contract Verification

### `GET /api/v1/incidents?status=resolved`

Verified shape now includes:

- `items[].incident_id`
- `items[].title`
- `items[].status`
- `items[].artifact_id`
- `items[].resolved_at`
- `page_info.next_page_token`
- `meta.snapshot_at`

### `GET /api/v1/operator/post-incident-review/{incident_id}`

Still contract-ready with:

- `data.incident.*`
- `data.postmortem`
- `data.evolution_decisions[]`
- `data.lineage_edges[]`
- `data.telemetry_performance`
- `meta.snapshot_at`
- all required `meta.surfaces.*` keys

### `GET /api/v1/postmortems`

Still navigation-only and non-blocking for this screen.

---

## Next UI Cycle

Lovable should resume implementation using the refreshed contract-ready packet
and backend-delivery response. If the live payload diverges again, emit a fresh
`bff-gap` payload rather than working around the contract in the UI.
