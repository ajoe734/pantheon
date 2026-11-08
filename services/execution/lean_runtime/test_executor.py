import unittest

from services.execution.lean_runtime.executor import (
    BRACKET_ORDER_STATUS_LOGGED_ONLY,
    BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER,
    ExecutionError,
    _build_bracket_legs,
    _seed_signal_market_price,
    _signal_context_metadata,
    execute,
)


class _Security:
    Price = 100.0


class _Holding:
    Quantity = 0.0


class _SpyAlgo:
    def __init__(self):
        self.Portfolio = {"AAPL": _Holding()}
        self.Securities = {"AAPL": _Security()}
        self.market_orders = []
        self.limit_orders = []
        self.stop_market_orders = []
        self.bracket_logs = []
        self.order_rejections = []
        self.signal_noops = []
        self.current_signal_context = {}
        self.signal_contexts = []

    def MarketOrder(self, symbol, quantity):  # noqa: N802
        self.market_orders.append((symbol, quantity))
        self.signal_contexts.append(dict(self.current_signal_context))

    def LimitOrder(self, symbol, quantity, limit_price):  # noqa: N802
        self.limit_orders.append((symbol, quantity, limit_price))

    def StopMarketOrder(self, symbol, quantity, stop_price):  # noqa: N802
        self.stop_market_orders.append((symbol, quantity, stop_price))

    def RecordBracketOrderLogged(  # noqa: N802
        self,
        symbol,
        *,
        signal_id,
        stop_loss_pct,
        take_profit_pct,
        broker_submission_status,
        submitted_to_broker,
        metadata=None,
    ):
        payload = {
            "symbol": symbol,
            "signal_id": signal_id,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "broker_submission_status": broker_submission_status,
            "submitted_to_broker": submitted_to_broker,
        }
        if metadata is not None:
            payload["metadata"] = metadata
        self.bracket_logs.append(payload)

    def RecordOrderRejected(  # noqa: N802
        self,
        symbol,
        *,
        signal_id,
        reject_reason,
        requested_quantity,
        computed_quantity,
        quantity_type,
        order_type,
        broker_submission_status,
        submitted_to_broker,
        price=None,
    ):
        payload = {
            "symbol": symbol,
            "signal_id": signal_id,
            "reject_reason": reject_reason,
            "requested_quantity": requested_quantity,
            "computed_quantity": computed_quantity,
            "quantity_type": quantity_type,
            "order_type": order_type,
            "broker_submission_status": broker_submission_status,
            "submitted_to_broker": submitted_to_broker,
        }
        if price is not None:
            payload["price"] = price
        self.order_rejections.append(payload)

    def RecordSignalNoop(  # noqa: N802
        self,
        symbol,
        *,
        signal_id,
        noop_reason,
        requested_quantity,
        quantity_type,
        order_type,
        broker_submission_status,
        submitted_to_broker,
        computed_quantity=None,
        price=None,
        metadata=None,
    ):
        payload = {
            "symbol": symbol,
            "signal_id": signal_id,
            "noop_reason": noop_reason,
            "requested_quantity": requested_quantity,
            "computed_quantity": computed_quantity,
            "quantity_type": quantity_type,
            "order_type": order_type,
            "broker_submission_status": broker_submission_status,
            "submitted_to_broker": submitted_to_broker,
        }
        if price is not None:
            payload["price"] = price
        if metadata is not None:
            payload["metadata"] = metadata
        self.signal_noops.append(payload)

    def SetHoldings(self, symbol, target_percent):  # noqa: N802
        raise AssertionError("SetHoldings should not be used by this signal")

    def Liquidate(self, symbol):  # noqa: N802
        raise AssertionError("Liquidate should not be used by this signal")

    def SetCurrentSignalContext(self, metadata):  # noqa: N802
        self.current_signal_context = dict(metadata)

    def ClearCurrentSignalContext(self):  # noqa: N802
        self.current_signal_context = {}


