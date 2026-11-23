"""Persona component tests through the ordinary cron/broker entry point.

Research HTTP is a contract stub, not integrated/hosted acceptance. The real
Persona owners, packet store, adapter protocol and citation gates are exercised.
"""
import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from integrations.openclaw.persona_agent_sync import PERSONA_AGENT_TOOL_PROFILE
from services.control_plane.persona.persona_policy_resolver import (
    PersonaPolicyResolver, RoutePolicy, ToolProfile, evaluate_participant_action_capability,
)
from services.persona.test_persona_cycle_controller import system, memory_service, run
from services.persona.persona_cycle_controller import read_packets, build_case_file_prompt
from services.persona.write_owner import PersistentCapabilitySnapshotOwner, UpsertCapabilitySnapshotRequest, create_app
from services.runtime_auth_inbound import encode_jwt_hs256


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def ticket(n=1, *, owner="persona-cycle-test", assignee_kind="persona", subject_ref="pool:persona-cycle-test",
           meeting_type="MT-1", action_state="open", required_actor=None):
    tid = f"rt-action-{n:032d}"
    return {"ticket_id": tid, "action_id": tid, "tenant_id": "tenant-a", "owner": owner,
            "revision": 0, "action_state": action_state, "required_actor": required_actor or owner, "evidence_refs": [],
            "meeting_action": {"meeting_id": "meeting-origin", "meeting_type": meeting_type,
                "subject_ref": subject_ref, "source_action_id": f"action-{n}",
                "assignee_kind": assignee_kind, "deliverable": "Compare staying flat with the current momentum allocation",
                "acceptance_criterion": "Cite the observed evidence and state the limitation",
                "due_at": "2026-11-24T00:00:00Z", "source_refs": []}}


@pytest.fixture
def action_system(system, tmp_path, monkeypatch):
    controller, gateway, owner, pool = system
    cap = PersistentCapabilitySnapshotOwner.from_json_path(tmp_path / "capability.json")
    cap.upsert(UpsertCapabilitySnapshotRequest(actor_id="operator", snapshot_id="cap-1",
        persona_id="persona-cycle-test", capabilities=["research_note"],
        effective_tools=PERSONA_AGENT_TOOL_PROFILE["allow"], effective_workflows=["persona_daily_cycle"],
        generated_at="2026-11-23T00:00:00Z"))
    controller.owners._action_capability_owner = cap
    state = {"tickets": {ticket()["ticket_id"]: ticket()}, "notes": {}, "commands": {}, "calls": [], "meetings": {},
             "patch_outage": False, "lose_submit_reply": False}

    class Research(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, payload):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            assert self.headers["Authorization"] == "Bearer research-contract-token"
            assert self.headers["X-Tenant-Id"] == "tenant-a"
            state["calls"].append(("GET", self.path))
            url = urlsplit(self.path)
            if url.path == "/api/research/tickets":
                query = parse_qs(url.query)
                assert query["meeting_linked"] == ["true"]
                states = query["action_state"][0].split(",")
                if states == ["open", "in_progress", "returned"]:
                    assert "owner" in query and "subject_ref" not in query
                else:
                    assert "subject_ref" in query
                rows = [copy.deepcopy(r) for r in sorted(state["tickets"].values(), key=lambda r: r["ticket_id"])
                        if r["action_state"] in states
                        and ("owner" not in query or r["owner"] == query["owner"][0])
                        and ("subject_ref" not in query or r.get("_unfiltered")
                             or r["meeting_action"].get("subject_ref") == query["subject_ref"][0])]
                anchor = query.get("page_token", [""])[0]
                rows = [r for r in rows if r["ticket_id"] > anchor]
                size = int(query["page_size"][0])
                self.reply(200, {"data": rows[:size], "page_info": {
                    "next_page_token": rows[size-1]["ticket_id"] if len(rows) > size else None}})
            elif url.path.startswith("/api/research/notes/"):
                note = state["notes"].get(url.path.rsplit("/", 1)[1])
                self.reply(200 if note else 404, note or {})
            elif url.path.startswith("/api/research/tickets/"):
                self.reply(200, state["tickets"][url.path.rsplit("/", 1)[1]])
            elif url.path.startswith("/api/consult/meetings/"):
                self.reply(200, state["meetings"].get(url.path.rsplit("/", 1)[1], {}))
            else:
                self.reply(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append(("POST", self.path))
            assert self.path == "/api/research/notes"
            nid = "note-action-" + digest(["tenant-a", body["meeting_ticket_id"], "persona-pool-cycle", body["idempotency_key"]])[:32]
            note = {"note_id": nid, **body, "_meeting_binding": {"tenant_id": "tenant-a",
                "ticket_id": body["meeting_ticket_id"], "actor_id": "persona-pool-cycle"}, "_meeting_digest": digest(body)}
            if nid in state["notes"] and state["notes"][nid] != note:
                self.reply(409, {})
                return
            state["notes"][nid] = note
            self.reply(200, note)

        def do_PATCH(self):
            cmd = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["patch"]
            state["calls"].append(("PATCH", self.path))
            if state["patch_outage"]:
                self.reply(503, {})
                return
            row = state["tickets"][self.path.rsplit("/", 1)[1]]
            key = cmd["idempotency_key"]
            if key in state["commands"]:
                assert state["commands"][key] == cmd
            elif cmd["expected_revision"] != row["revision"]:
                self.reply(409, {})
                return
            else:
                state["commands"][key] = cmd
                row["revision"] += 1
                row["action_state"] = "submitted" if cmd["action"] == "submit" else "waiting_for_input"
                row["evidence_refs"] = [{**r, "checksum": state["notes"][r["artifact_id"]]["_meeting_digest"]}
                                         for r in cmd.get("evidence_refs", [])]
                row["required_actor"] = "consultation-svc" if cmd["action"] == "submit" else cmd["human_actor_id"]
            if state["lose_submit_reply"]:
                state["lose_submit_reply"] = False
                self.reply(503, {})  # committed write, response lost: never count it as submitted yet
            else:
                self.reply(200, row)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Research)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    controller.owners.env.update(PANTHEON_RESEARCH_API_URL=f"http://127.0.0.1:{server.server_port}",
                                 PANTHEON_RESEARCH_SERVICE_TOKEN="research-contract-token",
                                 PANTHEON_CONSULTATION_API_URL=f"http://127.0.0.1:{server.server_port}",
                                 CONSULTATION_SERVICE_TOKEN="research-contract-token")
    controller.owners.env.pop("PANTHEON_RESEARCH_SERVICE_TOKEN_FILE", None)
    try:
        yield controller, gateway, owner, cap, state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        assert not thread.is_alive()


