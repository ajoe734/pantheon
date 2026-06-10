# Contract: postmortem_bridge — POST-EVO-BRIDGE

**Task:** POST-EVO-BRIDGE  
**Owner:** Claude2  
**Reviewer:** Codex2  
**Phase:** Sprint 7 / EPIC-EVOLUTION-FOLLOWUP  
**Status:** done — review approved; closeout complete  

---

## Purpose

`postmortem_bridge.py` is the auto-trigger bridge that connects the
postmortem-published event (POST-001) to the EvolutionDecision governance flow
(EVO-001). It is a pure transformation module: it reads a postmortem event
payload and decides whether to emit an `EvolutionDecisionProposal` dict.

The bridge **never** writes to the governance store. It returns a proposal dict
that the caller (governance or supervisor) must admit through the existing
`POST /api/evolution/proposals` endpoint.

---

## Public Interface

```python
from services.evolution.postmortem_bridge import on_postmortem_published, PostmortemBridgeError

proposal: dict | None = on_postmortem_published(postmortem_event: dict)
```

### Input: postmortem event payload dict

| Field | Type | Required | Description |
|---|---|---|---|
| `postmortem_id` | str | yes | unique postmortem id |
| `incident_id` | str | yes | parent IncidentCase id |
| `severity` | str | yes | `low \| medium \| high \| critical` |
| `artifact_id` | str | yes | artifact under execution at incident time |
| `artifact_version` | str | yes | artifact version |
| `corrective_action_required` | bool | no (default false) | explicit corrective flag |
| `deployment_stage` | str | no | `paper \| canary \| live \| frozen` |
| `evidence_refs` | list[dict] | no | upstream evidence refs forwarded into proposal |

The `severity` field carries the severity of the linked `IncidentCase`.
It is the caller's responsibility to propagate this field from the incident
record into the event payload before calling the bridge.

### Output: EvolutionDecisionProposal dict (or None)

| Field | Type | Description |
|---|---|---|
| `source_postmortem_id` | str | postmortem that triggered this proposal |
| `source_incident_id` | str | parent IncidentCase |
| `proposed_action` | str | one of `rollback \| freeze \| retrain \| revalidate \| redeploy \| retire` |
| `cooldown_window_hours` | int | recommended governance cooldown |
| `evidence_refs` | list[dict] | bridge ref + incident ref + upstream refs |
| `target_artifact_id` | str | artifact under evaluation |
| `target_artifact_version` | str | artifact version |
| `target_deployment_stage` | str \| None | forwarded from event payload |
| `rationale` | str | human-readable trigger rationale |
| `created_by_id` | str | always `"postmortem-bridge"` |
| `created_by_role` | str | always `"evolution_controller"` |

Returns `None` when no proposal is warranted (severity below trigger threshold
and `corrective_action_required` is false or absent).

---

## Trigger Rules

Rules are evaluated in priority order; first match wins.

| Priority | Condition | proposed_action | cooldown_window_hours |
|---|---|---|---|
| 1 | `severity == "critical"` | `freeze` | 168 (7 days) |
| 2 | `severity == "high"` | `rollback` | 72 (3 days) |
| 3 | `corrective_action_required == true` | `retrain` | 24 (1 day) |
| 4 | _(otherwise)_ | _(no proposal)_ | — |

**Severity overrides corrective flag.** When severity is `high` or `critical`,
the severity-based action is returned even if `corrective_action_required` is
also true.

---

## Error Handling

`PostmortemBridgeError` (subclass of `ValueError`) is raised for:

- input is not a `dict`
- any required field is missing or empty
- `severity` is not one of `low | medium | high | critical`

The bridge is fail-fast. Callers must validate event payload completeness
before calling the bridge, or handle `PostmortemBridgeError` and route the
malformed event to a dead-letter queue.

---

## Isolation Guarantees

- **No governance store writes.** The bridge only computes and returns a dict.
- **No HTTP calls.** The bridge is a pure function with no I/O side effects.
- **No POST-001 / EVO-001 API changes.** The bridge is an independent module.
- **Input dict not mutated.** The caller's dict is never modified.

---

## Integration Path

```
postmortem published event
        │
        ▼
on_postmortem_published(event_dict)
        │
   ┌────┴────┐
   │ None    │  ← severity low/medium, no corrective flag → drop
   │ proposal│  ← caller POSTs to /api/evolution/proposals
   └─────────┘
```

The bridge is called by whichever component subscribes to the
`postmortem.published` event — the supervisor event bus, a background worker,
or the incident service itself after a postmortem reaches `PUBLISHED` status.

---

## Acceptance Verification

```bash
cd /home/lupin/code/pantheon
python3 -m pytest services/evolution/test_postmortem_bridge.py -q
```

Expected: all tests pass, exit 0.

---

## Dependency Map

| Dependency | Status | Note |
|---|---|---|
| POST-001 | done | Postmortem schema and endpoint (incident service) |
| EVO-001 | done | EvolutionDecision service — proposal admission target |

The bridge reads the Postmortem schema shape for field names only. It does
not import from `services/incident/incident.py` at runtime to remain fully
decoupled.
