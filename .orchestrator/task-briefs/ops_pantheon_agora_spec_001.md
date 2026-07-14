# Task Brief: OPS-PANTHEON-AGORA-SPEC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Align pantheon agora spec bundle with live PINT/ooda endpoints (unblocks EP reconcile)
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review additive Agora v1.7 route reconciliation bundle and parity evidence.

## Summary

Publish an additive Agora v1.7 contract bundle over frozen v1.6. The bundle
describes the merged PINT interaction and governed-proposal routes plus the
existing OODA readback projections without granting order, broker, capital,
RuntimeBinding, memory, or Persona self-approval authority.

## Owned artifacts

- `services/control-plane/specs/agora/v8/pint_ooda_live_routes.schema.json`
- `services/control-plane/specs/agora/v8/capability_manifest_v1_7.json`
- `services/control-plane/specs/agora/bundle_index.v1_7.json`
- `services/control-plane/openapi/agora_v1_7.openapi.yaml`
- `scripts/test_agora_v1_7_bundle.py`

## Verification

- `python3 -m pytest scripts/test_agora_v1_6_bundle.py scripts/test_agora_v1_7_bundle.py -q`
- `jq empty services/control-plane/specs/agora/bundle_index.v1_7.json services/control-plane/specs/agora/v8/*.json`
- YAML parse of `services/control-plane/openapi/agora_v1_7.openapi.yaml`
- `git diff --check`
