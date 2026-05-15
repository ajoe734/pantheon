#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import validate_ep5_live_order_cancel as validator


class ValidateEp5LiveOrderCancelTest(unittest.TestCase):
    def write_packet(self, packet_dir: Path, *, quantity: int = 1) -> None:
        (packet_dir / "runtime-manager-command-envelope.dry-run.json").write_text(
            json.dumps(
                {
                    "origin_service": "runtime-manager",
                    "command_type": "runtime_manager.live_canary_order.submit_cancel",
                    "dry_run": False,
                    "requires_explicit_human_approval": True,
                    "human_approval_ref": "approval-001",
                    "operator_id": "operator-1",
                    "runtime_binding_id": "rb-live",
                    "deployment_plan_id": "plan-live",
                    "target_stage": "live",
                    "idempotency_key": "idem-001",
                    "payload_refs": {
                        "submit_request": "live-order-submit.request.json",
                        "cancel_request": "live-order-cancel.request.json",
                    },
                }
            ),
            encoding="utf-8",
        )
        (packet_dir / "ibkr-packet-manifest.json").write_text(
            json.dumps(
                {
                    "packet_type": "ep5_runtime_manager_live_canary_order_cancel",
                    "broker": "IBKR",
                    "origin_service": "runtime-manager",
                    "runtime_binding_id": "rb-live",
                    "deployment_plan_id": "plan-live",
                    "operator_id": "operator-1",
                    "guardrails": {
                        "symbol": "AAPL",
                        "quantity": 1,
                        "order_type": "LMT",
                        "outside_rth": False,
                        "submit_after_human_approval_only": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        (packet_dir / "runtime-manager-lifecycle.schema.json").write_text(
            json.dumps(
                {
                    "schema": "runtime_manager_live_canary_order_lifecycle_v1",
                    "origin_service": "runtime-manager",
                    "required_events": [
                        "human_approval_archived",
                        "live_order_submitted",
                        "telemetry_trace_archived",
                        "closeout_archived",
                    ],
                }
            ),
            encoding="utf-8",
        )
        (packet_dir / "live-order-submit.request.json").write_text(
            json.dumps(
                {
                    "body": {
                        "account": "U123",
                        "symbol": "AAPL",
                        "security_type": "STK",
                        "exchange": "SMART",
                        "currency": "USD",
                        "action": "BUY",
                        "quantity": quantity,
                        "order_type": "LMT",
                        "limit_price": "1.00",
                        "time_in_force": "DAY",
                        "outside_rth": False,
                    }
                }
            ),
            encoding="utf-8",
        )
        (packet_dir / "live-order-submit.response.json").write_text(
            json.dumps({"order_id": "ord-001", "order_status": "Submitted"}),
            encoding="utf-8",
        )
        (packet_dir / "live-order-cancel.request.json").write_text(
            json.dumps({"body": {"order_id": "ord-001"}}),
            encoding="utf-8",
        )
        (packet_dir / "live-order-cancel.response.json").write_text(
            json.dumps({"order_id": "ord-001", "order_status": "Cancelled"}),
            encoding="utf-8",
        )
        (packet_dir / "telemetry-event-trace.response.json").write_text(
            json.dumps(
                {
                    "target_type": "telemetry_event",
                    "target_id": "evt-live-order-001",
                    "event_type": "live_order_absent_no_fill_verified",
                    "order_id": "ord-001",
                    "refs": {
                        "runtime_binding_ids": ["rb-live"],
                        "deployment_plan_ids": ["plan-live"],
                    },
                }
            ),
            encoding="utf-8",
        )
        (packet_dir / "runtime-manager-event-excerpt.json").write_text(
            json.dumps(
                {
                    "events": [
                        {"event_type": "live_order_submitted", "order_id": "ord-001"},
                        {"event_type": "live_order_cancelled", "order_id": "ord-001"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (packet_dir / "tws-open-order-transcript.md").write_text(
            "AAPL order ord-001 observed as Submitted in TWS.",
            encoding="utf-8",
        )
        (packet_dir / "operator-checklist.md").write_text(
            "human approval approval-001 recorded; runtime-manager healthy; limit non-marketable.",
            encoding="utf-8",
        )
        (packet_dir / "validator-expectations.md").write_text(
            "runtime-manager origin, IBKR guardrails, telemetry refs, lifecycle excerpt, closeout.",
            encoding="utf-8",
        )
        (packet_dir / "closeout-template.md").write_text(
            "final_disposition: canceled\nbroker_order_id: ord-001\ntelemetry_event_id: evt-live-order-001",
            encoding="utf-8",
        )
        (packet_dir / "operator-note.md").write_text(
            "Operator confirmed the live order was canceled and not filled.",
            encoding="utf-8",
        )

    def write_read_only_summary(
        self,
        packet_dir: Path,
        *,
        order_id: str = "ord-001",
        account: str = "U123",
        symbol: str = "AAPL",
    ) -> None:
        summary_dir = packet_dir / "read-only-verify-20260427T000000Z"
        summary_dir.mkdir()
        (summary_dir / "summary.json").write_text(
            json.dumps(
                {
                    "status": "ib_read_only_verified",
                    "generated_at": "2026-04-27T00:00:00Z",
                    "target": {
                        "account": account,
                        "symbol": symbol,
                        "order_id": order_id,
                    },
                    "session": {
                        "status": "ok",
                        "account_ref_present": True,
                        "next_valid_order_id": 1,
                    },
                    "open_orders": {
                        "status": "ok",
                        "open_order_count": 0,
                    },
                    "executions": {
                        "status": "ok",
                        "fill_status": "no_matching_executions",
                        "matching_execution_count": 0,
                        "matching_shares": 0.0,
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_valid_packet_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_dir = Path(tmpdir) / "packet"
            packet_dir.mkdir()
            self.write_packet(packet_dir)

            result = validator.validate_packet(packet_dir)

            self.assertEqual(result["status"], "validated")
            self.assertTrue(all(check["status"] == "pass" for check in result["checks"]))

    def test_guardrail_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_dir = Path(tmpdir) / "packet"
            packet_dir.mkdir()
            self.write_packet(packet_dir, quantity=2)

            result = validator.validate_packet(packet_dir)
            checks = {check["name"]: check for check in result["checks"]}

            self.assertEqual(result["status"], "failed")
            self.assertEqual(checks["minimal_live_order_guardrails"]["status"], "fail")

    def test_init_packet_creates_pending_scaffold_that_fails_until_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_dir = Path(tmpdir) / "packet"

            result = validator.init_packet(
                packet_dir,
                account="U123",
                limit_price="1.00",
                runtime_binding_id="rb-live",
                deployment_plan_id="plan-live",
                operator_id="operator-1",
            )
            validation = validator.validate_packet(packet_dir)
            checks = {check["name"]: check for check in validation["checks"]}

            self.assertEqual(result["status"], "initialized")
            self.assertTrue((packet_dir / "README.md").exists())
            self.assertEqual(validation["status"], "failed")
            self.assertEqual(checks["placeholders_replaced"]["status"], "fail")

    def test_missing_tws_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_dir = Path(tmpdir) / "packet"
            packet_dir.mkdir()
            self.write_packet(packet_dir)
            (packet_dir / "tws-open-order-transcript.md").unlink()

            result = validator.validate_packet(packet_dir)
            checks = {check["name"]: check for check in result["checks"]}

            self.assertEqual(result["status"], "failed")
            self.assertEqual(checks["tws_open_order_evidence_present"]["status"], "fail")

    def test_read_only_absent_no_fill_can_satisfy_broker_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_dir = Path(tmpdir) / "packet"
            packet_dir.mkdir()
            self.write_packet(packet_dir)
            (packet_dir / "live-order-cancel.response.json").write_text(
                json.dumps({"order_id": "ord-001", "order_status": "PreSubmitted"}),
                encoding="utf-8",
            )
            self.write_read_only_summary(packet_dir)

            result = validator.validate_packet(packet_dir)
            checks = {check["name"]: check for check in result["checks"]}

            self.assertEqual(result["status"], "validated")
            self.assertEqual(checks["broker_confirmed_cancel"]["status"], "pass")
            self.assertEqual(
                checks["broker_confirmed_cancel"]["detail"]["proof_source"],
                "ib_read_only_absent_no_fill",
            )

    def test_read_only_absent_no_fill_must_match_order_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_dir = Path(tmpdir) / "packet"
            packet_dir.mkdir()
            self.write_packet(packet_dir)
            (packet_dir / "live-order-cancel.response.json").write_text(
                json.dumps({"order_id": "ord-001", "order_status": "PreSubmitted"}),
                encoding="utf-8",
            )
            self.write_read_only_summary(packet_dir, order_id="other-order")

            result = validator.validate_packet(packet_dir)
            checks = {check["name"]: check for check in result["checks"]}

            self.assertEqual(result["status"], "failed")
            self.assertEqual(checks["broker_confirmed_cancel"]["status"], "fail")

    def test_record_helpers_replace_pending_packet_until_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_dir = Path(tmpdir) / "packet"
            validator.init_packet(
                packet_dir,
                account="U123",
                limit_price="<operator-set-price>",
                runtime_binding_id="rb-live",
                deployment_plan_id="plan-live",
                operator_id="operator-1",
            )
            validator.record_submit_request(
                packet_dir,
                account="U123",
                limit_price="1.00",
            )

            validator.record_submit_response(
                packet_dir,
                order_id="ord-001",
                status="Submitted",
                captured_at="2026-04-26T12:00:00Z",
            )
            validator.record_cancel_response(
                packet_dir,
                status="Cancelled",
                captured_at="2026-04-26T12:00:30Z",
            )
            validator.record_telemetry_trace(
                packet_dir,
                event_id="evt-live-order-001",
                runtime_binding_id="rb-live",
                deployment_plan_id="plan-live",
                order_id="ord-001",
                perm_id="123",
                observed_at="2026-04-26T12:00:30Z",
            )
            validator.record_runtime_excerpt(
                packet_dir,
                runtime_binding_id="rb-live",
                deployment_plan_id="plan-live",
                operator_id="operator-1",
                submitted_at="2026-04-26T12:00:00Z",
                canceled_at="2026-04-26T12:00:30Z",
            )
            validator.record_tws_transcript(
                packet_dir,
                state="Submitted",
                operator_id="operator-1",
                observed_at="2026-04-26T12:00:05Z",
            )
            validator.record_operator_note(
                packet_dir,
                operator_id="operator-1",
                submitted_at="2026-04-26T12:00:00Z",
                canceled_at="2026-04-26T12:00:30Z",
            )

            result = validator.validate_packet(packet_dir)

            self.assertEqual(result["status"], "validated")
            self.assertTrue(all(check["status"] == "pass" for check in result["checks"]))


if __name__ == "__main__":
    unittest.main()
