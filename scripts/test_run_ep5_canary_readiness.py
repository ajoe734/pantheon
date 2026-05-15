#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import run_ep5_canary_readiness as readiness


def base_env() -> dict[str, str]:
    return {
        "EXECUTION_BROKER_PROVIDER": "IBKR",
        "TW_EXECUTION_PROVIDER": "Shioaji",
        "CRYPTO_EXECUTION_PROVIDER": "Kraken",
        "TW_RESEARCH_PROVIDER": "TEJ",
        "BROKER_API_KEY_SECRET_NAME": "pantheon-dev-broker-api-key",
        "BROKER_API_SECRET_SECRET_NAME": "pantheon-dev-broker-api-secret",
        "SHIOAJI_API_KEY_SECRET_NAME": "pantheon-dev-shioaji-api-key",
        "SHIOAJI_SECRET_KEY_SECRET_NAME": "pantheon-dev-shioaji-secret-key",
        "KRAKEN_API_KEY_SECRET_NAME": "pantheon-dev-kraken-api-key",
        "KRAKEN_API_SECRET_SECRET_NAME": "pantheon-dev-kraken-api-secret",
        "TEJ_API_KEY_SECRET_NAME": "pantheon-dev-tej-api-key",
        "CANARY_BROKER_ACCOUNT_REF": "broker-subaccount-paper-us-equities",
        "CANARY_VENUE_REF": "venue-nyse-arca-smart",
        "CANARY_POOL_ID": "pool-canary-001",
        "CANARY_APPROVAL_DECISION_ID": "apv-canary-001",
        "CANARY_PERSONA_CAPITAL_BINDING_ID": "pcb-canary-001",
        "CANARY_HUMAN_GATE_PACKET_REF": "docs/deployment/evidence/ep5-human-gate-input/test/human-gate-packet.json",
        "CANARY_BROKER_SANDBOX_SMOKE_REF": "docs/deployment/evidence/execution-sandbox-canary-activation-ready/test",
        "CANARY_RISK_OWNER_APPROVAL_REF": "approval://risk-owner/canary-001",
        "CANARY_OPERATOR_APPROVAL_REF": "approval://operator/canary-001",
        "CANARY_REGISTRY_ID": "reg-canary-001",
        "CANARY_REGISTRY_VERSION": "2.0.0",
        "CANARY_ARTIFACT_TYPE": "model_artifact",
        "CANARY_ARTIFACT_CHECKSUM": "sha256-canary",
        "CANARY_STRATEGY_ID": "strategy-canary",
        "CANARY_CURRENT_STAGE": "paper",
        "CANARY_TARGET_STAGE": "canary",
        "CANARY_PLAN_ID": "plan-canary-001",
        "CANARY_PERSONA_ID": "persona-canary",
        "CANARY_RUNTIME_CONFIG_REF": "runtime-config://canary/test",
        "CANARY_CAPITAL_SCALE_PCT": "5",
        "CANARY_GROSS_SCALE_PCT": "25",
        "CANARY_RAMP_SCHEDULE": "day1:1%;day2:5%",
        "CANARY_FALLBACK_ARTIFACT_ID": "reg-paper-baseline-001",
        "CANARY_FALLBACK_ARTIFACT_VERSION": "1.1.9",
        "SHIOAJI_ACCOUNT_NAME": "paper-tw-equities",
        "TEJ_DATASET_CODE": "TWN/APRCD1",
    }


