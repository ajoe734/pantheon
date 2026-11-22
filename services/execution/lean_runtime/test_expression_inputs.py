"""Expression-layer inputs from the pool policy and the published ledger truth."""

from __future__ import annotations

import json
import os
import unittest
import urllib.error
from typing import Any, Mapping
from unittest.mock import patch

from services.capital.risk_policy import (
    RiskPolicy,
    capital_pool_approval_digest,
    resolve_pool_risk_policy_binding,
    risk_policy_digest,
)
from services.execution.lean_runtime.expression_inputs import (
    CapitalPoolReadError,
    CapitalPoolReader,
    ExpressionInputsProvider,
    ExpressionInputsUnavailable,
    RuntimeSummaryReader,
    build_expression_inputs,
    capital_pool_reader_from_env,
    runtime_summary_reader_from_env,
)

def _compose_environment(service_name: str, token_file: str | None) -> dict[str, str]:
    """The producer/reconciler environment exactly as docker-compose.yml and the issuer set it."""
    import re
    from pathlib import Path

    import yaml

    from scripts.issue_dev_paper_principals import issue_environment

    root = Path(__file__).resolve().parents[3]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    issued = issue_environment(
        {
            "PANTHEON_ENV": "dev",
            "PANTHEON_DEV_BFF_TENANT_ID": "tenant-dev",
            "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
            "PANTHEON_DEV_BFF_JWT_SECRET": "compose-environment-key-" * 2,
            "PANTHEON_DEV_BFF_JWT_ISSUER": "compose-issuer",
            "PANTHEON_DEV_BFF_JWT_AUDIENCE": "compose-audience",
        },
        now=2_000_000_000,
    )
    env: dict[str, str] = {}
    for name, raw in compose["services"][service_name]["environment"].items():
        match = re.match(r"^\$\{([A-Z0-9_]+):-(.*)\}$", str(raw))
        env[name] = (match.group(2) or issued.get(match.group(1), "")) if match else str(raw)
    assert env["DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE"] == "/run/pantheon-principals/DEPLOYMENT_CAPITAL_SERVICE_TOKEN"
    if token_file is None:
        env.pop("DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE")
    else:
        # The mounted principals directory, relocated into a temporary directory.
        env["DEPLOYMENT_CAPITAL_SERVICE_TOKEN_FILE"] = token_file
    return env


POLICY = {
    "risk_policy_id": "rp-pool-1",
    "version": "1.0",
    "status": "active",
    "allowed_stages": ["paper"],
    "gross_limit": 2.0,
    "net_limit": 2.0,
    "max_leverage": 2.0,
    "allowed_asset_classes": ["equity", "future"],
}


def _ensure_default_pool_owner_transport(pool_snapshot: Mapping[str, Any]) -> None:
    from services.capital import risk_policy as _rp_mod
    from services.governance.test_approval_authority import SnapshotApprovalReader

    current_http = getattr(_rp_mod, "_capital_owner_http_get_json", None)
    if callable(current_http) and not getattr(current_http, "_pool_1_fixture_wrapped", False):
        base_http = current_http

        def _wrapped_owner_http(url: str, headers: Mapping[str, str], timeout: float) -> Any:
            if url.rstrip("/") in {
                "http://capital/api/capital-pools/pool-1/approvals/apv-pool-1",
                "http://capital:8092/api/capital-pools/pool-1/approvals/apv-pool-1",
            }:
                return _approval_evidence_json(pool_snapshot, decision_id="apv-pool-1")
            return base_http(url, headers, timeout)

        setattr(_wrapped_owner_http, "_pool_1_fixture_wrapped", True)
        setattr(_rp_mod, "_capital_owner_http_get_json", _wrapped_owner_http)

    if not getattr(getattr(_rp_mod, "configured_approval_reader", None), "_pool_1_fixture_reader", False):

        def _fixture_approval_reader(_domain: str = "capital", **_kwargs: Any) -> Any:
            return SnapshotApprovalReader(_approval_evidence_json(pool_snapshot, decision_id="apv-pool-1"))

        setattr(_fixture_approval_reader, "_pool_1_fixture_reader", True)
        setattr(_rp_mod, "configured_approval_reader", _fixture_approval_reader)