def propose(gateway, tid, action="submit", **parameters):
    gateway["answer"] = {"proposals": [{"kind": "meeting_action", "reason": "Follow the assigned criterion",
        "evidence_refs": [], "case_file_item_ids": [f"facts:meeting_action_ticket:{tid}"],
        "parameters": {"ticket_id": tid, "action": action, "title": "Allocation comparison",
                       "analysis": "The available book supports staying flat pending a validated signal; no new capital allocation is justified.",
                       **parameters}}]}


def test_submit_next_same_subject_citation_and_restart_concurrency(action_system):
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    tid = ticket()["ticket_id"]
    propose(gateway, tid)
    with ThreadPoolExecutor(max_workers=2) as executor:
        packets = list(executor.map(lambda _: controller.consume_run(persona, "job-1", run(1)), range(2)))
    assert packets[0] == packets[1]
    assert packets[0]["persona_cycle"]["failure_reason"] is None
    result = packets[0]["persona_cycle"]["decisions"][0]
    assert result["status"] == "submitted" and result["delivered_by"] == persona["persona_id"]
    assert result["run_id"] == "gw-run-1"
    assert len(research["notes"]) == len(research["commands"]) == 1
    assert not packets[0]["act"]["live_capital_side_effects"]
    note = next(iter(research["notes"].values()))
    assert "run:gw-run-1" in note["body"] and "persona:persona-cycle-test" in note["body"]
    gateway["answer"] = {"proposals": [{"kind": "no_action", "reason": "The verified analysis supports keeping course",
        "evidence_refs": [], "case_file_item_ids": [f"facts:meeting_action_result:{tid}"]}]}
    packet = controller.consume_run(persona, "job-1", run(2))
    assert packet["persona_cycle"]["failure_reason"] is None
    assert note["note_id"] in gateway["prompts"][persona["persona_id"]]
    assert packet["persona_cycle"]["decisions"][0]["reason"].endswith("keeping course")
    assert len(research["notes"]) == 1
    import runpy
    from pathlib import Path
    verify = runpy.run_path(str(Path(__file__).parents[2] / "docs/deployment/evidence/"
        "PERSONA-MEETING-ACTION-EXECUTION-20261123/probe.py"))["verify"]
    receipt = verify(research["tickets"][tid], note, read_packets(controller.path), persona["persona_id"], "tenant-a")
    assert receipt["status"] == "passed" and receipt["run_id"] == "gw-run-1"
    with pytest.raises(AssertionError):
        verify(research["tickets"][tid], note, {packets[0]["packet_id"]: packets[0]}, persona["persona_id"], "tenant-a")


@pytest.mark.parametrize("lost_reply", [False, True, "accepted_after_reply_loss"])
def test_uncertain_write_journal_replays_without_new_note_or_model_work(action_system, lost_reply):
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    propose(gateway, ticket()["ticket_id"])
    research["patch_outage"] = not lost_reply
    research["lose_submit_reply"] = lost_reply
    first = controller.consume_run(persona, "job-1", run(1))
    assert first["persona_cycle"]["decisions"][0]["status"] == "unavailable"
    assert len(research["notes"]) == 1
    research["patch_outage"] = False
    if lost_reply == "accepted_after_reply_loss":
        research["tickets"][ticket()["ticket_id"]].update(action_state="accepted", revision=2)
    gateway["answer"] = {"proposals": [{"kind": "no_action", "reason": "Await the review",
        "evidence_refs": [], "none_applicable_reason": "No further proposal"}]}
    second = controller.consume_run(persona, "job-1", run(2))
    assert second["persona_cycle"]["meeting_action_recovery"][0]["status"] == "submitted"
    assert len(research["notes"]) == len(research["commands"]) == 1
    assert second["persona_cycle"]["meeting_action_recovery"][0]["run_id"] == "gw-run-1"


