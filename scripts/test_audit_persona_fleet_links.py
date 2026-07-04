from __future__ import annotations

from scripts import audit_persona_fleet_links as audit


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return self.responses.get(path, audit.HttpResult(path=path, status=404, body={}))


def test_persona_detail_requires_matching_detail_id_even_when_list_contains_row():
    row = {"persona_id": "persona-a", "links": {"detail": "/personas/persona-a"}}
    client = FakeClient(
        {
            "/bff/personas/persona-a": audit.HttpResult(
                path="/bff/personas/persona-a",
                status=200,
                body={"data": {"persona_id": "persona-b"}},
            ),
            "/bff/personas?persona_id=persona-a": audit.HttpResult(
                path="/bff/personas?persona_id=persona-a",
                status=200,
                body={"items": [{"persona_id": "persona-a"}]},
            ),
        }
    )

    checks = audit.validate_persona(row, client)

    assert checks[0]["severity"] == "fail"
    assert checks[0]["status"] == "broken_available"
    assert checks[0]["suggested_target"] == "/bff/personas?persona_id=persona-a"


def test_data_source_list_target_must_contain_matching_persona():
    row = {
        "persona_id": "persona-a",
        "links": {"source_health": "/bff/v5/execution/persona-health?persona_id=persona-a"},
    }
    client = FakeClient(
        {
            "/bff/v5/execution/persona-health": audit.HttpResult(
                path="/bff/v5/execution/persona-health",
                status=200,
                body={"items": [{"persona_id": "persona-b"}]},
            )
        }
    )

    checks = audit.validate_data_source(row, client)

    assert checks[0]["severity"] == "fail"
    assert "did not contain" in checks[0]["message"]


def test_human_gate_stale_route_fails_but_suggests_readiness_blocker():
    row = {
        "persona_id": "persona-a",
        "review": {
            "route": "/bff/management/human-inbox/promotion_review:missing",
            "inbox_id": "promotion_review:missing",
            "requires_human_gate": True,
        },
    }
    readiness = "/bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-a"
    client = FakeClient(
        {
            "/bff/management/human-inbox?page_size=200": audit.HttpResult(
                path="/bff/management/human-inbox?page_size=200",
                status=200,
                body={
                    "items": [
                        {
                            "id": "readiness_blocker:persona:persona-a",
                            "inbox_id": "readiness_blocker:persona:persona-a",
                            "persona_id": "persona-a",
                        }
                    ]
                },
            ),
        }
    )

    checks = audit.validate_human_gate(row, client)

    assert checks[0]["severity"] == "fail"
    assert checks[0]["suggested_target"] == readiness


def test_artifact_summary_without_target_is_warning_not_failure():
    row = {
        "persona_id": "persona-a",
        "research_summary": {"artifact_id": "artifact-a"},
    }
    client = FakeClient({})

    checks = audit.validate_artifact(row, client)

    assert checks[0]["severity"] == "warn"
    assert checks[0]["status"] == "unavailable_with_summary"
    assert checks[0]["expected_id"] == "artifact-a"
    assert client.calls == []


def test_advertised_artifact_target_is_a_failure_when_detail_is_missing():
    row = {
        "persona_id": "persona-a",
        "linkTargets": {"artifact": {"available": True, "href": "/management/artifacts/artifact-a"}},
    }
    client = FakeClient(
        {
            "/bff/artifacts/artifact-a": audit.HttpResult(
                path="/bff/artifacts/artifact-a",
                status=404,
                body={},
            )
        }
    )

    checks = audit.validate_artifact(row, client)

    assert checks[0]["severity"] == "fail"
    assert checks[0]["canonical_target"] == "/bff/artifacts/artifact-a"


