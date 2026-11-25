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
"""
from __future__ import annotations

import threading
import time
import urllib.error
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core import display_names, owner_reads
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.management_read_models import cited_evidence
from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.meetings.client import MeetingsOwnerClient, MeetingsOwnerUnavailableError
from services.control_plane.bff.ports.research_knowledge_source import EvidenceReadResult
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
PERSONA = "persona-ee7112a829d38b9135ac"
MEETING = "mtg-6edaff7d0ff76275"


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

    # Display-name directories.
    def list_personas(self) -> List[Dict[str, Any]]:
        return [{"persona_id": PERSONA, "display_name": "Macro Mia"}]

    def get_strategy_labels(self) -> Dict[str, Any]:
        return {ART_B: {"title": "Carry Basket B"}}

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

    def approval_records():
        if state["approvals_exc"] is not None:
            raise state["approvals_exc"]
        return list(state["approvals"])

    def read_records(url_builder, path, key=None):
        assert path == "/api/research/persona-demands" and key == "demands"
        return list(state["demands"])

    def ticket_page(self, **kw):
        assert kw.get("page_size") == 100
        return state["tickets"]

    monkeypatch.setattr(MeetingsOwnerClient, "list_meetings", list_meetings)
    monkeypatch.setattr(MeetingsOwnerClient, "get_meeting", get_meeting)
    monkeypatch.setattr(MeetingsOwnerClient, "list_proposals", list_proposals)
    monkeypatch.setattr(owner_reads, "approval_records", approval_records)
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
    assert reg["resolved_link"]["route_href"] == f"/management/strategies/{ART_B}"
    assert set(reg["cited_as"]) == {REG_B, ART_B}
    by = {(c["entity_type"], c["entity_ref"]) for c in reg["cited_by"]}
    assert ("meeting_position", f"{MEETING}#round-1/A") in by
    assert ("meeting_proposal", "prop-1") in by
    assert ("approval", "apr-1") in by
    for c in reg["cited_by"]:
        assert set(c) >= {"entity_type", "entity_ref", "label", "route_href", "time"}
        assert c["label"] and c["route_href"] and c["time"]
    pos = next(c for c in reg["cited_by"] if c["entity_type"] == "meeting_position")
    assert "Macro Mia" in pos["label"] and PERSONA not in pos["label"]
    assert pos["route_href"] == f"/management/meetings/{MEETING}"

    # An id no directory can name says name_missing.
    unnamed = rows[ART_A]
    assert unnamed["name_missing"] is True and unnamed["label"] == ART_A

    pool = rows[POOL]
    assert pool["label"] == "Macro Mia strategy pool v4" and pool["source_owner"] == "persona"
    assert pool["resolved_link"]["route_href"] == f"/management/personas/{PERSONA}"
    assert {c["entity_type"] for c in pool["cited_by"]} == {"meeting_position", "research_demand"}

    book = rows[BOOK_B]
    assert book["label"] == "Carry Basket B virtual book"
    assert book["cited_by"][0]["entity_type"] == "persona_cycle_decision"
    assert book["cited_by"][0]["label"] == "Macro Mia daily cycle decision (meeting_action)"

    # A ko-evi knowledge object resolves to its evidence item by one bounded item read.
    ko = rows[KO]
    assert ko["ref_kind"] == "knowledge_object"
    assert ko["label"] == "Fed minutes excerpt" and ko["source_class"] == "primary_source"
    assert ko["resolved_link"] == {"route_href": f"/knowledge/evidence/{EVI}", "evidence_ref_id": EVI}
    assert store.item_reads == [EVI]

    mem = rows[SHARED_MEM]
    assert mem["source_owner"] == "memory" and mem["resolved_link"]["route_href"] == "/knowledge/memory/mem-shared-0002"

    assert rows["note-111"]["cited_by"][0]["entity_type"] == "research_ticket"
    assert rows["note-222"]["cited_by"][0]["time"] == "2026-11-24T11:00:00Z"
    assert rows["note-111"]["resolved_link"]["route_href"] == "/knowledge/notes/note-111"
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
    assert len(store.item_reads) == 20
    assert body["meta"]["item_reads"]["truncated"] is True
    assert body["meta"]["cost"]["item_reads"] == 20


def test_redaction_reused_for_caller_without_capability(store, owners):
    body = _cited(store, headers=VIEWER, page_size=100)
    rows = body["data"]["items"]
    assert rows and all(r["redacted"] is True for r in rows)
    assert body["data"]["summary"]["redacted_count"] == len(rows)
    assert all(r["label"] == r["ref_id"] and r["resolved_link"] is None for r in rows)
    assert store.item_reads == []  # no item reads for withheld refs


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
    assert cited_evidence.parse_ref({"ref_type": "registry_entry", "ref_id": ART_A}).ref_id == ART_A
    assert cited_evidence.parse_ref({"kind": "research_note", "artifact_id": "note-1"}).ref_kind == "research_note"
    assert cited_evidence._ko_evidence_candidate("ko-evi-1217cd1c8ccc7274-94a73ebc0814") == EVI
    assert cited_evidence._ko_evidence_candidate("ko-evi-1217cd1c8ccc7274-000000000000") is None
