"""Integration: real owner HTTP fixtures + the REAL memory service retrieval route.

The memory service is the production FastAPI app served over real HTTP (uvicorn),
with the real governance authz evaluator.  Registry / research / pool / telemetry /
search are HTTP fixtures serving owner-shaped payloads.  Entries are written through
the real memory write routes.
"""
from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jsonschema
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.knowledge import case_file as cf  # noqa: E402

SCHEMA = json.loads(Path(__file__).with_name("case_file.schema.json").read_text())
TENANT = "t1"


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Fixture(ThreadingHTTPServer):
    def __init__(self, routes):
        self.routes, self.calls = routes, []

        class H(BaseHTTPRequestHandler):
            def _do(h):
                n = int(h.headers.get("Content-Length") or 0)
                body = json.loads(h.rfile.read(n)) if n else None
                self.calls.append((h.command, h.path, h.headers.get("X-Tenant-Id"), body))
                path = h.path.split("?")[0]
                for prefix, payload in self.routes.items():
                    if path.startswith(prefix):
                        data = json.dumps(payload).encode()
                        h.send_response(200)
                        break
                else:
                    data = b'{"detail":"nope"}'
                    h.send_response(404)
                h.send_header("Content-Type", "application/json")
                h.send_header("Content-Length", str(len(data)))
                h.end_headers()
                h.wfile.write(data)
            do_GET = do_POST = _do

            def log_message(h, *a):
                pass
        super().__init__(("127.0.0.1", 0), H)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def endpoint(self):
        return cf.OwnerEndpoint(f"http://127.0.0.1:{self.server_address[1]}")


def _registry(refs):
    routes = {f"/api/registry/entries/{r}": {"registry_id": r, "artifact_type": "strategy_spec",
              "strategy_id": "momentum", "version": "1", "artifact_state": "candidate",
              "evaluation_summary": {"sharpe": 1.1}} for r in refs}
    routes["/api/registry/strategies/momentum/entries"] = [
        {"registry_id": "reg-old", "strategy_id": "momentum", "version": "0", "artifact_state": "retired",
         "evaluation_summary": {"reason": "decayed"}},
        {"registry_id": "reg-cand", "strategy_id": "momentum", "version": "1", "artifact_state": "candidate"}]
    return routes


class CaseFileIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = {k: os.environ.get(k) for k in (
            "PANTHEON_MEMORY_STORE", "PANTHEON_PERSONA_MEMORY_STORE", "PANTHEON_MEMORY_STORE_BACKEND",
            "PANTHEON_PERSONA_MEMORY_STORE_BACKEND", "PANTHEON_MEMORY_AUTHZ_MODE",
            "PANTHEON_RUNTIME_AUTH_MODE", "PANTHEON_SEARCH_POSTGRES_DSN")}
        os.environ.update({
            "PANTHEON_MEMORY_STORE": str(Path(cls.tmp.name) / "inst.json"),
            "PANTHEON_PERSONA_MEMORY_STORE": str(Path(cls.tmp.name) / "persona.json"),
            "PANTHEON_MEMORY_STORE_BACKEND": "json", "PANTHEON_PERSONA_MEMORY_STORE_BACKEND": "json",
            "PANTHEON_MEMORY_AUTHZ_MODE": "local"})
        os.environ.pop("PANTHEON_RUNTIME_AUTH_MODE", None)
        os.environ.pop("PANTHEON_SEARCH_POSTGRES_DSN", None)
        memory = importlib.import_module("services.memory.main")
        port = _port()
        cls.server = uvicorn.Server(uvicorn.Config(memory.app, host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)
        assert cls.server.started, "memory service did not start"
        cls.memory = cf.OwnerEndpoint(f"http://127.0.0.1:{port}")
        cls._seed()

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        for k, v in cls.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        cls.tmp.cleanup()

    @classmethod
    def _seed(cls):
        def post(path, body):
            r = cf.http_json(cls.memory, path, tenant_id=TENANT, method="POST", body=body)
            return r
        inst = lambda eid, headline, tags: {
            "entry_id": f"mem-{eid}", "knowledge_type": "research_finding",
            "content": {"headline": headline, "body": headline, "tags": tags},
            "source_event_type": "research_task_completed", "source_event_id": f"rt-{eid}",
            "written_at": "2026-04-20T04:00:00Z", "write_authority": "research-svc",
            "scope": "strategy_family", "scope_filter": "momentum", "contributing_persona_ids": ["persona-a"],
            "reuse_count": 0}
        post("/api/memory/entries", inst("11111111-1111-1111-1111-111111111111",
             "momentum 2330 equity trial admission lesson", ["family:momentum", "underlying:2330"]))
        post("/api/memory/entries", inst("22222222-2222-2222-2222-222222222222",
             "momentum equity failed trial negative memory", ["family:momentum", "negative_memory"]))
        post("/api/memory/entries", inst("33333333-3333-3333-3333-333333333333",
             "unrelated crypto carry note", ["family:carry"]))
        pers = lambda mid, pid, scope, summary: {
            "memory_id": f"pmem-{mid}", "persona_id": pid, "memory_type": "strategy_lesson",
            "content": {"summary": summary, "tags": ["family:momentum", "underlying:2330"]},
            "source_event_type": "postmortem_published", "source_event_id": f"PM-{mid}",
            "written_at": "2026-06-09T01:00:00Z", "write_authority": "incident-svc",
            "relevance_scope": scope, "reuse_count": 0, "tenant_id": TENANT}
        post("/api/memory/persona-entries", pers("aaaaaaaa-1111-1111-1111-111111111111", "persona-a",
             "persona_private", "private momentum 2330 sizing lesson"))
        post("/api/memory/persona-entries", pers("aaaaaaaa-2222-2222-2222-222222222222", "persona-a",
             "persona_and_committee", "shared momentum 2330 committee lesson"))
        post("/api/memory/persona-entries", pers("bbbbbbbb-1111-1111-1111-111111111111", "persona-b",
             "persona_private", "persona b private momentum 2330 secret"))

    def owners(self, refs, down=()):
        pool = {"/api/personas/persona-a/strategy-pool/versions": {"versions": [], "refusals": [
                    {"refusal_id": "ref-1", "reason": "correlation_cap"}]},
                "/api/personas/persona-a/strategy-pool": {"pool_version": 4, "members": list(refs)}}
        telemetry = {"/api/telemetry/runtime-summaries": {"summaries": [{
            "persona_capital_binding_id": "bind-1", "tenant_id": TENANT,
            "member_virtual_books": [{"member_id": r, "net_of_costs": {"pnl": 12}} for r in refs]}]}}
        research = {"/api/research-orchestrator/backtest-runs/bt-1/owner-binding": {
            "status": "done", "gate_verdict": {"passed": True}, "evaluation_digest": "d"}}
        search = {"/api/search/v2/query": {"retrieval_mode": "keyword", "results": [{
            "result_id": "r1", "evidence_bundle_id": "eb-1", "relevance_score": 0.7,
            "citations": ["doc-9"], "answer_context": "ctx",
            "matched_items": [{"object_id": "doc-9", "excerpt": "momentum 2330 backtest note"}]}]}}
        self.fixtures = {n: _Fixture(r) for n, r in {
            "registry": _registry(refs), "research": research, "pool": pool,
            "telemetry": telemetry, "search": search}.items() if n not in down}
        for f in self.fixtures.values():
            self.addCleanup(f.shutdown)
        ep = {n: f.endpoint for n, f in self.fixtures.items()}
        return cf.Owners(registry=ep.get("registry"), research=ep.get("research"), pool=ep.get("pool"),
                         telemetry=ep.get("telemetry"), memory=self.memory, search=ep.get("search"))

    def _subject(self, decision_type, refs):
        return cf.Subject(decision_type=decision_type, persona_id="persona-a", tenant_id=TENANT,
                          strategy_family="momentum", underlyings=("2330",), instrument_class="equity",
                          member_artifact_refs=tuple(refs), backtest_run_refs=("bt-1",),
                          persona_capital_binding_id="bind-1")

    def _compose(self, decision_type, refs, caller, **kw):
        out = cf.compose_case_file(self._subject(decision_type, refs), caller, self.owners(refs, **kw))
        jsonschema.validate(out, SCHEMA)
        return out

    def test_trial_admission_for_the_persona_itself(self):
        me = cf.Caller("persona-a", ("persona_session",), "sess-1", persona_id="persona-a")
        out = self._compose("trial_admission", ["reg-new"], me)
        g = out["groups"]
        self.assertEqual({i["kind"] for i in g["facts"]["items"]},
                         {"registry_entry", "backtest_gate_verdict", "strategy_pool", "member_virtual_book"})
        self.assertEqual(g["facts"]["status"], "ok", g["facts"])
        hist = {i["kind"]: i for i in g["similar_history"]["items"]}
        self.assertIn("retired_strategy", hist); self.assertIn("pool_refusal", hist)
        self.assertIn("memory_negative_memory", hist)
        neg = [i for i in g["similar_history"]["items"] if i["kind"] == "memory_negative_memory"]
        self.assertEqual([i["source_id"] for i in neg], ["mem-22222222-2222-2222-2222-222222222222"])
        lessons = {i["source_id"]: i for i in g["lessons"]["items"]}
        self.assertIn("pmem-aaaaaaaa-1111-1111-1111-111111111111", lessons)   # own private
        self.assertIn("pmem-aaaaaaaa-2222-2222-2222-222222222222", lessons)   # committee-shared
        self.assertFalse([s for s in lessons if "bbbbbbbb" in s])             # other persona never
        self.assertNotIn("mem-33333333-3333-3333-3333-333333333333", lessons)  # unrelated, filtered
        for it in g["lessons"]["items"] + g["evidence"]["items"]:
            self.assertTrue(it["source_id"] and it["excerpt"] and it["retrieval_mode"])
        self.assertEqual(g["evidence"]["items"][0]["source_id"], "doc-9")
        self.assertEqual(g["evidence"]["items"][0]["retrieval_mode"], "keyword")

    def test_member_removal_as_operator_excludes_private_memory(self):
        op = cf.Caller("op-1", ("operator",), "sess-2")
        out = self._compose("member_removal", ["reg-m1"], op)
        ids = {i["source_id"] for i in out["groups"]["lessons"]["items"]}
        self.assertNotIn("pmem-aaaaaaaa-1111-1111-1111-111111111111", ids)
        self.assertFalse([s for s in ids if "bbbbbbbb" in s])
        self.assertIn("pmem-aaaaaaaa-2222-2222-2222-222222222222", ids)
        self.assertIn("caller_is_not_the_persona",
                      [u["reason"] for u in out["groups"]["lessons"]["unavailable"]])
        self.assertTrue(any(i["kind"] == "member_virtual_book" and i["source_id"] == "reg-m1"
                            for i in out["groups"]["facts"]["items"]))

    def test_removed_member_history_and_no_invented_role(self):
        op = cf.Caller("op-1", ("operator",), "sess-2")
        owners = self.owners(["reg-m1"])
        self.fixtures["pool"].routes["/api/personas/persona-a/strategy-pool/versions"]["versions"] = [
            {"pool_version": 1, "members": [{"artifact_ref": "reg-removed", "stage": "removed",
                                             "removal_reason": "drawdown", "risk_weight": 0.0},
                                            {"artifact_ref": "reg-live", "stage": "trial"}]}]
        out = cf.compose_case_file(self._subject("member_removal", ["reg-m1"]), op, owners)
        jsonschema.validate(out, SCHEMA)
        rem = [i for i in out["groups"]["similar_history"]["items"] if i["kind"] == "removed_member"]
        self.assertEqual(len(rem), 1)
        self.assertIn("drawdown", rem[0]["excerpt"])
        self.assertIn("reg-removed", rem[0]["source_id"])

    def test_strict_auth_operator_token_gets_committee_memory(self):
        from types import SimpleNamespace
        from unittest import mock
        memory = importlib.import_module("services.memory.main")
        ctx = SimpleNamespace(actor_id="op-1", roles={"operator"}, claims={"tenant_id": TENANT})
        op = cf.Caller("op-1", ("operator",), "sess-2")
        with mock.patch.object(memory, "_authenticate_memory_request", return_value=ctx):
            out = self._compose("member_removal", ["reg-m1"], op)
        lessons = out["groups"]["lessons"]
        ids = {i["source_id"] for i in lessons["items"]}
        self.assertIn("pmem-aaaaaaaa-2222-2222-2222-222222222222", ids)
        self.assertNotIn("pmem-aaaaaaaa-1111-1111-1111-111111111111", ids)
        self.assertFalse([u for u in lessons["unavailable"] if str(u["reason"]).startswith("http_")],
                         lessons["unavailable"])

    def test_facts_come_from_owners_not_search(self):
        op = cf.Caller("op-1", ("operator",), "sess-2")
        out = self._compose("member_removal", ["reg-m1"], op)
        self.assertTrue(all(i["source_owner"] != "search" for i in out["groups"]["facts"]["items"]))
        search_calls = [c for c in self.fixtures["search"].calls]
        self.assertTrue(all(c[1].startswith("/api/search/v2/query") for c in search_calls))

    def test_missing_owner_is_recorded(self):
        op = cf.Caller("op-1", ("operator",), "sess-2")
        out = self._compose("member_removal", ["reg-m1"], op, down=("search", "registry"))
        self.assertEqual(out["groups"]["evidence"]["status"], "unavailable")
        self.assertIn("owner_unconfigured", out["groups"]["evidence"]["reason"])
        self.assertIn("registry:reg-m1", [u["owner"] for u in out["groups"]["facts"]["unavailable"]])


HS_URL = os.getenv("PANTHEON_HINDSIGHT_IT_URL", "")
HS_KEY = os.getenv("PANTHEON_HINDSIGHT_IT_KEY", "")


@unittest.skipUnless(HS_URL and HS_KEY, "set PANTHEON_HINDSIGHT_IT_URL and PANTHEON_HINDSIGHT_IT_KEY")
class CaseFileHindsightAnchors(unittest.TestCase):
    """Real memory route backed by real Hindsight: anchors exclude decision-tag-only lessons."""

    def test_decision_tag_only_lesson_excluded_and_on_subject_lesson_returned(self):
        import uuid
        from services.memory import hindsight_index as hs
        memory = importlib.import_module("services.memory.main")
        from services.memory.institutional_memory_store import InstitutionalMemoryStore
        from services.memory.persona_memory_store import PersonaMemoryStore
        from services.memory.test_hindsight_index import institution
        from services.memory.test_main import JWT_SECRET, _make_jwt
        tenant = "it-" + uuid.uuid4().hex[:10]
        stores = (InstitutionalMemoryStore(), PersonaMemoryStore())
        saved = {k: os.environ.get(k) for k in (
            "PANTHEON_MEMORY_AUTH_MODE", "PANTHEON_MEMORY_JWT_SECRET",
            "PANTHEON_MEMORY_AUTHZ_MODE", hs.URL_ENV, hs.KEY_ENV, "PANTHEON_RUNTIME_AUTH_MODE")}
        os.environ.update({
            "PANTHEON_MEMORY_AUTH_MODE": "strict", "PANTHEON_MEMORY_JWT_SECRET": JWT_SECRET,
            "PANTHEON_MEMORY_AUTHZ_MODE": "local", hs.URL_ENV: HS_URL, hs.KEY_ENV: HS_KEY})
        os.environ.pop("PANTHEON_RUNTIME_AUTH_MODE", None)
        hs.set_hindsight_client(None)
        client = hs.HindsightClient(HS_URL, HS_KEY)
        orig = (memory._store, memory._persona_store)
        memory._store, memory._persona_store = (lambda: stores[0]), (lambda: stores[1])
        port = _port()
        server = uvicorn.Server(uvicorn.Config(memory.app, host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()

        def cleanup():
            server.should_exit = True
            memory._store, memory._persona_store = orig
            hs.flush_mirrors()
            client.delete_bank(hs.facts_bank(tenant))
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            hs.set_hindsight_client(None)
        self.addCleanup(cleanup)
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        self.assertTrue(server.started)
        on_subject = stores[0].create(institution(tenant_id=tenant, content={
            "headline": "momentum 2330 trial admission lesson", "body": "momentum 2330 trial admission lesson",
            "tags": ["family:momentum", "underlying:2330", "decision:trial_admission"]}))
        other_decision = stores[0].create(institution(tenant_id=tenant, content={
            "headline": "momentum 2330 member removal lesson", "body": "momentum 2330 member removal lesson",
            "tags": ["family:momentum", "underlying:2330", "decision:member_removal"]}))
        decision_only = stores[0].create(institution(tenant_id=tenant, content={
            "headline": "carry trial admission lesson", "body": "carry trial admission lesson",
            "tags": ["family:carry", "decision:trial_admission"]}))
        neg_on_subject = stores[0].create(institution(tenant_id=tenant, content={
            "headline": "momentum 2330 failed trial negative lesson", "body": "momentum 2330 failed trial negative lesson",
            "tags": ["family:momentum", "underlying:2330", "decision:trial_admission", "negative_memory"]}))
        neg_decision_only = stores[0].create(institution(tenant_id=tenant, content={
            "headline": "carry failed trial negative lesson", "body": "carry failed trial negative lesson",
            "tags": ["family:carry", "decision:trial_admission", "negative_memory"]}))
        self.assertTrue(hs.flush_mirrors())
        subject = cf.Subject(decision_type="trial_admission", persona_id="persona-a", tenant_id=tenant,
                             strategy_family="momentum", underlyings=("2330",), member_artifact_refs=("reg-1",))
        caller = cf.Caller("op-1", ("operator",), "sess-1")
        token = _make_jwt(sub="op-1", tenant_id=tenant, roles=["operator"])
        owners = cf.Owners(None, None, None, None, cf.OwnerEndpoint(f"http://127.0.0.1:{port}", token=token), None)
        g = cf._Group("lessons")
        cf._lessons(subject, caller, owners, cf.Budgets(), g)
        got = {i["source_id"]: i for i in g.items}
        self.assertIn(on_subject.entry_id, got)
        self.assertNotIn(other_decision.entry_id, got)
        self.assertNotIn(decision_only.entry_id, got)
        self.assertNotIn(neg_decision_only.entry_id, got)
        self.assertEqual(got[on_subject.entry_id]["retrieval_mode"], "hindsight")
        self.assertEqual(got[on_subject.entry_id]["llm_layers"], "disabled")
        hist = cf._Group("similar_history")
        cf._history(subject, caller, owners, cf.Budgets(), hist)
        neg_got = {i["source_id"]: i for i in hist.items if i["kind"] == "memory_negative_memory"}
        self.assertIn(neg_on_subject.entry_id, neg_got)
        self.assertNotIn(neg_decision_only.entry_id, neg_got)
        self.assertEqual(neg_got[neg_on_subject.entry_id]["retrieval_mode"], "hindsight")
        self.assertEqual(neg_got[neg_on_subject.entry_id]["llm_layers"], "disabled")


if __name__ == "__main__":
    unittest.main()
