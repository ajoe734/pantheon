# SRC-005 Review: OpenClaw cron / ingest job trigger

Reviewer: Claude
Owner: Gemini2
Date: 2026-05-16

## Status: APPROVED

## Artifacts Reviewed

- `scripts/trigger_openclaw_ingest.sh` — convenience trigger script
- `services/control-plane/cron/cli.py` — CLI entry point
- `services/control-plane/cron/models.py` — dataclass models
- `services/control-plane/cron/workflows.py` — workflow catalog (4 workflows)
- `services/control-plane/cron/service.py` — CronOrchestrator
- `services/control-plane/cron/openclaw_client.py` — OpenClawCronClient
- `services/control-plane/cron/schema_validation.py` — handoff schema validation
- `services/control-plane/cron/test_cron.py` — 12 unit tests
- `services/control-plane/cron/smoke_test.py` — fake/live smoke test runner
- `services/control-plane/cron/test_ingest_payload.json` — sample payload
- `services/control-plane/cron/README.md` — documentation

## Verification

```
cd services/control-plane/cron
python3 -m pytest test_cron.py -q  =>  12 passed
python3 -c "import py_compile; py_compile.compile('cli.py'); ..."  =>  py_compile OK
bash scripts/trigger_openclaw_ingest.sh services/control-plane/cron/test_ingest_payload.json  =>  dry-run produces valid governed handoff
```

## Review Findings

### Governance Compliance

- `pantheon.ingest`: `approval_required=False`, `execution_context=research`, `allowed_tool_classes=("research","status")` — correct for research intake
- `pantheon.review`: `approval_required=True`, `execution_context=paper` — creates approval_request, does not self-approve ✓
- `pantheon.retrain`: `approval_required=True` — registry_submission, stays in research context ✓
- `pantheon.deploy`: `approval_required=True`, `uses_promotion_gate=True` — routes through canonical DeploymentPlan / StagePlanner; no direct LEAN calls ✓

### Dispatch Envelope

`OpenClawCronClient.prepare_dispatch` correctly attaches:
- runtime pin (`repository_url`, `release_tag`, `commit_sha`, `image_ref`)
- governance context (`policy_id`, `execution_context`, `allowed_tool_classes`, `approval_required`)
- pantheon adapter boundary with `credential_sharing: disallowed`, `filesystem_scope: persona_workspace_only`

### Handoff Schema

`validate_workflow_handoff` validates required fields with jsonschema when available, falls back to manual field check. Correctly enforced on every handoff path.

### Deploy Path

`_run_deploy` uses `StagePlanner.create_plan(...)` → `build_execution_projection(...)` → `DeploymentSagaOrchestrator.bootstrap(...)`. This satisfies the L1 policy that deploy must create a first-class `DeploymentPlan` before emitting any deployment request.

### Trigger Script

`scripts/trigger_openclaw_ingest.sh` works correctly. Minor note: `$PAYLOAD_FILE` on line 13 is unquoted in the CMD string, which would break on paths with spaces. This is a cosmetic fix — the script is a dev-only convenience tool and paths without spaces work correctly.

### Tests

12 tests cover: client dispatch envelope, payload validation, adapter boundary env vars, gateway transport RPC mapping, orchestrator ingest/review/retrain/deploy, and deploy guard conditions (non-approved artifact, mismatched capital pool). All pass.

## Minor Follow-Up (Non-Blocking)

1. Quote `$PAYLOAD_FILE` in `trigger_openclaw_ingest.sh` line 13 for robustness with space-containing paths.
2. Replace `v0.0.0-local` smoke-test runtime pin with a real upstream release tag when transport wiring is finalized (already noted in README).

Neither item blocks finalization.
