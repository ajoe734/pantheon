# TRL DPO Backend Evidence

Task: `RES-ACT-TRL-001-V2`
Owner: `Codex`
Reviewer: `Gemini`
Status: adapter-specific backend evidence artifact

## Scope

This evidence records the TRL real-backend activation posture for the bounded
FB-002 preference dataset proof. It distinguishes three states:

1. the real upstream backend requested by the activation harness;
2. the explicit dependency/config failure recorded in this workspace;
3. the stub-produced handoff artifacts that remain labeled as stub output.

The evidence does not claim successful upstream DPO training in this workspace.

## Command Under Review

The reviewed activation command is:

```bash
python3 services/learning/trl/activation_smoke.py \
  --enable-activation-ready \
  --backend real \
  --output-dir /tmp/pantheon/learning/trl/runtime-data-activation-real
```

The harness requires an explicit activation flag before it builds preference
pairs or attempts `TRLDPOBackend`.

## Real Backend Attempt

Source file:
`support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/real_backend_attempt.json`

| Field | Recorded value |
|---|---|
| `backend` | `trl_dpo` |
| `status` | `dependency_or_config_error` |
| `error_type` | `TRLWorkflowError` |
| `cause_type` | `ModuleNotFoundError` |
| `cause_message` | `No module named 'trl'` |
| `message` | `TRL backend unavailable. Install services/learning/trl/requirements.txt first.` |
| `silent_stub_fallback` | false |

This is the correct fail-closed behavior for a workspace without the upstream
`trl` package installed. The missing dependency is recorded as evidence; it is
not converted into a silent stub success.

## Stub Handoff Boundary

Because `TRLDPOBackend` could not run in this workspace, the durable handoff
artifacts were produced by `StubDPOBackend` and remain labeled that way:

| Handoff field | Recorded value |
|---|---|
| `handoff_backend` | `stub_dpo` |
| `artifact_bundle.evaluation_summary.backend` | `stub_dpo` |
| `artifact_bundle.model.predictor` | `stub_dpo` |
| `candidate_packet.evaluation_summary.backend` | `stub_dpo` |
| `candidate_packet.candidate_registry_projection.metadata.training_backend` | `stub_dpo` |

The stub handoff is an offline evaluator / registry packet shape proof only. It
does not satisfy a successful real upstream DPO training claim.

## Backend Configuration

| Setting | Value |
|---|---|
| Package pin | `trl>=0.8.0,<0.10.0` in `services/learning/trl/requirements.txt` |
| Real backend implementation | `TRLDPOBackend` in `services/learning/trl/adapter/trl_adapter.py` |
| Default model family | preference model |
| Algorithm | DPO |
| Training method | `dpo` |
| Epochs | 1 |
| Batch size | 8 |
| Seed | 42 |
| Artifact family | `trl_preference_model` |
| Registry artifact type | `model_artifact` |
| Registry state at output | `draft` |
| Deployment stage | `none` |

## Fail-Closed Requirements

A future real-backend rerun may be accepted only when all of the following stay
true:

- `--enable-activation-ready` is still required.
- `--backend real` attempts `TRLDPOBackend` explicitly.
- missing upstream packages or model configuration produce
  `dependency_or_config_error` or an equivalent explicit failure.
- `silent_stub_fallback` remains false.
- any stub fallback handoff is still labeled `stub_dpo`.
- no raw credentials or model-hub tokens are persisted.
- registry writes and deployment-stage changes remain outside the TRL adapter.

## Verification

Focused verification for this evidence lives in
`tests/governance/test_trl_proof_artifacts.py`.

Expected command:

```bash
pytest -q tests/governance/test_trl_proof_artifacts.py
```