def _pool(**overrides: Any) -> dict[str, Any]:
    pool: dict[str, Any] = {
        "pool_id": "pool-1",
        "tenant_id": "default",
        "owner_id": "default",
        "owner_type": "org",
        "status": "active",
        "currency": "TWD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "metadata": {
            "risk_policy": dict(POLICY),
            "requested_risk_profile": "low",
            "require_approved_profile_mapping": True,
            "profile_mapping_approval_id": "apv-pool-1",
        },
    }
    pool.update(overrides)
    if "approval_digest" not in pool and isinstance(pool.get("metadata"), Mapping):
        pool["approval_digest"] = capital_pool_approval_digest(pool)
    snapshot = json.loads(json.dumps(pool))
    meta = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), Mapping) else {}
    if snapshot.get("pool_id") == "pool-1" and meta.get("profile_mapping_approval_id") == "apv-pool-1":
        _ensure_default_pool_owner_transport(snapshot)
    return snapshot


def _approval_evidence_json(
    pool: Mapping[str, Any] | None = None,
    *,
    decision_id: str | None = None,
) -> dict[str, Any]:
    from services.governance.test_approval_authority import approval_snapshot

    target_pool = dict(pool or _pool())
    meta = dict(target_pool.get("metadata") or {})
    policy = dict(meta.get("risk_policy") or POLICY)
    pool_id = str(target_pool.get("pool_id") or "pool-1")
    tenant_id = str(target_pool.get("tenant_id") or "default")
    resolved_decision_id = str(
        decision_id
        or meta.get("profile_mapping_approval_id")
        or f"apv-capital-pool-{pool_id}"
    )
    target_version = str(
        target_pool.get("approval_digest")
        or capital_pool_approval_digest(target_pool)
    )
    return json.loads(
        json.dumps(
            approval_snapshot(
                decision_id=resolved_decision_id,
                tenant_id=tenant_id,
                owner_user_id="proposer-1",
                target_type="capital_pool_activation",
                target_id=pool_id,
                target_version=target_version,
                metadata={
                    "subject": {
                        "pool_id": pool_id,
                        "risk_direction": "increase",
                        "risk_policy_id": str(policy.get("risk_policy_id") or "rp-pool-1"),
                        "risk_policy_version": str(policy.get("version") or "1.0"),
                        "risk_policy_digest": risk_policy_digest(policy),
                        "requested_risk": str(meta.get("requested_risk_profile") or "low"),
                    },
                    "approvals": [
                        {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                        {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
                    ],
                },
            )
        )
    )


def _summary(**overrides: Any) -> dict[str, Any]:
    summary = {
        "runtime_id": "rt-1",
        "binding_id": "rb-1",
        "ledger_fill_count": 0,
        "executed_trade_count": 0,
        "positions": None,
    }
    summary.update(overrides)
    return summary


def _binding() -> dict[str, Any]:
    return {"binding_id": "rb-1", "runtime_id": "rt-1", "capital_pool_id": "pool-1"}


class BuildExpressionInputsTests(unittest.TestCase):
    def _policy(self, **overrides: Any) -> RiskPolicy:
        return RiskPolicy.from_mapping({**POLICY, **overrides})

    def test_fresh_ledger_uses_the_pool_budget_and_policy_multiples(self) -> None:
        inputs = build_expression_inputs(pool=_pool(), policy=self._policy(), summary=_summary())
        budget = inputs["risk_budget"]
        self.assertEqual(budget["equity"], 100000.0)
        self.assertEqual(budget["available_cash"], 100000.0)
        self.assertEqual(budget["gross_limit"], 200000.0)
        self.assertEqual(budget["gross_used"], 0.0)
        self.assertEqual(budget["max_leverage"], 2.0)
        self.assertEqual(inputs["persona_book"], {"positions": [], "gross_notional": 0.0})

    def test_the_limit_is_the_policy_multiple_not_a_constant(self) -> None:
        inputs = build_expression_inputs(
            pool=_pool(budget=50000.0), policy=self._policy(gross_limit=3.5), summary=_summary()
        )
        self.assertEqual(inputs["risk_budget"]["gross_limit"], 175000.0)

    def test_cash_share_position_and_ledger_cash(self) -> None:
        summary = _summary(
            ledger_fill_count=1,
            executed_trade_count=1,
            ledger_cash=55650.0,
            positions=[
                {
                    "symbol": "2330.TWSE",
                    "quantity": 20.0,
                    "average_price": 2235.0,
                    "mark_price": 2300.0,
                    "market_value": 46000.0,
                    "currency": "TWD",
                }
            ],
        )
        inputs = build_expression_inputs(pool=_pool(), policy=self._policy(), summary=summary)
        self.assertEqual(
            inputs["persona_book"]["positions"],
            [
                {
                    "underlying": "2330",
                    "instrument_id": "eq:2330",
                    "kind": "cash_share",
                    "direction": "LONG",
                    "units": 20,
                    "multiplier": 1.0,
                    "notional": 46000.0,
                }
            ],
        )
        self.assertEqual(inputs["persona_book"]["gross_notional"], 46000.0)
        budget = inputs["risk_budget"]
        self.assertEqual(budget["equity"], 101650.0)  # cash + holdings value
        self.assertEqual(budget["available_cash"], 55650.0)
        self.assertEqual(budget["gross_used"], 46000.0)
        self.assertEqual(budget["gross_limit"], 2.0 * 101650.0)

    def test_stock_future_position_reserves_margin_and_adds_unrealized(self) -> None:
        summary = _summary(
            ledger_fill_count=1,
            executed_trade_count=1,
            ledger_cash=100000.0,
            positions=[
                {
                    "symbol": "2330.TWSE",
                    "instrument_id": "ssf:QFF:202611",
                    "kind": "stock_future",
                    "quantity": -1.0,
                    "multiplier": 100.0,
                    "average_price": 2300.0,
                    "mark_price": 2290.0,
                    "unrealized_pnl": 1000.0,
                    "margin_reserved": 30000.0,
                }
            ],
        )
        inputs = build_expression_inputs(pool=_pool(), policy=self._policy(), summary=summary)
        [position] = inputs["persona_book"]["positions"]
        self.assertEqual(position["instrument_id"], "ssf:QFF:202611")
        self.assertEqual(position["direction"], "SHORT")
        self.assertEqual(position["units"], 1)
        budget = inputs["risk_budget"]
        self.assertEqual(budget["equity"], 101000.0)
        self.assertEqual(budget["margin_reserved"], 30000.0)
        self.assertEqual(budget["available_cash"], 71000.0)
        self.assertEqual(budget["gross_used"], 229000.0)

    def test_stale_mark_still_prices_the_position(self) -> None:
        summary = _summary(
            ledger_fill_count=1,
            executed_trade_count=1,
            ledger_cash=50000.0,
            positions=[
                {"symbol": "0050.TWSE", "quantity": 100.0, "mark_stale": True, "last_known_mark_price": 92.5}
            ],
        )
        inputs = build_expression_inputs(pool=_pool(), policy=self._policy(), summary=summary)
        self.assertEqual(inputs["persona_book"]["gross_notional"], 9250.0)

    def test_fills_without_ledger_cash_are_unavailable(self) -> None:
        summary = _summary(ledger_fill_count=2, executed_trade_count=2, positions=[])
        with self.assertRaises(ExpressionInputsUnavailable) as caught:
            build_expression_inputs(pool=_pool(), policy=self._policy(), summary=summary)
        self.assertEqual(caught.exception.missing, "ledger")

    def test_projection_fill_gap_is_unavailable(self) -> None:
        summary = _summary(
            ledger_fill_count=3,
            positions=[],
            positions_status="unavailable",
            positions_unavailable_reason="fill_event_gap: runtime ledger fill_count=3 but 1 fill event(s) projected",
        )
        with self.assertRaises(ExpressionInputsUnavailable) as caught:
            build_expression_inputs(pool=_pool(), policy=self._policy(), summary=summary)
        self.assertEqual(caught.exception.missing, "ledger")
        self.assertIn("fill_event_gap", caught.exception.detail)

    def test_an_unpriced_position_is_unavailable(self) -> None:
        summary = _summary(
            ledger_fill_count=1,
            executed_trade_count=1,
            ledger_cash=1000.0,
            positions=[{"symbol": "2330.TWSE", "quantity": 1.0}],
        )
        with self.assertRaises(ExpressionInputsUnavailable) as caught:
            build_expression_inputs(pool=_pool(), policy=self._policy(), summary=summary)
        self.assertEqual(caught.exception.missing, "ledger")

    def test_missing_policy_limit_and_missing_budget_are_named(self) -> None:
        no_limit = RiskPolicy.from_mapping({k: v for k, v in POLICY.items() if k != "gross_limit"})
        with self.assertRaises(ExpressionInputsUnavailable) as caught:
            build_expression_inputs(pool=_pool(), policy=no_limit, summary=_summary())
        self.assertEqual(caught.exception.missing, "risk_policy")
        for budget in (None, 0, -5.0, True):
            with self.subTest(budget=budget), self.assertRaises(ExpressionInputsUnavailable) as caught:
                build_expression_inputs(pool=_pool(budget=budget), policy=self._policy(), summary=_summary())
            self.assertEqual(caught.exception.missing, "pool_budget")

    def test_non_positive_equity_is_unavailable(self) -> None:
        summary = _summary(
            ledger_fill_count=1,
            executed_trade_count=1,
            ledger_cash=-500.0,
            positions=[],
        )
        with self.assertRaises(ExpressionInputsUnavailable):
            build_expression_inputs(pool=_pool(), policy=self._policy(), summary=summary)


class ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        from services.capital import risk_policy as _rp_mod

        super().setUp()
        env_patcher = patch.dict(
            os.environ,
            {
                "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
                "DEPLOYMENT_CAPITAL_SERVICE_TOKEN": "capital-token",
                "PANTHEON_TENANT_ID": "default",
            },
            clear=False,
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        def fetch_owner_approval(url: str, headers: Mapping[str, str], timeout: float) -> Any:
            self.assertEqual(headers["Authorization"], "Bearer capital-token")
            self.assertEqual(headers["X-Pantheon-Service"], "runtime-manager")
            self.assertTrue(url.endswith("/api/capital-pools/pool-1/approvals/apv-pool-1"))
            return _approval_evidence_json()

        http_patcher = patch.object(_rp_mod, "_capital_owner_http_get_json", fetch_owner_approval)
        http_patcher.start()
        self.addCleanup(http_patcher.stop)

    def test_provider_reads_the_pool_policy_through_the_governed_resolver(self) -> None:
        pools: list[str] = []

        def reader(pool_id: str) -> dict[str, Any]:
            pools.append(pool_id)
            return _pool()

        provider = ExpressionInputsProvider(
            capital_pool_reader=reader, runtime_summary_reader=lambda rid: _summary(runtime_id=rid)
        )
        inputs = provider(_binding())
        self.assertEqual(pools, ["pool-1"])
        self.assertEqual(inputs["risk_budget"]["gross_limit"], 200000.0)

    def test_each_missing_input_is_named(self) -> None:
        def boom(_: str) -> Any:
            raise CapitalPoolReadError("capital down")

        def summary(_: str) -> Any:
            raise ExpressionInputsUnavailable("ledger", "telemetry down")

        cases = {
            "capital_pool": ExpressionInputsProvider(capital_pool_reader=boom, runtime_summary_reader=lambda _: _summary()),
            "capital_pool ": ExpressionInputsProvider(capital_pool_reader=lambda _: None, runtime_summary_reader=lambda _: _summary()),
            "risk_policy": ExpressionInputsProvider(
                capital_pool_reader=lambda _: _pool(metadata={}), runtime_summary_reader=lambda _: _summary()
            ),
            "ledger": ExpressionInputsProvider(capital_pool_reader=lambda _: _pool(), runtime_summary_reader=summary),
        }
        for expected, provider in cases.items():
            with self.subTest(expected=expected), self.assertRaises(ExpressionInputsUnavailable) as caught:
                provider(_binding())
            self.assertEqual(caught.exception.missing, expected.strip())

    def test_inactive_policy_is_unavailable(self) -> None:
        pool = _pool(metadata={"risk_policy": {**POLICY, "status": "retired"}})
        provider = ExpressionInputsProvider(
            capital_pool_reader=lambda _: pool, runtime_summary_reader=lambda _: _summary()
        )
        with self.assertRaises(ExpressionInputsUnavailable) as caught:
            provider(_binding())
        self.assertEqual(caught.exception.missing, "risk_policy")

    def test_unconfigured_readers_name_their_input(self) -> None:
        with self.assertRaises(ExpressionInputsUnavailable) as caught:
            ExpressionInputsProvider(capital_pool_reader=None, runtime_summary_reader=None)(_binding())
        self.assertEqual(caught.exception.missing, "capital_pool")
        with self.assertRaises(ExpressionInputsUnavailable) as caught:
            ExpressionInputsProvider(capital_pool_reader=lambda _: _pool(), runtime_summary_reader=None)(_binding())
        self.assertEqual(caught.exception.missing, "ledger")
        with self.assertRaises(ExpressionInputsUnavailable) as caught:
            ExpressionInputsProvider(capital_pool_reader=lambda _: _pool(), runtime_summary_reader=lambda _: _summary())(
                {"binding_id": "rb-1", "runtime_id": "rt-1"}
            )
        self.assertEqual(caught.exception.missing, "capital_pool")


class HttpReaderTests(unittest.TestCase):
    ENV = {
        "PANTHEON_CAPITAL_API_URL": "http://capital:8092/",
        "DEPLOYMENT_CAPITAL_SERVICE_TOKEN": "capital-token",
        "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
        "PANTHEON_TELEMETRY_SERVICE_TOKEN": "telemetry-token",
        "PANTHEON_TENANT_ID": "tenant-a",
    }

    def test_capital_reader_urls_headers_and_cache(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fetch(url: str, headers: Any, timeout: float) -> Any:
            calls.append((url, dict(headers)))
            return _pool()

        reader = capital_pool_reader_from_env(self.ENV, fetch_json=fetch)
        assert reader is not None
        self.assertEqual(reader("pool/1")["pool_id"], "pool-1")
        reader("pool/1")
        self.assertEqual(len(calls), 1)  # cached
        url, headers = calls[0]
        self.assertEqual(url, "http://capital:8092/api/capital-pools/pool%2F1")
        self.assertEqual(headers["Authorization"], "Bearer capital-token")
        self.assertEqual(headers["X-Tenant-Id"], "tenant-a")

    def test_capital_reader_failure_modes(self) -> None:
        def missing(url: str, headers: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)

        def denied(url: str, headers: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(url, 403, "denied", {}, None)

        def down(url: str, headers: Any, timeout: float) -> Any:
            raise OSError("refused")

        self.assertIsNone(capital_pool_reader_from_env(self.ENV, fetch_json=missing)("p"))
        for fetch in (denied, down, lambda *_: ["not", "an", "object"]):
            with self.assertRaises(CapitalPoolReadError):
                capital_pool_reader_from_env(self.ENV, fetch_json=fetch)("p")

    def test_unconfigured_environment_builds_no_reader(self) -> None:
        self.assertIsNone(capital_pool_reader_from_env({}))
        self.assertIsNone(capital_pool_reader_from_env({"PANTHEON_CAPITAL_API_URL": "http://capital:8092"}))
        self.assertIsNone(runtime_summary_reader_from_env({}))

    def test_summary_reader_maps_every_failure_to_the_ledger_input(self) -> None:
        seen: list[str] = []

        def ok(url: str, headers: Any, timeout: float) -> Any:
            seen.append(url)
            assert headers["Authorization"] == "Bearer telemetry-token"
            assert headers["X-Tenant-Id"] == "tenant-a"
            return _summary()

        reader = runtime_summary_reader_from_env(self.ENV, fetch_json=ok)
        assert isinstance(reader, RuntimeSummaryReader)
        self.assertEqual(reader("rt-1")["runtime_id"], "rt-1")
        self.assertEqual(seen, ["http://telemetry:8083/api/telemetry/runtime-summaries/rt-1"])

        def not_found(url: str, headers: Any, timeout: float) -> Any:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)

        for fetch in (not_found, lambda *_: (_ for _ in ()).throw(OSError("down")), lambda *_: "text"):
            with self.assertRaises(ExpressionInputsUnavailable) as caught:
                runtime_summary_reader_from_env(self.ENV, fetch_json=fetch)("rt-1")
            self.assertEqual(caught.exception.missing, "ledger")
        with self.assertRaises(ExpressionInputsUnavailable):
            reader("")


class ProducerComposeEnvironmentTest(unittest.TestCase):
    """Build the provider the way the entrypoint does, from the compose environment."""

    def _provider(self, service: str, file_present: bool) -> ExpressionInputsProvider:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "DEPLOYMENT_CAPITAL_SERVICE_TOKEN"
            if file_present:
                path.write_text("issued-capital-reader-token", encoding="utf-8")
                path.chmod(0o600)  # the issuer writes principal files 0600
            env = _compose_environment(service, str(path))
            env["PANTHEON_PAPER_EXPRESSION_LAYER"] = "on"
            from services.execution.lean_runtime.expression_inputs import build_production_expression_inputs

            return build_production_expression_inputs(env)

    def test_mounted_principal_configures_capital_and_ledger_readers(self) -> None:
        provider = self._provider("paper-signal-producer", True)
        self.assertTrue(provider.capital_reader_configured)
        self.assertTrue(provider.ledger_reader_configured)

    def test_absent_mount_reproduces_the_hosted_capital_service_not_configured(self) -> None:
        provider = self._provider("paper-signal-producer", False)
        self.assertFalse(provider.capital_reader_configured)
        with self.assertRaises(ExpressionInputsUnavailable) as caught:
            provider({"capital_pool_id": "pool-1", "runtime_id": "rt-1"})
        self.assertEqual(caught.exception.missing, "capital_pool")

    def test_reconciler_environment_resolves_the_same_principal(self) -> None:
        self.assertTrue(self._provider("paper-fleet-reconciler", True).capital_reader_configured)

    def test_composed_producer_and_reconciler_environments_resolve_json_pool_and_owner_approval_without_callable_metadata(
        self,
    ) -> None:
        import tempfile
        from pathlib import Path
        from services.capital import risk_policy as _rp_mod
        from services.execution.lean_runtime import expression_inputs as _ei_mod

        for service in ("paper-signal-producer", "paper-fleet-reconciler"):
            with self.subTest(service=service), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "DEPLOYMENT_CAPITAL_SERVICE_TOKEN"
                path.write_text("issued-capital-reader-token", encoding="utf-8")
                path.chmod(0o600)
                env = _compose_environment(service, str(path))
                env["PANTHEON_PAPER_EXPRESSION_LAYER"] = "on"
                self.assertEqual([k for k in env if "GOVERNANCE" in k], [])

                tenant_id = "tenant-dev"
                pool_json = _pool(
                    pool_id="pool-review",
                    tenant_id=tenant_id,
                    owner_id=tenant_id,
                    metadata={
                        "risk_policy": dict(POLICY, risk_policy_id="rp-pool-review"),
                        "requested_risk_profile": "low",
                        "require_approved_profile_mapping": True,
                        "profile_mapping_approval_id": "apv-review",
                    },
                )
                self.assertNotIn("approval_reader", pool_json["metadata"])
                approval_json = _approval_evidence_json(pool_json, decision_id="apv-review")
                seen_approval_urls: list[str] = []

                def fetch_capital_or_summary(url: str, headers: Mapping[str, str], timeout: float) -> Any:
                    if "/api/capital-pools/" in url:
                        self.assertEqual(headers["Authorization"], "Bearer issued-capital-reader-token")
                        return pool_json
                    if "/api/telemetry/runtime-summaries/" in url:
                        return _summary(runtime_id="rt-review")
                    raise AssertionError(url)

                def fetch_owner_approval(url: str, headers: Mapping[str, str], timeout: float) -> Any:
                    seen_approval_urls.append(url)
                    self.assertEqual(headers["Authorization"], "Bearer issued-capital-reader-token")
                    self.assertEqual(headers["X-Pantheon-Service"], "runtime-manager")
                    self.assertEqual(headers["X-Tenant-Id"], tenant_id)
                    if url.endswith("/api/capital-pools/pool-review/approvals/apv-review"):
                        return approval_json
                    raise urllib.error.HTTPError(url, 404, "not found", {}, None)

                with patch.dict(os.environ, env, clear=True), patch.object(
                    _ei_mod, "_http_get_json", fetch_capital_or_summary
                ), patch.object(_rp_mod, "_capital_owner_http_get_json", fetch_owner_approval):
                    res = resolve_pool_risk_policy_binding(pool=pool_json)
                    self.assertTrue(res.resolved, res.reason)
                    self.assertEqual(res.profile_mapping_status, "approved_profile_mapping")
                    self.assertEqual(res.profile_mapping_approval_id, "apv-review")

                    provider = _ei_mod.build_production_expression_inputs(env)
                    inputs = provider({"capital_pool_id": "pool-review", "runtime_id": "rt-review"})
                    self.assertEqual(inputs["risk_budget"]["gross_limit"], 200000.0)
                    self.assertTrue(
                        seen_approval_urls
                        and all(
                            u == "http://capital:8092/api/capital-pools/pool-review/approvals/apv-review"
                            for u in seen_approval_urls
                        )
                    )

                def deny_owner_approval(url: str, headers: Mapping[str, str], timeout: float) -> Any:
                    raise urllib.error.HTTPError(url, 404, "not found", {}, None)

                with patch.dict(os.environ, env, clear=True), patch.object(
                    _ei_mod, "_http_get_json", fetch_capital_or_summary
                ), patch.object(_rp_mod, "_capital_owner_http_get_json", deny_owner_approval):
                    denied = resolve_pool_risk_policy_binding(pool=pool_json)
                    self.assertFalse(denied.resolved)
                    self.assertEqual(denied.reason_code, "risk_policy_profile_mapping_unapproved")
                    provider_denied = _ei_mod.build_production_expression_inputs(env)
                    with self.assertRaises(ExpressionInputsUnavailable) as caught:
                        provider_denied({"capital_pool_id": "pool-review", "runtime_id": "rt-review"})
                    self.assertEqual(caught.exception.missing, "risk_policy")
                    self.assertIn("risk_policy_profile_mapping_unapproved", caught.exception.detail)

    def test_build_expression_inputs_surfaces_policy_identity_net_single_name_reserve_and_reservations(self) -> None:
        policy = RiskPolicy.from_mapping(
            {
                **POLICY,
                "version": "1.2",
                "gross_limit": 1.8,
                "net_limit": 1.2,
                "max_leverage": 1.5,
                "max_single_name_weight": 0.30,
                "min_cash_reserve_pct": 0.10,
            }
        )
        summary = _summary(
            ledger_fill_count=1,
            executed_trade_count=1,
            ledger_cash=80000.0,
            positions=[
                {
                    "symbol": "2317.TWSE",
                    "quantity": 100.0,
                    "average_price": 200.0,
                    "mark_price": 200.0,
                    "market_value": 20000.0,
                    "currency": "TWD",
                }
            ],
            order_reservations=[
                {
                    "reservation_id": "res-competing-1",
                    "symbol": "2330.TW",
                    "side": "buy",
                    "kind": "cash_share",
                    "quantity": 10,
                    "price": 1000.0,
                    "notional": 10000.0,
                    "cash_reserved": 10014.25,
                }
            ],
        )
        inputs = build_expression_inputs(pool=_pool(budget=100000.0), policy=policy, summary=summary)
        budget = inputs["risk_budget"]
        self.assertEqual(budget["risk_policy_id"], "rp-pool-1")
        self.assertEqual(budget["risk_policy_version"], "1.2")
        self.assertEqual(budget["risk_policy_digest"], risk_policy_digest(policy))
        self.assertEqual(budget["equity"], 100000.0)
        self.assertEqual(budget["raw_cash"], 80000.0)
        self.assertEqual(budget["cash_reserved"], 10014.25)
        self.assertEqual(budget["cash_reserve_required"], 10000.0)
        self.assertEqual(budget["available_cash"], 59985.75)
        self.assertEqual(budget["available_buying_power"], 59985.75)
        self.assertEqual(budget["gross_limit"], 180000.0)
        self.assertEqual(budget["gross_used"], 30000.0)
        self.assertEqual(budget["net_limit"], 120000.0)
        self.assertEqual(budget["net_used"], 30000.0)
        self.assertEqual(budget["signed_net_used"], 30000.0)
        self.assertEqual(budget["max_leverage"], 1.5)
        self.assertEqual(budget["max_single_name_weight"], 0.30)
        self.assertEqual(budget["max_single_name_notional"], 30000.0)
        self.assertEqual(budget["underlying_exposures"], {"2317": 20000.0, "2330": 10000.0})
        self.assertEqual(len(budget["order_reservations"]), 1)
        self.assertEqual(set(inputs["persona_book"].keys()), {"positions", "gross_notional"})


if __name__ == "__main__":
    unittest.main()
