"""Case-file views on the OODA packet detail and Agora panel/memo reads.

Case files are composed by the real knowledge composer over the real memory
service (HTTP transport), persisted as the owners persist them (packet record,
Consultation transcript), then read back through the BFF routes.
"""
from __future__ import annotations

import copy
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.consultation import models as cm
from services.control_plane.bff.auth.policy import bff_error, extract_identity, require_read_role
from services.control_plane.bff.bootstrap.dependencies import AppDependencies
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.personas.router import create_personas_router
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.ports import (
    DefaultResearchKnowledgeSourcePort,
    OodaPacketsPort,
    create_in_memory_lifecycle_telemetry_governance_port,
    create_in_memory_operations_consultation_port,
    create_in_memory_persona_capital_runtime_port,
)
from services.control_plane.bff.tests.test_agora_committee_memory_owners import (  # noqa: F401
    PANEL, headers, stack,
)
from services.knowledge import case_file as cf
from services.persona.cron_ooda_closure import append_ooda_packet

TENANT = "tenant-a"

EVIDENCE_PERSONA_PACKETS = (
    (
        "persona-2448c7053e33ea48da47",
        (
            "ooda-persona-f18d09ad03612a6c41df58704b3f85b64149e740c98e87ff36f7a5dc63ff7d32",
            "ooda-persona-2d3bba6f386a49e3124f36b0549a8b72d976b6d7938ae67d924040496de3453d",
        ),
    ),
    (
        "persona-ee7112a829d38b9135ac",
        (
            "ooda-persona-c4462c5671f9cb686d8ff95c9ef0486e65e548d72391ccf70b87d8e804abad2a",
            "ooda-persona-e1adeccdbe2914860351fb80a8d58996a45764606209af438051e8adb4397426",
        ),
    ),
    (
        "persona-6507750312870d03a241",
        (
            "ooda-persona-b6c33d8775a0b8a99a131ef6fb1f030936b0fc8e3036c74c865153779aac3ce2",
            "ooda-persona-f323181a0f972b0efcb692ea74ee6081aa4807e88d910e0644cf865e48b04b89",
        ),
    ),
)


TAGS = ["family:momentum", "decision:consult"]


@pytest.fixture
def seeded(stack):
    """Real memory writes tagged for this decision: one committee-shared, one private, one institutional."""
    from uuid import uuid4
    memory = stack[4]["memory.test"]
    common = {"source_event_type": "postmortem_published", "source_event_id": "case-file-view",
              "written_at": "2026-11-12T00:00:00Z", "write_authority": "incident-svc", "reuse_count": 0}
    rows = [("persona-entries", {**common, "memory_id": "pmem-" + str(uuid4()), "persona_id": "persona-a",
                                  "memory_type": "strategy_lesson", "relevance_scope": "persona_private",
                                  "content": {"summary": "momentum consult private lesson", "tags": TAGS}}),
            ("persona-entries", {**common, "memory_id": "pmem-" + str(uuid4()), "persona_id": "persona-a",
                                  "memory_type": "strategy_lesson", "relevance_scope": "persona_and_committee",
                                  "content": {"summary": "momentum consult shared lesson", "tags": TAGS}}),
            ("entries", {**common, "entry_id": "mem-" + str(uuid4()), "knowledge_type": "incident_lesson",
                         "content": {"headline": "momentum consult", "body": "institutional lesson", "tags": TAGS},
                         "scope": "system_wide", "contributing_persona_ids": ["persona-a"]})]
    for endpoint, payload in rows:
        r = memory.post("/api/memory/" + endpoint, headers=headers(), json=payload)
        assert r.status_code == 201, r.text
    return stack


def compose(persona_caller: str):
    token = headers()["Authorization"].removeprefix("Bearer ")
    subject = cf.Subject(decision_type="consult", persona_id="persona-a", tenant_id=TENANT,
                         strategy_family="momentum")
    caller = cf.Caller(actor_id="alice", actor_roles=("operator",), session_id="s-1", persona_id=persona_caller)
    return cf.compose_case_file(subject, caller, cf.Owners(memory=cf.OwnerEndpoint("http://memory.test", token)))


