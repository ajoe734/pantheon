# OpenClaw Integration — Smoke Test Plan

Last updated: 2026-04-10
Owner: OSS-001 (Qwen)
Reviewer: Codex
Status: defined v1 plan
Related: `integration.md`, `governance.md`, `OPENCLAW_RUNTIME_CONTRACT.md`

## 1. Objective

Prove that the pinned upstream OpenClaw runtime can be started, invoked, and its outputs normalized into governed Pantheon artifacts.

---

## 2. Prerequisites

| Requirement | Details |
|---|---|
| Docker runtime | Docker Engine 24+ or equivalent |
| Pinned image | `openclaw/openclaw:v2026.4.7` (SHA `5050017`) |
| Python 3.10+ | For validation scripts |
| Network access | Outbound HTTPS to pull upstream image |
| Local workspace | `/tmp/openclaw-smoke-test` (isolated) |

---

## 3. Test Sequence

### Step 1: Start Pinned Runtime

**Goal:** Prove the pinned image starts and reaches a ready state.

```bash
# Pull the pinned image
docker pull openclaw/openclaw:v2026.4.7

# Start with minimal config
docker run -d \
  --name openclaw-smoke \
  --workspace /tmp/openclaw-smoke \
  -p 3000:3000 \
  openclaw/openclaw:v2026.4.7

# Verify readiness
curl -s http://localhost:3000/health | jq '.status'
# Expected: "ready"
```

**Acceptance:** Container starts, health endpoint returns `"ready"` within 60 seconds.

---

### Step 2: Invoke Minimal Approved Workflow

**Goal:** Prove a research-intake workflow can be registered and invoked.

```bash
# Register a minimal research-intake workflow
curl -s -X POST http://localhost:3000/control/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "job_type": "research_task",
    "payload": {
      "query": "summarize the latest research on portfolio optimization using reinforcement learning",
      "max_results": 3,
      "output_format": "json"
    }
  }' | jq '.job_id'

# Wait for completion (poll)
JOB_ID="<from above>"
curl -s http://localhost:3000/control/jobs/$JOB_ID | jq '.status'
# Expected: "completed"
```

**Acceptance:** Job completes within 120 seconds, returns a structured result payload.

---

### Step 3: Capture Governed Handoff Payload

**Goal:** Prove the workflow output can be captured as a local JSON handoff.

```bash
# Capture the raw output
curl -s http://localhost:3000/control/jobs/$JOB_ID/output > /tmp/openclaw-smoke/raw_handoff.json

# Verify structure
cat /tmp/openclaw-smoke/raw_handoff.json | jq 'keys'
# Expected: contains at minimum ["result", "metadata", "lineage"]
```

**Acceptance:** Handoff file exists, contains structured JSON with result and metadata fields.

---

### Step 4: Normalize into Local StrategySpec + WorkflowHandoff

**Goal:** Prove the handoff payload can be normalized into Pantheon's canonical `StrategySpec` and wrapped in a `WorkflowHandoff`.

