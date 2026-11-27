"""Tests for PAPER-TRADER-BOOK-ENROLLMENT-SKIP-20261127 (AC1, AC2, AC4).

Covers:
1. AC1: Paper enrollment reconcile in ``services/control-plane/bff/personas/service.py``
   (and ``PersonaProvisioningCoordinator.reconcile_paper_enrollment``) never acts on a
   trader servant paper book record: it reuses ``is_non_trading_persona`` /
   ``persona_class == 'agora_servant'`` or ``book_kind == 'trader_servant_paper_book'``
   with no second list, makes zero pool envelope publish calls, and leaves the
   CapitalPool, PersonaCapitalBinding, and RuntimeBinding unchanged.
2. AC2: Pool publish refusals in ``bff.persona_pool_publish_records`` are bounded:
   identical ``(step, reason)`` as the newest entry increments ``count`` and updates
   ``last_at`` instead of appending; at most 20 entries are kept; ``previous_envelope_kept``
   stores only ``version`` and ``digest`` instead of a full envelope copy; 144 identical
   refusals leave 1 entry with ``count == 144``.
3. AC4: Order-time risk policy for the servant paper book (``AGORA-PAPER-INTENT-EXECUTION-20261127``
   -> ``PaperExecutionAlgorithm.EvaluateOrderRiskGate`` -> ``resolve_bound_pool_risk_policy``)
   requires an approved risk policy profile mapping on the servant pool; ``ensure_trader_paper_book``
   obtains and binds it through the existing P-4 automated paper risk gate (risk-officer draft +
   automated gate approval, no human approval in paper, no hardcoded limits), GET-first and
   idempotently.
"""
from __future__ import annotations

import copy
import email.message
import importlib
import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from scripts import issue_dev_paper_principals as issuer
from services.control_plane.bff.persona_pool_publisher import (
    InMemoryPublishRecords,
    PersonaPoolPublisher,
    PoolPublishRefused,
)
from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    ProvisioningRecord,
)
from services.control_plane.bff.persona_provisioning_coordinator import (
    DraftResult,
    PersonaProvisioningCoordinator,
)
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.service import (
    _is_non_trading_paper_enrollment_record,
    _is_paper_enrollment_record_actionable,
    _PersonaOwnerHttpTransport,
    _reconcile_paper_enrollment_once,
)
from services.control_plane.bff.personas.trader_paper_book import (
    ensure_trader_paper_book,
    read_trader_paper_book,
    resolve_trader_paper_starting_capital,
    trader_paper_book_ids,
    trader_paper_book_provisioning_record,
)
from services.control_plane.persona.persona_strategy_pool import PoolNotFound


ROOT = Path(__file__).resolve().parents[4]
TENANT = "tenant-dev"
SERVANT_PERSONA = "agora-servant-799a3ce8c021b91cbb54"


