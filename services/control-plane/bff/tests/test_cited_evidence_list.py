"""BFF-EVIDENCE-CITED-LIST-20261125: GET /bff/management/evidence?scope=cited.

The evidence library lists only references that decisions, meetings,
approvals and research really cite. Every fixture below uses the recorded
owner shapes (Consultation meeting detail / proposals, Governance approval
``evidence_refs``, Research ticket ``evidence_refs`` + ``human_input``,
Research demand ``retrieval_influence.metadata.cited_item_ids`` and OODA
packet ``persona_cycle.decisions[].cited_case_file_item_ids``) and the
hosted reference ids observed 2026-11-25. Only the transport below each
existing owner read is replaced; the BFF readers, withholding rules,
redaction and composition run for real.

BFF-EVIDENCE-CITED-NAMES-LINKS-20261125: a cited artifact is named through the
real Registry owner read (``DefaultResearchKnowledgeSourcePort.
read_registry_strategy_artifact_detail`` -> ``GET /api/registry/entries/{id}``,
answering the recorded ``RegistryEntryView`` shape) and the strategy label
directory is keyed by strategy id only, as ``get_strategy_labels`` answers.

BFF-EVIDENCE-CITED-REG-ENTRY-NAMES-20261126: a cited ``registry_entry`` such as
``reg-strategy-spec-persona-*`` (``artifact_type`` ``strategy_spec``, cited by
approvals of ``decision_type`` ``registry_entry``) is named through the real
``DefaultResearchKnowledgeSourcePort.read_registry_entry_strategy`` ->
``GET /api/registry/entries/{id}`` and links to its strategy page.
"""
from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core import display_names, owner_reads
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.management_read_models import cited_evidence
from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.meetings.client import MeetingsOwnerClient, MeetingsOwnerUnavailableError
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
    EvidenceReadResult,
)
from services.control_plane.bff.research.client import ResearchServiceClient

TENANT = "tenant-a"
ADMIN = {"Authorization": "Bearer op-cited:admin"}
VIEWER = {"Authorization": "Bearer op-cited:viewer"}

# Hosted reference shapes (coordinator note, 2026-11-25).
ART_A = "artifact-persona-paper-039ad01f8bcd2b4aa466"
ART_B = "artifact-persona-paper-a81b4fd15944698341aa"
REG_B = f"facts:registry_entry:{ART_B}"
POOL = "facts:strategy_pool:persona-ee7112a829d38b9135ac@4"
BOOK_B = f"facts:member_virtual_book:{ART_B}"
TOPIC = "facts:meeting_topic:mtg-6edaff7d0ff76275"
KO = "evidence:search_excerpt:ko-evi-1217cd1c8ccc7274-94a73ebc0814"
EVI = "evi-1217cd1c8ccc7274"
PRIVATE_MEM = "lessons:memory_persona_private:mem-private-0001"
SHARED_MEM = "lessons:memory_committee_shared:mem-shared-0002"
SUPPLIED_ONLY = "facts:registry_entry:artifact-persona-paper-ffffffffffffffffffff"
BARE_MEM = "mem-bare-0003"
PERSONA = "persona-ee7112a829d38b9135ac"
GOV_URL = "http://governance.test"
# Governance owner rule (services/governance/main.py::_approval_reader): listing
# approvals needs a write-authority, approval_reader/proposer or operator role.
GOV_READ_ROLES = {"admin", "operator", "approver", "approval_reader", "approval_proposer"}
MEETING = "mtg-6edaff7d0ff76275"
# Hosted 2026-11-25: GET /bff/artifacts/{ART_B} answers this strategy_id.
STRATEGY_B = "strategy-persona-a81b4fd15944698341aa"
REGISTRY_URL = "http://registry.test"


def registry_entry_view(registry_id: str, strategy_id: str, *, owner_tenant: str = TENANT) -> Dict[str, Any]:
    """Registry GET /api/registry/entries/{id} shape (RegistryEntryView: entry + deployment_stage)."""
    return {
        "entry": {
            "registry_id": registry_id, "artifact_type": "execution_bundle", "strategy_id": strategy_id,
            "version": "4", "artifact_state": "approved", "lineage": {"parent_registry_id": None},
            "storage_ref": {"uri": f"gs://artifacts/{registry_id}.tar"}, "checksum": "sha256:" + "0" * 64,
            "metadata": {"persona_id": PERSONA}, "owner_tenant": owner_tenant,
            "created_at": "2026-11-20T00:00:00Z",
        },
        "deployment_stage": "paper",
    }


def _case_item(item_id: str, **extra: Any) -> Dict[str, Any]:
    group, kind, source_id = item_id.split(":", 2)
    owner = {"facts": "registry", "lessons": "memory", "evidence": "search"}[group]
    if kind == "strategy_pool":
        owner = "persona"
    if kind == "member_virtual_book":
        owner = "telemetry"
    return {"item_id": item_id, "group": group, "kind": kind, "source_owner": owner,
            "source_id": source_id, "excerpt": "x", **extra}


def meeting_detail(meeting_id: str = MEETING, *, tenant: str = TENANT, created_at: str = "2026-11-25T10:00:00Z",
                   citations: Optional[List[str]] = None) -> Dict[str, Any]:
    """Consultation GET /api/consult/meetings/{id} shape (rounds/positions/citations + case file)."""
    cites = citations if citations is not None else [TOPIC, REG_B, POOL, PRIVATE_MEM, KO]
    return {
        "meeting_id": meeting_id,
        "tenant_id": tenant,
        "topic": f"Pool evolution review for {PERSONA} (2026-W48)",
        "status": "concluded",
        "created_at": created_at,
        "case_file_items": [
            _case_item(REG_B), _case_item(POOL), _case_item(KO), _case_item(SUPPLIED_ONLY),
            _case_item(PRIVATE_MEM, memory_layer="persona_private", relevance_scope="persona_private"),
        ],
        "rounds": [
            {"round": 1, "positions": [
                {"label": "A", "persona_id": PERSONA, "stance": "support", "citations": cites},
            ]},
        ],
        "synthesis": {"proposals": []},
        "evidence_refs": [SUPPLIED_ONLY],
    }