def test_performance_summary_without_href_is_warning_not_failure():
    row = {
        "persona_id": "persona-a",
        "perf_delta": 0.12,
        "performance_summary": {"pnl": 1000.0},
    }
    client = FakeClient(
        {
            "/bff/management/performance-attribution/by-persona": audit.HttpResult(
                path="/bff/management/performance-attribution/by-persona",
                status=200,
                body={"data": {"items": [{"persona_id": "unassigned"}]}},
            )
        }
    )

    checks = audit.validate_performance(row, client)

    assert checks[0]["severity"] == "warn"
    assert checks[0]["status"] == "unavailable_with_summary"


def test_audit_rows_aggregates_each_required_category():
    row = {
        "persona_id": "persona-a",
        "links": {"detail": "/personas/persona-a"},
        "research_summary": {"artifact_id": "artifact-a"},
        "linkTargets": {"artifact": {"available": True, "bffHref": "/bff/artifacts/artifact-a"}},
    }
    client = FakeClient(
        {
            "/bff/personas/persona-a": audit.HttpResult(
                path="/bff/personas/persona-a",
                status=200,
                body={"data": {"persona_id": "persona-a"}},
            ),
            "/bff/artifacts/artifact-a": audit.HttpResult(
                path="/bff/artifacts/artifact-a",
                status=200,
                body={"data": {"artifact_id": "artifact-a"}},
            ),
        }
    )

    matrix = audit.audit_rows([row], client)

    assert set(matrix[0]["categories"]) == set(audit.MATRIX_CATEGORIES)
    assert matrix[0]["categories"]["persona"]["status"] == "ok"
    assert matrix[0]["categories"]["artifact"]["status"] == "ok"


def test_available_false_link_target_warns_without_probe():
    row = {
        "persona_id": "persona-a",
        "linkTargets": {
            "performance": {
                "available": False,
                "href": "/management/performance-attribution?persona=persona-a",
                "reason": "no attribution row",
            }
        },
    }
    client = FakeClient({})

    checks = audit.validate_performance(row, client)

    assert checks[0]["severity"] == "warn"
    assert checks[0]["status"] == "unavailable_with_summary"
    assert client.calls == []


def test_available_true_without_href_is_broken_contract():
    row = {"persona_id": "persona-a", "link_targets": {"data-source": {"available": True}}}
    client = FakeClient({})

    checks = audit.validate_data_source(row, client)

    assert checks[0]["severity"] == "fail"
    assert "available=true" in checks[0]["message"]
    assert client.calls == []


def test_bff_href_wins_over_frontend_href_for_research_target():
    row = {
        "persona_id": "persona-a",
        "market_scope": ["TW"],
        "research_summary": {"experiment_id": "exp-a", "stage": "done", "framework": "qlib"},
        "linkTargets": {
            "research": {
                "available": True,
                "href": "/management/experiments/wrong-exp",
                "bffHref": "/bff/research-experiments/exp-a",
            }
        },
    }
    client = FakeClient(
        {
            "/bff/research-experiments/exp-a": audit.HttpResult(
                path="/bff/research-experiments/exp-a",
                status=200,
                body={
                    "data": {
                        "experiment_id": "exp-a",
                        "stage": "done",
                        "parameter_set": {"framework": "qlib", "market": "tw"},
                    }
                },
            )
        }
    )

    checks = audit.validate_research(row, client)

    assert checks[0]["severity"] == "ok"
    assert checks[0]["canonical_target"] == "/bff/research-experiments/exp-a"
    assert client.calls == ["/bff/research-experiments/exp-a"]


def test_management_page_hrefs_map_to_canonical_bff_targets():
    assert audit._canonical_href("/management/experiments/exp-a") == "/bff/research-experiments/exp-a"
    assert (
        audit._canonical_href("/management/performance-attribution?persona=persona-a")
        == "/bff/management/performance-attribution/by-persona?persona_id=persona-a"
    )
    assert (
        audit._canonical_href("/management/data-sources?persona=persona-a&source=finmind")
        == "/bff/v5/execution/persona-health?persona_id=persona-a&source=finmind"
    )