def test_request_exact_human_input_and_resume_original_ticket(action_system):
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    tid = ticket()["ticket_id"]
    propose(gateway, tid, "request_input", missing_requirement="Supply the approved dataset snapshot", human_actor_id="human-analyst")
    first = controller.consume_run(persona, "job-1", run(1))
    assert first["persona_cycle"]["decisions"][0]["status"] == "waiting_for_input"
    assert list(research["commands"].values())[0]["reason"] == "Supply the approved dataset snapshot"
    assert not research["notes"]
    assert controller.owners.observe_meeting_actions(persona, {})["runnable"] == []
    # A permitted human response is Research's responsibility. Stub its owner-issued result.
    row = research["tickets"][tid]
    note = {"note_id": "human-note", "body": "Authorized dataset snapshot findings",
            "_meeting_binding": {"ticket_id": tid, "tenant_id": "tenant-a", "actor_id": "human-analyst"},
            "_meeting_digest": "human-checksum"}
    research["notes"]["human-note"] = note
    refs = [{"kind": "research_note", "artifact_id": "human-note", "checksum": "human-checksum"}]
    row.update(action_state="in_progress", required_actor=persona["persona_id"], revision=2,
               human_input={"evidence_refs": refs, "provided_by": "human-analyst"})
    propose(gateway, tid, evidence_refs=[{"kind": "research_note", "artifact_id": "human-note"}])
    resumed = controller.consume_run(persona, "job-1", run(2))
    assert resumed["persona_cycle"]["decisions"][0]["status"] == "submitted"
    assert research["tickets"][tid]["owner"] == persona["persona_id"]
    assert "Authorized dataset snapshot findings" in gateway["prompts"][persona["persona_id"]]


def test_pagination_continues_without_hiding_unfinished_work(action_system):
    controller, gateway, owner, cap, research = action_system
    research["tickets"] = {ticket(n)["ticket_id"]: ticket(n) for n in range(1, 10)}
    persona = owner.list_json()[0]
    seen, cursor = set(), {}
    for _ in range(3):
        observed = controller.owners.observe_meeting_actions(persona, cursor)
        assert len(observed["runnable"]) <= 4
        cursor = observed["cursors"]
        seen.update(r["ticket_id"] for r in observed["runnable"])
    assert seen == set(research["tickets"])
    assert cursor["runnable"] is None
    assert len(research["calls"]) == 9  # three bounded list requests per normal tick (runnable + pool series + persona series)


@pytest.mark.parametrize("fault", ["decline", "uncited", "foreign_ticket", "no_tool_record", "timeout", "no_analysis", "unicode_record"])
def test_declined_rejected_or_unrecorded_output_never_delivers(action_system, fault):
    controller, gateway, owner, cap, research = action_system
    propose(gateway, ticket()["ticket_id"])
    p = gateway["answer"]["proposals"][0]
    if fault == "decline":
        p["parameters"]["action"] = "decline"
    elif fault == "uncited":
        p.pop("case_file_item_ids")
        p["none_applicable_reason"] = "Did not read the ticket"
    elif fault == "foreign_ticket":
        p["parameters"]["ticket_id"] = "foreign-ticket"
    elif fault == "no_tool_record":
        gateway["answer"] = None
    elif fault == "timeout":
        gateway["outage"] = True
    elif fault == "no_analysis":
        p["parameters"].pop("analysis")
    else:
        p["parameters"]["analysis"] = "研" * 2600  # 7,800 UTF-8 bytes, exceeds owner's ASCII JSON cap
    controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert not research["notes"] and not research["commands"]


def test_capability_joins_authorities_and_denies_labels_or_expired_access(action_system):
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    snapshot = cap.get_for_persona(persona["persona_id"]).model_dump()
    evaluate = lambda p=persona, s=snapshot, **kw: evaluate_participant_action_capability(p, s, **kw)
    assert evaluate()["executable"]
    assert evaluate(run_purpose="panel_interpretation")["status"] == "advisory_only"
    assert not evaluate(run_purpose="consultation")["can_mutate_trading_state"]
    assert evaluate({**persona, "lifecycle_state": "consultable", "role": "chair"})["status"] == "advisory_only"
    for origin in ("private_servant", "agora_advisor"):
        assert evaluate({**persona, "metadata": {**persona["metadata"], "origin": origin}})["status"] == "advisory_only"
    assert evaluate({**persona, "consult_policy_id": "consult-panel.non-trading"})["status"] == "advisory_only"
    assert evaluate({**persona, "status": "suspended"})["status"] == "suspended"
    assert evaluate(s={**snapshot, "effective_workflows": []})["status"] == "no_workflow"
    assert evaluate(requirements={"required_data": ["foreign-private-bank"]})["status"] == "restricted_data"
    assert evaluate(s={**snapshot, "metadata": {**snapshot["metadata"], "expires_at": "2026-01-01T00:00:00Z"}})["status"] == "expired_access"
    assert evaluate(s={**snapshot, "effective_tools": ["read"]})["status"] == "downgraded_access"
    assert evaluate(owner_available=False)["status"] == "owner_unavailable"
    assert evaluate(s={**snapshot, "metadata": {}})["status"] == "downgraded_access"
    assert evaluate({**persona, "route_policy_id": "unknown"})["status"] == "no_workflow"
    resolver = PersonaPolicyResolver(tool_profiles={"tp": ToolProfile("tp", ["read"])},
        route_policies={"rp": RoutePolicy("rp", ["persona_daily_cycle"])})
    assert evaluate({**persona, "tool_profile_id": "tp", "route_policy_id": "rp"}, resolver=resolver)["status"] == "downgraded_access"


