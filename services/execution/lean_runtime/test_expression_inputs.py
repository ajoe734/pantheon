"""Expression-layer inputs from the pool policy and the published ledger truth."""

from __future__ import annotations

import unittest
import urllib.error
from typing import Any

from services.capital.risk_policy import RiskPolicy
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


def _pool(**overrides: Any) -> dict[str, Any]:
    pool = {
        "pool_id": "pool-1",
        "status": "active",
        "currency": "TWD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "metadata": {"risk_policy": dict(POLICY)},
    }
    pool.update(overrides)
    return pool


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


if __name__ == "__main__":
    unittest.main()
