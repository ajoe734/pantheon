# AUTO-HARDEN-CW03-001 — BFF & Frontend Handoff Packet

**Sidecar type:** `bff_handoff_packet`
**Parent task:** `AUTO-HARDEN-CW03-001` (Harden CW-03 committee board truth source)
**Prepared by:** Claude
**Reviewer:** Codex
**Date:** 2026-04-20
**Status:** ready for review

---

## 1. Scope of this packet

This packet is a support artifact only. It does not modify canonical truth (no edits to
`docs/bff/CW-03-committee-board.md`, `services/control-plane/bff/main.py`,
`services/control-plane/bff/read_store.py`, or any L1 policy file).

The parent task `AUTO-HARDEN-CW03-001` (owned by Codex2) must absorb these findings and
decide which hardening actions to materialise.

---

## 2. Current implementation summary

### Routes confirmed live

| Route | Handler | Status |
|---|---|---|
| `GET /api/v1/committees` | `list_committees` | implemented |
| `GET /api/v1/committees/{committee_id}` | `get_committee` | implemented |
| `POST /api/v1/operator/commands` — `RecordSponsorDecision` | command dispatch + worker | implemented |

### Key functions

| Function / method | File | Purpose |
|---|---|---|
| `_cw03_committee_projection` | `main.py:1184` | Full detail projection; all contract fields present |
| `_cw03_committee_surface_state` | `main.py:1140` | Derives `"ok"/"stale"/"degraded"/"unavailable"` |
| `_cw03_allowed_actions` | `main.py:1162` | `canRecordSponsorDecision` authority gate |
| `_validate_record_sponsor_decision` | `main.py:1218` | Pre-flight validation for command payload |
| `read_store.list_committees` | `read_store.py:6285` | Projects committee rows from consult sessions |
| `read_store.get_committee` | `read_store.py:6321` | Builds full committee detail from backing sessions |
| `read_store.record_sponsor_decision` | `read_store.py:6382` | Applies sponsor decision mutation to local snapshot |
| Command worker dispatch | `main.py:8423` | Calls `record_sponsor_decision` and marks command EXECUTED |

---

## 3. BFF query gaps and hardening items

### GAP-CW03-01 — `linked_evidence` shape mismatch

**Location:** `read_store.py:6379`, `main.py:1207`

**Problem:**
`read_store.get_committee` derives `linked_evidence` from:

```python
"linked_evidence": json.loads(json.dumps(consult.get("evidence_refs") or []))
```

The backing consultation data stores `evidence_refs` as a list of plain string IDs (e.g.
`["telemetry-vol-spike-20260419", "dp-20260419-014"]`). The contract and canonical example
(`docs/examples/CW-03-committee-board.json`) require rich objects:

```json
{
  "id": "telemetry-vol-spike-20260419",
  "type": "evidence_link",
  "evidence_type": "telemetry",
  "artifact_ref": "artifact-042",
  "description": "Volatility spike - 2026-04-19",
  "link": "/telemetry/events/telemetry-vol-spike-20260419"
}
```

**Risk:** Frontend receives raw strings; any component relying on `linked_evidence[].type` or
`.link` will silently fail or show blank.

**Hardening path (for Codex2):**
- Add a `_resolve_evidence_ref(ref: str | dict) -> dict` helper in `read_store.py` that
  normalises string refs into the canonical shape.
- Call it inside `get_committee` when building `linked_evidence`.
- Add a contract test asserting the first item in `linked_evidence` has all required fields.

---

### GAP-CW03-02 — `canRecordSponsorDecision` does not guard missing sponsor assignment

**Location:** `main.py:1162–1181`

**Problem:**
The authority check for `canRecordSponsorDecision`:

```python
return {
    "canRecordSponsorDecision": (
        sponsor_decision in (None, "")
        and consensus_state == "sponsor_required"
        and bool(roles.intersection({"operator", "reviewer", "approver", "admin"}))
    )
}
```