def test_capability_read_endpoint_is_tenant_scoped_and_rechecks_at_execution(action_system, monkeypatch):
    controller, gateway, owner, cap, research = action_system
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PERSONA_JWT_SECRET", "action-test-secret")
    client = TestClient(create_app(owner, capability_owner=cap))
    def headers(tenant):
        token = encode_jwt_hs256({"sub": "consultation-svc", "roles": ["viewer"],
            "tenant_id": tenant, "allowed_tenants": [tenant], "exp": 4102444800}, secret="action-test-secret")
        return {"Authorization": "Bearer " + token, "X-Tenant-Id": tenant}
    path = "/api/personas/persona-cycle-test/action-capability"
    assert client.get(path, headers=headers("tenant-a")).json()["executable"]
    assert client.get(path, headers=headers("tenant-b")).status_code == 404
    assert client.get(path, headers=headers("tenant-a"), params={"run_purpose": "consultation"}).json()["status"] == "advisory_only"
    propose(gateway, ticket()["ticket_id"])
    original = controller.owners.meeting_action_plan
    def suspend(*args):
        plan = original(*args)
        raw = owner._records.get("persona-cycle-test")
        owner._records.compare_and_set("persona-cycle-test", raw, {**raw, "status": "suspended"})
        return plan
    monkeypatch.setattr(controller.owners, "meeting_action_plan", suspend)
    packet = controller.consume_run(owner.list_json()[0], "job-1", run(1))
    assert packet["persona_cycle"]["decisions"][0]["status"] == "unavailable"
    assert not research["notes"]


def test_chair_string_citations_resolve_real_content_and_private_memory_is_refused(action_system):
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    tid = ticket()["ticket_id"]
    ref = "facts:registry_entry:artifact-cycle"
    research["tickets"][tid]["meeting_action"]["source_refs"] = [ref]
    item = {"item_id": ref, "source_id": "artifact-cycle", "kind": "registry_entry", "source_owner": "registry",
            "excerpt": "Owner execution bundle: momentum, 2330.TW; evaluated net-of-cost return 0.03"}
    research["meetings"]["meeting-origin"] = {"meeting_id": "meeting-origin", "tenant_id": "tenant-a",
        "case_file": {"digest": "frozen-public-case", "groups": {"facts": {"items": [item]}}}}
    propose(gateway, tid, evidence_refs=[{"kind": "meeting_case_item", "artifact_id": ref}])
    packet = controller.consume_run(persona, "job-1", run(1))
    assert packet["persona_cycle"]["decisions"][0]["status"] == "submitted"
    assert "evaluated net-of-cost return 0.03" in gateway["prompts"][persona["persona_id"]]
    item["memory_layer"] = "persona_private"
    research["tickets"][tid].update(action_state="returned", revision=2, required_actor=persona["persona_id"])
    observed = controller.owners.observe_meeting_actions(persona, {})
    # The verified ticket stays visible (request_input only); the private source is never exposed.
    assert [r["source_unavailable"] for r in observed["runnable"]] == ["private_source_forbidden"]
    assert observed["runnable"][0]["evidence_refs"] == []
    assert observed["unavailable"][0]["reason"] == "private_source_forbidden"


def test_claimed_packet_restart_replays_saved_plan_without_buying_original_turn(action_system, monkeypatch):
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    propose(gateway, ticket()["ticket_id"])
    execute = controller.owners.meeting_action_execute
    def crash(*args):
        execute(*args)  # result made it to Research, then the process lost its packet update
        raise SystemExit("simulated process loss")
    monkeypatch.setattr(controller.owners, "meeting_action_execute", crash)
    with pytest.raises(SystemExit):
        controller.consume_run(persona, "job-1", run(1))
    assert next(iter(read_packets(controller.path).values()))["persona_cycle"]["phase"] == "claimed"
    assert controller.consume_run(persona, "job-1", run(1))["persona_cycle"]["failure_reason"] == "interrupted_cycle_outcome_unknown"
    monkeypatch.setattr(controller.owners, "meeting_action_execute", execute)
    gateway["answer"] = {"proposals": [{"kind": "no_action", "reason": "Keep course",
        "evidence_refs": [], "none_applicable_reason": "Await review"}]}
    recovered = controller.consume_run(persona, "job-1", run(2))
    assert recovered["persona_cycle"]["meeting_action_recovery"][0]["status"] == "submitted"
    assert gateway["turns"] == 2
    assert len(research["notes"]) == len(research["commands"]) == 1


def test_meeting_action_budget_measurement(action_system):
    from pathlib import Path
    import os
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    research["tickets"] = {ticket(n)["ticket_id"]: ticket(n) for n in range(1, 5)}
    gateway["answer"] = {"proposals": []}
    for tid in research["tickets"]:
        propose(gateway, tid)
        gateway["answer"].setdefault("proposals", [])
    # Submit all four from the same real broker record; no extra turns/cadence.
    template = copy.deepcopy(gateway["answer"]["proposals"][0])
    gateway["answer"]["proposals"] = []
    for tid in research["tickets"]:
        p = copy.deepcopy(template)
        p["parameters"]["ticket_id"] = tid
        p["case_file_item_ids"] = [f"facts:meeting_action_ticket:{tid}"]
        gateway["answer"]["proposals"].append(p)
    controller.path = controller.path.with_suffix(".json")
    packet = controller.consume_run(persona, "job-1", run(1))
    assert all(d["status"] == "submitted" for d in packet["persona_cycle"]["decisions"])
    counts = {m: sum(method == m for method, _ in research["calls"]) for m in ("GET", "POST", "PATCH")}
    observed = packet["persona_cycle"]["observe_bundle"]
    report = {"fixture": "four assignments, one ordinary Persona turn, empty source refs",
        "owner_requests": counts, "total_owner_requests": len(research["calls"]),
        "turn_input_bytes": len(gateway["prompts"][persona["persona_id"]].encode()),
        "meeting_envelope_bytes": len(json.dumps(observed["meeting_actions"], ensure_ascii=False).encode()),
        "packet_bytes": len(json.dumps(packet, ensure_ascii=False).encode()),
        "hosted_format_store_bytes": controller.path.stat().st_size,
        "retained_packet_count": len(read_packets(controller.path)), "notes": len(research["notes"]),
        "notes_total_bytes": len(json.dumps(research["notes"], ensure_ascii=False).encode())}
    report["three_persona_example_per_trading_day"] = {
        "full_packet_bytes": 3 * report["packet_bytes"],
        "hosted_format_store_bytes": 3 * report["hosted_format_store_bytes"],
        "notes": 3 * report["notes"],
        "notes_bytes": 3 * report["notes_total_bytes"],
    }
    assert report["total_owner_requests"] == 19
    assert report["meeting_envelope_bytes"] < 196608
    target = os.getenv("PERSONA_ACTION_MEASUREMENT_PATH")
    if target:
        Path(target).write_text(json.dumps(report, indent=2) + "\n")