def cycle_packet() -> Dict[str, Any]:
    """OODA packet with persona_cycle.case_files + decisions[].cited_case_file_item_ids."""
    cid = "casefile-0123456789abcdef01234567"
    return {
        "packet_id": "ooda-pkt-0001", "tenant_id": TENANT, "environment": "paper", "persona_ids": [PERSONA],
        "status": "decided", "updated_at": "2026-11-25T12:00:00Z", "created_at": "2026-11-25T11:00:00Z",
        "persona_cycle": {
            "phase": "terminal",
            "case_files": {cid: {
                "case_file_id": cid,
                "subject": {"tenant_id": TENANT, "persona_id": PERSONA, "decision_type": "pool_review"},
                "groups": {
                    "facts": {"status": "ok", "items": [_case_item(BOOK_B), _case_item(SUPPLIED_ONLY)]},
                    "lessons": {"status": "ok", "items": [
                        _case_item(PRIVATE_MEM, memory_layer="persona_private", relevance_scope="persona_private"),
                        _case_item(SHARED_MEM, memory_layer="persona", relevance_scope="persona_and_committee"),
                    ]},
                },
            }},
            "decisions": [{"kind": "meeting_action", "status": "requires_owner_gate", "case_file_id": cid,
                           "cited_case_file_item_ids": [BOOK_B, PRIVATE_MEM, SHARED_MEM]}],
        },
    }


class _Ooda:
    def __init__(self, packets: List[Dict[str, Any]], fail: bool = False) -> None:
        self.packets, self.fail = packets, fail

    def get_surface_status(self) -> Dict[str, Any]:
        return {"status": "unavailable" if self.fail else "ok"}

    def list_ooda_packets(self) -> List[Dict[str, Any]]:
        return list(self.packets)


class _OodaManagement:
    def __init__(self, ooda: _Ooda) -> None:
        self.ooda = ooda


class CitedStore:
    def __init__(self) -> None:
        self.ooda_management = _OodaManagement(_Ooda([cycle_packet()]))
        self.item_reads: List[str] = []
        # Real Registry owner read; only urlopen below it is replaced (owners fixture).
        self.registry = DefaultResearchKnowledgeSourcePort(registry_service_url=REGISTRY_URL)
        self.evolution: List[Dict[str, Any]] = [{
            "proposal_id": "evo-prop-1", "tenant_id": TENANT, "title": "Retire weak member",
            "created_at": "2026-11-24T09:00:00Z",
            "evidence_refs": [{"ref_type": "telemetry_summary", "ref_id": "telemetry-sum-001"}],
        }, {
            "proposal_id": "evo-prop-other", "tenant_id": "tenant-b", "title": "Other tenant",
            "evidence_refs": [{"ref_type": "telemetry_summary", "ref_id": "telemetry-sum-other-tenant"}],
        }]

    # Ingested evidence (existing read contract).
    def read_evidence_refs(self, **kwargs: Any) -> Any:
        item = {"ref_id": "evref-ingested-1", "source_document": {"title": "Ingested doc", "source_type": "artifact"}}
        return EvidenceReadResult([item], 1, "ok", "service_backend")

    def read_evidence_ref_item(self, ref_id: Optional[str]) -> Any:
        self.item_reads.append(str(ref_id))
        if ref_id == EVI:
            return EvidenceReadResult([{"ref_id": EVI, "title": "Fed minutes excerpt", "source_class": "primary_source",
                                        "source_document": {"title": "Fed minutes excerpt"}}], 1, "ok", "service_client")
        if ref_id == "evref-ingested-1":
            return EvidenceReadResult([{"ref_id": "evref-ingested-1", "source_document": {"title": "Ingested doc"}}],
                                      1, "ok", "service_client")
        return EvidenceReadResult([], 0, "ok", "service_client")

    def list_evolution_decisions(self) -> List[Dict[str, Any]]:
        return list(self.evolution)

    def read_registry_strategy_artifact_detail(self, artifact_id: Optional[str]) -> Any:
        return self.registry.read_registry_strategy_artifact_detail(artifact_id)

    def read_registry_entry_strategy(self, entry_id: Optional[str]) -> Any:
        return self.registry.read_registry_entry_strategy(entry_id)

    # Display-name directories.
    def list_personas(self) -> List[Dict[str, Any]]:
        return [{"persona_id": PERSONA, "display_name": "Macro Mia"}]

    def get_strategy_labels(self) -> Dict[str, Any]:
        # Keyed by strategy id only (hosted shape); no artifact id is a key here.
        return {STRATEGY_B: {"title": "Carry Basket B"}}

    def dataset_source(self, dataset: str) -> str:
        return "service_backend"


