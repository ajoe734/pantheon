"""Tests for L12-MFC-R4-AGORA-001: Agora handoff drainer and single intake path."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add services/policy-learning to path for hyphenated folder import if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agora_dataset_authority import (
    AgoraAuthorityUnavailable,
    AgoraDatasetAuthority,
)
from agora_handoff_drainer import process_drainer_cycle
from agora_handoff_drainer import DrainerRequestError


class TestAgoraDatasetAuthorityScannerToggle(unittest.TestCase):
    def test_scanner_disabled_via_env(self) -> None:
        with patch.dict(os.environ, {"AGORA_DATASET_STORE_SCANNER_ENABLED": "false", "AGORA_DATASET_STORE_BACKEND": "postgres"}):
            auth = AgoraDatasetAuthority(dsn="postgresql://user:pass@localhost:5432/db")
            self.assertEqual(auth.backend, "disabled")
            with self.assertRaises(AgoraAuthorityUnavailable) as ctx:
                auth._select(tenant_id="tenant-1", user_id="user-1", dataset_kind="learn")
            self.assertEqual(ctx.exception.reason, "agora_scanner_disabled")

    def test_scanner_enabled_default(self) -> None:
        with patch.dict(os.environ, {"AGORA_DATASET_STORE_BACKEND": "memory"}):
            auth = AgoraDatasetAuthority(records=[])
            self.assertEqual(auth.backend, "memory")


class TestAgoraHandoffDrainer(unittest.TestCase):
    @patch("agora_handoff_drainer.fetch_pending_handoffs")
    @patch("agora_handoff_drainer.post_handoff_to_policy_learning")
    @patch("agora_handoff_drainer.acknowledge_agora_handoff")
    def test_drainer_cycle_success(
        self, mock_ack: MagicMock, mock_post: MagicMock, mock_fetch: MagicMock
    ) -> None:
        mock_fetch.return_value = [
            {
                "handoff_id": "h-001",
                "dataset_version_id": "ver-001",
                "dataset_ref": {"dataset_version_id": "ver-001", "tenant_id": "tenant-test"},
                "acknowledged": False,
            }
        ]
        mock_post.return_value = {
            "status": "acknowledged",
            "candidate_id": "c-001",
            "dataset_version_id": "ver-001",
        }
        mock_ack.return_value = {"status": "acknowledged", "idempotent": False}

        res = process_drainer_cycle(
            agora_url="http://agora-bff:8000",
            policy_learning_url="http://policy-learning:8100",
            tenant_id="tenant-test",
            agora_token="agora-test-token",
            policy_learning_token="policy-test-token",
        )

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["processed_count"], 1)
        self.assertEqual(res["acked_count"], 1)
        mock_post.assert_called_once()
        mock_ack.assert_called_once()

    @patch("agora_handoff_drainer.fetch_pending_handoffs")
    def test_fetch_failure_is_not_reported_as_empty_success(
        self,
        mock_fetch: MagicMock,
    ) -> None:
        mock_fetch.side_effect = DrainerRequestError("BFF rejected service token")

        res = process_drainer_cycle(
            agora_url="http://agora-bff:8000",
            policy_learning_url="http://policy-learning:8100",
            tenant_id="tenant-test",
            agora_token="agora-test-token",
            policy_learning_token="policy-test-token",
        )

        self.assertEqual(res["status"], "error")
        self.assertEqual(res["reason"], "agora_handoff_fetch_failed")
        self.assertEqual(res["processed_count"], 0)

    def test_configuration_refuses_cross_service_token_reuse(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGORA_HANDOFF_SERVICE_TOKEN": "same-token",
                "POLICY_LEARNING_SERVICE_TOKEN": "same-token",
                "POLICY_LEARNING_AGORA_TENANT_ID": "tenant-test",
            },
            clear=False,
        ):
            from agora_handoff_drainer import (
                DrainerConfigurationError,
                require_drainer_configuration,
            )

            with self.assertRaises(DrainerConfigurationError):
                require_drainer_configuration()

    def test_drainer_delivers_four_decision_evidence_kinds_from_locks_and_journal(self) -> None:
        """End-to-end proof: decision, rejection, deferral, and intervention extracted from
        Agora decision locks and journal entries route to learn and are delivered by the
        existing agora_handoff_drainer into policy-learning without a second loop.
        """
        import importlib.util
        import tempfile
        from pathlib import Path
        from urllib.parse import urlsplit
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        from services.agora.service import AgoraWriteService
        from services.agora.shadow_mode import build_decision_lock
        from services.agora.store import AgoraStore
        from services.control_plane.bff.agora.dataset_extraction.extractor import (
            AgoraDatasetStore,
        )
        from services.control_plane.bff.agora.dataset_extraction.router import (
            create_dataset_extraction_router,
        )
        from services.control_plane.bff.agora.service import AgoraService
        from services.control_plane.bff.governance.decision_journal_write_owner import (
            build_decision_journal_write_owner,
        )
        from services.control_plane.bff.models import OperatorIdentity
        from services.research.imitation.agora_dataset_source import build_dataset_payload

        class _MemLockTable:
            def __init__(self, **_kw):
                self.rows = {}

            def insert_if_absent(self, record_id, payload, **_kw):
                if record_id in self.rows:
                    return False, dict(self.rows[record_id])
                self.rows[record_id] = dict(payload)
                return True, dict(payload)

            def get(self, record_id):
                return dict(self.rows[record_id]) if record_id in self.rows else None

            def list_all(self, **_kw):
                return [dict(r) for r in self.rows.values()]

        ds_store = AgoraDatasetStore(backend="memory")
        with patch("services.agora.store.PostgresJsonOwnerStore", _MemLockTable):
            lock_owner = AgoraWriteService(
                store=AgoraStore(dsn="postgresql://test/none", bootstrap=False),
                dataset_store=ds_store,
            )
        event = {
            "decision_event_id": "evt-drain-1",
            "event_kind": "entry",
            "origin": "strategy_signal",
            "strategy_id": "strat-tw-semi",
            "strategy_version_id": "v1",
            "strategy_spec_registry_id": "reg-tw-semi",
            "subject": {"symbol": "2330.TW"},
            "triggered_at": "2026-11-27T01:00:00Z",
            "data_cutoff": "2026-11-27T00:59:00Z",
            "suggested_action": "enter",
            "confidence": {"value": 0.81},
            "probability": {"value": 0.66},
            "expected_value": {"net": 0.028, "downside": -0.012},
        }
        # 1. Emit decision and rejection through production AgoraWriteService.create_decision_lock
        for idx, verdict in enumerate(("approve", "reject")):
            lock = build_decision_lock(
                human_decision_id=f"hdl-drain-{idx}",
                decision_record_id=f"drec-drain-{idx}",
                event={**event, "decision_event_id": f"evt-drain-{idx}"},
                decision_event_id=f"evt-drain-{idx}",
                tenant_id="tenant-drain",
                user_id="trader-alice",
                body_decision=verdict,
                rationale=f"Lock verdict {verdict}",
                modifications=None,
                decision_time=f"2026-11-27T01:0{idx}:00Z",
                actor_roles=["trader"],
            )
            lock_owner.create_decision_lock(lock, ["trader"])

        # 2. Emit deferral and intervention through production AgoraService.create_journal_entry
        with tempfile.TemporaryDirectory() as journal_dir:
            journal_owner = build_decision_journal_write_owner(
                data_dir=journal_dir,
            )
            journal_owner.dataset_store = ds_store
            agora_svc = AgoraService(
                journal_write_owner=journal_owner,
            )
            agora_svc.dataset_store = ds_store
            identity = OperatorIdentity(
                operator_id="trader-alice",
                roles=["operator"],
                claims={"tenant_id": "tenant-drain"},
            )
            for idx, entry in enumerate((
                {
                    "id": "jrnl-drain-defer",
                    "title": "Defer entry 2317.TW",
                    "body": "Deferred entry until afternoon session liquidity check.",
                    "decision": "defer",
                    "symbol": "2317.TW",
                    "strategy_id": "strat-tw-semi",
                    "data_cutoff": "2026-11-27T01:10:00Z",
                    "event_time": "2026-11-27T01:12:00Z",
                    "visibility": "private",
                },
                {
                    "id": "jrnl-drain-intervene",
                    "title": "Intervene trim 2330.TW",
                    "body": "Intervened to trim position size by half.",
                    "modifications": {"action": "reduce", "size_hint": "small"},
                    "tags": ["intervention", "risk_trim"],
                    "symbol": "2330.TW",
                    "strategy_id": "strat-tw-semi",
                    "data_cutoff": "2026-11-27T01:15:00Z",
                    "event_time": "2026-11-27T01:16:00Z",
                    "visibility": "private",
                },
            )):
                agora_svc.create_journal_entry(
                    payload=entry,
                    identity=identity,
                    idempotency_key=f"jrnl-drain-key-{idx}",
                    tenant_id="tenant-drain",
                    user_id="trader-alice",
                )

        # Mount real Agora dataset extraction router
        bff_app = FastAPI()
        bff_app.include_router(
            create_dataset_extraction_router(
                extract_identity=lambda _a: None,
                require_read_role=lambda _i: None,
                require_write_role=lambda _i: None,
                bff_error=lambda status, code, msg, reason, **kw: HTTPException(status, detail=reason),
                utc_now=lambda: "2026-11-27T02:00:00Z",
                dataset_store=ds_store,
            )
        )
        bff_client = TestClient(bff_app)

        service_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as data_dir, patch.dict(
            os.environ,
            {
                "POLICY_LEARNING_DATA_DIR": data_dir,
                "POLICY_LEARNING_STORE_BACKEND": "json",
                "PERSISTENCE_POSTURE": "lenient",
                "POLICY_LEARNING_DATASET_MODE": "product",
                "POLICY_LEARNING_SERVICE_TOKEN": "pl-drain-token",
                "POLICY_LEARNING_SERVICE_TENANTS": "tenant-drain",
                "AGORA_HANDOFF_SERVICE_TOKEN": "agora-drain-token",
                "AGORA_HANDOFF_SERVICE_TENANTS": "tenant-drain",
                "AGORA_DATASET_STORE_BACKEND": "memory",
            },
            clear=False,
        ):
            sys.modules.pop("store", None)
            spec = importlib.util.spec_from_file_location("pl_drainer_e2e_main", service_dir / "main.py")
            assert spec and spec.loader
            pl_mod = importlib.util.module_from_spec(spec)
            sys.modules["pl_drainer_e2e_main"] = pl_mod
            spec.loader.exec_module(pl_mod)
            pl_client = TestClient(pl_mod.app)

            def route_http(url: str, method: str = "GET", body=None, headers=None, timeout_seconds: float = 30.0):
                target = bff_client if url.startswith("http://agora-bff") else pl_client
                resp = target.request(method, urlsplit(url).path, json=body, headers=headers)
                self.assertLess(resp.status_code, 300, resp.text)
                return resp.json()

            with patch("agora_handoff_drainer._http_request", side_effect=route_http):
                cycle = process_drainer_cycle(
                    agora_url="http://agora-bff:8001",
                    policy_learning_url="http://policy-learning:8100",
                    tenant_id="tenant-drain",
                    agora_token="agora-drain-token",
                    policy_learning_token="pl-drain-token",
                )

            self.assertEqual(cycle["status"], "ok")
            self.assertEqual(cycle["processed_count"], 4)
            self.assertEqual(cycle["acked_count"], 4)

            # Verify zero pending handoffs remain on Agora BFF
            self.assertEqual(ds_store.list_handoffs_for_tenant(tenant_id="tenant-drain", pending_only=True), [])

            # Verify policy-learning authority holds all 4 decision evidence versions with snapshot
            versions = pl_mod.dataset_authority().list_decision_evidence_versions(
                tenant_id="tenant-drain",
                user_id="trader-alice",
            )
            self.assertEqual(len(versions), 4)
            self.assertEqual(
                {v.interaction_kind for v in versions},
                {"decision", "rejection", "deferral", "intervention"},
            )
            payload = build_dataset_payload(versions)
            self.assertEqual(len(payload["sessions"]), 4)
            for session in payload["sessions"]:
                self.assertIn("decision_time_snapshot", session)
                self.assertIn(session["evidence_kind"], {"decision", "rejection", "deferral", "intervention"})


if __name__ == "__main__":
    unittest.main()