def items(case):
    return [i for g in case["groups"].values() for i in g["items"]]


def packet_with(case, persona_ids=("persona-a",), packet_id="cycle-1", tenant_id=TENANT,
                environment="paper", updated_at="2026-11-21T10:00:00Z", cron_run_at_ms=100, **cycle):
    return {"packet_id": packet_id, "loop_type": "paper_strategy", "status": "decided", "environment": environment,
            "tenant_id": tenant_id, "persona_ids": list(persona_ids),
            "created_at": updated_at, "updated_at": updated_at,
            "producer": {"cron_run_at_ms": cron_run_at_ms},
            "persona_cycle": {"phase": "terminal", "harness_version": 1,
                              "case_files": {case["case_file_id"]: case} if case else {},
                              **cycle}}


def _compose_packet_app(
    tmp_path: Path,
    monkeypatch,
    packets=(),
    *,
    personas=(),
    store_name="ooda_packets.json",
    ooda_port: OodaPacketsPort | None = None,
):
    for env_key in ("PANTHEON_OODA_DATA_DIR", "PANTHEON_CONTROL_PLANE_DATA_DIR"):
        monkeypatch.delenv(env_key, raising=False)
    store_path = tmp_path / store_name / "ooda_packets.json" if store_name.endswith("_dir") else tmp_path / store_name
    store_path.parent.mkdir(parents=True, exist_ok=True)
    for pkt in packets:
        append_ooda_packet(store_path, pkt)
    monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(store_path))

    deps = AppDependencies.create_default(
        command_store=CommandStore(str(tmp_path / f"commands-{uuid4()}.jsonl")),
        ooda_port=ooda_port,
    )
    if personas:
        persona_rows = [copy.deepcopy(p) for p in personas]
        deps.read_surface.persona_capital_runtime = create_in_memory_persona_capital_runtime_port(
            personas=persona_rows,
        )
        rks = DefaultResearchKnowledgeSourcePort()
        rks._has_injected_strategy_specs = True
        deps.read_surface.research_knowledge_source = rks
        deps.read_surface.operations_consultation = create_in_memory_operations_consultation_port()
        deps.read_surface.lifecycle_telemetry_governance = (
            create_in_memory_lifecycle_telemetry_governance_port()
        )
    service = PersonaService(
        write_owner=deps.persona_write_owner,
        read_store=deps.read_surface,
        ranking_write_owner=deps.ranking_write_owner,
        command_store=deps.command_store,
        provisioning_store=MemoryPersonaProvisioningStore(),
    )
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(
        create_control_loops_router(
            read_surface=deps.read_surface,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
        )
    )
    app.include_router(
        create_personas_router(
            service=service,
            extract_identity_fn=extract_identity,
        )
    )
    return TestClient(app, raise_server_exceptions=False), store_path, deps


def packet_client(packet, tmp_path: Path, monkeypatch, *, store_name: str | None = None):
    client, _, _ = _compose_packet_app(
        tmp_path,
        monkeypatch,
        packets=(packet,),
        store_name=store_name or f"packets-{uuid4()}.json",
    )
    return client


