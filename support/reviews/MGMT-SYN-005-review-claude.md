# Review: MGMT-SYN-005 — AllocationPolicyArtifact output

Reviewer: Claude
Date: 2026-05-15
Status: **Approved — no blocking findings**

## Scope

Task-owned files reviewed:

- `services/optimizer-svc/portfolio_synthesis/models.py`
- `services/optimizer-svc/portfolio_synthesis/__init__.py`
- `services/optimizer-svc/portfolio_synthesis/allocation_policy_artifact.schema.json`
- `services/optimizer-svc/main.py`
- `services/optimizer-svc/test_allocation_policy_artifact_output.py`

## Verification

```
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/optimizer-svc/portfolio_synthesis/models.py services/optimizer-svc/main.py services/optimizer-svc/test_allocation_policy_artifact_output.py
# => PASS (no output)

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/optimizer-svc/test_allocation_policy_artifact_output.py -q
# => 3 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/optimizer-svc -q
# => 33 passed
```

## Findings

**No blocking issues.**

### Correctness

- `AllocationPolicyArtifact` is a properly frozen dataclass. `__post_init__` validates all required string fields as non-empty, synthesis_method against the enum, target_weights keying/range/sum (≤1.0 + 1e-9 tolerance), constraints_bundle as Mapping, optional risk_budget range [0,1], provenance_refs non-empty list of non-empty strings, metadata as Mapping.
- `to_dict()` delegates to `asdict()` — correct for dataclasses; produces JSON-serializable output.
- `validate_allocation_policy_artifact_json` performs structural required-field checks then delegates to constructor validation — correct two-phase approach.

### JSON Schema

- Required fields match dataclass required fields exactly.
- `additionalProperties: false` at top level prevents schema drift.
- `target_weights.patternProperties` enforces `^[A-Za-z0-9._:/-]+$` keys and `[0,1]` number values; `additionalProperties: false` blocks unconstrained keys.
- synthesis_method enum aligns with `SynthesisMethod` values: weighted_fusion, committee_override, single_proposal.

### API payload

- `POST /api/optimizer/synthesize` now returns `allocation_policy_artifact` (full to_dict payload) and `conflict_resolution_log` (full to_dict payload) alongside top-level compatibility fields — correct.
- Committee referral path similarly returns `committee_referral` and `conflict_resolution_log` payloads.
- `GET /api/optimizer/policies/{id}` and `GET /api/optimizer/logs/{id}` readback via `to_dict()` — correct.

### Safety boundary

- No broker session opened, no capital mutation, no live order route, no registry write. Confirmed by code inspection: synthesizer is in-memory only.

## Non-blocking observations

1. `conflict_resolution_log_id: str = ""` field has an empty-string default but `__post_init__` rejects it — callers must always supply it. Slightly confusing API surface but not a functional issue since the synthesizer always supplies the log id.
2. `get_policy()` has a dead `isinstance(entry, AllocationPolicyArtifact)` branch — both branches call `entry.to_dict()` identically. Could simplify to one return. Not a bug.

## Conclusion

AllocationPolicyArtifact output contract is correctly implemented: `to_dict()` plus structural validation, JSON Schema, and full API payload with backward-compatible top-level fields. 33 optimizer-svc tests pass. Task is approved for closeout by owner (Codex).