def test_paper_advisory_receipt_does_not_cap_daily_cycle_and_explicit_limits_win(action_system):
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    explicit = cap.get_for_persona(persona["persona_id"]).model_dump()
    # Actual hosted shape: owner-verified advisory receipt, persona_opinion only, no tools/workflows.
    advisory = {**explicit, "snapshot_id": "cap-advisory-" + persona["persona_id"], "capabilities": ["persona_opinion"],
                "allowed_capabilities": ["persona_opinion"], "effective_tools": [], "effective_workflows": [],
                "generated_at": "2026-11-24T00:00:00Z",
                "metadata": {"canonical_write_authority": "persona_service", "scope": "paper_advisory"}}
    evaluate = lambda p=persona, s=advisory, **kw: evaluate_participant_action_capability(p, s, **kw)
    assert evaluate()["executable"] and evaluate()["allowed_deliverables"] == ["research_note"]
    assert not evaluate(run_purpose="consultation")["executable"]  # advisory receipt grants nothing outside daily purpose
    assert not evaluate(run_purpose="consultation")["can_mutate_trading_state"]
    assert evaluate({**persona, "status": "suspended"})["status"] == "suspended"
    assert evaluate({**persona, "metadata": {**persona["metadata"], "persona_harness": {}}})["status"] == "no_workflow"
    assert evaluate(s={**advisory, "restrictions": ["advisory_only"]})["status"] == "advisory_only"
    assert evaluate(requirements={"required_data": ["foreign-private-bank"]})["status"] == "restricted_data"
    assert evaluate(s={**advisory, "metadata": {**advisory["metadata"], "access_status": "expired"}})["status"] == "expired_access"
    assert evaluate(s={**advisory, "metadata": {**advisory["metadata"], "canonical_write_authority": "bff"}})["status"] == "downgraded_access"
    # A later advisory receipt never shadows an explicit daily denial.
    cap.upsert(UpsertCapabilitySnapshotRequest(actor_id="operator", snapshot_id="cap-denied",
        persona_id=persona["persona_id"], capabilities=["research_note"], restrictions=["no_execution"],
        effective_tools=PERSONA_AGENT_TOOL_PROFILE["allow"], effective_workflows=["persona_daily_cycle"],
        generated_at="2026-11-23T01:00:00Z"))
    cap.upsert(UpsertCapabilitySnapshotRequest(actor_id="operator", snapshot_id=advisory["snapshot_id"],
        persona_id=persona["persona_id"], capabilities=["persona_opinion"], generated_at="2026-11-24T00:00:00Z",
        metadata={"canonical_write_authority": "persona_service", "scope": "paper_advisory"}))
    assert cap.get_for_persona(persona["persona_id"], "daily_cycle").snapshot_id == "cap-denied"
    assert not controller.owners.action_capability(persona)["executable"]


def test_title_only_research_artifact_is_not_evidence(action_system):
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    controller.owners._action_http = lambda *a, **k: {"data": {"artifact_id": "art-1", "title": "Document title only",
        "checksum": "abc", "tenant_id": "tenant-a"}}
    with pytest.raises(ValueError, match="evidence_content_missing"):
        controller.owners._action_evidence(persona, {"kind": "research_artifact", "artifact_id": "art-1"}, "t")


REAL_GATED_DATA_SOURCES = [
    {"dataset": "tw_price_daily", "market": "TW", "cadence": "daily", "source_class": "live_pull",
     "connector_candidates": ["tw-twse-tpex-official-market"],
     "policy_gates": ["require_connector_approved", "require_schedule_active", "require_source_health_ok", "no_live_capital"]},
    {"dataset": "tw_broker_top", "market": "TW", "cadence": "daily", "source_class": "live_pull",
     "connector_candidates": ["tw-finmind-broker-daily-report"],
     "policy_gates": ["require_connector_approved", "require_schedule_active", "require_source_health_ok", "no_live_capital"]},
]
REAL_ENROLLED_PERSONA_IDS = (
    "persona-2448c7053e33ea48da47",
    "persona-6507750312870d03a241",
    "persona-ee7112a829d38b9135ac",
)