def test_packet_with_case_file_shows_items_and_citations(seeded, tmp_path, monkeypatch):
    case = compose("persona-a")
    cited = next(i for i in items(case) if i["kind"].startswith("memory_"))
    assert cited["retrieval_mode"]
    packet = packet_with(case, decisions=[
        {"kind": "no_action", "status": "no_action", "case_file_id": case["case_file_id"],
         "cited_case_file_item_ids": [cited["item_id"], "lessons:ghost"], "none_applicable_reason": None}],
        turn={"prompt": "PRIVATE_PROMPT"})
    r = packet_client(packet, tmp_path, monkeypatch).get("/bff/ooda/packets/cycle-1", headers=headers())
    assert r.status_code == 200, r.text
    view = r.json()["data"]["case_file_view"]
    assert view["status"] == "ok"
    got = {i["item_id"]: i for g in view["case_files"][0]["groups"].values() for i in g["items"]}
    assert got[cited["item_id"]]["excerpt"] == cited["excerpt"]
    assert got[cited["item_id"]]["source_id"] == cited["source_id"]
    assert got[cited["item_id"]]["retrieval_mode"] == cited["retrieval_mode"]
    assert view["decisions"][0]["cited_item_ids"] == [cited["item_id"]]
    assert view["decisions"][0]["unresolved_item_ids"] == ["lessons:ghost"]
    assert "case_files" not in r.json()["data"]["persona_cycle"]


def test_packet_without_case_file_fails_closed_with_reason(stack, tmp_path, monkeypatch):
    packet = packet_with(None, failure_reason="case_file_unavailable:owner_unconfigured")
    r = packet_client(packet, tmp_path, monkeypatch).get("/bff/ooda/packets/cycle-1", headers=headers())
    assert r.status_code == 200
    assert r.json()["data"]["case_file_view"] == {
        "status": "unavailable", "reason": "case_file_unavailable:owner_unconfigured", "case_file": None}


def test_packet_never_shows_another_personas_private_memory(seeded, tmp_path, monkeypatch):
    case = compose("persona-a")
    private = [i for i in items(case) if i.get("relevance_scope") == "persona_private"]
    assert private, "fixture must contain the persona's private memory"
    h = headers()
    own = packet_client(packet_with(case), tmp_path, monkeypatch)
    own_ids = {i["item_id"] for g in own.get("/bff/ooda/packets/cycle-1", headers=h).json()["data"][
        "case_file_view"]["case_files"][0]["groups"].values() for i in g["items"]}
    other = packet_client(packet_with(case, persona_ids=("persona-b",)), tmp_path, monkeypatch)
    view = other.get("/bff/ooda/packets/cycle-1", headers=h).json()["data"]["case_file_view"]
    other_ids = {i["item_id"] for g in view["case_files"][0]["groups"].values() for i in g["items"]}
    assert {p["item_id"] for p in private} <= own_ids
    assert not ({p["item_id"] for p in private} & other_ids)
    assert view["case_files"][0]["private_items_withheld"] == len(private)


