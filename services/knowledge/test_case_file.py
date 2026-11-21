"""Unit tests for the decision case file composer (owner calls stubbed at http_json)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.knowledge import case_file as cf  # noqa: E402

SCHEMA = json.loads((Path(__file__).with_name("case_file.schema.json")).read_text())
OWNERS = cf.Owners(*(cf.OwnerEndpoint("http://x") for _ in range(6)))


def subject(**kw):
    base = dict(decision_type="trial_admission", persona_id="persona-a", tenant_id="t1",
                strategy_family="momentum", underlyings=("2330",), instrument_class="equity",
                member_artifact_refs=("reg-1",), backtest_run_refs=("bt-1",),
                persona_capital_binding_id="bind-1")
    base.update(kw)
    return cf.Subject(**base)


OP = cf.Caller("op-1", ("operator",), "sess-1")
SELF = cf.Caller("sess-a", ("persona_session",), "sess-1", persona_id="persona-a")


def fake(overrides=None, down=()):
    overrides = overrides or {}

    def call(endpoint, path, *, tenant_id, method="GET", body=None, params=None, timeout=10.0):
        for name in down:
            if name in path:
                raise cf.OwnerUnavailable("http_503")
        for prefix, value in overrides.items():
            if path.startswith(prefix):
                return value(params) if callable(value) else value
        if path.startswith("/api/registry/entries/"):
            return {"registry_id": path.rsplit("/", 1)[1], "strategy_id": "momentum", "artifact_state": "candidate"}
        if "/entries" in path:
            return []
        if "owner-binding" in path:
            return {"status": "done", "gate_verdict": {"passed": True}}
        if path.endswith("/strategy-pool"):
            return {"pool_version": 3, "members": []}
        if path.endswith("/versions"):
            return {"versions": [], "refusals": []}
        if path.startswith("/api/telemetry"):
            return {"summaries": [{"persona_capital_binding_id": "bind-1", "tenant_id": "t1",
                                   "member_virtual_books": [{"member_id": "reg-1", "pnl": 1}]}]}
        if path.startswith("/api/memory/retrieve"):
            return {"hits": []}
        if path.startswith("/api/search"):
            return {"retrieval_mode": "hybrid", "results": []}
        raise AssertionError(path)
    return call


def compose(sub=None, caller=OP, **kw):
    with mock.patch.object(cf, "http_json", fake(**kw)):
        return cf.compose_case_file(sub or subject(), caller, OWNERS)


class CaseFileTests(unittest.TestCase):
    def test_schema_valid_with_four_groups(self):
        out = compose()
        jsonschema.validate(out, SCHEMA)
        self.assertEqual(set(out["groups"]), set(cf.GROUPS))
        kinds = {i["kind"] for i in out["groups"]["facts"]["items"]}
        self.assertEqual(kinds, {"registry_entry", "backtest_gate_verdict", "strategy_pool", "member_virtual_book"})

    def test_missing_subject_fails_closed(self):
        for kw in ({"strategy_family": ""}, {"member_artifact_refs": ()}, {"decision_type": "yolo"}):
            with self.assertRaises(cf.CaseFileError):
                compose(subject(**kw))

    def test_unavailable_owner_recorded_not_silent(self):
        out = compose(down=("/api/registry/entries/",))
        facts = out["groups"]["facts"]
        self.assertEqual(facts["status"], "partial")
        self.assertIn({"owner": "registry:reg-1", "reason": "http_503"}, facts["unavailable"])
        out = compose(down=("/api/search",))
        self.assertEqual(out["groups"]["evidence"]["status"], "unavailable")
        self.assertIn("search_gateway", out["groups"]["evidence"]["reason"])

    def test_lesson_query_and_tags_from_subject(self):
        seen = []
        compose(overrides={"/api/memory/retrieve": lambda p: seen.append(dict(p, _l=p)) or {"hits": []}})
        inst = [p for p in seen if ("scope", "institutional") in p["_l"]][0]["_l"]
        d = dict(inst)
        self.assertIn("momentum", d["query"]); self.assertIn("2330", d["query"])
        tags = [v for k, v in inst if k == "tags"]
        for t in ("family:momentum", "underlying:2330", "instrument:equity", "decision:trial_admission"):
            self.assertIn(t, tags)

    def test_subject_anchors_passed_to_lessons_and_negative_memory(self):
        seen = []
        hit = {"type": "institutional", "relevance_score": 1.0,
               "entry": {"entry_id": "mem-1", "content": {"summary": "s", "tags": ["family:momentum", "negative_memory"]}}}
        other_decision_hit = {"type": "institutional", "relevance_score": 1.0,
                              "entry": {"entry_id": "mem-other-decision", "content": {
                                  "summary": "removal only", "tags": ["family:momentum", "decision:member_removal", "negative_memory"]}}}
        out = compose(overrides={"/api/memory/retrieve": lambda p: seen.append(list(p)) or {
            "hits": [hit, other_decision_hit], "retrieval_mode": "hindsight", "llm_layers": "disabled"}})
        self.assertTrue(seen)
        for params in seen:
            anchors = [v for k, v in params if k == "subject_anchors"]
            self.assertEqual(anchors, ["family:momentum", "underlying:2330", "instrument:equity"])
        items = out["groups"]["lessons"]["items"] + [
            i for i in out["groups"]["similar_history"]["items"] if i["kind"] == "memory_negative_memory"]
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item["source_id"], "mem-1")
            self.assertEqual(item["retrieval_mode"], "hindsight")
            self.assertEqual(item["llm_layers"], "disabled")

    def test_regime_anchor_only_when_subject_carries_it(self):
        self.assertFalse([a for a in cf.subject_anchors(subject()) if a.startswith("regime:")])
        self.assertIn("regime:bull", cf.subject_anchors(subject(regime_tag="Bull")))
        self.assertFalse([a for a in cf.subject_anchors(subject()) if a.startswith("decision:")])

    def _persona_hits(self, params):
        scope = dict(params)["scope"]
        if scope == "institutional":
            return {"hits": []}
        roles = [v for k, v in params if k == "actor_roles"]
        hits = [{"type": "persona", "relevance_score": 2.0, "entry": {
            "memory_id": "pm-private", "persona_id": "persona-a", "relevance_scope": "persona_private",
            "content": {"summary": "private lesson"}}},
            {"type": "persona", "relevance_score": 1.0, "entry": {
                "memory_id": "pm-shared", "persona_id": "persona-a", "relevance_scope": "persona_and_committee",
                "content": {"summary": "shared lesson"}}},
            {"type": "persona", "relevance_score": 3.0, "entry": {
                "memory_id": "pm-other", "persona_id": "persona-b", "relevance_scope": "persona_and_committee",
                "content": {"summary": "other persona"}}}]
        return {"hits": hits, "roles": roles}

    def test_private_only_for_own_persona(self):
        ids = lambda out: {i["source_id"] for i in out["groups"]["lessons"]["items"]}
        op = compose(caller=OP, overrides={"/api/memory/retrieve": self._persona_hits})
        self.assertEqual(ids(op), {"pm-shared"})
        me = compose(caller=SELF, overrides={"/api/memory/retrieve": self._persona_hits})
        self.assertEqual(ids(me), {"pm-private", "pm-shared"})
        self.assertNotIn("pm-other", ids(me))

    def test_bounds_id_digest(self):
        big = {"hits": [{"type": "institutional", "relevance_score": i, "entry": {
            "entry_id": f"e{i}", "content": {"summary": "x" * 900}}} for i in range(20)]}
        with mock.patch.object(cf, "http_json", fake(overrides={"/api/memory/retrieve": big})):
            out = cf.compose_case_file(subject(), OP, OWNERS, budgets=cf.Budgets(3, 50, 200))
        lessons = out["groups"]["lessons"]
        self.assertLessEqual(len(lessons["items"]), 3)
        self.assertTrue(all(len(i["excerpt"]) <= 50 for g in out["groups"].values() for i in g["items"]))
        self.assertLessEqual(sum(len(i["excerpt"]) for g in out["groups"].values() for i in g["items"]), 200)
        self.assertTrue(lessons["truncated"])
        self.assertEqual(out["case_file_id"], "casefile-" + out["digest"][:24])
        from datetime import datetime, timezone
        fixed = lambda: datetime(2026, 11, 13, tzinfo=timezone.utc)
        with mock.patch.object(cf, "http_json", fake()):
            a = cf.compose_case_file(subject(), OP, OWNERS, now=fixed)
            b = cf.compose_case_file(subject(), OP, OWNERS, now=fixed)
        self.assertEqual(a, b)

    def test_hosted_member_entry_pool_weights_and_zero_observation_virtual_book_facts(self):
        ref = "artifact-persona-paper-039ad01f8bcd2b4aa466"
        pid = "persona-2448c7053e33ea48da47"
        binding_id = "pcb-persona-paper-039ad01f8bcd2b4aa466"
        hosted_entry = {
            "registry_id": ref,
            "artifact_type": "execution_bundle",
            "strategy_id": "strategy-persona-039ad01f8bcd2b4aa466",
            "version": "1.0.0",
            "artifact_state": "approved",
            "owner_tenant": "t1",
            "metadata": {
                "market": "TW",
                "tenant_id": "t1",
                "persona_id": pid,
                "execution_context": "paper",
                "persona_capital_binding_id": binding_id,
                "strategy_artifact": {
                    "artifact_id": ref,
                    "strategy_id": "strategy-persona-039ad01f8bcd2b4aa466",
                    "version": "1.0.0",
                    "strategy_logic": {
                        "kind": "close_to_close_momentum",
                        "positive_action": "BUY",
                        "non_positive_action": "SELL",
                    },
                    "parameters": {
                        "market": "TW",
                        "symbols": ["2330.TWSE"],
                        "data_source": "source-ingest:tw_price_daily",
                        "bar_frequency": "1d",
                        "lookback_bars": 2,
                        "momentum_threshold": 0.0,
                        "order_quantity": 1,
                        "quantity_type": "SHARES",
                        "zero_momentum_action": "SELL",
                        "strength_scale_k": 1.0,
                        "strength_floor_momentum": 0.01,
                    },
                },
            },
        }
        hosted_pool = {
            "pool_version": 4,
            "persona_id": pid,
            "tenant_id": "t1",
            "change_reason": "paper pool reweight: refused; virtual_book_source_missing",
            "members": [
                {
                    "artifact_ref": ref,
                    "stage": "active",
                    "risk_weight": 1.0,
                    "checksum": "sha256:" + "d" * 64,
                }
            ],
            "decision": {
                "action": "reweight",
                "status": "refused",
                "reasons": ["virtual_book_source_missing"],
                "health": {
                    "demand_reasons": ["health_inputs_unknown"],
                    "effective_bets": {"status": "unknown", "value": None, "reason": "health_window_missing_or_invalid"},
                },
            },
        }
        hosted_telemetry = {
            "summaries": [
                {
                    "persona_capital_binding_id": binding_id,
                    "tenant_id": "t1",
                    "member_virtual_books": [
                        {
                            "member_id": ref,
                            "artifact_id": ref,
                            "observation_count": 0,
                            "total_return": 0.0,
                            "sharpe_ratio": None,
                            "sharpe_undefined_reason": "insufficient_or_constant_returns",
                            "max_drawdown": 0.0,
                            "marks_complete": False,
                        }
                    ],
                }
            ]
        }
        sub = cf.Subject(
            decision_type="consult",
            persona_id=pid,
            tenant_id="t1",
            strategy_family="MT-1",
            member_artifact_refs=(ref,),
        )
        out = compose(
            sub,
            caller=OP,
            overrides={
                f"/api/registry/entries/{ref}": hosted_entry,
                f"/api/personas/{pid}/strategy-pool/versions": {"versions": [], "refusals": []},
                f"/api/personas/{pid}/strategy-pool": hosted_pool,
                "/api/telemetry/runtime-summaries": hosted_telemetry,
            },
        )
        jsonschema.validate(out, SCHEMA)
        facts = out["groups"]["facts"]
        by_kind = {i["kind"]: json.loads(i["excerpt"]) for i in facts["items"]}
        self.assertEqual(by_kind["registry_entry"]["strategy_family"], "close_to_close_momentum")
        self.assertEqual(by_kind["registry_entry"]["approved_scope"], "paper")
        self.assertEqual(by_kind["registry_entry"]["parameters"]["lookback_bars"], 2)
        self.assertEqual(by_kind["registry_entry"]["parameters"]["symbols"], ["2330.TWSE"])
        self.assertEqual(by_kind["strategy_pool"]["pool_version"], 4)
        self.assertEqual(
            by_kind["strategy_pool"]["members"],
            [{"artifact_ref": ref, "stage": "active", "risk_weight": 1.0}],
        )
        self.assertIn("virtual_book_source_missing", by_kind["strategy_pool"]["pool_health"]["unavailable_reason"])
        self.assertEqual(by_kind["member_virtual_book"]["observation_count"], 0)
        self.assertIn("zero_observations", by_kind["member_virtual_book"]["unavailable_reason"])
        self.assertIn(
            {"owner": f"telemetry:virtual_book:{ref}", "reason": "zero_observations:insufficient_or_constant_returns"},
            facts["unavailable"],
        )

    def test_bounded_owner_reads_and_structured_subject_fields(self):
        calls = []

        def tracking_http(endpoint, path, *, tenant_id, method="GET", body=None, params=None, timeout=10.0):
            calls.append(path)
            if path.startswith("/api/registry/entries/"):
                raise cf.OwnerUnavailable("http_404")
            if "owner-binding" in path:
                return {"status": "done", "gate_verdict": {"passed": True}}
            if path.endswith("/strategy-pool"):
                return {
                    "pool_version": 4,
                    "change_reason": "z" * 1000,
                    "members": [{"artifact_ref": f"m-{i}", "stage": "active", "risk_weight": 0.1} for i in range(20)],
                }
            if path.endswith("/versions"):
                return {"versions": [], "refusals": []}
            if path.startswith("/api/telemetry"):
                return {"summaries": []}
            if path.startswith("/api/memory/retrieve"):
                return {"hits": []}
            if path.startswith("/api/search"):
                return {"retrieval_mode": "hybrid", "results": []}
            return []

        many_sub = subject(
            member_artifact_refs=tuple(f"reg-{i}" for i in range(20)),
            backtest_run_refs=tuple(f"bt-{i}" for i in range(20)),
            strategy_families=tuple(f"fam-{i}" for i in range(20)),
        )
        with mock.patch.object(cf, "http_json", tracking_http):
            out = cf.compose_case_file(many_sub, OP, OWNERS)
        jsonschema.validate(out, SCHEMA)
        reg_reads = [p for p in calls if p.startswith("/api/registry/entries/")]
        bt_reads = [p for p in calls if "owner-binding" in p]
        fam_reads = [p for p in calls if "/api/registry/strategies/" in p]
        self.assertEqual(len(reg_reads), 8)
        self.assertEqual(len(bt_reads), 8)
        self.assertEqual(len(fam_reads), 8)
        facts = out["groups"]["facts"]
        self.assertTrue(facts["truncated"])
        self.assertLessEqual(len(facts["items"]), 8)
        for grp in out["groups"].values():
            for item in grp["items"]:
                self.assertLessEqual(len(item["excerpt"]), 400)
                self.assertLessEqual(len(json.dumps(item["subject_fields"])), 400)