@pytest.fixture
def owners(monkeypatch):
    """Patch only the transport below each existing owner read."""
    state: Dict[str, Any] = {
        "meetings": [meeting_detail()],
        "detail_fail": set(),
        "in_flight": 0, "max_in_flight": 0, "detail_calls": 0,
        "approvals": [{
            "decision_id": "apr-1", "tenant_id": TENANT, "decision_type": "PoolChange", "target_id": PERSONA,
            "decided_at": "2026-11-25T13:00:00Z",
            "evidence_refs": [{"ref_type": "registry_entry", "ref_id": ART_A}, {"ref_type": "registry_entry", "ref_id": ART_B}],
        }],
        "approvals_exc": None,
        "proposals": [{"proposal_id": "prop-1", "meeting_id": MEETING, "kind": "pool_change", "title": "Add Carry Basket B",
                       "created_at": "2026-11-25T10:30:00Z", "citations": [REG_B, TOPIC]}],
        "demands": [{"demand_id": "dem-1", "tenant_id": TENANT, "persona_id": PERSONA, "short_measure": "carry decay",
                     "created_at": "2026-11-23T08:00:00Z",
                     "retrieval_influence": {"metadata": {"cited_item_ids": [POOL, PRIVATE_MEM]}}}],
        "tickets": {"data": [{"ticket_id": "rt-1", "tenant_id": TENANT, "title": "Check carry decay",
                              "submitted_at": "2026-11-24T10:00:00Z",
                              "evidence_refs": [{"kind": "research_note", "artifact_id": "note-111", "tenant_id": TENANT}],
                              "human_input": {"provided_at": "2026-11-24T11:00:00Z",
                                              "evidence_refs": [{"kind": "research_note", "artifact_id": "note-222", "tenant_id": TENANT}]}}],
                    "page_info": {"next_page_token": None}},
    }
    # Registry owner (GET /api/registry/entries/{id}); ART_A is unknown -> 404.
    state["registry"] = {ART_B: registry_entry_view(ART_B, STRATEGY_B)}
    state["registry_reads"] = []
    state["registry_tenants"] = []
    state["registry_fail"] = set()
    state["registry_block"] = None
    lock = threading.Lock()

    def list_meetings(self, **kw):
        rows = [{"meeting_id": m["meeting_id"], "tenant_id": m["tenant_id"], "created_at": m["created_at"]}
                for m in state["meetings"]]
        return {"items": rows, "next_cursor": state.get("next_cursor")}

    def get_meeting(self, meeting_id):
        with lock:
            state["in_flight"] += 1
            state["detail_calls"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        try:
            time.sleep(0.01)
            if meeting_id in state["detail_fail"]:
                raise MeetingsOwnerUnavailableError("consultation_owner_unavailable")
            return dict(next(m for m in state["meetings"] if m["meeting_id"] == meeting_id))
        finally:
            with lock:
                state["in_flight"] -= 1

    def list_proposals(self, **kw):
        return {"items": list(state["proposals"])}

    state["gov_auth_seen"] = []
    real_urlopen = urllib.request.urlopen

    def governance_urlopen(req, timeout=None, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.startswith(REGISTRY_URL):
            return registry_urlopen(req, url)
        if not url.startswith(GOV_URL):
            return real_urlopen(req, timeout=timeout, *a, **kw)
        assert url.startswith(GOV_URL + "/api/governance/approvals") and req.get_method() == "GET"
        auth = req.get_header("Authorization") or ""
        state["gov_auth_seen"].append(auth)
        role = auth.rsplit(":", 1)[-1] if auth.startswith("Bearer op-cited:") else ""
        if state["approvals_exc"] is not None:
            raise state["approvals_exc"]
        if role not in GOV_READ_ROLES:
            raise urllib.error.HTTPError(url, 403, "Approval read role required", {}, io.BytesIO(b"{}"))

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Resp(json.dumps(state["approvals"]).encode("utf-8"))

    def registry_urlopen(req, url):
        prefix = REGISTRY_URL + "/api/registry/entries/"
        assert url.startswith(prefix) and req.get_method() == "GET", url
        rid = urllib.parse.unquote(url[len(prefix):])
        with lock:
            state["registry_reads"].append(rid)
            state["registry_tenants"].append(req.get_header("X-tenant-id"))
        if state["registry_block"] is not None:
            state["registry_block"].wait(5)
        if rid in state["registry_fail"]:
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, io.BytesIO(b"{}"))
        view = state["registry"].get(rid)
        if view is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))

        class _Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Resp(json.dumps(view).encode("utf-8"))

    def read_records(url_builder, path, key=None):
        assert path == "/api/research/persona-demands" and key == "demands"
        return list(state["demands"])

    def ticket_page(self, **kw):
        assert kw.get("page_size") == 100
        return state["tickets"]

    monkeypatch.setattr(MeetingsOwnerClient, "list_meetings", list_meetings)
    monkeypatch.setattr(MeetingsOwnerClient, "get_meeting", get_meeting)
    monkeypatch.setattr(MeetingsOwnerClient, "list_proposals", list_proposals)
    monkeypatch.setattr(urllib.request, "urlopen", governance_urlopen)
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", GOV_URL)
    monkeypatch.setattr(owner_reads, "read_records", read_records)
    monkeypatch.setattr(ResearchServiceClient, "list_research_ticket_page", ticket_page)
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research.test")
    monkeypatch.delenv("PANTHEON_EVOLUTION_API_URL", raising=False)
    return state


@pytest.fixture
def store(monkeypatch):
    s = CitedStore()
    monkeypatch.setattr(display_names, "_source", s)
    return s


def _client(store: Any) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    # Same request context as the real app: caller authorization + request cache.
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(create_management_router(read_surface=store, tenant_payload_fn=lambda identity: {"id": TENANT}))
    return TestClient(app)


