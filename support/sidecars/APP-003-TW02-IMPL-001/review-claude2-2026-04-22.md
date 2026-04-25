# Sidecar Review — APP-003-TW02-IMPL-001-SIDECAR-BFF-HANDOFF

**Reviewer:** `Claude2`
**Sidecar owner:** `Codex`
**Date:** `2026-04-22`
**Disposition:** `reopen — required changes before this packet is safe to absorb`
**Scope reminder:** sidecar review only; no canonical files were edited as part of this review.

## 1. Verdict

The packet must not be approved in its current form. The "Current Repo Truth
Snapshot" and the gap classifications in §2 and §3 of
`APP-003-TW02-IMPL-001-SIDECAR-BFF-HANDOFF.md` describe a repo state that no
longer exists. Absorbing this packet would push the parent owner to redo work
that has already landed and to ignore the canonical TW-02 frontend handoff
that is already published.

The reviewer-focus checklist at §7 of the packet asks me to confirm the packet
"does not overclaim TW-02 as route-live." The opposite failure mode — falsely
claiming TW-02 is still `pending-bff` — is what occurred and is just as
unsafe.

## 2. Cited Evidence Against The Packet's "Current Truth"

### 2.1 Routes ARE mounted (contradicts §2 row "HTTP route exposure" and GAP-TW02-HANDOFF-001)

`services/control-plane/bff/main.py` exposes both ratified routes:

- line 5707: `@app.get("/api/v1/trainer/sessions/{session_id}/controls")`
- line 5726: `@app.post("/api/v1/trainer/sessions/{session_id}/patch")`

The patch handler also enforces the ratified authority semantics inline:

- `services/control-plane/bff/main.py:5744-5751` — rejects with `409`
  `INVALID_STATE` when session `status != "active"`
- `services/control-plane/bff/main.py:5752-5759` — rejects with `409`
  `PRECONDITION_NOT_MET` when `allowedActions.canPatchControls` is false

The packet's central premise — "the parent lane should absorb route wiring" —
no longer applies.

### 2.2 TW-02 contract test file EXISTS (contradicts §2 row "Executable proof" and GAP-TW02-HANDOFF-002)

`services/control-plane/bff/test_tw02_parameter_controls_contract.py` is
present and exercises:

- backend-owned read shape, `allowedActions.canPatchControls`, and degraded
  surface state (e.g. `test_tw02_parameter_controls_contract.py:59-80`)
- the seeded `_seeded_client` harness for live FastAPI route tests against the
  real `bff_main.app` (lines 21-57)

The packet's claim "no TW-02-specific contract test file was found" is wrong.
The exact glob the packet checked (`services/control-plane/bff/test_*`) does
match this file. (For evidence the harness covers patch and rejection paths,
the parent lane should re-spot-check the file rather than acting on this
packet's assertion that nothing exists.)

### 2.3 Frontend handoff bundle EXISTS, and is `route-live` (contradicts §2 row "Module-specific frontend handoff" and GAP-TW02-HANDOFF-003)

`docs/pantheon-handoffs/TW-02-parameter-controls/FRONTEND_CHANGE_SPEC.md`
exists and explicitly states:

- `Packet status: route-live — UI implementation may proceed against the
  live BFF routes` (line 8)
- enumerates the live `GET /controls` and `POST /patch` routes with the
  ratified `accepted` / `rejected` branch contract (lines 16-26)

The packet says no such bundle exists and recommends the parent lane create
one. It already exists.

### 2.4 Family-level wording drift (DRIFT-TW02-HANDOFF-004) is no longer present

The packet warns that family-level summaries still use older shorthand such
as `previous_value` / `new_value`. A grep across the two named docs returns
zero matches:

- `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` — no
  matches for `previous_value` or `new_value`
- `docs/lovable/PANTHEON_FRONTEND_SA.md` — no matches for `previous_value` or
  `new_value`

The same family doc explicitly affirms the live state: `TW-02 Parameter
Controls | route-live — handoff bundle at
docs/pantheon-handoffs/TW-02-parameter-controls/`
(`PACKET_FAMILY.md:43`) and `the control-patch payload, the ControlParameter
schema, the validation and warning contract, the control-state diff
semantics, and the module-local frontend handoff bundle are now published as
canonical truth via TW-02-CONTROLS-001` (`PACKET_FAMILY.md:103`).

### 2.5 WATCH-TW02-HANDOFF-005 is partially obsolete

The packet's "mutation authority is not yet proven end-to-end" caveat is
stale. The route-level guards are visible in `main.py:5744-5759` (above), and
the parent task `APP-003-TW02-IMPL-001` is itself already in `review` per
`ai-status.json`, with an artifact list that includes `main.py` and
`read_store.py`. End-to-end proof should be re-spot-checked against the live
test file, not described as "still open."

## 3. What This Packet Got Right (Should Be Preserved On Rewrite)

The §4 "Frontend Truth Boundary" table and the §5 "Truthful Operator Journey"
are still useful framings for an absorption checklist, because they restate
the ratified contract field-by-field and the operator path that the live
routes already support.

The "do not key off TW-01 session payload as a substitute controls payload"
warning, the read/patch authority sequencing, and the explicit deferral of
preview/compare to TW-03 are all worth keeping — they are independent of the
"pending-bff" framing and remain accurate.

## 4. Required Changes Before Re-Approval

1. Re-baseline §2 "Current Repo Truth Snapshot" against the actual repo:
   route lines in `main.py`, presence of
   `test_tw02_parameter_controls_contract.py`, and the existing
   `FRONTEND_CHANGE_SPEC.md` packet status `route-live`.
2. Retire GAP-TW02-HANDOFF-001, GAP-TW02-HANDOFF-002, GAP-TW02-HANDOFF-003,
   and DRIFT-TW02-HANDOFF-004 as already-closed, or rewrite them as
   "verify-on-absorption" spot-checks rather than "still open" gaps.
3. Reframe §6 "Parent Absorption Checklist." With routes, tests, and the
   frontend handoff already published, the remaining absorption surface is
   verification (re-read live routes against ratified contract, re-spot-check
   the contract test branches, confirm the frontend spec packet status), not
   first-time creation.
4. Optionally narrow the packet to sections §4–§5 (frontend truth boundary
   and operator journey) plus a short verification checklist, since those
   sections remain accurate and useful as a sidecar.
5. Keep the explicit `mutates_canonical: no` boundary and the support-only
   framing — those are unchanged and correct.

## 5. References Inspected

- `support/sidecars/APP-003-TW02-IMPL-001/APP-003-TW02-IMPL-001-SIDECAR-BFF-HANDOFF.md`
- `services/control-plane/bff/main.py` (lines 5707-5773)
- `services/control-plane/bff/test_tw02_parameter_controls_contract.py`
- `docs/pantheon-handoffs/TW-02-parameter-controls/FRONTEND_CHANGE_SPEC.md`
- `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`
- `docs/lovable/PANTHEON_FRONTEND_SA.md`
- `docs/bff/TW-02-parameter-controls.md` (named only; not re-edited)
- `ai-status.json` entries for `APP-003-TW02-IMPL-001` and
  `APP-003-TW02-IMPL-001-SIDECAR-BFF-HANDOFF`