def test_real_three_persona_gated_data_shape_executes_note_and_restricts_unavailable_feed(action_system):
    controller, gateway, owner, cap, research = action_system
    base_persona = owner.list_json()[0]
    for pid in REAL_ENROLLED_PERSONA_IDS:
        meta = copy.deepcopy(base_persona["metadata"])
        meta["persona_harness"]["versions"][0]["persona_id"] = pid
        real_persona = {
            **base_persona,
            "persona_id": pid,
            "tenant_id": "tenant-dev",
            "lifecycle_state": "paper_owner",
            "status": "active",
            "required_data_sources": copy.deepcopy(REAL_GATED_DATA_SOURCES),
            "metadata": meta,
        }
        advisory = {
            "snapshot_id": f"cap-advisory-{pid}",
            "persona_id": pid,
            "capabilities": ["persona_opinion"],
            "allowed_capabilities": ["persona_opinion"],
            "effective_tools": [],
            "effective_skills": [],
            "effective_workflows": [],
            "restrictions": [],
            "generated_at": "2026-11-23T05:45:54Z",
            "source_refs": ["governance:paper_advisory"],
            "metadata": {"canonical_write_authority": "persona_service", "scope": "paper_advisory", "accessible_data": []},
        }
        daily = evaluate_participant_action_capability(real_persona, advisory, run_purpose="daily_cycle")
        assert daily["status"] == "allowed" and daily["executable"] is True
        assert daily["can_be_assigned"] and daily["can_receive_persona_action"] and daily["can_execute_in_current_run"]
        assert not daily["can_mutate_trading_state"] and daily["allowed_deliverables"] == ["research_note"]
        assert daily["accessible_data"] == []  # never fabricate accessible_data
        consult = evaluate_participant_action_capability(real_persona, advisory, run_purpose="consultation")
        assert not consult["executable"] and not consult["can_execute_in_current_run"] and not consult["can_mutate_trading_state"]
        for ds in ("tw_price_daily", "tw_broker_top", "foreign-private-bank"):
            restricted = evaluate_participant_action_capability(
                real_persona, advisory, run_purpose="daily_cycle", requirements={"required_data": [ds]})
            assert restricted["status"] == "restricted_data" and not restricted["executable"]
            assert restricted["reason"] == "deliverable_data_not_accessible" and restricted["missing_data"] == [ds]
        market_req = evaluate_participant_action_capability(
            real_persona, advisory, run_purpose="daily_cycle", requirements={"requires_market_data": True})
        assert market_req["status"] == "restricted_data" and market_req["missing_data"] == ["tw_broker_top", "tw_price_daily"]
        with_access = {**advisory, "metadata": {**advisory["metadata"], "accessible_data": ["tw_price_daily"]}}
        allowed_feed = evaluate_participant_action_capability(
            real_persona, with_access, run_purpose="daily_cycle", requirements={"required_data": ["tw_price_daily"]})
        assert allowed_feed["status"] == "allowed" and allowed_feed["executable"] and allowed_feed["accessible_data"] == ["tw_price_daily"]
        for bad_meta in (
            {"unhealthy_data": ["tw_price_daily"]},
            {"data_health": {"tw_price_daily": "unhealthy"}},
            {"inactive_schedules": ["tw_price_daily"]},
            {"unapproved_connectors": ["tw_price_daily"]},
            {"revoked_data": ["tw_price_daily"]},
            {"failed_policy_gates": {"tw_price_daily": ["require_source_health_ok"]}},
        ):
            denied = evaluate_participant_action_capability(
                real_persona, {**advisory, "metadata": {**with_access["metadata"], **bad_meta}},
                run_purpose="daily_cycle", requirements={"required_data": ["tw_price_daily"]})
            assert denied["status"] == "restricted_data" and denied["missing_data"] == ["tw_price_daily"]
        seed_persona = {**real_persona, "required_data_sources": [
            {**REAL_GATED_DATA_SOURCES[0], "source_class": "seed_only"}]}
        assert evaluate_participant_action_capability(
            seed_persona, with_access, run_purpose="daily_cycle",
            requirements={"required_data": ["tw_price_daily"]})["status"] == "restricted_data"

    # End-to-end execution + later decision citation on the gated Persona record with accessible_data=[].
    raw_record = owner._records.get("persona-cycle-test")
    owner._records.compare_and_set("persona-cycle-test", raw_record, {
        **raw_record, "required_data_sources": copy.deepcopy(REAL_GATED_DATA_SOURCES)})
    cap._records = PersistentCapabilitySnapshotOwner.from_json_path(
        controller.path.parent / "capability-advisory-only.json")._records
    cap.upsert(UpsertCapabilitySnapshotRequest(
        actor_id="operator", snapshot_id="cap-advisory-persona-cycle-test",
        persona_id="persona-cycle-test", capabilities=["persona_opinion"],
        generated_at="2026-11-24T00:00:00Z",
        metadata={"canonical_write_authority": "persona_service", "scope": "paper_advisory", "accessible_data": []}))
    persona = owner.list_json()[0]
    tid = ticket()["ticket_id"]
    propose(gateway, tid)
    first = controller.consume_run(persona, "job-1", run(1))
    assert first["persona_cycle"]["failure_reason"] is None
    assert first["persona_cycle"]["decisions"][0]["status"] == "submitted"
    gateway["answer"] = {"proposals": [{"kind": "no_action", "reason": "Same-ticket note suffices to keep course",
        "evidence_refs": [], "case_file_item_ids": [f"facts:meeting_action_result:{tid}"]}]}
    second = controller.consume_run(persona, "job-1", run(2))
    assert second["persona_cycle"]["failure_reason"] is None
    assert second["persona_cycle"]["decisions"][0]["cited_case_file_item_ids"] == [f"facts:meeting_action_result:{tid}"]
    # A ticket explicitly requiring unavailable gated market data remains blocked at execution.
    gated_ticket = ticket(2)
    gated_ticket["meeting_action"]["required_data"] = ["tw_price_daily"]
    research["tickets"][gated_ticket["ticket_id"]] = gated_ticket
    propose(gateway, gated_ticket["ticket_id"])
    third = controller.consume_run(persona, "job-1", run(3))
    assert third["persona_cycle"]["decisions"][0]["status"] == "unavailable"
    assert third["persona_cycle"]["decisions"][0]["reason"] == "deliverable_data_not_accessible"


