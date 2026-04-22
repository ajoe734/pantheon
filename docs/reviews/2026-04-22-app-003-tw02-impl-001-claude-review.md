# Review: APP-003-TW02-IMPL-001

**Reviewer:** Claude
**Date:** 2026-04-22
**Outcome:** Approved

## Acceptance Check

1. **Trainer control state route is implemented** ✅
   - `GET /api/v1/trainer/sessions/{session_id}/controls` mounted at
     `services/control-plane/bff/main.py:5707`, calling
     `read_store.get_trainer_controls(...)` and surfacing a 404 when the
     trainer session is missing.
   - `ReadSurfaceStore.get_trainer_controls` at
     `services/control-plane/bff/read_store.py:9752` returns the ratified
     shape: `object_ref={type:"TrainerControlState", id:session_id}`,
     `session_id`, `status`, `controls[]`,
     `allowedActions.canPatchControls`, `meta.snapshot_at`,
     `meta.staleness`, and `meta.surfaces.trainer_controls.state` with
     `ok | degraded | unavailable` enumeration
     (`read_store.py:9632-9640`).
   - `canPatchControls` is derived from
     `status == "active" AND surface_state == "ok"`, matching the
     contract's mutation-authority rule in
     `docs/bff/TW-02-parameter-controls.md:159-167`.

2. **Patch route enforces the ratified diff semantics** ✅
   - `POST /api/v1/trainer/sessions/{session_id}/patch` mounted at
     `services/control-plane/bff/main.py:5726`. Payload shape is validated
     by `_tw02_validate_patch_payload` (`main.py:3770`) — rejects unknown
     top-level fields, empty/malformed `patches[]`, unknown patch fields,
     missing/empty `parameter_key`, and duplicate keys with 422 errors.
   - Guards applied before any mutation: 404 on missing session, 409
     `INVALID_STATE` when `status != "active"`, and 409
     `PRECONDITION_NOT_MET` when `allowedActions.canPatchControls` is
     false (`main.py:5744-5759`).
   - Accepted response in `read_store.patch_trainer_controls`
     (`read_store.py:9907-9920`) emits
     `status="accepted"`, `warnings[]`, `diff.updated_controls[]` with
     `field/before/after/validation_status="accepted"`,
     `current_controls[]`, `allowedActions.canPatchControls`, and meta
     block — matching the canonical v1 diff shape in
     `docs/bff/TW-02-parameter-controls.md:60-90`.
   - Rejected response (`read_store.py:9862-9877`) emits
     `status="rejected"`, `error_code="CONTROL_PATCH_VALIDATION_FAILED"`,
     `field_errors[]` with `field/reason/current_value/requested_value/
     allowed_range`, `rejected_changes[]`, `current_controls[]`, and
     meta block — matching `docs/bff/TW-02-parameter-controls.md:93-133`.
   - Validation delegates to `_tw02_validate_control_patch`
     (`read_store.py:9649`) which enforces `control_type` family
     (number/integer/enum/boolean), numeric coercion, integer integrality,
     enum membership, and `allowed_range.{min,max}` bounds — no silent
     clipping.
   - Field-level partial patch is preserved: omitted controls are not
     touched (`read_store.py:9879-9895`); replace-style updates are not
     accepted (top-level payload shape only allows `patches[]`).

3. **Tests cover authority validation and rejected patch responses** ✅
   - `services/control-plane/bff/test_tw02_parameter_controls_contract.py`
     has 5 tests covering GET shape with degraded surface, accepted
     patch with service-backed persistence, rejected patch with
     `exceeds_allowed_range`, non-editable session via
     `canPatchControls=false`, and non-`active` session status.
   - Local run: `python3 -m pytest
     services/control-plane/bff/test_tw02_parameter_controls_contract.py -v`
     → 5 passed.

## Review Notes

Static review plus targeted pytest run — no additional integration or
frontend smoke was executed. The implementation matches the ratified
contract and the sidecar handoff packet's gap list
(`support/sidecars/APP-003-TW02-IMPL-001/APP-003-TW02-IMPL-001-SIDECAR-BFF-HANDOFF.md`)
is closed for the BFF surface:

- GAP-TW02-HANDOFF-001 (route mount) — closed; both routes mounted.
- GAP-TW02-HANDOFF-002 (test proof) — closed; 5 contract tests pass.
- WATCH-TW02-HANDOFF-005 (end-to-end authority) — covered by
  `test_tw02_patch_rejects_when_patch_authority_is_false` and
  `test_tw02_patch_rejects_non_active_session_status`.

## Follow-up (non-blocking)

- GAP-TW02-HANDOFF-003 (frontend handoff bundle under
  `docs/pantheon-handoffs/TW-02-parameter-controls/`) is out of scope
  for this BFF implementation task. Parent closure should still track
  the frontend packet creation separately before Lovable activation.
- DRIFT-TW02-HANDOFF-004 (family-level summaries still using older
  `valid/applied/updated_controls[]/previous_value/new_value`
  shorthand in `docs/pantheon-handoffs/TW-007-trainer-workbench/
  PACKET_FAMILY.md` and `docs/lovable/PANTHEON_FRONTEND_SA.md`) is a
  documentation truth-sync slice, not a route-family implementation
  blocker.