def _load_runtime_manager_client_class() -> Any:
    module_path = ROOT / "services" / "runtime-manager" / "runtime_manager_client.py"
    spec = importlib.util.spec_from_file_location("pantheon_rm_client_skip_test", module_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RuntimeManagerClient


class _HttpResponse(io.BytesIO):
    status = 200

    def getcode(self) -> int:
        return 200

    @property
    def headers(self) -> email.message.Message:
        message = email.message.Message()
        message["Content-Type"] = "application/json"
        return message


# ---------------------------------------------------------------------------
# AC2: Bounded pool publish refusals in bff.persona_pool_publish_records
# ---------------------------------------------------------------------------


class _NoCoordinator:
    transport = None
    runtime_binding_reader = None

    def publish_pool_envelope(self, publication: Any) -> Any:  # pragma: no cover
        raise AssertionError("owner chain must not run")


class _MissingPoolStore:
    def current(self, persona_id: str, tenant_id: str) -> Dict[str, Any]:
        raise PoolNotFound(f"no strategy pool for persona {persona_id!r}")

    def version(self, persona_id: str, tenant_id: str, number: int) -> Dict[str, Any]:
        return self.current(persona_id, tenant_id)


def test_144_identical_pool_publish_refusals_collapse_to_one_entry_with_count_144_and_compact_kept_envelope() -> None:
    timestamps = iter([f"2026-11-27T10:{i // 60:02d}:{i % 60:02d}Z" for i in range(200)])
    records = InMemoryPublishRecords()
    expected_digest = "sha256:" + "d" * 64
    # Pre-seed a record whose current_envelope contains a full envelope payload to verify
    # that _refuse stores only compact {version, digest} in previous_envelope_kept.
    records.compare_and_set(
        f"{TENANT}:{SERVANT_PERSONA}",
        None,
        {
            "tenant_id": TENANT,
            "persona_id": SERVANT_PERSONA,
            "published": {
                "2": {
                    "pool_version": 2,
                    "pool_digest": expected_digest,
                    "envelope_artifact_id": "artifact-persona-pool-v2",
                    "envelope_version": "1.2.0",
                    "status": "published",
                }
            },
            "refusals": [],
            "current_envelope": {
                "artifact_id": "artifact-persona-pool-v2",
                "version": "1.2.0",
                "pool_version": 2,
                "parameters": {"pool_version": 2, "pool_digest": expected_digest, "symbols": ["2330.TW"]},
                "strategy_logic": {"members": [{"member_id": "m1", "risk_weight": 1.0}]},
            },
        },
    )

    publisher = PersonaPoolPublisher(
        coordinator=_NoCoordinator(),  # type: ignore[arg-type]
        pool_store=_MissingPoolStore(),
        records=records,
        now=lambda: next(timestamps),
    )

    for _ in range(144):
        with pytest.raises(PoolPublishRefused) as exc_info:
            publisher.publish(
                tenant_id=TENANT,
                persona_id=SERVANT_PERSONA,
                requested_by="pantheon-persona-owner-bff",
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.step == "pool"

    history = publisher.history(TENANT, SERVANT_PERSONA)
    refusals = history["refusals"]
    assert len(refusals) == 1
    entry = refusals[0]
    assert entry["count"] == 144
    assert entry["at"] == "2026-11-27T10:00:00Z"
    assert entry["last_at"] == "2026-11-27T10:02:23Z"
    assert entry["step"] == "pool"
    assert f"no strategy pool for persona {SERVANT_PERSONA!r}" in entry["reason"]
    assert entry["previous_envelope_kept"] == {
        "version": "1.2.0",
        "digest": expected_digest,
    }
    assert "strategy_logic" not in json.dumps(entry["previous_envelope_kept"])
    assert len(json.dumps(entry)) < 512


def test_pool_publish_refusals_retain_at_most_20_entries() -> None:
    tick = 0

    def _clock() -> str:
        nonlocal tick
        tick += 1
        return f"2026-11-27T11:00:{tick:02d}Z"

    class _VaryingPoolStore:
        def __init__(self) -> None:
            self.attempt = 0

        def current(self, persona_id: str, tenant_id: str) -> Dict[str, Any]:
            self.attempt += 1
            raise PoolNotFound(f"no strategy pool attempt {self.attempt}")

    publisher = PersonaPoolPublisher(
        coordinator=_NoCoordinator(),  # type: ignore[arg-type]
        pool_store=_VaryingPoolStore(),
        records=InMemoryPublishRecords(),
        now=_clock,
    )

    for _ in range(28):
        with pytest.raises(PoolPublishRefused):
            publisher.publish(
                tenant_id=TENANT,
                persona_id=SERVANT_PERSONA,
                requested_by="pantheon-persona-owner-bff",
            )

    refusals = publisher.history(TENANT, SERVANT_PERSONA)["refusals"]
    assert len(refusals) == PersonaPoolPublisher.MAX_REFUSALS == 20
    assert [r["reason"] for r in refusals] == [
        f"no strategy pool attempt {idx}" for idx in range(9, 29)
    ]
    assert all(r["count"] == 1 for r in refusals)
    assert all(r["previous_envelope_kept"] is None for r in refusals)


def _legacy_full_envelope(pool_version: int, digest: str) -> Dict[str, Any]:
    """Build a ~32-KiB pre-change full envelope snapshot for a given historical pool_version."""
    padding = f"member-payload-v{pool_version}-" + ("x" * 32_768)
    return {
        "artifact_id": f"artifact-persona-pool-v{pool_version}",
        "version": f"1.{pool_version}.0",
        "pool_version": pool_version,
        "parameters": {
            "pool_version": pool_version,
            "pool_digest": digest,
            "symbols": ["2330.TW"],
        },
        "strategy_logic": {
            "kind": "persona_portfolio_v1",
            "members": [
                {
                    "member_id": f"m-{pool_version}",
                    "risk_weight": 1.0,
                    "embedded_blob": padding,
                }
            ],
        },
    }


def test_legacy_full_envelope_refusals_are_compacted_preserving_historical_identity_on_distinct_and_same_reason_retries() -> None:
    """Regression for PR #7967 P2 review:
    1. Seed 20 pre-change refusals each holding a 32-KiB full envelope in previous_envelope_kept
       with distinct historical version/digest, then trigger _refuse with a distinct reason:
       all 19 retained legacy entries normalize previous_envelope_kept to their own historical
       {version, digest} (never overwritten by current_envelope) and total refusals JSON is <=10 KB.
    2. Seed 20 pre-change refusals each holding a 32-KiB full envelope in previous_envelope_kept
       and trigger repeated same-reason retries (which update only the newest entry in place and
       never slice out older entries): all 19 older entries are still compacted to their own
       historical {version, digest} on the first CAS mutation and remain stable across retries.
    """
    # --- Part 1: distinct reason after 20 seeded 32-KiB legacy refusals ---
    records_distinct = InMemoryPublishRecords()
    published_map: Dict[str, Dict[str, Any]] = {}
    legacy_refusals_distinct: List[Dict[str, Any]] = []
    for idx in range(1, 21):
        hist_digest = f"sha256:{idx:064x}"
        published_map[str(idx)] = {
            "pool_version": idx,
            "pool_digest": hist_digest,
            "envelope_artifact_id": f"artifact-persona-pool-v{idx}",
            "envelope_version": f"1.{idx}.0",
            "status": "published",
        }
        legacy_refusals_distinct.append(
            {
                "at": f"2026-11-27T09:{idx:02d}:00Z",
                "pool_version": idx,
                "step": "member",
                "reason": f"legacy refusal reason {idx}",
                "previous_envelope_kept": _legacy_full_envelope(idx, hist_digest),
            }
        )
    current_digest = f"sha256:{21:064x}"
    published_map["21"] = {
        "pool_version": 21,
        "pool_digest": current_digest,
        "envelope_artifact_id": "artifact-persona-pool-v21",
        "envelope_version": "1.21.0",
        "status": "published",
    }
    seeded_record_distinct = {
        "tenant_id": TENANT,
        "persona_id": SERVANT_PERSONA,
        "published": copy.deepcopy(published_map),
        "refusals": legacy_refusals_distinct,
        "current_envelope": _legacy_full_envelope(21, current_digest),
    }
    assert len(json.dumps(seeded_record_distinct["refusals"])) > 640_000
    records_distinct.compare_and_set(f"{TENANT}:{SERVANT_PERSONA}", None, seeded_record_distinct)

    publisher_distinct = PersonaPoolPublisher(
        coordinator=_NoCoordinator(),  # type: ignore[arg-type]
        pool_store=_MissingPoolStore(),
        records=records_distinct,
        now=lambda: "2026-11-27T10:30:00Z",
    )
    with pytest.raises(PoolPublishRefused):
        publisher_distinct.publish(
            tenant_id=TENANT,
            persona_id=SERVANT_PERSONA,
            requested_by="pantheon-persona-owner-bff",
        )

    compacted_distinct = publisher_distinct.history(TENANT, SERVANT_PERSONA)["refusals"]
    assert len(compacted_distinct) == 20
    # Entries 0..18 correspond to historical pool versions 2..20 and preserve their own version/digest.
    for pos, hist_version in enumerate(range(2, 21)):
        item = compacted_distinct[pos]
        assert item["reason"] == f"legacy refusal reason {hist_version}"
        assert item["previous_envelope_kept"] == {
            "version": f"1.{hist_version}.0",
            "digest": f"sha256:{hist_version:064x}",
        }
    # Entry 19 is the newly appended refusal and carries current_envelope's version/digest.
    assert compacted_distinct[19]["step"] == "pool"
    assert compacted_distinct[19]["count"] == 1
    assert compacted_distinct[19]["previous_envelope_kept"] == {
        "version": "1.21.0",
        "digest": current_digest,
    }
    serialized_distinct = json.dumps(compacted_distinct)
    assert "embedded_blob" not in serialized_distinct
    assert len(serialized_distinct) <= 10_000

    # --- Part 2: repeated same-reason retries on 20 seeded 32-KiB legacy refusals ---
    records_same = InMemoryPublishRecords()
    same_reason = f"no strategy pool for persona {SERVANT_PERSONA!r}"
    legacy_refusals_same: List[Dict[str, Any]] = []
    for idx in range(1, 21):
        hist_digest = f"sha256:{idx:064x}"
        # Even without a published[idx] entry, _envelope_summary extracts parameters.pool_digest
        # or canonical_digest from each legacy full envelope itself.
        legacy_refusals_same.append(
            {
                "at": f"2026-11-27T09:{idx:02d}:00Z",
                "pool_version": idx,
                "step": "pool" if idx == 20 else "member",
                "reason": same_reason if idx == 20 else f"older legacy refusal {idx}",
                "previous_envelope_kept": _legacy_full_envelope(idx, hist_digest),
            }
        )
    records_same.compare_and_set(
        f"{TENANT}:{SERVANT_PERSONA}",
        None,
        {
            "tenant_id": TENANT,
            "persona_id": SERVANT_PERSONA,
            "published": {},
            "refusals": legacy_refusals_same,
            "current_envelope": _legacy_full_envelope(21, current_digest),
        },
    )

    retry_clock = iter([f"2026-11-27T11:{i:02d}:00Z" for i in range(1, 40)])
    publisher_same = PersonaPoolPublisher(
        coordinator=_NoCoordinator(),  # type: ignore[arg-type]
        pool_store=_MissingPoolStore(),
        records=records_same,
        now=lambda: next(retry_clock),
    )
    for _ in range(25):
        with pytest.raises(PoolPublishRefused):
            publisher_same.publish(
                tenant_id=TENANT,
                persona_id=SERVANT_PERSONA,
                requested_by="pantheon-persona-owner-bff",
            )

    compacted_same = publisher_same.history(TENANT, SERVANT_PERSONA)["refusals"]
    assert len(compacted_same) == 20
    # Older entries 0..18 never aged out by slicing, yet were compacted in place to their own historical version/digest.
    for pos, hist_version in enumerate(range(1, 20)):
        item = compacted_same[pos]
        assert item["reason"] == f"older legacy refusal {hist_version}"
        assert item["previous_envelope_kept"] == {
            "version": f"1.{hist_version}.0",
            "digest": f"sha256:{hist_version:064x}",
        }
    # Entry 19 updated in place 25 times (1 legacy + 25 retries = 26).
    newest = compacted_same[19]
    assert newest["reason"] == same_reason
    assert newest["at"] == "2026-11-27T09:20:00Z"
    assert newest["last_at"] == "2026-11-27T11:25:00Z"
    assert newest["count"] == 26
    assert newest["previous_envelope_kept"] == {
        "version": "1.21.0",
        "digest": current_digest,
    }
    serialized_same = json.dumps(compacted_same)
    assert "embedded_blob" not in serialized_same
    assert len(serialized_same) <= 10_000


# ---------------------------------------------------------------------------
# AC1 & AC4: Real Capital + Governance + Runtime-Manager fixture
# ---------------------------------------------------------------------------


class _EnrollmentCapablePersonaOwner:
    """Persona write owner implementing the full paper enrollment owner surface."""

    def __init__(self) -> None:
        self.personas: Dict[str, Dict[str, Any]] = {}
        self.seed_calls: List[str] = []
        self.advance_calls: List[str] = []
        self.harness_calls: List[str] = []

    def get_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
        item = self.personas.get(persona_id)
        return json.loads(json.dumps(item)) if item is not None else None

    def update_persona(self, persona_id: str, **kwargs: Any) -> Dict[str, Any]:
        rec = dict(self.personas.get(persona_id) or {"id": persona_id, "persona_id": persona_id})
        for key in ("name", "lifecycle_state", "risk_level", "metadata", "updated_at"):
            if kwargs.get(key) is not None:
                rec[key] = json.loads(json.dumps(kwargs[key]))
        self.personas[persona_id] = rec
        return json.loads(json.dumps(rec))

    def advance_lifecycle_governed(self, persona_id: str, **kwargs: Any) -> Dict[str, Any]:
        self.advance_calls.append(persona_id)
        raise AssertionError(f"advance_lifecycle_governed must not run for servant {persona_id}")

    def get_strategy_pool(self, persona_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return None

    def seed_strategy_pool(self, persona_id: str, **kwargs: Any) -> Dict[str, Any]:
        self.seed_calls.append(persona_id)
        raise AssertionError(f"seed_strategy_pool must not run for servant {persona_id}")

    def get_harness(self, persona_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return None

    def initialize_harness(self, persona_id: str, **kwargs: Any) -> Dict[str, Any]:
        self.harness_calls.append(persona_id)
        raise AssertionError(f"initialize_harness must not run for servant {persona_id}")


@pytest.fixture
def servant_paper_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Real Capital app, real Governance app (issued P-4 paper principals), and real RuntimeManagerService."""
    from services.governance import approval_authority
    from services.governance import main as gov_main
    from services.governance.test_approval_authority_postgres import OperatorMemoryStore

    gov_secret = "g" * 40
    cap_secret = "capital-servant-book-secret-0123456789"
    proposer_id = "pantheon-dev-persona-enrollment-proposer"
    issued = issuer.issue_environment(
        {
            "PANTHEON_ENV": "dev",
            "PANTHEON_DEV_BFF_TENANT_ID": TENANT,
            "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
            "PANTHEON_DEV_BFF_JWT_SECRET": gov_secret,
            "PANTHEON_DEV_BFF_JWT_ISSUER": "iss-test",
            "PANTHEON_DEV_BFF_JWT_AUDIENCE": "aud-test",
        }
    )
    grant = tmp_path / "grant"
    grant.write_text(issuer.GOVERNANCE_GRANT_CONTENT, encoding="utf-8")
    grant.chmod(0o600)

    for name, value in {"JWT_SECRET": gov_secret, "JWT_ISSUER": "iss-test", "JWT_AUDIENCE": "aud-test"}.items():
        monkeypatch.setenv(f"PANTHEON_GOVERNANCE_{name}", value)
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_GRANT_FILE", str(grant))
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(tmp_path / "no-policies"))
    monkeypatch.setattr(gov_main, "store", OperatorMemoryStore())
    monkeypatch.setattr(gov_main, "verify_paper_candidate_for_decision", lambda *a, **k: None)
    monkeypatch.setattr(gov_main, "_paper_registry_reader", lambda: None)
    gov_client = TestClient(gov_main.app)

    cap_dir = tempfile.mkdtemp(prefix="capital_servant_book_")
    rm_store_path = tmp_path / "runtime_manager" / "bindings.json"
    rm_store_path.parent.mkdir(parents=True, exist_ok=True)
    for key, value in {
        "CAPITAL_DATA_DIR": cap_dir,
        "PANTHEON_GOVERNANCE_DATA_DIR": cap_dir,
        "CAPITAL_STORE_BACKEND": "json",
        "CAPITAL_AUDIT_BACKEND": "jsonl",
        "CAPITAL_AUTH_DISABLED": "false",
        "CAPITAL_AUTH_MODE": "strict",
        "CAPITAL_JWT_SECRET": cap_secret,
        "PANTHEON_CAPITAL_JWT_SECRET": cap_secret,
        "CAPITAL_ALLOWED_CALLER_SERVICES": personas_service.PERSONA_OWNER_SERVICE_ACTOR_ID,
        "CAPITAL_GOVERNANCE_BASE_URL": "http://governance.test",
        "CAPITAL_GOVERNANCE_SERVICE_TOKEN": issued["CAPITAL_GOVERNANCE_SERVICE_TOKEN"],
        "PANTHEON_GOVERNANCE_APPROVAL_API_URL": "http://governance.test",
        "PANTHEON_CAPITAL_API_URL": "http://capital.test",
        "PANTHEON_REGISTRY_API_URL": "http://registry.test",
        "PANTHEON_DEPLOYMENT_API_URL": "http://deployment.test",
        "PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN": issued["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"],
        "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID": issued["PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID"],
        "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID": proposer_id,
        "PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN": issued["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN"],
        "PANTHEON_BFF_JWT_SECRET": cap_secret,
        "PANTHEON_RUNTIME_BINDING_STORE_PATH": str(rm_store_path),
        "PANTHEON_SINGLE_RUNTIME_ENFORCED": "true",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("OPENCLAW_GATEWAY_URL", raising=False)
    monkeypatch.delenv("PANTHEON_RUNTIME_MANAGER_URL", raising=False)

    sys.modules.pop("services.capital.main", None)
    cap_main = importlib.reload(importlib.import_module("services.capital.main"))
    cap_client = TestClient(cap_main.app)

    from services.control_plane.bff.test_persona_provisioning_coordinator import (
        FakeOwnerTransport,
    )

    authority_transport = FakeOwnerTransport(
        governance_subject=issued["PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID"],
    )
    seen_http: List[Tuple[str, str, str]] = []

    def _urlopen(request: Any, timeout: Any = None, **_kw: Any) -> _HttpResponse:
        url = request.full_url
        owner, _, path = url.partition(".test")
        owner = owner.rsplit("//", 1)[-1]
        path = path or "/"
        method = request.get_method().upper()
        seen_http.append((owner, method, path))
        if owner in {"registry", "deployment"}:
            if method == "GET":
                found = authority_transport.get(owner, path)
                if found is None:
                    raise urllib.error.HTTPError(
                        url,
                        404,
                        "not found",
                        email.message.Message(),
                        io.BytesIO(b'{"detail":"not found"}'),
                    )
                return _HttpResponse(json.dumps(found).encode("utf-8"))
            payload = json.loads(request.data.decode("utf-8")) if request.data else {}
            if owner == "registry" and path.endswith("/advance"):
                decision_id = str(payload.get("approval_decision_id") or "").strip()
                if decision_id:
                    gov_path = f"/api/governance/approvals/{decision_id}"
                    gov_resp = gov_client.get(
                        gov_path,
                        headers={
                            "Authorization": "Bearer "
                            + issued["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"]
                        },
                    )
                    if gov_resp.status_code == 200:
                        authority_transport.objects[("governance", gov_path)] = (
                            gov_resp.json()
                        )
            created = authority_transport.post(owner, path, payload)
            return _HttpResponse(json.dumps(created).encode("utf-8"))
        client = {"governance": gov_client, "capital": cap_client}[owner]
        headers = {k: v for k, v in request.header_items()}
        resp = client.request(method, path, content=request.data, headers=headers)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                url,
                resp.status_code,
                "error",
                email.message.Message(),
                io.BytesIO(resp.content),
            )
        return _HttpResponse(resp.content)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(personas_service.urllib_request, "urlopen", _urlopen)
    monkeypatch.setattr(
        approval_authority,
        "build_opener",
        lambda *a, **k: SimpleNamespace(open=lambda request, timeout=None: _urlopen(request, timeout)),
    )

    rm_client_cls = _load_runtime_manager_client_class()
    rm_client = rm_client_cls(allow_local=True)
    rm_service = rm_client._local()

    provisioning_store = MemoryPersonaProvisioningStore()
    persona_owner = _EnrollmentCapablePersonaOwner()
    persona_owner.personas[SERVANT_PERSONA] = {
        "id": SERVANT_PERSONA,
        "persona_id": SERVANT_PERSONA,
        "name": "Agora Servant",
        "archetype": "agora_servant",
        "lifecycle_state": "draft",
        "status": "draft",
        "risk_level": "low",
        "mandate": "user_private_agora_servant",
        "strategy_family": "agora_servant",
        "metadata": {
            "tenant_id": TENANT,
            "agora_user_id": "trader-user-1",
            "persona_class": "agora_servant",
            "environment_ceiling": "paper",
            "deployment_stage": "paper",
            "execution_authority": "none",
        },
    }

    ids = trader_paper_book_ids(TENANT, SERVANT_PERSONA)
    drafted_policy = {
        "risk_policy_id": f"rp-{ids.capital_pool_id}",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity", "future"],
        "gross_limit": 0.25,
        "net_limit": 0.25,
        "max_leverage": 0.25,
        "max_single_name_weight": 0.10,
        "min_cash_reserve_pct": 0.50,
    }
    draft = DraftResult(
        True,
        policy=dict(drafted_policy),
        provenance={
            "source": "risk_officer_draft",
            "risk_persona_id": "persona-risk-officer",
            "input_digest": "servant-paper-book-e2e",
        },
    )
    monkeypatch.setattr(
        PersonaProvisioningCoordinator,
        "_drafter",
        lambda self: (lambda _inputs: draft),
    )
    monkeypatch.setattr(personas_service, "_persona_provisioning_store", lambda: provisioning_store)
    monkeypatch.setattr(personas_service, "_get_active_write_owner", lambda: persona_owner)
    monkeypatch.setattr(personas_service, "_runtime_manager_client", lambda: rm_client)

    try:
        yield SimpleNamespace(
            gov=gov_client,
            cap=cap_client,
            cap_main=cap_main,
            rm_client=rm_client,
            rm_service=rm_service,
            provisioning_store=provisioning_store,
            persona_owner=persona_owner,
            issued=issued,
            seen_http=seen_http,
            ids=ids,
            drafted_policy=drafted_policy,
        )
    finally:
        cap_client.close()
        gov_client.close()
        sys.modules.pop("services.capital.main", None)


def test_paper_enrollment_reconcile_never_acts_on_trader_servant_paper_book_record(
    servant_paper_world: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: _is_paper_enrollment_record_actionable, _reconcile_paper_enrollment_once, and
    coordinator.reconcile_paper_enrollment skip trader servant paper book records with zero
    pool publish calls and leave the pool, capital binding, and RuntimeBinding unchanged."""
    world = servant_paper_world
    book = ensure_trader_paper_book(
        TENANT,
        SERVANT_PERSONA,
        requested_by="trader-user-1",
        agora_user_id="trader-user-1",
        persona_record=world.persona_owner.get_persona(SERVANT_PERSONA),
        persona_write_owner=world.persona_owner,
        runtime_client=world.rm_client,
    )
    pool_id = book["capital_pool_id"]
    pcb_id = book["persona_capital_binding_id"]
    rb_id = book["runtime_binding_id"]

    # 1. Store both a newly created trader_paper_book_provisioning_record and the exact
    #    hosted 2026-11-27T09:42Z record shape (state='provisioning', mandate='user_private_agora_servant_paper_book').
    starting = resolve_trader_paper_starting_capital(as_of="2026-11-27T09:42:00Z")
    current_record = trader_paper_book_provisioning_record(
        TENANT,
        SERVANT_PERSONA,
        requested_by="trader-user-1",
        starting_capital=starting,
        created_at="2026-11-27T09:42:00Z",
    )
    hosted_legacy_payload = {
        k: v
        for k, v in current_record.request_payload.items()
        if k not in {"persona_class", "book_kind"}
    }
    reserved, _ = world.provisioning_store.reserve(
        tenant_id=current_record.tenant_id,
        idempotency_key=current_record.idempotency_key,
        request_hash=current_record.request_hash,
        normalized_name=current_record.normalized_name,
        persona_id=current_record.persona_id,
        request_payload=hosted_legacy_payload,
    )
    leased = world.provisioning_store.acquire(
        reserved.tenant_id,
        reserved.idempotency_key,
        lease_owner="initial-book-provision",
        lease_seconds=180,
    )
    assert leased is not None and leased.state == "provisioning"
    world.provisioning_store.release(
        leased,
        lease_owner="initial-book-provision",
        lease_seconds=180,
    )

    now = datetime(2026, 11, 27, 12, 30, 0, tzinfo=timezone.utc)
    stored_record = world.provisioning_store.get(TENANT, current_record.idempotency_key)
    assert stored_record is not None and stored_record.state == "provisioning"

    # Both the current record shape and the hosted 09:42Z record shape are non-trading and non-actionable.
    assert _is_non_trading_paper_enrollment_record(current_record, persona_owner=world.persona_owner) is True
    assert _is_non_trading_paper_enrollment_record(stored_record, persona_owner=world.persona_owner) is True
    assert _is_paper_enrollment_record_actionable(current_record, now, 600, persona_owner=world.persona_owner) is False
    assert _is_paper_enrollment_record_actionable(stored_record, now, 600, persona_owner=world.persona_owner) is False

    # Also verify synthetic records matching solely by book_kind or solely by persona_class.
    by_book_kind = ProvisioningRecord(
        tenant_id=TENANT,
        idempotency_key="custom-book-key",
        request_hash="1" * 64,
        normalized_name="book-only",
        persona_id="unknown-persona",
        request_payload={"book_kind": "trader_servant_paper_book"},
        state="provisioning",
    )
    by_persona_class = ProvisioningRecord(
        tenant_id=TENANT,
        idempotency_key="custom-servant-key",
        request_hash="2" * 64,
        normalized_name="servant-only",
        persona_id="unknown-persona",
        request_payload={"persona_class": "agora_servant"},
        state="succeeded",
    )
    assert _is_paper_enrollment_record_actionable(by_book_kind, now, 600, persona_owner=world.persona_owner) is False
    assert _is_paper_enrollment_record_actionable(by_persona_class, now, 600, persona_owner=world.persona_owner) is False

    # 2. Spy on PersonaPoolPublisher.publish and owner state before running reconcile.
    publish_calls: List[Tuple[str, str]] = []
    orig_publish = PersonaPoolPublisher.publish

    def _spy_publish(self: PersonaPoolPublisher, **kwargs: Any) -> Dict[str, Any]:
        publish_calls.append((str(kwargs.get("tenant_id")), str(kwargs.get("persona_id"))))
        return orig_publish(self, **kwargs)

    monkeypatch.setattr(PersonaPoolPublisher, "publish", _spy_publish)

    pool_before = world.cap_main.pool_store.get(pool_id).to_dict()
    pcb_before = world.cap_main.binding_store.get(pcb_id).to_dict()
    rb_before = world.rm_service.get_binding(rb_id).to_dict()
    world.seen_http.clear()

    # Run _reconcile_paper_enrollment_once() and direct coordinator.reconcile_paper_enrollment().
    assert _reconcile_paper_enrollment_once() == 0

    coordinator = PersonaProvisioningCoordinator(
        store=world.provisioning_store,
        transport=_PersonaOwnerHttpTransport(tenant_id=TENANT),
        schedule_registrar=lambda *_a, **_kw: {},
        lease_owner="reconcile-check",
        actor_id=personas_service.PERSONA_OWNER_SERVICE_ACTOR_ID,
        persona_owner_provider=lambda: world.persona_owner,
    )
    after_direct = coordinator.reconcile_paper_enrollment(stored_record)
    assert "paper_enrollment" not in (after_direct.references or {})

    # Zero pool publish calls, zero lifecycle/pool/harness owner calls, and zero HTTP calls.
    assert publish_calls == []
    assert world.persona_owner.seed_calls == []
    assert world.persona_owner.advance_calls == []
    assert world.persona_owner.harness_calls == []
    assert world.seen_http == []

    # CapitalPool, PersonaCapitalBinding, and RuntimeBinding are completely unchanged.
    assert world.cap_main.pool_store.get(pool_id).to_dict() == pool_before
    assert world.cap_main.binding_store.get(pcb_id).to_dict() == pcb_before
    assert world.rm_service.get_binding(rb_id).to_dict() == rb_before

    readback = read_trader_paper_book(
        TENANT,
        SERVANT_PERSONA,
        agora_user_id="trader-user-1",
        persona_record=world.persona_owner.get_persona(SERVANT_PERSONA),
        runtime_client=world.rm_client,
    )
    assert readback is not None
    assert readback["capital_pool_id"] == pool_id
    assert readback["persona_capital_binding_id"] == pcb_id
    assert readback["runtime_binding_id"] == rb_id


def test_ensure_trader_paper_book_obtains_p4_automated_gate_risk_policy_and_order_risk_gate_passes(
    servant_paper_world: Any,
) -> None:
    """AC4: ensure_trader_paper_book obtains an approved capital_pool_activation risk policy
    mapping via the P-4 automated paper risk gate (risk-officer draft, no human approval,
    no hardcoded limits), is GET-first idempotent on replay, and satisfies the order-time
    risk gate in PaperExecutionAlgorithm.EvaluateOrderRiskGate."""
    from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm

    world = servant_paper_world
    persona_rec = world.persona_owner.get_persona(SERVANT_PERSONA)

    # 1. First ensure provisions pool, binding, RuntimeBinding AND obtains P-4 gate-approved risk policy.
    first = ensure_trader_paper_book(
        TENANT,
        SERVANT_PERSONA,
        requested_by="trader-user-1",
        agora_user_id="trader-user-1",
        persona_record=persona_rec,
        persona_write_owner=world.persona_owner,
        runtime_client=world.rm_client,
    )
    pool_id = first["capital_pool_id"]
    assert first["idempotent_replay"] is False
    resolution = first.get("risk_policy_resolution")
    assert isinstance(resolution, dict) and resolution["resolved"] is True
    assert resolution["allow_new_or_increased_risk"] is True
    assert resolution["profile_mapping_status"] == "approved_profile_mapping"
    approval_id = resolution["profile_mapping_approval_id"]
    assert isinstance(approval_id, str) and approval_id.startswith("apv-risk-profile-")

    # Verify Governance holds one decided/approved capital_pool_activation decision by automated_gate (P-4).
    gov_headers = {"Authorization": "Bearer " + world.issued["PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN"]}
    gov_resp = world.gov.get(f"/api/governance/approvals/{approval_id}", headers=gov_headers)
    assert gov_resp.status_code == 200, gov_resp.text
    decision = gov_resp.json()
    assert decision["target_type"] == "capital_pool_activation"
    assert decision["target_id"] == pool_id
    assert decision["decision_state"] == "decided"
    assert decision["decision"] == "approved"
    assert decision["actor_role"] == "automated_gate"
    assert decision["actor_id"] == "pantheon-dev-paper-provisioner"
    assert decision["metadata"]["subject"]["risk_officer_draft"]["policy"]["gross_limit"] == 0.25

    # 2. Second ensure is GET-first and idempotent: zero POST or PATCH requests to Capital or Governance.
    world.seen_http.clear()
    second = ensure_trader_paper_book(
        TENANT,
        SERVANT_PERSONA,
        requested_by="trader-user-1",
        agora_user_id="trader-user-1",
        persona_record=world.persona_owner.get_persona(SERVANT_PERSONA),
        persona_write_owner=world.persona_owner,
        runtime_client=world.rm_client,
    )
    assert second["idempotent_replay"] is True
    assert second["risk_policy_resolution"]["resolved"] is True
    assert second["risk_policy_resolution"]["profile_mapping_approval_id"] == approval_id
    mutations = [(owner, method, path) for owner, method, path in world.seen_http if method != "GET"]
    assert mutations == []

    # 3. Order-time verification through PaperExecutionAlgorithm.EvaluateOrderRiskGate
    #    (the exact gate invoked when AGORA-PAPER-INTENT-EXECUTION-20261127 signals are consumed).
    algo = PaperExecutionAlgorithm(
        initial_cash=10_000_000.0,
        deployment_stage="paper",
        capital_pool_id=pool_id,
        risk_policy=None,
        risk_policy_id=None,
        capital_pool_reader=lambda pid: (
            world.cap_main.pool_store.get(pid).to_dict()
            if world.cap_main.pool_store.get(pid) is not None
            else None
        ),
    )
    resolved_policy = algo._resolve_active_risk_policy()
    assert resolved_policy.risk_policy_id == f"rp-{pool_id}"
    assert resolved_policy.version == "1.0"
    verdict = algo.EvaluateOrderRiskGate(
        symbol="2330.TW",
        side="buy",
        quantity=100,
        price=600.0,
        instrument_id="eq:2330",
    )
    assert verdict["accepted"] is True, verdict
    assert verdict["risk_status"] == "succeeded"

    # Counterfactual: an unbound pool without the P-4 gate-approved mapping fails closed at order time.
    unbound_algo = PaperExecutionAlgorithm(
        initial_cash=10_000_000.0,
        deployment_stage="paper",
        capital_pool_id="pool-unbound-servant",
        risk_policy=None,
        risk_policy_id=None,
        capital_pool_reader=lambda pid: {
            "pool_id": pid,
            "tenant_id": TENANT,
            "status": "active",
            "currency": "TWD",
            "budget": 10_000_000.0,
            "single_runtime_enforced": True,
            "metadata": {
                "tenant_id": TENANT,
                "persona_id": SERVANT_PERSONA,
                "execution_context": "paper",
            },
        },
    )
    rejected = unbound_algo.EvaluateOrderRiskGate(
        symbol="2330.TW",
        side="buy",
        quantity=100,
        price=600.0,
        instrument_id="eq:2330",
    )
    assert rejected["accepted"] is False
    assert rejected["reason"] == "risk_policy_unavailable"