def test_mt1_pool_series_human_and_persona_results_reach_decide_with_zero_semantic_hits(action_system, monkeypatch):
    controller, gateway, owner, cap, research = action_system
    persona = owner.list_json()[0]
    pid = persona["persona_id"]

    # 1. Accepted Persona-owned MT-1 row with M's normalized pool:<chair> subject_ref.
    t_accepted = ticket(1, owner=pid, assignee_kind="persona", subject_ref=f"pool:{pid}", action_state="accepted")
    t_accepted["review_decision"] = {"decision": "accept", "reason": "Verified pool analysis", "review_source": "mtg-w48"}
    n_accepted = {"note_id": "note-persona-accepted", "title": "Pool drawdown review",
                  "body": "Accepted Persona pool analysis: drawdown stayed within 1.8% budget.",
                  "_meeting_binding": {"tenant_id": "tenant-a", "ticket_id": t_accepted["ticket_id"], "actor_id": "persona-pool-cycle"},
                  "_meeting_digest": "digest-persona-accepted"}
    t_accepted["evidence_refs"] = [{"kind": "research_note", "artifact_id": "note-persona-accepted", "checksum": "digest-persona-accepted"}]

    # 2. Submitted (unreviewed) Persona-owned MT-1 row for the same pool.
    t_submitted = ticket(2, owner=pid, assignee_kind="persona", subject_ref=f"pool:{pid}", action_state="submitted")
    n_submitted = {"note_id": "note-persona-submitted", "title": "Pending turnover check",
                   "body": "Submitted Persona analysis awaiting next chair review.",
                   "_meeting_binding": {"tenant_id": "tenant-a", "ticket_id": t_submitted["ticket_id"], "actor_id": "persona-pool-cycle"},
                   "_meeting_digest": "digest-persona-submitted"}
    t_submitted["evidence_refs"] = [{"kind": "research_note", "artifact_id": "note-persona-submitted", "checksum": "digest-persona-submitted"}]

    # 3. Reviewed human deliverable (owner=operator, assignee_kind=human) for the same pool:<chair>.
    t_human = ticket(3, owner="operator", assignee_kind="human", subject_ref=f"pool:{pid}",
                     action_state="accepted", required_actor="operator")
    t_human["description"] = "Only a human can obtain the signed custodian fee schedule."
    t_human["meeting_action"]["deliverable"] = "Verify custodian fee tier schedule for the pool"
    t_human["review_decision"] = {"decision": "accept", "reason": "Custodian schedule verified", "review_source": "mtg-w48"}
    n_human = {"note_id": "note-human-accepted", "title": "Custodian fee schedule",
               "body": "Human operator verified custodian fee is 4 bps; no fee tier change.",
               "_meeting_binding": {"tenant_id": "tenant-a", "ticket_id": t_human["ticket_id"], "actor_id": "signed-in-alice"},
               "_meeting_digest": "digest-human-accepted"}
    t_human["evidence_refs"] = [{"kind": "research_note", "artifact_id": "note-human-accepted", "checksum": "digest-human-accepted"}]

    # 4. Supported genuine historical/persona series (MT-2 persona:<id>).
    t_hist = ticket(4, owner=pid, assignee_kind="persona", subject_ref=f"persona:{pid}",
                    meeting_type="MT-2", action_state="accepted")
    n_hist = {"note_id": "note-hist-accepted", "title": "Historical MT-2 lesson",
              "body": "Genuine historical persona series note.",
              "_meeting_binding": {"tenant_id": "tenant-a", "ticket_id": t_hist["ticket_id"], "actor_id": "persona-pool-cycle"},
              "_meeting_digest": "digest-hist-accepted"}
    t_hist["evidence_refs"] = [{"kind": "research_note", "artifact_id": "note-hist-accepted", "checksum": "digest-hist-accepted"}]

    # 5. Unrelated pools, invalid historical series, and open human ticket (must all be suppressed).
    t_other_pool = ticket(5, owner="persona-6507750312870d03a241", assignee_kind="persona",
                          subject_ref="pool:persona-6507750312870d03a241", action_state="accepted")
    t_other_pool["_unfiltered"] = True  # even if an unfiltered response returned it, Python series check suppresses it
    t_other_pool["evidence_refs"] = [{"kind": "research_note", "artifact_id": "note-other-pool", "checksum": "digest-other"}]
    n_other = {"note_id": "note-other-pool", "title": "Foreign pool note", "body": "Unrelated pool secret",
               "_meeting_binding": {"tenant_id": "tenant-a", "ticket_id": t_other_pool["ticket_id"], "actor_id": "persona-pool-cycle"},
               "_meeting_digest": "digest-other"}
    t_fake_hist = ticket(6, owner=pid, assignee_kind="persona", subject_ref=f"persona:{pid}",
                         meeting_type="MT-3", action_state="accepted")
    t_fake_hist["evidence_refs"] = [{"kind": "research_note", "artifact_id": "note-hist-accepted", "checksum": "digest-hist-accepted"}]
    t_open_human = ticket(7, owner="operator", assignee_kind="human", subject_ref=f"pool:{pid}", action_state="open")

    research["tickets"] = {t["ticket_id"]: t for t in (
        t_accepted, t_submitted, t_human, t_hist, t_other_pool, t_fake_hist, t_open_human)}
    research["notes"] = {n["note_id"]: n for n in (n_accepted, n_submitted, n_human, n_hist, n_other)}

    # Force zero semantic memory/search hits in the composed case file.
    orig_case_file = controller.owners.case_file
    def zero_semantic_case_file(*args, **kwargs):
        case = orig_case_file(*args, **kwargs)
        for gname in ("similar_history", "lessons", "evidence"):
            case["groups"][gname]["items"] = []
        return case
    monkeypatch.setattr(controller.owners, "case_file", zero_semantic_case_file)

    gateway["answer"] = {"proposals": [{"kind": "no_action",
        "reason": "Keep course: accepted pool analysis and verified human custodian fee schedule show no drift",
        "evidence_refs": [],
        "case_file_item_ids": [
            f"facts:meeting_action_result:{t_accepted['ticket_id']}",
            f"facts:meeting_action_result:{t_human['ticket_id']}",
            f"facts:meeting_action_result:{t_submitted['ticket_id']}",
            f"facts:meeting_action_result:{t_hist['ticket_id']}",
        ]}]}

    packet = controller.consume_run(persona, "job-1", run(1))
    assert packet["persona_cycle"]["failure_reason"] is None
    observed = packet["persona_cycle"]["observe_bundle"]["meeting_actions"]
    assert observed["runnable"] == [] and observed["unavailable"] == []
    by_id = {r["ticket_id"]: r for r in observed["verified_results"]}
    assert set(by_id) == {t_accepted["ticket_id"], t_submitted["ticket_id"], t_human["ticket_id"], t_hist["ticket_id"]}
    assert by_id[t_accepted["ticket_id"]]["review_status"] == "accepted" and by_id[t_accepted["ticket_id"]]["accepted"] is True
    assert by_id[t_accepted["ticket_id"]]["accountable_persona_id"] == pid
    assert by_id[t_submitted["ticket_id"]]["review_status"] == "submitted" and by_id[t_submitted["ticket_id"]]["accepted"] is False
    assert by_id[t_human["ticket_id"]]["assignee_kind"] == "human" and by_id[t_human["ticket_id"]]["owner"] == "operator"
    assert by_id[t_human["ticket_id"]]["accountable_persona_id"] is None
    assert by_id[t_human["ticket_id"]]["review_status"] == "accepted" and by_id[t_human["ticket_id"]]["accepted"] is True

    prompt = gateway["prompts"][pid]
    assert "Human operator verified custodian fee is 4 bps" in prompt
    assert "Unrelated pool secret" not in prompt and t_other_pool["ticket_id"] not in prompt and t_fake_hist["ticket_id"] not in prompt
    case = next(iter(packet["persona_cycle"]["case_files"].values()))
    fact_items = {i["item_id"]: i for i in case["groups"]["facts"]["items"] if i["kind"] == "meeting_action_result"}
    assert fact_items[f"facts:meeting_action_result:{t_human['ticket_id']}"]["subject_fields"]["assignee_kind"] == "human"
    assert fact_items[f"facts:meeting_action_result:{t_human['ticket_id']}"]["subject_fields"]["owner"] == "operator"
    assert fact_items[f"facts:meeting_action_result:{t_human['ticket_id']}"]["subject_fields"]["accepted"] is True
    assert fact_items[f"facts:meeting_action_result:{t_submitted['ticket_id']}"]["subject_fields"]["accepted"] is False
    assert fact_items[f"facts:meeting_action_result:{t_submitted['ticket_id']}"]["subject_fields"]["review_status"] == "submitted"
    assert "[accepted; human:operator]" in fact_items[f"facts:meeting_action_result:{t_human['ticket_id']}"]["excerpt"]
    assert "[submitted; persona:" in fact_items[f"facts:meeting_action_result:{t_submitted['ticket_id']}"]["excerpt"]

    # Shared peer-Persona research on the same pool is available as evidence without treating the peer as the deciding Persona.
    t_peer = ticket(8, owner="persona-2448c7053e33ea48da47", assignee_kind="persona",
                    subject_ref=f"pool:{pid}", action_state="accepted")
    n_peer = {"note_id": "note-peer-accepted", "title": "Peer cross-check",
              "body": "Peer persona confirmed liquidity capacity on 2330.TW.",
              "_meeting_binding": {"tenant_id": "tenant-a", "ticket_id": t_peer["ticket_id"], "actor_id": "persona-pool-cycle"},
              "_meeting_digest": "digest-peer-accepted"}
    t_peer["evidence_refs"] = [{"kind": "research_note", "artifact_id": "note-peer-accepted", "checksum": "digest-peer-accepted"}]
    research["tickets"] = {t_peer["ticket_id"]: t_peer}
    research["notes"]["note-peer-accepted"] = n_peer
    peer_obs = controller.owners.observe_meeting_actions(persona, {})
    assert len(peer_obs["verified_results"]) == 1
    assert peer_obs["verified_results"][0]["owner"] == "persona-2448c7053e33ea48da47"
    assert peer_obs["verified_results"][0]["accountable_persona_id"] is None