```python
# services/control-plane/specs/normalize_handoff.py (reference implementation)
# This script:
# 1. Reads raw_handoff.json
# 2. Builds a canonical StrategySpec (all required fields per strategy_spec.schema.json)
# 3. Wraps it in a canonical WorkflowHandoff (carries registry_hints, governance_context)
# 4. Writes both strategy_spec.json and workflow_handoff.json

import json
from datetime import datetime, timezone

handoff = json.load(open("/tmp/openclaw-smoke-test/raw_handoff.json"))
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# --- Canonical StrategySpec (validates against strategy_spec.schema.json) ---
strategy_spec = {
    "spec_version": "1.0",
    "strategy_id": f"strat-{handoff.get('metadata', {}).get('topic', 'research')}",
    "title": handoff.get("metadata", {}).get("topic", "Research Strategy"),
    "hypothesis": handoff.get("result", {}).get("summary", ""),
    "objective": "Evaluate and replicate the research finding under governed promotion gates.",
    "market_scope": {
        "symbols": ["RESEARCH_UNIVERSE"],
        "frequency": "1d"
    },
    "data_dependencies": [
        {
            "ref": handoff.get("metadata", {}).get("source_id", "unknown"),
            "kind": "note"
        }
    ],
    "execution_profile": {
        "signal_schema_version": "1.0",
        "quantity_type": "PERCENT_PORTFOLIO"
    },
    "evaluation_plan": {
        "metrics": ["sharpe_ratio", "max_drawdown", "win_rate"]
    },
    "governance": {
        "approval_required": True
    },
    "provenance": {
        "source_kind": "workflow",
        "created_at": now,
        "source_refs": [handoff.get("metadata", {}).get("source_id", "unknown")],
        "created_by": "openclaw-smoke-test"
    }
}

# --- Canonical WorkflowHandoff (validates against workflow_handoff.schema.json) ---
workflow_handoff = {
    "handoff_version": "1.0",
    "handoff_id": f"handoff-{strategy_spec['strategy_id']}",
    "handoff_type": "strategy_spec",
    "from_stage": "research_ingest",
    "to_stage": "replication_gate",
    "created_at": now,
    "strategy_spec": strategy_spec,
    "registry_hints": {
        "artifact_type": "strategy_spec",
        "initial_lifecycle_state": "draft",
        "lineage_ref": handoff.get("metadata", {}).get("source_id", "unknown")
    },
    "governance_context": {
        "approval_required": True,
        "execution_context": "research"
    },
    "provenance": {
        "created_by": "openclaw-smoke-test",
        "created_at": now,
        "source_task_id": "OSS-001-smoke",
        "source_channel": "research_task",
        "source_persona": "openclaw"
    }
}

json.dump(strategy_spec, open("/tmp/openclaw-smoke-test/strategy_spec.json", "w"), indent=2)
json.dump(workflow_handoff, open("/tmp/openclaw-smoke-test/workflow_handoff.json", "w"), indent=2)
print("Canonical StrategySpec and WorkflowHandoff written.")
```

**Acceptance:** Both `strategy_spec.json` and `workflow_handoff.json` are written with canonical field shapes.

---

### Step 5: Validate Against Local Schemas

**Goal:** Prove both the normalized `StrategySpec` and the `WorkflowHandoff` validate against their respective local schemas.

```bash
# Validate both artifacts against their schemas
python3 -c "
import json
from jsonschema import Draft7Validator, RefResolver, ValidationError
from pathlib import Path

schema_dir = Path('services/control-plane/specs')
spec_schema = json.load(open(schema_dir / 'strategy_spec.schema.json'))
handoff_schema = json.load(open(schema_dir / 'workflow_handoff.schema.json'))

# Build resolver store so the \$ref to strategy_spec.schema.json resolves offline
spec_file_uri = (schema_dir / 'strategy_spec.schema.json').resolve().as_uri()
store = {
    'https://pantheon/workflow-handoff/strategy_spec.schema.json': spec_schema,
    spec_file_uri: spec_schema,
}
resolver = RefResolver(
    base_uri=f'{schema_dir.resolve().as_uri()}/',
    referrer=handoff_schema,
    store=store,
)
handoff_validator = Draft7Validator(handoff_schema, resolver=resolver)

# Validate StrategySpec
strategy_spec = json.load(open('/tmp/openclaw-smoke-test/strategy_spec.json'))
try:
    Draft7Validator(spec_schema).validate(instance=strategy_spec)
    print('PASS: StrategySpec validates against strategy_spec.schema.json')
except ValidationError as e:
    print(f'FAIL (StrategySpec): {e.message}')
    exit(1)

# Validate WorkflowHandoff
workflow_handoff = json.load(open('/tmp/openclaw-smoke-test/workflow_handoff.json'))
try:
    handoff_validator.validate(instance=workflow_handoff)
    print('PASS: WorkflowHandoff validates against workflow_handoff.schema.json')
except ValidationError as e:
    print(f'FAIL (WorkflowHandoff): {e.message}')
    exit(1)

print('ALL SCHEMA VALIDATIONS PASSED')
"
```