def test_normal_composition_persona_summary_and_ooda_routes_resolve_same_owner_packets(
    seeded, tmp_path, monkeypatch
):
    """Prove the 11/21 Dev Root 44915049855 summary-to-detail flow through AppDependencies.create_default."""
    base_case = compose("persona-a")
    cited_item = next(i for i in items(base_case) if i["kind"].startswith("memory_"))
    personas = []
    packets = []
    for idx, (persona_id, packet_ids) in enumerate(EVIDENCE_PERSONA_PACKETS):
        personas.append(
            {
                "persona_id": persona_id,
                "tenant_id": TENANT,
                "name": f"Trading Persona {idx + 1}",
                "status": "active",
                "lifecycle_state": "paper_running",
                "owner_lifecycle_state": "paper_owner",
                "metadata": {"tenant_id": TENANT},
            }
        )
        for p_idx, packet_id in enumerate(packet_ids):
            persona_case = copy.deepcopy(base_case)
            persona_case["case_file_id"] = f"cf-{packet_id}"
            persona_case["subject"]["persona_id"] = persona_id
            packets.append(
                packet_with(
                    persona_case,
                    persona_ids=(persona_id,),
                    packet_id=packet_id,
                    updated_at=f"2026-11-21T10:0{p_idx}:00Z",
                    cron_run_at_ms=200 - p_idx,
                    decisions=[
                        {
                            "kind": "no_action",
                            "status": "no_action",
                            "case_file_id": persona_case["case_file_id"],
                            "cited_case_file_item_ids": [cited_item["item_id"]],
                            "none_applicable_reason": None,
                        }
                    ],
                )
            )
    # Add a foreign-tenant packet and a non-paper packet to verify tenant and paper/live boundaries.
    packets.append(
        packet_with(
            copy.deepcopy(base_case),
            persona_ids=(EVIDENCE_PERSONA_PACKETS[0][0],),
            packet_id="ooda-persona-foreign-tenant",
            tenant_id="tenant-other",
        )
    )
    packets.append(
        packet_with(
            copy.deepcopy(base_case),
            persona_ids=(EVIDENCE_PERSONA_PACKETS[0][0],),
            packet_id="ooda-persona-live-env",
            environment="live",
        )
    )

    client, store_path, _ = _compose_packet_app(
        tmp_path, monkeypatch, packets=packets, personas=personas
    )
    before_bytes = store_path.read_bytes()

    # 1. OODA list resolves the 6 tenant-scoped paper packets.
    listed = client.get("/bff/ooda/packets", headers=headers(TENANT))
    assert listed.status_code == 200, listed.text
    listed_body = listed.json()
    assert listed_body["meta"]["surfaces"]["ooda_packets"]["status"] == "ok"
    assert listed_body["meta"]["surfaces"]["ooda_packets"]["source"] == "store"
    expected_six = {pid for _, pids in EVIDENCE_PERSONA_PACKETS for pid in pids}
    assert {item["packet_id"] for item in listed_body["items"]} == expected_six

    # 2. Each Persona summary returns its paper packet IDs, and every displayed packet ID
    #    resolves to HTTP 200 with case_file_view on GET /bff/ooda/packets/{packet_id}.
    for persona_id, expected_pids in EVIDENCE_PERSONA_PACKETS:
        for route in (f"/bff/personas/{persona_id}", f"/api/v1/operator/persona-management/{persona_id}"):
            summary = client.get(route, headers=headers(TENANT))
            assert summary.status_code == 200, summary.text
            summary_body = summary.json()
            assert summary_body["meta"]["surfaces"]["persona_cycles"]["status"] == "ok"
            cycle_packets = summary_body["data"]["personaCycle"]["cycle"]["packets"]
            assert [p["packet_id"] for p in cycle_packets] == list(expected_pids)
            for pkt in cycle_packets:
                detail = client.get(f"/bff/ooda/packets/{pkt['packet_id']}", headers=headers(TENANT))
                assert detail.status_code == 200, detail.text
                detail_body = detail.json()
                assert detail_body["data"]["packet_id"] == pkt["packet_id"]
                assert detail_body["meta"]["surfaces"]["ooda_packet_detail"]["status"] == "ok"
                assert "case_files" not in detail_body["data"]["persona_cycle"]
                view = detail_body["data"]["case_file_view"]
                assert view["status"] == "ok"
                assert view["case_files"][0]["case_file_id"] == f"cf-{pkt['packet_id']}"
                assert view["decisions"][0]["cited_item_ids"] == [cited_item["item_id"]]

    # 3. Genuine missing packet and non-paper packet return 404 when the owner source exists.
    missing = client.get("/bff/ooda/packets/ooda-persona-does-not-exist", headers=headers(TENANT))
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    live_pkt = client.get("/bff/ooda/packets/ooda-persona-live-env", headers=headers(TENANT))
    assert live_pkt.status_code == 404, live_pkt.text

    # 4. Another tenant cannot read tenant-a personas or OODA packets, even with a spoofed header.
    foreign_headers = {**headers("tenant-other"), "X-Tenant-Id": TENANT}
    foreign_list = client.get("/bff/ooda/packets", headers=foreign_headers)
    assert foreign_list.status_code == 200
    assert foreign_list.json()["items"] == []
    first_pid = EVIDENCE_PERSONA_PACKETS[0][1][0]
    foreign_detail = client.get(f"/bff/ooda/packets/{first_pid}", headers=foreign_headers)
    assert foreign_detail.status_code == 404
    assert "case_file_view" not in foreign_detail.text
    assert store_path.read_bytes() == before_bytes


