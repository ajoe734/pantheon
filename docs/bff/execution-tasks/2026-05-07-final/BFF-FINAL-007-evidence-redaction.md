# BFF-FINAL-007 - Evidence Redaction

Priority: P1

Depends on: BFF-FINAL-001

Area: evidence refs and capability checks

## Goal

Return redacted evidence references when the operator lacks the capability to read linked evidence.

## Contract Inputs

Evidence capability map:

| EvidenceKind | Required capability |
|---|---|
| alert | `risk.alert.read` |
| incident | `risk.incident.read` |
| job | `job.read` |
| audit | `audit.read` |
| metric | `metric.read` |
| strategy | `strategy.view` |
| persona | `persona.view` |
| deployment | `deployment.read` |
| runtime | `runtime.read` |
| policy | `policy.read` |
| approval | `approval.read` |
| artifact | `artifact.read` |
| signal | `agora.signal.read` |
| journal | `agora.journal.read` |
| postmortem | `postmortem.read` |

## Implementation Scope

Likely files:

- `services/control-plane/bff/models.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_kw03_evidence_refs_contract.py`
- governance / incident / postmortem BFF surface tests

## Steps

1. Add final `EvidenceKind`.
2. Add backend `EVIDENCE_CAPABILITY_MAP`.
3. Add `RedactedEvidenceRef`.
4. Add a redaction helper that receives operator identity/capabilities and evidence refs.
5. Prefer returning redacted refs over silent omission.
6. When filtering cannot be avoided, include `redactedCount`.
7. Update evidence-heavy surfaces:
   - governance review
   - Sentinel / intervention views if implemented in BFF
   - knowledge/evidence refs
   - incident/postmortem evidence

## Acceptance Criteria

- Evidence surfaces never leak details beyond operator capability.
- Redacted refs include required capability and reason code.
- Tests cover at least one insufficient-capability evidence response.
- Existing KW-03 evidence tests remain compatible.

## Verification

```bash
python -m pytest services/control-plane/bff/test_kw03_evidence_refs_contract.py -q
python -m pytest services/control-plane/bff -k "evidence" -q
```