**Acceptance:** Both validations pass with no errors.

---

## 4. Full Smoke Test Script (Reference Implementation)

```bash
#!/usr/bin/env bash
set -euo pipefail

IMAGE="openclaw/openclaw:v2026.4.7"
CONTAINER_NAME="openclaw-smoke"
WORK_DIR="/tmp/openclaw-smoke-test"
PASS=0
FAIL=0

cleanup() {
  docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

mkdir -p "$WORK_DIR"

echo "=== Step 1: Pull and start pinned runtime ==="
docker pull "$IMAGE"
docker run -d --name "$CONTAINER_NAME" -p 3000:3000 "$IMAGE"

# Wait for readiness (up to 60s)
for i in $(seq 1 12); do
  if curl -sf http://localhost:3000/health | jq -e '.status == "ready"' > /dev/null 2>&1; then
    echo "PASS: Runtime ready"
    PASS=$((PASS + 1))
    break
  fi
  sleep 5
done
if [ $PASS -eq 0 ]; then
  echo "FAIL: Runtime not ready after 60s"
  FAIL=$((FAIL + 1))
  exit 1
fi

echo "=== Step 2: Invoke minimal workflow ==="
JOB_ID=$(curl -sf -X POST http://localhost:3000/control/jobs \
  -H 'Content-Type: application/json' \
  -d '{"job_type": "research_task", "payload": {"query": "test", "max_results": 1}}' \
  | jq -r '.job_id')

for i in $(seq 1 24); do
  STATUS=$(curl -sf http://localhost:3000/control/jobs/$JOB_ID | jq -r '.status')
  if [ "$STATUS" = "completed" ]; then
    echo "PASS: Workflow completed"
    PASS=$((PASS + 1))
    break
  fi
  sleep 5
done
if [ "$STATUS" != "completed" ]; then
  echo "FAIL: Workflow did not complete after 120s (status: $STATUS)"
  FAIL=$((FAIL + 1))
fi

echo "=== Step 3: Capture handoff payload ==="
curl -sf http://localhost:3000/control/jobs/$JOB_ID/output > "$WORK_DIR/raw_handoff.json"
if jq empty "$WORK_DIR/raw_handoff.json" 2>/dev/null; then
  echo "PASS: Handoff payload captured (valid JSON)"
  PASS=$((PASS + 1))
else
  echo "FAIL: Handoff payload is not valid JSON"
  FAIL=$((FAIL + 1))
fi

echo "=== Step 4: Normalize to StrategySpec + WorkflowHandoff ==="
python3 -c "
import json
from datetime import datetime, timezone

handoff = json.load(open('$WORK_DIR/raw_handoff.json'))
now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

strategy_spec = {
    'spec_version': '1.0',
    'strategy_id': f\"strat-{handoff.get('metadata', {}).get('topic', 'research')}\",
    'title': handoff.get('metadata', {}).get('topic', 'Research Strategy'),
    'hypothesis': str(handoff.get('result', {}).get('summary', '')),
    'objective': 'Evaluate and replicate the research finding under governed promotion gates.',
    'market_scope': {'symbols': ['RESEARCH_UNIVERSE'], 'frequency': '1d'},
    'data_dependencies': [{'ref': handoff.get('metadata', {}).get('source_id', 'unknown'), 'kind': 'note'}],
    'execution_profile': {'signal_schema_version': '1.0', 'quantity_type': 'PERCENT_PORTFOLIO'},
    'evaluation_plan': {'metrics': ['sharpe_ratio', 'max_drawdown', 'win_rate']},
    'governance': {'approval_required': True},
    'provenance': {
        'source_kind': 'workflow',
        'created_at': now,
        'source_refs': [handoff.get('metadata', {}).get('source_id', 'unknown')],
        'created_by': 'openclaw-smoke-test'
    }
}

workflow_handoff = {
    'handoff_version': '1.0',
    'handoff_id': f\"handoff-{strategy_spec['strategy_id']}\",
    'handoff_type': 'strategy_spec',
    'from_stage': 'research_ingest',
    'to_stage': 'replication_gate',
    'created_at': now,
    'strategy_spec': strategy_spec,
    'registry_hints': {
        'artifact_type': 'strategy_spec',
        'initial_lifecycle_state': 'draft',
        'lineage_ref': handoff.get('metadata', {}).get('source_id', 'unknown')
    },
    'governance_context': {
        'approval_required': True,
        'execution_context': 'research'
    },
    'provenance': {
        'created_by': 'openclaw-smoke-test',
        'created_at': now,
        'source_task_id': 'OSS-001-smoke',
        'source_channel': 'research_task',
        'source_persona': 'openclaw'
    }
}

json.dump(strategy_spec, open('$WORK_DIR/strategy_spec.json', 'w'), indent=2)
json.dump(workflow_handoff, open('$WORK_DIR/workflow_handoff.json', 'w'), indent=2)
" && echo "PASS: StrategySpec + WorkflowHandoff normalized" && PASS=$((PASS + 1)) \
  || { echo "FAIL: Normalization error"; FAIL=$((FAIL + 1)); }

echo "=== Step 5: Validate against local schemas ==="
python3 -c "
import json
from jsonschema import Draft7Validator, RefResolver

schema_dir = '$(pwd)/services/control-plane/specs'
spec_schema = json.load(open(f'{schema_dir}/strategy_spec.schema.json'))
handoff_schema = json.load(open(f'{schema_dir}/workflow_handoff.schema.json'))

spec_file_uri = 'file://' + f'{schema_dir}/strategy_spec.schema.json'
store = {
    'https://pantheon/workflow-handoff/strategy_spec.schema.json': spec_schema,
    spec_file_uri: spec_schema,
}
from pathlib import Path
resolver = RefResolver(
    base_uri=f'file://{Path(schema_dir).resolve().as_uri()}/',
    referrer=handoff_schema,
    store=store,
)
handoff_validator = Draft7Validator(handoff_schema, resolver=resolver)

strategy_spec = json.load(open('$WORK_DIR/strategy_spec.json'))
Draft7Validator(spec_schema).validate(instance=strategy_spec)
print('PASS: StrategySpec validates')

workflow_handoff = json.load(open('$WORK_DIR/workflow_handoff.json'))
handoff_validator.validate(instance=workflow_handoff)
print('PASS: WorkflowHandoff validates')
" && PASS=$((PASS + 1)) \
  || { echo "FAIL: Schema validation failed"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ $FAIL -eq 0 ] && echo "ALL SMOKE TESTS PASSED" || echo "SMOKE TESTS FAILED"
exit $FAIL
```

---

## 5. Acceptance Summary

| Step | What It Proves | Status |
|---|---|---|
| 1 | Pinned upstream runtime can start | ✅ Script defined |
| 2 | Minimal approved workflow can be invoked | ✅ Script defined |
| 3 | Governed handoff payload can be captured | ✅ Script defined |
| 4 | Payload normalizes into canonical StrategySpec + WorkflowHandoff | ✅ Script defined |
| 5 | Both artifacts validate against their respective local schemas | ✅ Script defined |

**Note:** Actual execution requires Docker access and the upstream image. The scripts above are the **complete test plan** — execution can proceed when the platform is ready for integration testing.

---

## 6. Follow-up Actions

| Action | Owner | Trigger |
|---|---|---|
| Execute smoke test in CI environment | Platform SRE | Docker image available in CI registry |
| Implement `normalize_handoff.py` in services/control-plane/specs/ | Task owner (Qwen or assignee) | OSS-001 implementation phase |
| Add smoke test to CI pipeline | Platform SRE | After first successful manual run |
| Create health-check readiness probe definition | Platform SRE | Before production deployment |