@pytest.mark.parametrize("contents", [None, "{corrupt-json"])
def test_absent_or_unreadable_owner_store_reports_unavailable_across_persona_and_ooda(
    seeded, tmp_path, monkeypatch, contents
):
    persona_id, (packet_id, _) = EVIDENCE_PERSONA_PACKETS[0]
    persona = {
        "persona_id": persona_id,
        "tenant_id": TENANT,
        "name": "Trading Persona 1",
        "status": "active",
        "lifecycle_state": "paper_running",
        "owner_lifecycle_state": "paper_owner",
        "metadata": {"tenant_id": TENANT},
    }
    client, store_path, _ = _compose_packet_app(
        tmp_path,
        monkeypatch,
        packets=(packet_with(compose("persona-a"), persona_ids=(persona_id,), packet_id=packet_id),),
        personas=(persona,),
        store_name=f"corrupt-or-missing-{uuid4()}.json",
    )
    if contents is None:
        store_path.unlink()
    else:
        store_path.write_text(contents, encoding="utf-8")

    summary = client.get(f"/bff/personas/{persona_id}", headers=headers(TENANT))
    assert summary.status_code == 200
    assert summary.json()["data"]["personaCycle"]["cycle"] is None
    assert summary.json()["meta"]["surfaces"]["persona_cycles"]["status"] == "unavailable"
    assert summary.json()["meta"]["surfaces"]["autonomy_cycles"]["status"] == "unavailable"

    listed = client.get("/bff/ooda/packets", headers=headers(TENANT))
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert listed.json()["meta"]["surfaces"]["ooda_packets"]["status"] == "unavailable"

    detail = client.get(f"/bff/ooda/packets/{packet_id}", headers=headers(TENANT))
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "degraded"
    assert detail.json()["meta"]["surfaces"]["ooda_packet_detail"]["status"] == "unavailable"


def test_injected_ooda_port_precedence_in_normal_composition(seeded, tmp_path, monkeypatch):
    persona_id = "persona-a"
    case = compose(persona_id)
    injected_packet = packet_with(case, persona_ids=(persona_id,), packet_id="ooda-injected-packet")
    injected_port = OodaPacketsPort(records_provider=lambda: [copy.deepcopy(injected_packet)])
    client, _, _ = _compose_packet_app(
        tmp_path,
        monkeypatch,
        packets=(packet_with(case, persona_ids=(persona_id,), packet_id="ooda-disk-packet"),),
        personas=(
            {
                "persona_id": persona_id,
                "tenant_id": TENANT,
                "name": "Persona A",
                "status": "active",
                "lifecycle_state": "paper_running",
                "owner_lifecycle_state": "paper_owner",
                "metadata": {"tenant_id": TENANT},
            },
        ),
        ooda_port=injected_port,
    )
    summary = client.get(f"/bff/personas/{persona_id}", headers=headers(TENANT)).json()
    assert [p["packet_id"] for p in summary["data"]["personaCycle"]["cycle"]["packets"]] == [
        "ooda-injected-packet"
    ]
    detail = client.get("/bff/ooda/packets/ooda-injected-packet", headers=headers(TENANT))
    assert detail.status_code == 200
    assert detail.json()["data"]["case_file_view"]["status"] == "ok"
    assert client.get("/bff/ooda/packets/ooda-disk-packet", headers=headers(TENANT)).status_code == 404


def record_panel(store, case, *, claims, rejected=None):
    actor = cm.ActorRef(actor_type="persona", actor_id="persona-a")
    base = {"case_file_id": case["case_file_id"], "digest": case["digest"]}
    events = [
        ("panel_case_file_attached", cm.ActorRef(actor_type="service", actor_id="consult"),
         {**base, "case_file": case}),
        ("persona_contribution", actor, {**base, "claims": claims, "recommendation": "approve",
                                         "role": "primary_reviewer"}),
    ]
    if rejected:
        events.append(("panel_contribution_rejected", cm.ActorRef(actor_type="service", actor_id="consult"),
                       {**base, **rejected}))
    store.put_transcript(cm.ConsultTranscript(
        transcript_id="transcript-panel-a", session_id="panel-a", request_id="panel-a",
        events=[cm.TranscriptEvent(event_id=f"e{n}", session_id="panel-a", sequence_no=n, event_type=t,
                                   actor=a, content=c) for n, (t, a, c) in enumerate(events, 1)]))


