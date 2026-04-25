# EXEC-FRONT-CW03-PARTIAL-001-SIDECAR-BFF-HANDOFF — BFF & Frontend Handoff Packet

**Sidecar type:** `bff_handoff_packet`
**Parent task:** `EXEC-FRONT-CW03-PARTIAL-001` (Implement CW-03 committee board partial-activation UI)
**Prepared by:** Gemini
**Reviewer:** Codex2
**Date:** 2026-04-20
**Status:** ready for review

---

## 1. Scope of this packet

This packet supports the **partial activation** of the CW-03 Committee Board. Per `MODULE_READINESS_RATIFICATION_2026-04-20.md`, CW-03 is permitted to activate in a reduced scope before CW-02 (Transcript) is fully live.

**Partial Scope:**
- Read-only committee overview.
- Sponsor status and assignment inspection.
- Outcome summary (synthesis) projection.
- Sponsor decision recording (if authorized).

**Explicitly Excluded (Gated on CW-02):**
- Transcript drill-down.
- Append-only event projection.
- Actor labeling and inline evidence-link truth for events.

---

## 2. Current BFF Status

### Routes Live

| Route | Purpose | Status |
|---|---|---|
| `GET /api/v1/committees` | List committee boards with filters | **Live** |
| `GET /api/v1/committees/{id}` | Get full committee detail projection | **Live** |
| `POST /api/v1/operator/commands` | Command: `RecordSponsorDecision` | **Live** |

### Implementation Mapping

- **Projection Logic:** `services/control-plane/bff/main.py:_cw03_committee_projection`
- **Authority Logic:** `services/control-plane/bff/main.py:_cw03_allowed_actions`
- **Data Layer:** `services/control-plane/bff/read_store.py:get_committee`
- **Command Worker:** `services/control-plane/bff/main.py:8423` (dispatches to `read_store.record_sponsor_decision`)

---

## 3. BFF Query Gaps & Residual Gates

### GAP-CW03-PARTIAL-01 — `linked_evidence` Shape
The BFF currently returns `linked_evidence` as a list of raw string IDs (from `consult["evidence_refs"]`).
**Contract Requirement:** Rich objects with `id`, `type`, `link`, and `description`.
**Impact:** Partial UI may show IDs or blank links until `read_store.py` adds a resolver for these refs.

### GAP-CW03-PARTIAL-02 — Missing `linked_transcript`
As CW-02 is still blocked, the BFF does not yet expose a `linked_transcript` field.
**UI Strategy:** The frontend must treat the transcript as "Unavailable" or "Pending Consultation Activation" and should not attempt to use `linked_session_id` to synthesize a transcript view locally.

### GAP-CW03-PARTIAL-03 — Synthesis Outcome Propagation
The `synthesis_summary.outcome` is correctly projected as `"pending"` until a sponsor decision is recorded.
**UI Strategy:** Ensure the UI distinguishes between a "voted" consensus state and the "final" sponsor-decided outcome.

---

## 4. Operator Journey (Partial Activation)

1. **Board Discovery:** Operator uses the Committee Board list to find sessions where `consensus_state == "sponsor_required"`.
2. **Context Review:** Operator opens the detail view to inspect:
   - `escalation_reason`: Why this committee was formed.
   - `participant_roster`: Who voted what (and their rationale refs).
   - `synthesis_summary`: The system-composed summary of the committee's findings.
3. **Decision Recording:**
   - If `allowedActions.canRecordSponsorDecision` is `true`, the "Record Sponsor Decision" CTA is displayed.
   - Operator records `approved`, `rejected`, or `conditional` with a rationale reference.
4. **Finalization:** The board moves to `consensus_state == "reached"` and the final outcome is reflected in the UI.

---

## 5. Handoff Materials

- **BFF Contract:** `docs/bff/CW-03-committee-board.md`
- **Example Payloads:** `docs/examples/CW-03-committee-board.json`
- **Readiness Truth:** `MODULE_READINESS_RATIFICATION_2026-04-20.md`

---

## 6. Acceptance Check for Partial Scope

| Criterion | Status | Note |
|---|---|---|
| Partial Activation Compliance | OK | Scope limited to read-only + sponsor decision; transcript omitted. |
| Authority Alignment | OK | `canRecordSponsorDecision` correctly guards roles and assignment. |
| Residual Gate Tracking | OK | Transcript drill-down explicitly marked as gated on CW-02. |