class _GuardedPaperAlgo(_SpyAlgo):
    DeploymentStage = "paper"
    BracketOrderExecutionEnabled = True

    def __init__(self):
        super().__init__()
        self.bracket_submissions = []

    def MarketOrder(self, symbol, quantity):  # noqa: N802
        super().MarketOrder(symbol, quantity)
        self.Portfolio[symbol].Quantity += quantity

    def SubmitBracketOrder(  # noqa: N802
        self,
        symbol,
        *,
        signal_id,
        legs,
        guard_stage,
        broker_submission_status,
        submitted_to_broker,
    ):
        submission = {
            "symbol": symbol,
            "signal_id": signal_id,
            "legs": legs,
            "guard_stage": guard_stage,
            "broker_submission_status": broker_submission_status,
            "submitted_to_broker": submitted_to_broker,
        }
        self.bracket_submissions.append(submission)
        return {"submission_id": "bracket-001", "legs": legs}


class _GuardedPaperFallbackAlgo(_SpyAlgo):
    DeploymentStage = "paper"
    BracketOrderExecutionEnabled = True

    def MarketOrder(self, symbol, quantity):  # noqa: N802
        super().MarketOrder(symbol, quantity)
        self.Portfolio[symbol].Quantity += quantity


class _MissingLimitPaperAlgo(_GuardedPaperFallbackAlgo):
    LimitOrder = None


class _LiveAlgo(_GuardedPaperAlgo):
    DeploymentStage = "live"


class _SimAlgo(_GuardedPaperAlgo):
    DeploymentStage = "sim"