It does not verify that `committee.get("sponsor_assignment")` is non-empty. A committee
where `consensus_state` is accidentally set to `"sponsor_required"` but no sponsor has been
assigned would expose the CTA incorrectly.

**Contract rule (non-goal):**
> The client must not show a sponsor-decision CTA when `allowedActions` is absent or false.

The BFF must enforce this server-side.

**Hardening path (for Codex2):**
Add a guard:

```python
sponsor_assignment = committee.get("sponsor_assignment")
has_sponsor = bool(sponsor_assignment and sponsor_assignment.get("participant_id"))
```

…and require `has_sponsor` in the `canRecordSponsorDecision` predicate.

---

### GAP-CW03-03 — `record_sponsor_decision` returns `None` with no error when local snapshot absent

**Location:** `read_store.py:6391–6405`

**Problem:**
```python
consultation_sessions = self._local_fallback("consultation_sessions")
if consultation_sessions is None:
    return None
```

If `_local_fallback` returns `None` (no local snapshot data), the function silently returns
`None`. The command worker at `main.py:8433` converts this to:

```python
raise ValueError(f"Committee {committee_id} could not be updated.")
```

This produces a generic internal error with no surface-state context. An operator who submits
a sponsor decision during a degraded-surface moment gets a 500-equivalent with no recovery
guidance.

**Hardening path (for Codex2):**
- Distinguish "local snapshot absent" from "committee not found" in `record_sponsor_decision`.
- Propagate a typed result (e.g. `{"error": "surface_unavailable"}`) so the command worker
  can set `CommandStatus.FAILED` with a user-readable `precondition_failed` reason.

---

### GAP-CW03-04 — `synthesis_summary.evidence_refs` vs `linked_evidence` not reconciled

**Location:** `read_store.py:6378–6379`

**Problem:**
The backing data has one `evidence_refs` field, used as:
- `synthesis_summary.evidence_refs` — list of IDs (correct per contract)
- `linked_evidence` — also pulled from `evidence_refs`, but contract requires rich objects

These point to the same source data. After GAP-CW03-01 is fixed (`linked_evidence` is
enriched), `synthesis_summary.evidence_refs` must remain as IDs (the contract requires
`string[]` there). The fix must not accidentally enrich `synthesis_summary.evidence_refs`.

**Hardening path (for Codex2):**
Verify the fix for GAP-CW03-01 only touches the `linked_evidence` key, not
`synthesis_summary["evidence_refs"]`.

---

### GAP-CW03-05 — Overly broad role set for sponsor-decision authority

**Location:** `main.py:1179`

**Problem:**
```python
and bool(roles.intersection({"operator", "reviewer", "approver", "admin"}))
```

The contract says: *"The command is only valid when `allowedActions.canRecordSponsorDecision`
is `true`."* The BFF contract doc (`docs/bff/CW-03-committee-board.md`) does not define which
operator roles may record a sponsor decision. The current implementation allows `"reviewer"`
which is a read-only role in other parts of the system (e.g. governance review queue).
Mixing write authority into `"reviewer"` is inconsistent.

**Hardening path (for Codex2):**
- Confirm with the L1 policy or `BINDING_AND_DEPLOYMENT_SEMANTICS.md` which roles carry
  sponsor-decision write authority.
- Remove `"reviewer"` from the allowed set unless policy explicitly grants it.
- Add a test that a `"reviewer"`-only token gets `canRecordSponsorDecision: false`.

---

## 4. Operator journey (for frontend integration)

The committee board screen follows this journey:

```
1. Load list          GET /api/v1/committees
                      ├── Filter by quorum_state / consensus_state
                      └── Each row: committee_id, consensus_state, quorum_state,
                                   escalation_reason, linked_request_id, route_href

2. Open detail        GET /api/v1/committees/{committee_id}
                      ├── Show participant_roster (role, outcome_signal, rationale_ref)
                      ├── Show synthesis_summary (backend-composed — never recompute client-side)
                      ├── Show linked_evidence (rich objects with type, link)
                      └── Check allowedActions.canRecordSponsorDecision
                          ├── true  → show "Record Sponsor Decision" CTA
                          └── false → hide CTA (do not derive from roster votes)

3. Record decision    POST /api/v1/operator/commands
                      Body: {
                        "command_type": "RecordSponsorDecision",
                        "committee_id": "<id>",
                        "sponsor_decision": "approved" | "rejected" | "conditional",
                        "rationale_ref": "workspace://..."
                      }
                      ├── 202 → poll for command status
                      └── 409 → surface_unavailable or canRecordSponsorDecision was false

4. Post-command       Reload detail to get updated consensus_state, sponsor_decision,
                      sponsor_decided_at, sponsor_decided_by
```

**Non-goals that the frontend must not do:**
- Derive a committee verdict from `participant_roster[].outcome_signal` votes.
- Infer sponsor identity from roster ordering.
- Show the sponsor-decision CTA when `allowedActions` is absent or false.
- Display a rich `linked_evidence` component from string refs (depends on GAP-CW03-01 fix).

---

## 5. Acceptance readiness status

| Acceptance criterion | Status | Notes |
|---|---|---|
| CW-03 truth source 更清楚 | partial | Routes live; linked_evidence shape gap (GAP-CW03-01) |
| Committee projection 對齊 live route | partial | projection correct; authority guards incomplete (GAP-CW03-02, GAP-CW03-05) |
| 測試涵蓋 sponsor decision path | unknown | No test file found for CW-03; Codex2 must add |

---

## 6. Suggested test coverage for Codex2

```
test_cw03_committee_board_contract.py
├── test_list_committees_returns_required_fields
├── test_list_committees_filter_by_quorum_state
├── test_list_committees_filter_by_consensus_state
├── test_get_committee_returns_all_contract_fields
├── test_get_committee_not_found_returns_404
├── test_linked_evidence_is_rich_objects (covers GAP-CW03-01)
├── test_synthesis_summary_evidence_refs_remain_strings (covers GAP-CW03-04)
├── test_can_record_sponsor_decision_true_when_sponsor_required
├── test_can_record_sponsor_decision_false_when_no_sponsor_assigned (covers GAP-CW03-02)
├── test_can_record_sponsor_decision_false_when_already_decided
├── test_can_record_sponsor_decision_false_when_reviewer_role (covers GAP-CW03-05)
├── test_record_sponsor_decision_command_executes_and_updates_state
├── test_record_sponsor_decision_surface_unavailable_returns_typed_error (covers GAP-CW03-03)
└── test_committee_surface_state_propagates_degraded
```

---

## 7. Files to read before implementing (for Codex2)

1. `docs/bff/CW-03-committee-board.md` — canonical contract
2. `docs/examples/CW-03-committee-board.json` — example payloads (implementation target)
3. `services/control-plane/bff/main.py:1140–1215` — projection and authority logic
4. `services/control-plane/bff/read_store.py:6264–6421` — data access layer
5. `services/control-plane/bff/main.py:8423–8456` — command execution worker
6. `BINDING_AND_DEPLOYMENT_SEMANTICS.md` — for role authority clarification (GAP-CW03-05)

---

## 8. Handoff notes

- This packet is a sidecar; it does not mutate canonical truth.
- All gaps above are hardening recommendations for Codex2 to action in `AUTO-HARDEN-CW03-001`.
- The command path is functionally wired end-to-end. Hardening is about correctness under
  edge cases (missing data, degraded surface, role boundaries) and frontend data shape fidelity.
- The frontend screen doc (`docs/screens/CW-03-committee-board.md`) does not exist yet;
  Codex2 may choose to create it as part of the hardening delivery, or defer it.