def _cited(store: Any, headers=ADMIN, **params) -> Dict[str, Any]:
    res = _client(store).get("/bff/management/evidence", params={"scope": "cited", **params}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _rows(body: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {r["ref_id"]: r for r in body["data"]["items"]}


def test_cited_rows_carry_owner_label_link_and_cited_by(store, owners):
    body = _cited(store, page_size=100)
    rows = _rows(body)
    assert body["data"]["scope"] == "cited"
    assert all(s["status"] == "ok" for s in body["data"]["sources"].values()), body["data"]["sources"]
    assert body["meta"]["surfaces"]["cited_evidence"]["status"] == "ok"
    assert body["data"]["summary"]["complete"] is True

    # facts:registry_entry:X and a bare X are the same registry artifact.
    reg = rows[ART_B]
    assert reg["ref_kind"] == "registry_artifact" and reg["source_owner"] == "registry"
    assert reg["label"] == "Carry Basket B" and reg["name_missing"] is False
    assert reg["source_class"] == "registry_record"
    # Hosted: /management/strategies/{artifact} is 404; the artifact page is the one that exists.
    assert reg["resolved_link"]["route_href"] == f"/management/artifacts/{ART_B}"
    # Named after the strategy the Registry records for the artifact.
    assert reg["label_parts"] == {"artifact_id": ART_B, "strategy_id": STRATEGY_B}
    assert set(reg["cited_as"]) == {REG_B, ART_B}
    by = {(c["entity_type"], c["entity_ref"]) for c in reg["cited_by"]}
    assert ("meeting_position", f"{MEETING}#round-1/A") in by
    assert ("meeting_proposal", "prop-1") in by
    assert ("approval", "apr-1") in by
    for c in reg["cited_by"]:
        assert set(c) >= {"entity_type", "entity_ref", "label", "parts", "route_href", "time"}
        assert c["route_href"] and c["time"]
    # Labels are recorded titles / display names only; structure goes in parts.
    pos = next(c for c in reg["cited_by"] if c["entity_type"] == "meeting_position")
    assert pos["label"] == f"Pool evolution review for Macro Mia (2026-W48)"
    assert pos["parts"] == {"meeting_id": MEETING, "meeting_name": pos["label"], "round": 1,
                            "position": "A", "persona_id": PERSONA}
    assert pos["route_href"] == f"/management/meetings/{MEETING}"
    prop = next(c for c in reg["cited_by"] if c["entity_type"] == "meeting_proposal")
    assert prop["label"] == "Add Carry Basket B" and prop["parts"] == {"meeting_id": MEETING, "kind": "pool_change"}
    apr = next(c for c in reg["cited_by"] if c["entity_type"] == "approval")
    assert apr["label"] is None  # the recorded decision has no title; nothing is composed
    assert apr["parts"] == {"decision_type": "PoolChange", "target_id": PERSONA}
    assert apr["route_href"] == "/management/approvals"

    # An id no directory can name says name_missing.
    unnamed = rows[ART_A]
    assert unnamed["name_missing"] is True and unnamed["label"] == ART_A
    assert unnamed["label_parts"] == {"artifact_id": ART_A, "strategy_id": None}
    assert unnamed["route_href"] == f"/management/artifacts/{ART_A}"

    pool = rows[POOL]
    assert pool["label"] == "Macro Mia" and pool["source_owner"] == "persona"
    assert pool["label_parts"] == {"persona_id": PERSONA, "pool_version": "4"}
    assert pool["resolved_link"]["route_href"] == f"/management/personas/{PERSONA}"
    assert {c["entity_type"] for c in pool["cited_by"]} == {"meeting_position", "research_demand"}

    book = rows[BOOK_B]
    assert book["label"] == "Carry Basket B" and book["ref_kind"] == "member_virtual_book"
    assert book["label_parts"] == {"artifact_id": ART_B, "strategy_id": STRATEGY_B}
    assert book["route_href"] == f"/management/artifacts/{ART_B}"
    assert book["cited_by"][0]["entity_type"] == "persona_cycle_decision"
    assert book["cited_by"][0]["label"] is None
    assert book["cited_by"][0]["parts"] == {"persona_id": PERSONA, "persona_name": "Macro Mia",
                                            "kind": "meeting_action", "decision_index": 0}

    # A ko-evi knowledge object resolves to its evidence item by one bounded item read.
    ko = rows[KO]
    assert ko["ref_kind"] == "knowledge_object"
    assert ko["label"] == "Fed minutes excerpt" and ko["source_class"] == "primary_source"
    assert ko["resolved_link"] == {"route_href": f"/management/evidence/{EVI}", "evidence_ref_id": EVI}
    assert store.item_reads == [EVI]

    # Shared memory is listed only because its recorded case item proves persona_and_committee.
    mem = rows[SHARED_MEM]
    assert mem["source_owner"] == "memory"
    assert mem["resolved_link"] is None and mem["route_href"] is None  # no page renders one memory entry

    assert rows["note-111"]["cited_by"][0]["entity_type"] == "research_ticket"
    assert rows["note-222"]["cited_by"][0]["time"] == "2026-11-24T11:00:00Z"
    assert rows["note-111"]["resolved_link"] is None  # no research-note page exists
    assert rows["note-111"]["cited_by"][0]["route_href"] is None  # nor a research-ticket page
    assert rows["telemetry-sum-001"]["cited_by"][0]["entity_type"] == "evolution_proposal"


def test_meeting_topic_supplied_only_private_and_other_tenant_never_listed(store, owners):
    body = _cited(store, page_size=100)
    ids = set(_rows(body))
    assert TOPIC not in ids and MEETING not in ids
    assert body["data"]["summary"]["excluded"]["meeting_topic"] >= 2
    # Supplied in case files (meeting + cycle) but never cited.
    assert SUPPLIED_ONLY not in ids and "artifact-persona-paper-ffffffffffffffffffff" not in ids
    # Private persona memory is dropped from meetings, demands and cycle decisions.
    assert PRIVATE_MEM not in ids and "mem-private-0001" not in str(body)
    assert body["data"]["sources"]["meeting_positions"]["withheld_count"] >= 1
    assert body["data"]["sources"]["persona_cycles"]["withheld_count"] >= 1
    assert body["data"]["sources"]["research_demands"]["withheld_count"] >= 1
    # Other-tenant owner rows are dropped.
    assert "telemetry-sum-other-tenant" not in ids
    assert body["data"]["sources"]["evolution_proposals"]["withheld_count"] == 1


def test_trader_items_never_listed_even_for_operator(store, owners):
    detail = meeting_detail(citations=[REG_B, "cf-trader-1"])
    detail["case_file_items"].append({"item_id": "cf-trader-1", "subject_kind": "trader", "trader_id": "tr-9"})
    owners["meetings"] = [detail]
    ids = set(_rows(_cited(store, page_size=100)))
    assert ART_B in ids and "cf-trader-1" not in ids


def test_denied_approvals_still_return_other_sources_marked_incomplete(store, owners):
    owners["approvals_exc"] = urllib.error.HTTPError("http://gov", 403, "Forbidden", {}, None)
    body = _cited(store, page_size=100)
    src = body["data"]["sources"]["governance_approvals"]
    assert src["status"] == "denied" and src["read_count"] is None
    assert body["data"]["summary"]["incomplete"] is True and body["data"]["summary"]["complete"] is False
    assert "governance_approvals" in body["data"]["summary"]["sources_not_ok"]
    assert body["meta"]["surfaces"]["cited_evidence"]["status"] == "degraded"
    rows = _rows(body)
    assert ART_A not in rows  # only cited by the approval
    assert all(c["entity_type"] != "approval" for r in rows.values() for c in r["cited_by"])
    assert ART_B in rows and POOL in rows and BOOK_B in rows


def test_no_source_failure_becomes_an_empty_ok_list(store, owners, monkeypatch):
    def boom(ctx):
        raise RuntimeError("owner down")

    for key in cited_evidence.SOURCE_KEYS:
        monkeypatch.setitem(cited_evidence.DEFAULT_SOURCE_READERS, key, boom)
    body = _cited(store)
    assert body["data"]["items"] == []
    assert body["page_info"]["total"] is None
    assert body["data"]["summary"]["reference_total"] is None
    assert body["data"]["summary"]["incomplete"] is True
    assert body["meta"]["surfaces"]["cited_evidence"]["status"] == "unavailable"
    assert {s["status"] for s in body["data"]["sources"].values()} == {"failed"}


def test_cycle_owner_unavailable_is_failed_not_empty(store, owners):
    store.ooda_management.ooda.fail = True
    body = _cited(store, page_size=100)
    assert body["data"]["sources"]["persona_cycles"]["status"] == "failed"
    assert BOOK_B not in _rows(body)
    assert body["data"]["summary"]["incomplete"] is True


def test_meeting_details_bounded_overlapped_and_partial(store, owners):
    owners["meetings"] = [
        meeting_detail(f"mtg-{i:04d}", created_at=f"2026-11-{(i % 28) + 1:02d}T00:00:{i % 60:02d}Z")
        for i in range(35)
    ]
    newest = sorted(owners["meetings"], key=lambda m: m["created_at"], reverse=True)[:30]
    owners["detail_fail"] = {newest[0]["meeting_id"]}
    body = _cited(store, page_size=100)
    src = body["data"]["sources"]["meeting_positions"]
    assert owners["detail_calls"] == 30  # cap: the 30 newest meetings
    assert 1 < owners["max_in_flight"] <= 4  # overlapped, at most 4 at a time
    assert src["status"] == "partial" and src["failed_count"] == 1
    assert src["read_count"] == 29 and src["truncated"] is True
    assert src["calls"] == 31 and src["response_bytes"] > 0
    assert body["data"]["summary"]["incomplete"] is True
    assert body["meta"]["cost"]["meeting_detail_cap"] == 30


def test_reference_cap_and_item_reads_bounded_to_visible_page(store, owners):
    many = [f"evidence:search_excerpt:evi-{i:016x}" for i in range(520)]
    owners["meetings"] = [meeting_detail(citations=many)]
    body = _cited(store, page_size=100)
    summary = body["data"]["summary"]
    assert summary["reference_total"] > 500 and summary["references_truncated"] is True
    assert body["page_info"]["total"] == 500 and summary["incomplete"] is True
    assert len(body["data"]["items"]) == 100
    # One shared budget of 20 owner reads for the visible page: the distinct
    # artifacts on it (ART_A, ART_B: REG_B/BOOK_B/ART_B are one artifact) take
    # one Registry read each and the evidence item reads take the rest.
    assert sorted(owners["registry_reads"]) == sorted([ART_A, ART_B])
    assert len(store.item_reads) == 20 - len(owners["registry_reads"]) == 18
    assert body["meta"]["item_reads"]["attempted"] == 20
    assert body["meta"]["item_reads"]["truncated"] is True
    assert body["meta"]["cost"]["item_reads"] == 20


def test_viewer_without_approval_role_is_denied_by_real_owner_path(store, owners):
    """No approval_records fake: owner_reads -> approval_owner -> HTTP with the caller's token."""
    body = _cited(store, headers=VIEWER, page_size=100)
    sources = body["data"]["sources"]
    assert owners["gov_auth_seen"] == [VIEWER["Authorization"]]  # caller token forwarded, one call
    assert sources["governance_approvals"]["status"] == "denied"
    assert sources["governance_approvals"]["reason"] == "governance_owner_http_403"
    others = {k: v["status"] for k, v in sources.items() if k != "governance_approvals"}
    assert set(others) == set(cited_evidence.SOURCE_KEYS) - {"governance_approvals"}
    assert set(others.values()) == {"ok"}, others
    assert body["data"]["summary"]["incomplete"] is True
    assert body["data"]["summary"]["sources_not_ok"] == ["governance_approvals"]
    assert body["meta"]["surfaces"]["cited_evidence"]["status"] == "degraded"
    assert body["data"]["items"]  # the other six sources still answer
    assert all(c["entity_type"] != "approval" for r in body["data"]["items"] for c in r["cited_by"])


def test_admin_reads_approvals_through_real_owner_path(store, owners):
    body = _cited(store, page_size=100)
    assert owners["gov_auth_seen"] == [ADMIN["Authorization"]]
    assert body["data"]["sources"]["governance_approvals"]["status"] == "ok"
    assert body["data"]["sources"]["governance_approvals"]["read_count"] == 1
    assert ("approval", "apr-1") in {(c["entity_type"], c["entity_ref"]) for c in _rows(body)[ART_A]["cited_by"]}


def test_memory_reference_withheld_unless_recorded_item_proves_shared_scope(store, owners):
    # Bare mem-... in a proposal, a meeting position and a persona cycle decision.
    owners["proposals"][0]["citations"] = [REG_B, BARE_MEM, SHARED_MEM]
    detail = meeting_detail(citations=[REG_B, BARE_MEM])
    detail["case_file_items"].append({"item_id": BARE_MEM, "source_owner": "memory", "excerpt": "x"})
    owners["meetings"] = [detail]
    packet = cycle_packet()
    case = next(iter(packet["persona_cycle"]["case_files"].values()))
    case["groups"]["lessons"]["items"].append({"item_id": BARE_MEM, "group": "lessons", "source_owner": "memory",
                                               "kind": "memory_entry", "excerpt": "x"})
    packet["persona_cycle"]["decisions"][0]["cited_case_file_item_ids"].append(BARE_MEM)
    store.ooda_management.ooda.packets = [packet]
    # Typed memory ref in an approval and a demand, no recorded item.
    owners["approvals"][0]["evidence_refs"].append({"ref_type": "persona_memory", "ref_id": "lesson-xyz"})
    owners["demands"][0]["retrieval_influence"]["metadata"]["cited_item_ids"].append(BARE_MEM)

    body = _cited(store, page_size=100)
    rows = _rows(body)
    assert BARE_MEM not in rows and BARE_MEM not in str(body["data"]["items"])
    assert "lesson-xyz" not in rows
    assert PRIVATE_MEM not in rows
    for key in ("meeting_proposals", "meeting_positions", "persona_cycles", "governance_approvals", "research_demands"):
        assert body["data"]["sources"][key]["withheld_count"] >= 1, key
    assert body["data"]["summary"]["excluded"]["memory_scope_unproven"] >= 5
    # SHARED_MEM: the proposal citation has no recorded item and is withheld;
    # the cycle citation's recorded item proves persona_and_committee.
    assert {c["entity_type"] for c in rows[SHARED_MEM]["cited_by"]} == {"persona_cycle_decision"}


def test_memory_scope_proof_rules():
    shared = {"item_id": SHARED_MEM, "relevance_scope": "persona_and_committee"}
    assert cited_evidence._memory_scope_proven_shared(shared) is True
    assert cited_evidence._memory_scope_proven_shared(None) is False
    assert cited_evidence._memory_scope_proven_shared({"item_id": BARE_MEM}) is False
    assert cited_evidence._memory_scope_proven_shared(
        {"relevance_scope": "persona_and_committee", "memory_layer": "persona_private"}) is False
    for raw in (BARE_MEM, SHARED_MEM, PRIVATE_MEM, {"ref_type": "persona_memory", "ref_id": "x"}):
        assert cited_evidence._is_memory_ref(cited_evidence.parse_ref(raw), None), raw


def test_routes_only_point_at_existing_management_pages(store, owners):
    import re as _re

    existing = _re.compile(r"^/management/(meetings|personas|strategies|artifacts|evidence)/[^/?#]+$"
                           r"|^/management/(approvals|evolution-journal)$")
    body = _cited(store, page_size=100)
    hrefs = []
    for r in body["data"]["items"]:
        hrefs.append(r["route_href"])
        hrefs.extend(c["route_href"] for c in r["cited_by"])
    present = [h for h in hrefs if h is not None]
    assert present and all(existing.match(h) for h in present), present
    assert None in hrefs  # memory / research note / ticket refs have no page
    # No artifact id is ever linked under the strategies page (hosted 404).
    assert not [h for h in present if h.startswith("/management/strategies/artifact-")], present
    for r in body["data"]["items"]:
        if r["ref_kind"] in {"registry_artifact", "member_virtual_book"}:
            assert r["route_href"].startswith("/management/artifacts/artifact-"), r


def test_redaction_reused_for_caller_without_capability(store, owners):
    body = _cited(store, headers=VIEWER, page_size=100)
    rows = body["data"]["items"]
    assert rows and all(r["redacted"] is True for r in rows)
    assert body["data"]["summary"]["redacted_count"] == len(rows)
    assert all(r["label"] == r["ref_id"] and r["resolved_link"] is None for r in rows)
    assert store.item_reads == []  # no item reads for withheld refs
    assert owners["registry_reads"] == []  # nor artifact owner reads


def test_without_scope_answer_is_unchanged_ingested_list(store, owners):
    res = _client(store).get("/bff/management/evidence", headers=ADMIN)
    assert res.status_code == 200
    body = res.json()
    assert body["data"]["id"] == "management-evidence"
    assert [r["ref_id"] for r in body["data"]["items"]] == ["evref-ingested-1"]
    assert owners["detail_calls"] == 0


def test_ref_id_lookup_resolves_ingested_item_in_every_scope(store, owners):
    for scope in (None, "ingested", "cited"):
        params = {"ref_id": "evref-ingested-1"}
        if scope:
            params["scope"] = scope
        res = _client(store).get("/bff/management/evidence", params=params, headers=ADMIN)
        assert res.status_code == 200
        assert [r["ref_id"] for r in res.json()["data"]["items"]] == ["evref-ingested-1"], scope
    assert owners["detail_calls"] == 0


def test_unknown_scope_is_rejected(store, owners):
    res = _client(store).get("/bff/management/evidence", params={"scope": "everything"}, headers=ADMIN)
    assert res.status_code == 400


def test_paging_is_stable_and_complete(store, owners):
    first = _cited(store, page_size=3)
    second = _cited(store, page_size=3, page_token=first["page_info"]["next_page_token"])
    all_ids = set(_rows(_cited(store, page_size=100)))
    got = [r["ref_id"] for r in first["data"]["items"] + second["data"]["items"]]
    assert len(got) == len(set(got)) == 6 and set(got) <= all_ids


def test_parse_ref_recorded_shapes():
    assert cited_evidence.parse_ref(TOPIC) is None
    assert cited_evidence.parse_ref(REG_B).ref_id == ART_B
    assert cited_evidence.parse_ref(ART_A).ref_kind == "registry_artifact"
    assert cited_evidence.parse_ref(POOL).source_id == "persona-ee7112a829d38b9135ac@4"
    assert cited_evidence.parse_ref(KO).ref_kind == "knowledge_object"
    assert cited_evidence.parse_ref(PRIVATE_MEM).private is True
    assert cited_evidence.parse_ref(BARE_MEM).ref_kind == "memory"
    assert cited_evidence.parse_ref({"ref_type": "registry_entry", "ref_id": ART_A}).ref_id == ART_A
    assert cited_evidence.parse_ref({"kind": "research_note", "artifact_id": "note-1"}).ref_kind == "research_note"
    assert cited_evidence._ko_evidence_candidate("ko-evi-1217cd1c8ccc7274-94a73ebc0814") == EVI
    assert cited_evidence._ko_evidence_candidate("ko-evi-1217cd1c8ccc7274-000000000000") is None


# ---------------------------------------------------------------------------
# BFF-EVIDENCE-CITED-NAMES-LINKS-20261125: artifact names come from the
# Registry owner's strategy id; artifact links go to the artifact page.
# ---------------------------------------------------------------------------


def test_artifact_named_by_registry_strategy_one_read_per_distinct_artifact(store, owners):
    body = _cited(store, page_size=100)
    rows = _rows(body)
    # ART_B is cited as REG_B, bare ART_B and BOOK_B (two rows), still one owner read.
    assert sorted(owners["registry_reads"]) == sorted([ART_A, ART_B])
    for ref in (ART_B, BOOK_B):
        row = rows[ref]
        assert row["label"] == "Carry Basket B" and row["name_missing"] is False
        assert row["label_parts"] == {"artifact_id": ART_B, "strategy_id": STRATEGY_B}
        assert row["route_href"] == row["resolved_link"]["route_href"] == f"/management/artifacts/{ART_B}"
    # ART_A: the Registry answers 404 -> no strategy id, no name, link still to the artifact page.
    assert rows[ART_A]["label"] == ART_A and rows[ART_A]["name_missing"] is True
    assert rows[ART_A]["label_parts"]["strategy_id"] is None
    reads = body["meta"]["item_reads"]
    assert reads["attempted"] == 3 and reads["resolved"] == 2 and reads["failed"] == 0  # 2 artifacts + 1 ko-evi
    assert body["meta"]["cost"]["item_reads"] == 3
    # Other link kinds are unchanged.
    assert rows[POOL]["route_href"] == f"/management/personas/{PERSONA}"
    assert rows[KO]["route_href"] == f"/management/evidence/{EVI}"
    assert rows[SHARED_MEM]["route_href"] is None and rows["note-111"]["route_href"] is None


def test_strategy_id_is_read_from_the_owner_never_derived_from_the_id(store, owners, monkeypatch):
    # The Registry records a strategy id that no text rule could derive from the artifact id.
    owners["registry"][ART_B] = registry_entry_view(ART_B, "strategy-carry-basket-0042")
    monkeypatch.setattr(store, "get_strategy_labels", lambda: {
        STRATEGY_B: {"title": "Derived (wrong)"}, "strategy-carry-basket-0042": {"title": "Carry Basket Recorded"},
    })
    rows = _rows(_cited(store, page_size=100))
    assert rows[ART_B]["label"] == "Carry Basket Recorded"
    assert rows[ART_B]["label_parts"]["strategy_id"] == "strategy-carry-basket-0042"


def test_failed_artifact_read_keeps_row_unnamed_and_counted(store, owners):
    owners["registry_fail"] = {ART_B}
    body = _cited(store, page_size=100)
    rows = _rows(body)
    # The directory could name STRATEGY_B, but without the owner answer nothing is guessed.
    for ref in (ART_B, BOOK_B):
        assert rows[ref]["label"] == ref and rows[ref]["name_missing"] is True
        assert rows[ref]["label_parts"] == {"artifact_id": ART_B, "strategy_id": None}
        assert rows[ref]["route_href"] == f"/management/artifacts/{ART_B}"
    reads = body["meta"]["item_reads"]
    assert reads["attempted"] == 3 and reads["failed"] == 1 and reads["resolved"] == 1
    assert body["meta"]["surfaces"]["cited_evidence"]["status"] == "ok"  # the list itself is complete


def test_slow_artifact_read_is_abandoned_at_the_read_deadline(store, owners, monkeypatch):
    gate = threading.Event()
    owners["registry_block"] = gate
    real_budget = cited_evidence.remaining_management_read_budget

    def budget(deadline=None, *, reserve_seconds=0.0):
        # Only the visible-page read wait is shortened; source reads keep the real budget.
        return 0.2 if reserve_seconds == 0.05 else real_budget(deadline, reserve_seconds=reserve_seconds)

    monkeypatch.setattr(cited_evidence, "remaining_management_read_budget", budget)
    try:
        body = _cited(store, page_size=100)
    finally:
        gate.set()
    rows = _rows(body)
    assert rows[ART_B]["label"] == ART_B and rows[ART_B]["name_missing"] is True
    assert rows[ART_B]["label_parts"]["strategy_id"] is None
    assert body["meta"]["item_reads"]["failed"] >= 2  # both blocked Registry reads


def test_artifact_of_another_tenant_is_never_used(store, owners):
    owners["registry"][ART_B] = registry_entry_view(ART_B, STRATEGY_B, owner_tenant="tenant-b")
    body = _cited(store, page_size=100)
    row = _rows(body)[ART_B]
    assert row["label"] == ART_B and row["name_missing"] is True and row["label_parts"]["strategy_id"] is None
    assert body["meta"]["item_reads"]["failed"] == 1


def test_artifact_read_carries_caller_tenant(store, owners):
    _cited(store, headers={**ADMIN, "X-Tenant-Id": TENANT}, page_size=100)
    assert owners["registry_tenants"] and set(owners["registry_tenants"]) == {TENANT}


def test_artifact_reads_share_the_item_read_cap(store, owners):
    arts = [f"artifact-persona-paper-{i:020x}" for i in range(30)]
    owners["registry"].update({a: registry_entry_view(a, STRATEGY_B) for a in arts})
    owners["meetings"] = [meeting_detail(citations=arts)]
    body = _cited(store, page_size=100)
    assert len(owners["registry_reads"]) == cited_evidence.MAX_ITEM_READS
    assert len(owners["registry_reads"]) == len(set(owners["registry_reads"]))
    assert body["meta"]["item_reads"]["truncated"] is True
    assert body["meta"]["item_reads"]["attempted"] == cited_evidence.MAX_ITEM_READS
    unread = [r for r in body["data"]["items"]
              if r["ref_kind"] == "registry_artifact" and r["ref_id"] not in owners["registry_reads"]]
    assert unread and all(r["name_missing"] is True and r["label"] == r["ref_id"] for r in unread)


def test_approval_targeting_an_artifact_carries_its_strategy_name(store, owners):
    owners["approvals"].extend([{
        "decision_id": "apr-art", "tenant_id": TENANT, "decision_type": "StrategyArtifactPromotion",
        "target_id": ART_B, "decided_at": "2026-11-25T14:00:00Z",
        "evidence_refs": [{"ref_type": "registry_entry", "ref_id": ART_B}],
    }, {
        "decision_id": "apr-unknown", "tenant_id": TENANT, "decision_type": "StrategyArtifactPromotion",
        "target_id": ART_A, "decided_at": "2026-11-25T14:00:00Z",
        "evidence_refs": [{"ref_type": "registry_entry", "ref_id": ART_A}],
    }])
    rows = _rows(_cited(store, page_size=100))
    apr = next(c for c in rows[ART_B]["cited_by"] if c["entity_ref"] == "apr-art")
    assert apr["label"] is None  # contract: the recorded decision has no title
    assert apr["parts"] == {"decision_type": "StrategyArtifactPromotion", "target_id": ART_B,
                            "target_display_name": "Carry Basket B"}
    # Unanswered artifact target and non-artifact target: no name added.
    unknown = next(c for c in rows[ART_A]["cited_by"] if c["entity_ref"] == "apr-unknown")
    assert "target_display_name" not in unknown["parts"]
    persona_target = next(c for c in rows[ART_B]["cited_by"] if c["entity_ref"] == "apr-1")
    assert "target_display_name" not in persona_target["parts"]
    # Still one Registry read per distinct artifact.
    assert sorted(owners["registry_reads"]) == sorted([ART_A, ART_B])


# ---------------------------------------------------------------------------
# BFF-EVIDENCE-CITED-REG-ENTRY-NAMES-20261126: registry_entry (strategy_spec)
# ---------------------------------------------------------------------------

# Hosted 8d506640a: approvals of decision_type registry_entry cite these entries.
REG_SPEC = "reg-strategy-spec-persona-715f5875971f2e342d8f"
REG_SPEC_2 = "reg-strategy-spec-persona-0c1d2e3f405162738495"
# Recorded by the Registry for REG_SPEC; not derivable from the entry id text.
STRATEGY_SPEC = "strategy-carry-spec-recorded-0007"


def strategy_spec_entry_view(registry_id: str, strategy_id: Optional[str], *,
                             owner_tenant: str = TENANT) -> Dict[str, Any]:
    """GET /api/registry/entries/{id} for a strategy spec (paper_strategy_spec.build_registry_entry shape)."""
    entry: Dict[str, Any] = {
        "registry_id": registry_id, "artifact_type": "strategy_spec", "strategy_id": strategy_id,
        "version": "1", "artifact_state": "candidate", "created_at": "2026-11-20T00:00:00Z",
        "lineage": {"parent_registry_ids": ["spec-src"], "source_run_ids": ["run-1"]},
        "storage_ref": {"backend": "inline", "path": "$.strategy_spec"}, "checksum": "sha256:" + "1" * 64,
        "metadata": {"persona_id": PERSONA, "paper_loop_candidate": True}, "owner_tenant": owner_tenant,
    }
    return {"entry": entry, "deployment_stage": "none"}


def _registry_entry_approval(decision_id: str, entry_id: str, decided_at: str = "2026-11-25T15:00:00Z") -> Dict[str, Any]:
    return {
        "decision_id": decision_id, "tenant_id": TENANT, "decision_type": "registry_entry",
        "target_id": entry_id, "decided_at": decided_at,
        "evidence_refs": [{"ref_type": "registry_entry", "ref_id": entry_id}],
    }


@pytest.fixture
def spec_labels(store, monkeypatch):
    monkeypatch.setattr(store, "get_strategy_labels", lambda: {
        STRATEGY_B: {"title": "Carry Basket B"}, STRATEGY_SPEC: {"title": "Carry Spec Recorded"},
    })


def test_parse_registry_entry_ref_keeps_its_kind():
    parsed = cited_evidence.parse_ref({"ref_type": "registry_entry", "ref_id": REG_SPEC})
    assert parsed.ref_kind == "registry_entry" and parsed.source_owner == "registry"
    assert parsed.source_id == parsed.ref_id == REG_SPEC


def test_port_entry_reader_answers_strategy_spec_detail_reader_unchanged(owners):
    owners["registry"][REG_SPEC] = strategy_spec_entry_view(REG_SPEC, STRATEGY_SPEC)
    port = DefaultResearchKnowledgeSourcePort(registry_service_url=REGISTRY_URL)
    # GET /bff/artifacts behaviour is unchanged: a strategy_spec entry is no artifact.
    assert port.read_registry_strategy_artifact_detail(REG_SPEC) == (None, "ok")
    detail, status = port.read_registry_entry_strategy(REG_SPEC)
    assert status == "ok"
    assert detail == {"registry_id": REG_SPEC, "artifact_type": "strategy_spec",
                      "strategy_id": STRATEGY_SPEC, "owner_tenant": TENANT}
    assert port.read_registry_entry_strategy("reg-strategy-spec-unknown") == (None, "ok")
    owners["registry_fail"] = {REG_SPEC}
    assert port.read_registry_entry_strategy(REG_SPEC) == (None, "failed")
    assert DefaultResearchKnowledgeSourcePort().read_registry_entry_strategy(REG_SPEC)[1] in {"unconfigured", "ok"}


def test_registry_entry_named_by_recorded_strategy_and_links_to_strategy(store, owners, spec_labels):
    owners["registry"][REG_SPEC] = strategy_spec_entry_view(REG_SPEC, STRATEGY_SPEC)
    owners["approvals"].extend([
        _registry_entry_approval("apr-reg-1", REG_SPEC),
        _registry_entry_approval("apr-reg-2", REG_SPEC, "2026-11-25T16:00:00Z"),
    ])
    body = _cited(store, headers={**ADMIN, "X-Tenant-Id": TENANT}, page_size=100)
    row = _rows(body)[REG_SPEC]
    assert row["ref_kind"] == "registry_entry" and row["source_owner"] == "registry"
    assert row["source_class"] == "registry_record"
    assert row["label"] == "Carry Spec Recorded" and row["name_missing"] is False
    assert row["label_parts"] == {"registry_id": REG_SPEC, "strategy_id": STRATEGY_SPEC}
    href = f"/management/strategies/{STRATEGY_SPEC}"
    assert row["route_href"] == row["resolved_link"]["route_href"] == href
    assert "/management/artifacts/" not in json.dumps(row)
    # Cited twice, read once (and the existing artifact reads are unchanged).
    assert owners["registry_reads"].count(REG_SPEC) == 1
    assert sorted(r for r in owners["registry_reads"] if r != REG_SPEC) == sorted([ART_A, ART_B])
    assert set(owners["registry_tenants"]) == {TENANT}
    for apr in row["cited_by"]:
        assert apr["parts"] == {"decision_type": "registry_entry", "target_id": REG_SPEC,
                                "target_display_name": "Carry Spec Recorded"}
    reads = body["meta"]["item_reads"]
    assert reads["attempted"] == 4 and reads["resolved"] == 3 and reads["failed"] == 0


def test_registry_entry_without_recorded_strategy_has_no_name_and_no_link(store, owners, spec_labels):
    owners["registry"][REG_SPEC] = strategy_spec_entry_view(REG_SPEC, None)
    owners["approvals"].extend([_registry_entry_approval("apr-reg-1", REG_SPEC),
                                _registry_entry_approval("apr-reg-404", REG_SPEC_2)])
    body = _cited(store, page_size=100)
    rows = _rows(body)
    for rid in (REG_SPEC, REG_SPEC_2):  # no strategy id recorded / owner 404
        row = rows[rid]
        assert row["label"] == rid and row["name_missing"] is True
        assert row["label_parts"] == {"registry_id": rid, "strategy_id": None}
        assert row["route_href"] is None and row["resolved_link"] is None
        assert all("target_display_name" not in c["parts"] for c in row["cited_by"])
    assert body["meta"]["item_reads"]["failed"] == 0


def test_failed_registry_entry_read_keeps_ref_id_and_is_counted(store, owners, spec_labels):
    owners["registry"][REG_SPEC] = strategy_spec_entry_view(REG_SPEC, STRATEGY_SPEC)
    owners["registry_fail"] = {REG_SPEC}
    owners["approvals"].append(_registry_entry_approval("apr-reg-1", REG_SPEC))
    body = _cited(store, page_size=100)
    row = _rows(body)[REG_SPEC]
    assert row["label"] == REG_SPEC and row["name_missing"] is True
    assert row["label_parts"] == {"registry_id": REG_SPEC, "strategy_id": None}
    assert row["route_href"] is None
    assert "target_display_name" not in row["cited_by"][0]["parts"]
    assert body["meta"]["item_reads"]["failed"] == 1
    assert body["meta"]["surfaces"]["cited_evidence"]["status"] == "ok"


def test_slow_registry_entry_read_is_abandoned_and_counted(store, owners, spec_labels, monkeypatch):
    owners["registry"][REG_SPEC] = strategy_spec_entry_view(REG_SPEC, STRATEGY_SPEC)
    owners["approvals"].append(_registry_entry_approval("apr-reg-1", REG_SPEC))
    gate = threading.Event()
    owners["registry_block"] = gate
    real_budget = cited_evidence.remaining_management_read_budget

    def budget(deadline=None, *, reserve_seconds=0.0):
        return 0.2 if reserve_seconds == 0.05 else real_budget(deadline, reserve_seconds=reserve_seconds)

    monkeypatch.setattr(cited_evidence, "remaining_management_read_budget", budget)
    try:
        body = _cited(store, page_size=100)
    finally:
        gate.set()
    row = _rows(body)[REG_SPEC]
    assert row["label"] == REG_SPEC and row["name_missing"] is True and row["route_href"] is None
    assert "target_display_name" not in row["cited_by"][0]["parts"]
    assert body["meta"]["item_reads"]["failed"] >= 3  # REG_SPEC + both blocked artifact reads


def test_registry_entry_of_another_tenant_is_never_used(store, owners, spec_labels):
    owners["registry"][REG_SPEC] = strategy_spec_entry_view(REG_SPEC, STRATEGY_SPEC, owner_tenant="tenant-b")
    owners["approvals"].append(_registry_entry_approval("apr-reg-1", REG_SPEC))
    body = _cited(store, page_size=100)
    row = _rows(body)[REG_SPEC]
    assert row["label"] == REG_SPEC and row["name_missing"] is True and row["route_href"] is None
    assert body["meta"]["item_reads"]["failed"] == 1


def test_registry_entry_reads_share_the_item_read_cap(store, owners, spec_labels):
    entries = [f"reg-strategy-spec-persona-{i:020x}" for i in range(30)]
    owners["registry"].update({e: strategy_spec_entry_view(e, STRATEGY_SPEC) for e in entries})
    owners["approvals"] = [_registry_entry_approval(f"apr-{i}", e) for i, e in enumerate(entries)]
    body = _cited(store, page_size=100)
    assert len(owners["registry_reads"]) == cited_evidence.MAX_ITEM_READS
    assert len(owners["registry_reads"]) == len(set(owners["registry_reads"]))
    reads = body["meta"]["item_reads"]
    assert reads["truncated"] is True and reads["attempted"] == cited_evidence.MAX_ITEM_READS
    unread = [r for r in body["data"]["items"]
              if r["ref_kind"] == "registry_entry" and r["ref_id"] not in owners["registry_reads"]]
    assert unread and all(r["name_missing"] is True and r["route_href"] is None for r in unread)