class ExecutorBracketOrderTests(unittest.TestCase):
    def test_signal_context_metadata_carries_correlation_envelope(self):
        envelope = {
            "schema_version": "1.0",
            "journey_id": "journey-001",
            "event_id": "signal-event-001",
        }

        context = _signal_context_metadata(
            {
                "signal_id": "sig-envelope-001",
                "metadata": {"correlation_envelope": envelope},
            }
        )

        self.assertEqual(context["correlation_envelope"], envelope)

    def test_signal_timestamp_and_unscoped_price_are_execution_only(self):
        calls = []

        class Algo:
            def SetSecurityPrice(self, symbol, price):  # noqa: N802
                calls.append(("price", symbol, price))

            def SetSecurityMark(self, symbol, price, *, as_of, source):  # noqa: N802
                calls.append(("mark", symbol, price, as_of, source))

        signal = {
            "timestamp": "2026-07-14T12:00:00Z",
            "metadata": {"market_price": 101.5},
        }

        _seed_signal_market_price(Algo(), "AAPL", signal)

        self.assertEqual(calls, [("price", "AAPL", 101.5)])
        context = _signal_context_metadata(signal)
        self.assertNotIn("market_price_as_of", context)
        self.assertNotIn("market_price_source", context)

    def test_dedicated_market_evidence_can_seed_an_authoritative_mark(self):
        calls = []

        class Algo:
            def SetSecurityPrice(self, symbol, price):  # noqa: N802
                calls.append(("price", symbol, price))

            def SetSecurityMark(self, symbol, price, *, as_of, source):  # noqa: N802
                calls.append(("mark", symbol, price, as_of, source))

        signal = {
            "timestamp": "2026-07-14T12:00:10Z",
            "metadata": {
                "market_price": 999.0,
                "market_data": {
                    "close": 101.5,
                    "as_of": "2026-07-14T12:00:00Z",
                    "source_ref": "source-ingest://aapl-price",
                },
            },
        }

        _seed_signal_market_price(Algo(), "AAPL", signal)

        self.assertEqual(
            calls,
            [
                (
                    "mark",
                    "AAPL",
                    101.5,
                    "2026-07-14T12:00:00Z",
                    "source-ingest://aapl-price",
                )
            ],
        )
        context = _signal_context_metadata(signal)
        self.assertEqual(context["market_price"], 101.5)
        self.assertEqual(context["market_price_as_of"], "2026-07-14T12:00:00Z")
        self.assertEqual(context["market_price_source"], "source-ingest://aapl-price")

    def test_nonfinite_signal_prices_are_never_seeded_or_exposed_as_context(self):
        for invalid_price in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(price=repr(invalid_price)):
                calls = []

                class Algo:
                    def SetSecurityPrice(self, symbol, price):  # noqa: N802
                        calls.append(("price", symbol, price))

                    def SetSecurityMark(self, symbol, price, *, as_of, source):  # noqa: N802
                        calls.append(("mark", symbol, price, as_of, source))

                signal = {
                    "metadata": {
                        "price": invalid_price,
                        "market_data": {
                            "price": invalid_price,
                            "as_of": "2026-07-14T12:00:00Z",
                            "source_ref": "source-ingest://invalid",
                        }
                    }
                }

                _seed_signal_market_price(Algo(), "AAPL", signal)

                self.assertEqual(calls, [])
                context = _signal_context_metadata(signal)
                self.assertNotIn("price", context)
                self.assertNotIn("market_price", context)

    def test_nonfinite_or_negative_signal_quantities_fail_before_order_dispatch(self):
        for invalid_quantity in (float("nan"), float("inf"), float("-inf"), -1.0):
            with self.subTest(quantity=repr(invalid_quantity)):
                algo = _SpyAlgo()
                with self.assertRaisesRegex(ExecutionError, "quantity must be finite"):
                    execute(
                        {
                            "signal_id": "sig-invalid-quantity",
                            "symbol": "AAPL.US",
                            "action": "BUY",
                            "direction": "LONG",
                            "quantity": invalid_quantity,
                            "quantity_type": "SHARES",
                        },
                        algo,
                    )
                self.assertEqual(algo.market_orders, [])

    def test_nonfinite_execution_price_and_limit_price_fail_closed(self):
        cash_algo = _SpyAlgo()
        cash_algo.Securities["AAPL"].Price = float("inf")
        with self.assertRaisesRegex(ExecutionError, "cannot get price"):
            execute(
                {
                    "signal_id": "sig-invalid-cash-price",
                    "symbol": "AAPL.US",
                    "action": "BUY",
                    "direction": "LONG",
                    "quantity": 100.0,
                    "quantity_type": "CASH_VALUE",
                },
                cash_algo,
            )
        self.assertEqual(cash_algo.market_orders, [])

        limit_algo = _SpyAlgo()
        with self.assertRaisesRegex(ExecutionError, "limit_price must be finite"):
            execute(
                {
                    "signal_id": "sig-invalid-limit-price",
                    "symbol": "AAPL.US",
                    "action": "BUY",
                    "direction": "LONG",
                    "quantity": 1.0,
                    "quantity_type": "SHARES",
                    "order_type": "LIMIT",
                    "limit_price": float("inf"),
                },
                limit_algo,
            )
        self.assertEqual(limit_algo.limit_orders, [])

    def test_hold_signal_records_noop_feedback_without_order(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-hold-001",
                "symbol": "AAPL.US",
                "action": "HOLD",
                "direction": "LONG",
                "quantity": 0,
                "quantity_type": "SHARES",
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(len(algo.signal_noops), 1)
        noop = algo.signal_noops[0]
        self.assertEqual(noop["noop_reason"], "hold_signal")
        self.assertEqual(noop["requested_quantity"], 0.0)
        self.assertEqual(noop["quantity_type"], "SHARES")
        self.assertEqual(noop["broker_submission_status"], "not_submitted_signal_noop")
        self.assertFalse(noop["submitted_to_broker"])
        self.assertEqual(noop["price"], 100.0)

    def test_exit_long_without_position_records_noop_feedback(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-exit-empty-long-001",
                "symbol": "AAPL.US",
                "action": "EXIT",
                "direction": "LONG",
                "quantity": 0,
                "quantity_type": "SHARES",
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(len(algo.signal_noops), 1)
        noop = algo.signal_noops[0]
        self.assertEqual(noop["noop_reason"], "exit_long_without_position")
        self.assertEqual(noop["requested_quantity"], 0.0)
        self.assertEqual(noop["computed_quantity"], 0.0)
        self.assertEqual(noop["metadata"]["position_quantity"], 0.0)
        self.assertEqual(noop["metadata"]["exit_direction"], "LONG")
        self.assertEqual(noop["broker_submission_status"], "not_submitted_signal_noop")
        self.assertFalse(noop["submitted_to_broker"])

    def test_exit_short_covers_exact_fractional_holding_without_flipping_long(self):
        algo = _GuardedPaperAlgo()
        algo.Portfolio["AAPL"].Quantity = -2.5

        execute(
            {
                "signal_id": "sig-exit-fractional-short-001",
                "symbol": "AAPL.US",
                "action": "EXIT",
                "direction": "SHORT",
                "quantity": 0,
                "quantity_type": "SHARES",
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [("AAPL", 2.5)])
        self.assertEqual(algo.Portfolio["AAPL"].Quantity, 0.0)

    def test_zero_share_quantity_records_order_rejection(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-zero-shares",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 0.1,
                "quantity_type": "SHARES",
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(len(algo.order_rejections), 1)
        rejection = algo.order_rejections[0]
        self.assertEqual(rejection["reject_reason"], "shares_quantity_rounded_to_zero")
        self.assertEqual(rejection["requested_quantity"], 0.1)
        self.assertEqual(rejection["computed_quantity"], 0.0)
        self.assertEqual(rejection["broker_submission_status"], "rejected_before_broker")
        self.assertFalse(rejection["submitted_to_broker"])

    def test_default_market_order_type_is_present_in_signal_context(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-default-market-context",
                "symbol": "AAPL.US",
                "action": "SELL",
                "direction": "SHORT",
                "quantity": 2,
                "quantity_type": "SHARES",
                "metadata": {"alpha_source": "pure_quant_default_market"},
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [("AAPL", -2)])
        self.assertEqual(algo.signal_contexts[0]["order_type"], "MARKET")
        self.assertEqual(algo.signal_contexts[0]["requested_quantity"], 2.0)
        self.assertEqual(algo.signal_contexts[0]["alpha_source"], "pure_quant_default_market")

    def test_zero_cash_value_quantity_records_order_rejection(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-zero-cash",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10.0,
                "quantity_type": "CASH_VALUE",
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(len(algo.order_rejections), 1)
        rejection = algo.order_rejections[0]
        self.assertEqual(rejection["reject_reason"], "cash_value_resolved_to_zero_shares")
        self.assertEqual(rejection["requested_quantity"], 10.0)
        self.assertEqual(rejection["computed_quantity"], 0.0)
        self.assertEqual(rejection["price"], 100.0)

    def test_limit_order_without_limit_price_raises_before_market_order(self):
        algo = _SpyAlgo()

        with self.assertRaisesRegex(ExecutionError, "limit_price is required"):
            execute(
                {
                    "signal_id": "sig-limit-missing-price",
                    "symbol": "AAPL.US",
                    "action": "BUY",
                    "direction": "LONG",
                    "quantity": 10,
                    "quantity_type": "SHARES",
                    "order_type": "LIMIT",
                },
                algo,
            )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])

    def test_cash_value_limit_order_uses_limit_order_and_limit_price_for_sizing(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-cash-limit",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 990.0,
                "quantity_type": "CASH_VALUE",
                "order_type": "LIMIT",
                "limit_price": 99.0,
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [("AAPL", 10, 99.0)])

    def test_sell_long_limit_close_uses_limit_order_instead_of_liquidate(self):
        algo = _SpyAlgo()
        algo.Portfolio["AAPL"].Quantity = 10

        execute(
            {
                "signal_id": "sig-sell-long-limit-close",
                "symbol": "AAPL.US",
                "action": "SELL",
                "direction": "LONG",
                "quantity": 6,
                "quantity_type": "SHARES",
                "order_type": "LIMIT",
                "limit_price": 105.0,
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [("AAPL", -6.0, 105.0)])

    def test_sell_long_limit_close_without_limit_price_raises_before_liquidate(self):
        algo = _SpyAlgo()
        algo.Portfolio["AAPL"].Quantity = 10

        with self.assertRaisesRegex(ExecutionError, "LIMIT close failed: limit_price is required"):
            execute(
                {
                    "signal_id": "sig-sell-long-limit-missing-price",
                    "symbol": "AAPL.US",
                    "action": "SELL",
                    "direction": "LONG",
                    "quantity": 6,
                    "quantity_type": "SHARES",
                    "order_type": "LIMIT",
                },
                algo,
            )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])

    def test_percent_portfolio_limit_order_raises_before_setholdings(self):
        algo = _SpyAlgo()

        with self.assertRaisesRegex(ExecutionError, "PERCENT_PORTFOLIO quantity_type"):
            execute(
                {
                    "signal_id": "sig-percent-limit",
                    "symbol": "AAPL.US",
                    "action": "BUY",
                    "direction": "LONG",
                    "quantity": 0.25,
                    "quantity_type": "PERCENT_PORTFOLIO",
                    "order_type": "LIMIT",
                    "limit_price": 99.0,
                },
                algo,
            )

        self.assertEqual(algo.market_orders, [])
        self.assertEqual(algo.limit_orders, [])

    def test_bracket_order_is_logged_only_not_broker_submitted(self):
        algo = _SpyAlgo()

        execute(
            {
                "signal_id": "sig-bracket-001",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [("AAPL", 10)])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(algo.stop_market_orders, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["guard_stage"], "unknown")

    def test_guarded_paper_bracket_order_submits_simulated_children(self):
        algo = _GuardedPaperAlgo()

        execute(
            {
                "signal_id": "sig-bracket-002",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [("AAPL", 10)])
        self.assertEqual(algo.stop_market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(len(algo.bracket_submissions), 1)
        legs = algo.bracket_submissions[0]["legs"]
        self.assertEqual(
            legs,
            [
                {
                    "leg_type": "stop_loss",
                    "order_type": "STOP_MARKET",
                    "quantity": -10.0,
                    "stop_price": 98.0,
                },
                {
                    "leg_type": "take_profit",
                    "order_type": "LIMIT",
                    "quantity": -10.0,
                    "limit_price": 105.0,
                },
            ],
        )
        self.assertEqual(len(algo.bracket_logs), 1)
        self.assertEqual(
            algo.bracket_logs[0]["broker_submission_status"],
            BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER,
        )
        self.assertTrue(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["guard_stage"], "paper")
        self.assertEqual(algo.bracket_logs[0]["metadata"]["legs"], legs)

    def test_guarded_paper_bracket_order_can_submit_with_lean_order_methods(self):
        algo = _GuardedPaperFallbackAlgo()

        execute(
            {
                "signal_id": "sig-bracket-fallback",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.stop_market_orders, [("AAPL", -10.0, 98.0)])
        self.assertEqual(algo.limit_orders, [("AAPL", -10.0, 105.0)])
        self.assertEqual(
            algo.bracket_logs[0]["broker_submission_status"],
            BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER,
        )
        self.assertTrue(algo.bracket_logs[0]["submitted_to_broker"])

    def test_paper_bracket_order_guard_disabled_remains_logged_only(self):
        algo = _GuardedPaperAlgo()
        algo.BracketOrderExecutionEnabled = False

        execute(
            {
                "signal_id": "sig-bracket-disabled",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(algo.bracket_logs[0]["submitted_to_broker"])

    def test_missing_child_order_method_logs_only_without_partial_submission(self):
        algo = _MissingLimitPaperAlgo()

        execute(
            {
                "signal_id": "sig-bracket-missing-method",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.stop_market_orders, [])
        self.assertEqual(algo.limit_orders, [])
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["reason"], "bracket_submission_unavailable")

    def test_live_bracket_order_remains_logged_only_even_if_guard_flag_is_set(self):
        algo = _LiveAlgo()

        execute(
            {
                "signal_id": "sig-bracket-live",
                "symbol": "AAPL.US",
                "action": "BUY",
                "direction": "LONG",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        self.assertEqual(algo.market_orders, [("AAPL", 10)])
        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["guard_stage"], "live")

    def test_guarded_sim_short_bracket_order_submits_inverse_child_legs(self):
        algo = _SimAlgo()

        execute(
            {
                "signal_id": "sig-bracket-sim-short",
                "symbol": "AAPL.US",
                "action": "SELL",
                "direction": "SHORT",
                "quantity": 10,
                "quantity_type": "SHARES",
                "metadata": {
                    "risk_parameters": {
                        "stop_loss_pct": 0.02,
                        "take_profit_pct": 0.05,
                    }
                },
            },
            algo,
        )

        legs = algo.bracket_submissions[0]["legs"]
        self.assertEqual(
            legs,
            [
                {
                    "leg_type": "stop_loss",
                    "order_type": "STOP_MARKET",
                    "quantity": 10.0,
                    "stop_price": 102.0,
                },
                {
                    "leg_type": "take_profit",
                    "order_type": "LIMIT",
                    "quantity": 10.0,
                    "limit_price": 95.0,
                },
            ],
        )
        self.assertEqual(algo.bracket_logs[0]["broker_submission_status"], BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER)
        self.assertTrue(algo.bracket_logs[0]["submitted_to_broker"])
        self.assertEqual(algo.bracket_logs[0]["metadata"]["guard_stage"], "sim")


class _CanaryAlgo(_GuardedPaperAlgo):
    DeploymentStage = "canary"


class _PaperNoFillAlgo(_GuardedPaperAlgo):
    """Paper algo where MarketOrder records the call but does NOT update portfolio holdings."""

    def MarketOrder(self, symbol, quantity):  # noqa: N802
        _SpyAlgo.MarketOrder(self, symbol, quantity)


class _PaperWithLongAlgo(_GuardedPaperAlgo):
    """Paper algo that starts with an existing LONG position and tracks liquidations."""

    def __init__(self):
        super().__init__()
        self.liquidations: list = []
        holding = _Holding()
        holding.Quantity = 10.0
        self.Portfolio["AAPL"] = holding

    def Liquidate(self, symbol):  # noqa: N802
        self.liquidations.append(symbol)
        self.Portfolio[symbol].Quantity = 0.0


class BracketLegBuildTests(unittest.TestCase):
    """Direct unit tests for _build_bracket_legs deterministic price computation."""

    def test_buy_long_stop_and_take_profit_prices_are_deterministic(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=10, entry_price=100.0,
            stop_loss_pct=0.02, take_profit_pct=0.05,
        )
        self.assertEqual(len(legs), 2)
        stop = next(l for l in legs if l["leg_type"] == "stop_loss")
        tp = next(l for l in legs if l["leg_type"] == "take_profit")
        self.assertEqual(stop["order_type"], "STOP_MARKET")
        self.assertEqual(stop["quantity"], -10.0)
        self.assertAlmostEqual(stop["stop_price"], 98.0)    # 100 × (1 − 0.02)
        self.assertEqual(tp["order_type"], "LIMIT")
        self.assertEqual(tp["quantity"], -10.0)
        self.assertAlmostEqual(tp["limit_price"], 105.0)    # 100 × (1 + 0.05)

    def test_sell_short_stop_and_take_profit_prices_are_inverse(self):
        legs = _build_bracket_legs(
            action="SELL", direction="SHORT",
            entry_quantity=10, entry_price=100.0,
            stop_loss_pct=0.02, take_profit_pct=0.05,
        )
        self.assertEqual(len(legs), 2)
        stop = next(l for l in legs if l["leg_type"] == "stop_loss")
        tp = next(l for l in legs if l["leg_type"] == "take_profit")
        self.assertEqual(stop["quantity"], 10.0)            # positive: cover the short
        self.assertAlmostEqual(stop["stop_price"], 102.0)   # 100 × (1 + 0.02)
        self.assertEqual(tp["quantity"], 10.0)
        self.assertAlmostEqual(tp["limit_price"], 95.0)     # 100 × (1 − 0.05)

    def test_zero_entry_quantity_returns_empty_legs(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=0, entry_price=100.0,
            stop_loss_pct=0.02, take_profit_pct=0.05,
        )
        self.assertEqual(legs, [])

    def test_zero_entry_price_returns_empty_legs(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=10, entry_price=0.0,
            stop_loss_pct=0.02, take_profit_pct=0.05,
        )
        self.assertEqual(legs, [])

    def test_stop_loss_only_produces_single_stop_leg(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=10, entry_price=100.0,
            stop_loss_pct=0.03, take_profit_pct=0.0,
        )
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0]["leg_type"], "stop_loss")

    def test_take_profit_only_produces_single_limit_leg(self):
        legs = _build_bracket_legs(
            action="BUY", direction="LONG",
            entry_quantity=10, entry_price=100.0,
            stop_loss_pct=0.0, take_profit_pct=0.04,
        )
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0]["leg_type"], "take_profit")


class ExecutorBracketGuardEdgeCaseTests(unittest.TestCase):

    def _bracket_signal(self, action="BUY", direction="LONG"):
        return {
            "signal_id": f"sig-edge-{action.lower()}-{direction.lower()}",
            "symbol": "AAPL.US",
            "action": action,
            "direction": direction,
            "quantity": 10,
            "quantity_type": "SHARES",
            "metadata": {
                "risk_parameters": {
                    "stop_loss_pct": 0.02,
                    "take_profit_pct": 0.05,
                }
            },
        }

    def test_canary_bracket_order_is_fail_closed(self):
        """Canary stage is outside allowed bracket execution stages → always logged_only."""
        algo = _CanaryAlgo()
        execute(self._bracket_signal(), algo)
        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        log = algo.bracket_logs[0]
        self.assertEqual(log["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(log["submitted_to_broker"])
        self.assertEqual(log["metadata"]["guard_stage"], "canary")
        self.assertIn("paper/sim", log["metadata"]["guard_reason"])

    def test_invalid_bracket_zero_entry_fill_is_logged_only(self):
        """Guard passes but MarketOrder produces no portfolio delta → entry_qty=0 → invalid_bracket_quantity_or_price."""
        algo = _PaperNoFillAlgo()
        execute(self._bracket_signal(), algo)
        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        log = algo.bracket_logs[0]
        self.assertEqual(log["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(log["submitted_to_broker"])
        self.assertEqual(log["metadata"]["reason"], "invalid_bracket_quantity_or_price")

    def test_non_entry_exit_signal_with_risk_params_is_logged_only(self):
        """EXIT+LONG is not a bracket entry combination → risk params present → logged_only with not_entry_signal."""
        algo = _PaperWithLongAlgo()
        execute(self._bracket_signal(action="EXIT", direction="LONG"), algo)
        self.assertEqual(algo.bracket_submissions, [])
        self.assertEqual(len(algo.bracket_logs), 1)
        log = algo.bracket_logs[0]
        self.assertEqual(log["broker_submission_status"], BRACKET_ORDER_STATUS_LOGGED_ONLY)
        self.assertFalse(log["submitted_to_broker"])
        self.assertEqual(log["metadata"]["reason"], "not_entry_signal")


class _TaiwanAlgo:
    """Duck-typed runtime exposing the Taiwan broker boundary."""

    def __init__(self):
        self.submissions = []
        self.order_rejections = []
        self.signal_noops = []
        self.context = {}
        self.rejection_contexts = []

    def SetCurrentSignalContext(self, metadata):  # noqa: N802
        self.context = dict(metadata)

    def ClearCurrentSignalContext(self):  # noqa: N802
        self.context = {}

    def SubmitTaiwanBrokerOrder(self, symbol, **kwargs):  # noqa: N802
        self.submissions.append((symbol, kwargs))

    def RecordOrderRejected(self, symbol, **kwargs):  # noqa: N802
        self.order_rejections.append({"symbol": symbol, **kwargs})
        self.rejection_contexts.append(dict(self.context))

    def RecordSignalNoop(self, symbol, **kwargs):  # noqa: N802
        self.signal_noops.append({"symbol": symbol, **kwargs})
        self.rejection_contexts.append(dict(self.context))


class ExpressionEnvelopeExecutorTests(unittest.TestCase):
    def _signal(self, **metadata):
        return {
            "signal_id": "sig-expr-1",
            "symbol": "0050.TW",
            "action": "SELL",
            "direction": "SHORT",
            "quantity": 1,
            "quantity_type": "SHARES",
            "order_type": "MARKET",
            "decision_id": "dec-expr-1",
            "metadata": {"decision_id": "dec-expr-1", **metadata},
        }

    def test_chosen_stock_future_flows_to_taiwan_broker_order_with_expression_context(self):
        algo = _TaiwanAlgo()
        captured_contexts = []

        def _capture_submit(symbol, **kwargs):
            captured_contexts.append(dict(algo.context))
            algo.submissions.append((symbol, kwargs))

        algo.SubmitTaiwanBrokerOrder = _capture_submit
        record = {"decision_id": "dec-expr-1", "instrument_id": "ssf:SRF:202611", "units": 1}
        execute(
            self._signal(
                instrument_id="ssf:SRF:202611",
                kind="stock_future",
                units=1,
                multiplier=1000,
                contract_month="202611",
                reason_code="short_via_future",
                expression_decision=record,
            ),
            algo,
        )
        self.assertEqual(algo.order_rejections, [])
        [(symbol, kwargs)] = algo.submissions
        self.assertEqual((symbol, kwargs["side"], kwargs["quantity"]), ("0050.TW", "sell", 1.0))
        context = captured_contexts[0]
        self.assertEqual(context["instrument_id"], "ssf:SRF:202611")
        self.assertEqual(context["kind"], "stock_future")
        self.assertEqual(context["multiplier"], 1000)
        self.assertEqual(context["contract_month"], "202611")
        self.assertEqual(context["decision_id"], "dec-expr-1")
        self.assertEqual(context["expression_decision"], record)

    def test_unsupported_instrument_kind_is_rejected_by_the_ledger_with_no_fill(self):
        algo = _TaiwanAlgo()
        record = {"decision_id": "dec-expr-1", "instrument_id": "opt:CDO:202611", "units": 1}
        execute(
            self._signal(
                instrument_id="opt:CDO:202611",
                kind="stock_option",
                units=1,
                multiplier=2000,
                reason_code="short_via_option",
                expression_decision=record,
            ),
            algo,
        )
        self.assertEqual(algo.submissions, [])
        [rejection] = algo.order_rejections
        self.assertEqual(rejection["reject_reason"], "instrument_not_supported_by_ledger")
        self.assertEqual(rejection["computed_quantity"], 0.0)
        self.assertFalse(rejection["submitted_to_broker"])
        context = algo.rejection_contexts[0]
        self.assertEqual(context["instrument_id"], "opt:CDO:202611")
        self.assertEqual(context["kind"], "stock_option")
        self.assertEqual(context["multiplier"], 2000)
        self.assertEqual(context["decision_id"], "dec-expr-1")
        self.assertEqual(context["expression_decision"], record)

    def test_cash_share_choice_flows_to_the_existing_broker_path(self):
        algo = _TaiwanAlgo()
        execute(
            self._signal(instrument_id="eq:0050", kind="cash_share", units=1, multiplier=1, reason_code="long_cash"),
            algo,
        )
        self.assertEqual(algo.order_rejections, [])
        [(symbol, kwargs)] = algo.submissions
        self.assertEqual((symbol, kwargs["side"], kwargs["quantity"]), ("0050.TW", "sell", 1))

    def test_legacy_signal_without_a_kind_is_untouched(self):
        algo = _TaiwanAlgo()
        execute(self._signal(), algo)
        self.assertEqual(len(algo.submissions), 1)
        self.assertEqual(algo.order_rejections, [])

    def test_no_trade_hold_carries_the_record_into_the_noop_event(self):
        algo = _TaiwanAlgo()
        signal = self._signal(expression_decision={"reason_code": "no_feasible_instrument"}, reason_code="no_feasible_instrument")
        signal["action"] = "HOLD"
        execute(signal, algo)
        self.assertEqual(algo.submissions, [])
        self.assertEqual(algo.signal_noops[0]["noop_reason"], "hold_signal")
        self.assertEqual(
            algo.rejection_contexts[0]["expression_decision"], {"reason_code": "no_feasible_instrument"}
        )

    def test_stock_future_candidate_metadata_forwarded_and_signal_risk_policy_stripped(self):
        algo = _TaiwanAlgo()
        captured_contexts = []

        def _capture_submit(symbol, **kwargs):
            captured_contexts.append(dict(algo.context))
            algo.submissions.append((symbol, kwargs))

        algo.SubmitTaiwanBrokerOrder = _capture_submit
        execute(
            self._signal(
                instrument_id="ssf:QFF:202611",
                kind="stock_future",
                units=1,
                trade_date="2026-11-06",
                candidates=[
                    {
                        "instrument_id": "ssf:QFF:202611",
                        "kind": "stock_future",
                        "contract": "QFF",
                        "contract_month": "202611",
                        "multiplier": 100,
                        "price_basis": "futures_daily_settlement_prev_session",
                        "futures_settlement_price": 2247.0,
                        "price_date": "2026-11-05",
                        "settlement_date": "2026-11-18",
                    }
                ],
                risk_policy={"risk_policy_id": "forged-signal-policy", "max_leverage": 10.0},
                risk_policy_id="forged-signal-policy",
            ),
            algo,
        )
        self.assertEqual(len(algo.submissions), 1)
        ctx = captured_contexts[0]
        self.assertEqual(ctx["instrument_id"], "ssf:QFF:202611")
        self.assertEqual(ctx["contract"], "QFF")
        self.assertEqual(ctx["contract_month"], "202611")
        self.assertEqual(ctx["multiplier"], 100)
        self.assertEqual(ctx["price_basis"], "futures_daily_settlement_prev_session")
        self.assertEqual(ctx["futures_settlement_price"], 2247.0)
        self.assertEqual(ctx["price_date"], "2026-11-05")
        self.assertEqual(ctx["trade_date"], "2026-11-06")
        self.assertEqual(ctx["settlement_date"], "2026-11-18")
        self.assertNotIn("risk_policy", ctx)
        self.assertNotIn("risk_policy_id", ctx)


if __name__ == "__main__":
    unittest.main()