def test_panel_and_memo_show_case_file_citations_and_rejections(seeded):
    client, _, _, _, _, store = seeded
    case = compose("")  # service caller: shared memory only
    assert not [i for i in items(case) if i.get("relevance_scope") == "persona_private"]
    cited = items(case)[0]["item_id"]
    record_panel(store, case, claims=[{"claim": "grounded", "evidence_refs": [cited]},
                                      {"claim": "stray", "evidence_refs": ["lessons:ghost"]}],
                 rejected={"persona_id": "persona-b", "contribution_id": "c-2", "reason": "claim_uncited"})
    calls_before = len(seeded[1])
    panel = client.get(PANEL + "/panel-a", headers=headers())
    memos = client.get(PANEL + "/panel-a/memos", headers=headers())
    assert panel.status_code == memos.status_code == 200, (panel.text, memos.text)
    for view in (panel.json()["data"]["case_file_view"], memos.json()["data"][0]["case_file_view"]):
        assert view["status"] == "ok"
        assert view["case_file"]["case_file_id"] == case["case_file_id"]
        got = {i["item_id"]: i for g in view["case_file"]["groups"].values() for i in g["items"]}
        assert got[cited]["excerpt"] and got[cited]["retrieval_mode"] and got[cited]["source_id"]
        claims = view["contributions"][0]["claims"]
        assert claims[0]["cited_item_ids"] == [cited]
        assert claims[1]["cited_item_ids"] == [] and claims[1]["unresolved_item_ids"] == ["lessons:ghost"]
        assert view["rejected_contributions"] == [{"persona_id": "persona-b", "contribution_id": "c-2",
                                                   "reason": "claim_uncited", "cited": False}]
    assert all(m == "GET" for m, *_ in seeded[1][calls_before:])


def test_panel_without_case_file_and_foreign_tenant_case_file(stack):
    client, _, _, _, _, store = stack
    body = client.get(PANEL + "/panel-a", headers=headers()).json()["data"]
    assert body["case_file_view"]["reason"] == "no_case_file_recorded"
    assert body["case_file_view"]["case_file"] is None
    foreign = copy.deepcopy(compose(""))
    foreign["subject"]["tenant_id"] = "tenant-other"
    record_panel(store, foreign, claims=[])
    view = client.get(PANEL + "/panel-a", headers=headers()).json()["data"]["case_file_view"]
    assert view == {"status": "unavailable", "reason": "case_file_invalid", "case_file": None}


def test_panel_private_item_is_withheld(seeded):
    client, _, _, _, _, store = seeded
    case = compose("persona-a")  # includes persona-a private memory: must never ship in a panel view
    private = {i["item_id"] for i in items(case) if i.get("relevance_scope") == "persona_private"}
    assert private
    record_panel(store, case, claims=[])
    view = client.get(PANEL + "/panel-a", headers=headers()).json()["data"]["case_file_view"]
    shown = {i["item_id"] for g in view["case_file"]["groups"].values() for i in g["items"]}
    assert not (shown & private)


def test_consultation_outage_is_recorded_not_guessed(stack):
    client, _, control, *_ = stack
    resp = client.get(PANEL + "/panel-a", headers=headers())
    assert resp.status_code == 200
    control["response"][("consultation.test", "/api/consult/requests/panel-a/transcript")] = {"request_id": "x"}
    view = client.get(PANEL + "/panel-a", headers=headers()).json()["data"]["case_file_view"]
    assert view["reason"] == "invalid_transcript_response" and view["case_file"] is None