class RunEp5CanaryReadinessTest(unittest.TestCase):
    def test_evaluate_provider_matrix_requires_all_secret_refs(self) -> None:
        env = base_env()
        del env["KRAKEN_API_SECRET_SECRET_NAME"]

        items = readiness.evaluate_provider_matrix(env)
        by_name = {item.name: item for item in items}

        self.assertTrue(by_name["governed_provider_matrix_declared"].passed)
        self.assertFalse(by_name["governed_provider_secret_refs_present"].passed)
        self.assertIn("KRAKEN_API_SECRET_SECRET_NAME", by_name["governed_provider_secret_refs_present"].detail)

    def test_build_datasource_smoke_payloads_preserves_provider_boundaries(self) -> None:
        payload = readiness.build_datasource_smoke_payloads(base_env())

        self.assertEqual(payload["providers"]["ibkr"]["expected_provider"], "IBKR")
        self.assertEqual(payload["providers"]["shioaji"]["expected_provider"], "Shioaji")
        self.assertEqual(payload["providers"]["kraken"]["expected_provider"], "Kraken")
        self.assertEqual(payload["providers"]["tej"]["expected_provider"], "TEJ")
        self.assertEqual(payload["providers"]["ibkr"]["order_payload"]["broker"], "IBKR")
        self.assertEqual(payload["providers"]["shioaji"]["quote_subscription"]["exchange"], "TSE")
        self.assertEqual(payload["providers"]["kraken"]["order_payload"]["provider"], "Kraken")
        self.assertEqual(
            payload["providers"]["tej"]["normalized_dataset"]["governance_metadata"]["source_key"],
            "tej",
        )
        self.assertEqual(
            payload["providers"]["tej"]["normalized_dataset"]["governance_metadata"]["source_class"],
            "research_grade",
        )

    def test_run_datasource_smoke_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "canary.env"
            output_dir = Path(tmpdir) / "out"
            env_file.write_text(
                "\n".join(f"{key}={value}" for key, value in base_env().items()) + "\n",
                encoding="utf-8",
            )

            args = type("Args", (), {"env_file": str(env_file), "output_dir": str(output_dir)})
            exit_code = readiness.command_run_datasource_smoke(args)

            self.assertEqual(exit_code, 0)
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            payload = json.loads((output_dir / "datasource-smoke.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(sorted(summary["providers"]), ["ibkr", "kraken", "shioaji", "tej"])
            self.assertEqual(payload["task_id"], "APP-003-DATASOURCE-OPS-001")
            self.assertEqual(payload["providers"]["tej"]["dataset_code"], "TWN/APRCD1")

    def test_build_canary_plan_carries_runtime_manager_promotion_gate(self) -> None:
        plan, projection = readiness.build_canary_plan(base_env())

        gate = plan["metadata"]["promotion_gate"]
        self.assertEqual(plan["target_stage"], "canary")
        self.assertEqual(gate["broker_sandbox_smoke_ref"], "docs/deployment/evidence/execution-sandbox-canary-activation-ready/test")
        self.assertEqual(gate["risk_owner_approval_ref"], "approval://risk-owner/canary-001")
        self.assertEqual(gate["capital_scale_pct"], 5.0)
        self.assertEqual(projection["metadata"]["deployment_stage"], "canary")

    def test_emit_human_gate_packet_writes_packetized_trace_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            checklist_path = tmp / "operator-checklist.json"
            datasource_summary_path = tmp / "datasource-summary.json"
            plan_path = tmp / "canary-deployment-plan.json"
            drill_summary_path = tmp / "rollback-drill-summary.json"
            output_dir = tmp / "packet"

            checklist_path.write_text(
                json.dumps({"status": "pass", "check_health": True}),
                encoding="utf-8",
            )
            datasource_summary_path.write_text(
                json.dumps({"status": "pass", "providers": ["ibkr", "kraken", "shioaji", "tej"]}),
                encoding="utf-8",
            )
            plan_path.write_text(
                json.dumps(
                    {
                        "target_stage": "canary",
                        "scale": {"capital_scale_pct": 5.0, "gross_scale_pct": 25.0},
                        "rollback": {"action_type": "pause_then_replace"},
                    }
                ),
                encoding="utf-8",
            )
            drill_summary_path.write_text(
                json.dumps(
                    {
                        "status": "executed",
                        "replacement_binding_id": "rb-replacement-001",
                        "kill_switch_safe_mode": "paused",
                    }
                ),
                encoding="utf-8",
            )

            args = type(
                "Args",
                (),
                {
                    "checklist_json": str(checklist_path),
                    "datasource_summary_json": str(datasource_summary_path),
                    "plan_json": str(plan_path),
                    "drill_summary_json": str(drill_summary_path),
                    "dual_vm_evidence_dir": str(tmp),
                    "event_trace_status": "packetized",
                    "event_trace_note": "Trace query evidence remains packetized pending a replay-clean projection capture.",
                    "output_dir": str(output_dir),
                },
            )
            exit_code = readiness.command_emit_human_gate_packet(args)

            self.assertEqual(exit_code, 0)
            packet = json.loads((output_dir / "human-gate-packet.json").read_text(encoding="utf-8"))
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(packet["status"], "ready_for_review")
            self.assertEqual(packet["event_trace_read_model"]["status"], "packetized")
            self.assertEqual(summary["event_trace_status"], "packetized")
            self.assertEqual(packet["bundle"]["canary_plan"]["target_stage"], "canary")

    def test_emit_human_gate_packet_consumes_broker_smoke_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            checklist_path = tmp / "operator-checklist.json"
            datasource_summary_path = tmp / "datasource-summary.json"
            plan_path = tmp / "canary-deployment-plan.json"
            drill_summary_path = tmp / "rollback-drill-summary.json"
            broker_smoke_summary_path = tmp / "broker-smoke-summary.json"
            output_dir = tmp / "packet"

            checklist_path.write_text(json.dumps({"status": "pass", "check_health": True}), encoding="utf-8")
            datasource_summary_path.write_text(
                json.dumps({"status": "pass", "providers": ["ibkr", "kraken", "shioaji", "tej"]}),
                encoding="utf-8",
            )
            plan_path.write_text(
                json.dumps(
                    {
                        "target_stage": "canary",
                        "scale": {"capital_scale_pct": 5.0, "gross_scale_pct": 25.0},
                        "rollback": {"action_type": "pause_then_replace"},
                    }
                ),
                encoding="utf-8",
            )
            drill_summary_path.write_text(
                json.dumps(
                    {
                        "status": "executed",
                        "replacement_binding_id": "rb-replacement-001",
                        "kill_switch_safe_mode": "paused",
                    }
                ),
                encoding="utf-8",
            )
            broker_smoke_summary_path.write_text(
                json.dumps(
                    {
                        "task_id": "EP5-BROKER-TW-002",
                        "provider": "Shioaji",
                        "status": "passed",
                        "run_mode": "mock_api_replay",
                        "proof_boundary": "broker_adapter_sandbox_smoke; not canary/live/capital proof",
                        "order_ids": {
                            "pantheon_order_id": "order-001",
                            "shioaji_trade_id": "trade-001",
                        },
                        "reconciliation": {"status": "passed"},
                        "live_gate": {
                            "status": "rejected",
                            "response": {"error_code": "SHIOAJI_LIVE_DISABLED"},
                        },
                        "no_real_capital": {
                            "real_capital_used": False,
                            "production_live_order_submitted": False,
                        },
                    }
                ),
                encoding="utf-8",
            )

            args = type(
                "Args",
                (),
                {
                    "checklist_json": str(checklist_path),
                    "datasource_summary_json": str(datasource_summary_path),
                    "plan_json": str(plan_path),
                    "drill_summary_json": str(drill_summary_path),
                    "broker_smoke_summary_json": str(broker_smoke_summary_path),
                    "dual_vm_evidence_dir": str(tmp),
                    "event_trace_status": "packetized",
                    "event_trace_note": "Trace query evidence remains packetized pending a replay-clean projection capture.",
                    "output_dir": str(output_dir),
                },
            )
            exit_code = readiness.command_emit_human_gate_packet(args)

            self.assertEqual(exit_code, 0)
            packet = json.loads((output_dir / "human-gate-packet.json").read_text(encoding="utf-8"))
            self.assertEqual(packet["status"], "ready_for_review")
            self.assertEqual(
                packet["bundle"]["broker_sandbox_smoke"]["path"],
                str(broker_smoke_summary_path),
            )
            self.assertEqual(packet["bundle"]["broker_sandbox_smoke"]["provider"], "Shioaji")
            checks = {item["name"]: item for item in packet["acceptance_checks"]}
            self.assertEqual(checks["broker_sandbox_smoke_consumed"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
