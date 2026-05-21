# Final Go/No-Go Runbook

Status: active
Blueprint ref: `docs/04/pantheon_design_blueprint_supplement_2026-05-19/pantheon_blueprint_supplement.md#12-final-acceptance-matrix`
Task: BPC-002-V2

## Purpose

This runbook covers how to generate, interpret, and act on the final Go/No-Go packet
produced by `tools/final_go_no_go_assembler.py`. The packet aggregates all readiness
evidence from EPIC-BLUEPRINT-CLOSEOUT (BPC-001-V2 completion report, task archives,
HumanGateDecision records) into a single JSON artifact per blueprint §12.

---

## 1. Generate the packet

```bash
python3 tools/final_go_no_go_assembler.py \
  --repo-root . \
  --status-root "${PANTHEON_STATUS_ROOT:-.}" \
  --output support/evidence/BPC-002-V2/final_go_no_go_packet.json
```

Add `--print` to stream the JSON to stdout. Add `--strict` to exit non-zero when the
verdict is not `go` (useful in CI gates).

---

## 2. Interpret the verdict

| `overall_verdict` | `go` | Meaning |
|---|---|---|
| `go` | `true` | All required-before-live rows passed; no pending human signoff. |
| `pending_human_signoff` | `false` | One or more rows await human gate approval. The `pending_human_signoff_rows` list names them. |
| `no_go` | `false` | One or more required rows failed. The `failed_rows` list names them. |
| `incomplete` | `false` | Required rows have no BPC-001 evidence and no task archives; assembler cannot determine status. |

The `summary` field gives counts: `total`, `passed`, `failed`, `pending_human_signoff`.

---

## 3. Act on `pending_human_signoff`

When `overall_verdict` is `pending_human_signoff`:

1. Inspect `pending_human_signoff_rows` in the packet for the row IDs that are blocked.
2. Locate the corresponding `HumanGateDecision` records via the `human_gate_records`
   list in the packet (these are paths under `support/evidence/`).
3. The most common pending rows are:
   - `production_activation_gates` — awaits `PROD-WRITES-001-V2` and `LIVE-SCALE-001-V2`
     to resolve in `ai-status.json`. These require dual risk-owner + operator approval
     per the H3 schema (see `docs/04/.../pantheon_blueprint_supplement.md#H3`).
4. After the human gate is approved:
   - The owning task is moved to `done` in `ai-status.json`.
   - Re-run the assembler; `production_activation_gates` should now show `passed`.

---

## 4. Act on `no_go`

When `overall_verdict` is `no_go`:

1. Inspect `failed_rows` to identify which matrix row failed.
2. Each row entry in `matrix` includes:
   - `bpc001_condition_id` — the BPC-001 blueprint completion condition that sourced this row.
   - `bpc001_status` — the status inherited from BPC-001; if `failed`, the corresponding
     condition in `support/evidence/BPC-001-V2/blueprint_completion_report.json` will list
     `missing_required_tasks` or `missing_evidence_paths`.
   - `task_refs` — task archive references with their `status` fields.
   - `missing_evidence_paths` — evidence files the assembler expected but did not find.
3. For each failed row:
   a. Re-run the BPC-001 auditor to get a fresh completion report:
      ```bash
      python3 tools/blueprint_acceptance_audit.py \
        --repo-root . \
        --status-root "${PANTHEON_STATUS_ROOT:-.}" \
        --output support/evidence/BPC-001-V2/blueprint_completion_report.json
      ```
   b. Identify the blocking task(s) and resolve them through the normal
      task lifecycle (`in_progress → review → review_approved → done`).
   c. Re-run the assembler; the repaired rows should show `passed`.

---

## 5. Matrix rows reference

| Row ID | Capability | Required | Status target |
|---|---|---|---|
| `ooda_paper_loop` | OODA paper loop | Yes | closed |
| `ooda_canary_loop` | OODA canary loop | Yes | must pass |
| `ep4_governed_paper` | EP4 governed paper | Yes | stable |
| `ep5_canary_proof` | EP5 canary proof | Yes | must pass |
| `broker_live_criteria` | Broker live criteria | Yes | approved |
| `risk_owner_signoff` | Risk-owner signoff | Yes | approved, unexpired |
| `operator_signoff` | Operator signoff | Yes | approved, unexpired |
| `capital_binding_live_readiness` | Capital binding live readiness | Yes | approved |
| `bff_ha_production_topology` | BFF HA production topology | Yes | PoC passed + approved |
| `strict_publish_final_audit` | Strict publish final audit | Yes | passed |
| `telemetry_audit_incident` | Telemetry / audit / incident | Yes | available |
| `rollback_drill` | Rollback drill | Yes | passed |
| `kill_switch_demo` | Kill switch demo | Yes | passed |
| `multi_persona_sponsor_lineage` | Multi-persona sponsor lineage | Yes | bridged |
| `research_production_activation` | Research production activation | No | governed |
| `production_activation_gates` | Production real-writes and live-scale human signoff | Yes | approved |

---

## 6. Evidence retention

Human gate records and canary/live proof evidence are retained permanently per blueprint
§H5. The final Go/No-Go packet at `support/evidence/BPC-002-V2/final_go_no_go_packet.json`
is regenerated on each run; previous runs are not archived by this tool.

---

## 7. Fail-closed guarantees

- The assembler is read-only. It never modifies `ai-status.json`, task archives,
  or any evidence file.
- A row without task archives or BPC-001 evidence defaults to `incomplete` / no-go,
  not to `passed`.
- A row inheriting `pending_human_signoff` from BPC-001 propagates the same status
  rather than failing hard, because human gates are blocking non-errors.
