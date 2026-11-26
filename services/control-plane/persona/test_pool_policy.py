import math
from datetime import date, timedelta

import pytest

from pool_policy import load_policy, pool_health, reweight

BASIS = "underlying_exposure_fraction_net_taiwan_costs_equal_notional"


def book(returns, checksum="sum"):
    return {"source_ref": "test:observed-book", "checksum": checksum, "basis": BASIS,
            "since_inception": True, "dates": [(date(2026, 1, 1) + timedelta(days=i)).isoformat()
                                               for i in range(len(returns))], "returns": returns}


def member(ref, **kw):
    return {"artifact_ref": ref, "checksum": "sum", "stage": "active", "risk_weight": .5, **kw}


def observations(values):
    return [{"date": d, "source_ref": "test:observed-risk", "gross_notional": v,
             "gross_limit": 100, "drawdown": .02, "drawdown_limit": .1}
            for d, v in zip(book(values)["dates"], values)]


def test_health_window_utilisation_idle_days_and_correlation_spectrum():
    policy = load_policy()
    one = [.01, -.01, .01, -.01] * 5
    two = [.01, .01, -.01, -.01] * 5
    result = pool_health([member("a"), member("b")], {"a": book(one), "b": book(two)}, observations([0, 100] * 10), policy)
    assert result["gross_utilisation"]["value"] == .5
    assert result["drawdown_utilisation"]["value"] == pytest.approx(.2)
    assert result["idle_day_share"]["value"] == .5
    assert result["effective_bets"]["value"] == 2
    assert result["demand_reasons"] == ["low_risk_budget_utilisation"]
    result = pool_health([member("a"), member("b")], {"a": book(one), "b": book(one)}, observations([0] * 20), policy)
    assert result["effective_bets"]["value"] == pytest.approx(1)
    assert "too_few_independent_bets" in result["demand_reasons"]


def test_each_missing_measure_is_unknown_and_alignment_is_by_date():
    policy = load_policy()
    rows = observations([10] * 20)
    rows[0].pop("gross_limit")
    result = pool_health([member("a")], {}, rows, policy)
    assert result["gross_utilisation"]["status"] == "unknown"
    assert result["drawdown_utilisation"]["status"] == "known"
    assert result["effective_bets"]["reason"] == "virtual_book_source_missing"
    assert result["idle_day_share"]["status"] == "known"
    shifted = book([.01, -.01] * 10)
    shifted["dates"] = book([0] * 21)["dates"][1:]
    assert pool_health([member("a")], {"a": shifted}, observations([10] * 20), policy)["effective_bets"]["status"] == "unknown"
    assert all(pool_health([], {}, [], policy)[key]["status"] == "unknown" for key in
               ("gross_utilisation", "drawdown_utilisation", "effective_bets", "idle_day_share"))


def test_risk_parity_tilt_trial_cap_and_haircuts_leave_idle_budget():
    policy = load_policy().model_copy(update={"tilt_sharpe_scale": 0.0})
    values = [.01, -.005] * 10
    members = [member("a"), member("b", stage="trial")]
    result, _ = reweight(members, {"a": book(values), "b": book([v * 2 for v in values])}, policy, "now")
    assert result[0]["risk_weight"] == pytest.approx(2/3)
    assert result[1]["risk_weight"] == pytest.approx(1/3)  # 20 distinct observed days, cap lifted
    result, _ = reweight(members, {"a": book(values[:19]), "b": book(values[:19])}, policy, "now")
    assert result[1]["risk_weight"] == .1 and sum(m["risk_weight"] for m in result) == .6
    halved, stats = reweight([member("a")], {"a": book([.01, .02, -.05])}, policy, "now")
    repeated, _ = reweight(halved, {"a": book([.01, .02, -.05])}, policy, "later", stats)
    assert halved[0]["risk_weight"] == repeated[0]["risk_weight"] == .25
    removed, _ = reweight([member("a")], {"a": book([.01, .02, -.1])}, policy, "now")
    assert removed[0]["stage"] == "removed" and removed[0]["risk_weight"] == 0
    assert removed[0]["removal_reason"] == "own_high_water_drawdown"
    bankrupt, _ = reweight([member("a")], {"a": book([.01, .02, -1])}, policy, "now")
    assert bankrupt[0]["stage"] == "removed"
    tilted, _ = reweight([member("a"), member("b")], {"a": book(values), "b": book([-v for v in values])}, load_policy(), "now")
    assert tilted[0]["risk_weight"] > tilted[1]["risk_weight"]


@pytest.mark.parametrize("mutate", [lambda b: b.pop("since_inception"), lambda b: b.update(returns=[math.nan, .01]),
                                    lambda b: b.update(dates=["2026-01-01"] * 2), lambda b: b.update(checksum="wrong"),
                                    lambda b: b.update(returns=[0, 0]), lambda b: b.pop("source_ref")])
def test_bad_books_do_not_become_weights(mutate):
    observed = book([.01, -.01])
    mutate(observed)
    with pytest.raises(ValueError):
        reweight([member("a")], {"a": observed}, load_policy(), "now")


def test_promotion_condition_and_numbers_live_in_the_policy_file_marked_as_proposals():
    from pool_policy import promotion_verdict
    policy = load_policy()
    assert {"trial_promotion_min_days", "trial_promotion_max_drawdown", "publish_weight_tolerance"} <= set(policy.coordinator_proposals)
    trial = member("t", stage="trial")
    stat = {"days": policy.trial_promotion_min_days, "drawdown": 0.0}
    assert promotion_verdict(trial, stat, policy)["eligible"] is True
    assert promotion_verdict(trial, {**stat, "days": stat["days"] - 1}, policy)["reason"].startswith("trial_history_")
    assert promotion_verdict(trial, {**stat, "drawdown": policy.trial_promotion_max_drawdown}, policy)["reason"] == \
        "trial_drawdown_not_below_promotion_threshold"
    assert promotion_verdict(member("a"), stat, policy)["reason"] == "not_a_trial"
    with pytest.raises(ValueError):  # a looser promotion than the halving rule is not allowed
        type(policy).model_validate({**policy.model_dump(), "trial_promotion_max_drawdown": policy.halve_drawdown + .01})


def test_trial_publication_reading_virtual_before_weight_and_capped_after():
    from pool_policy import trial_publication
    assert trial_publication(member("t", stage="trial", risk_weight=0), .1) == (0, True)
    assert trial_publication(member("t", stage="trial", risk_weight=.3), .1) == (.1, False)
    assert trial_publication(member("t", stage="trial", risk_weight=.05), .1) == (.05, False)
    assert trial_publication(member("a", risk_weight=.7), .1) == (.7, False)


def test_publication_change_membership_stage_weight_and_tolerance():
    from pool_policy import last_published, publication_change
    policy = load_policy()
    pins = [{"artifact_ref": "a", "risk_weight": .6, "virtual_only": False, "stage": "active"},
            {"artifact_ref": "b", "risk_weight": 0, "virtual_only": True, "stage": "trial"}]
    published = {"pool_version": 2, "members": pins}
    pool = lambda *ms: {"members": list(ms)}
    same = pool(member("a", risk_weight=.6 + policy.publish_weight_tolerance), member("b", stage="trial", risk_weight=0))
    assert publication_change(published, same, policy)["changed"] is False
    moved = pool(member("a", risk_weight=.6 + policy.publish_weight_tolerance + .01), member("b", stage="trial", risk_weight=0))
    assert publication_change(published, moved, policy)["reasons"] == ["weight_changed:a"]
    given_weight = pool(member("a"), member("b", stage="trial", risk_weight=.01))
    assert "weight_changed:b" in publication_change(published, given_weight, policy)["reasons"]  # virtual -> trading
    promoted = pool(member("a", risk_weight=.6), member("b", stage="active", risk_weight=0))
    assert "stage_changed:b" in publication_change(published, promoted, policy)["reasons"]
    removed = pool(member("a", risk_weight=.6), member("b", stage="removed"))
    assert publication_change(published, removed, policy)["membership_changed"] is True
    assert publication_change(None, same, policy)["reasons"] == ["no_publication_record"]
    history = {"published": {"1": {"status": "published", "pool_version": 1}, "3": {"status": "dispatched", "pool_version": 3},
                             "2": {"status": "published", "pool_version": 2}}}
    assert last_published(history)["pool_version"] == 2 and last_published({}) is None


# ---- real runtime books feeding reweight (no field added by hand) --------------------------------
def _runtime_books(days, *, joined_at="2026-01-01T00:00:00+00:00", first_day=2, drop_joined=False):
    from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks
    runtime = MemberVirtualBooks()
    view = {"artifact_id": "a1", "version": "1.0.0", "checksum": "sum", "risk_weight": .5,
            "virtual_only": False, "underlying_count": 1, "joined_at": joined_at}
    if drop_joined:
        view.pop("joined_at")
    snapshot = []
    for i in range(days):
        price = 100 * (1 + .01 * ((i % 3) - 1)) + i
        snapshot = runtime.observe(
            binding_id="b", envelope_checksum="env", symbol="2330.TW",
            event_time=(date(2026, 1, first_day) + timedelta(days=i)).isoformat() + "T05:30:00+00:00",
            price=price, open_price=price, source_ref="recorded", bar_frequency="1d",
            members={"m1": {**view, "view": {"direction": "LONG" if i % 2 else "SHORT", "strength": 1}}})
    return snapshot


def _as_controller_books(snapshot):
    # persona_cycle_controller hands each book to the policy with its net-of-costs block merged in.
    return {b["member_id"]: {**b, **b["net_of_costs"]} for b in snapshot}


def test_runtime_book_since_inception_drives_reweight_with_history_and_refuses_without():
    policy = load_policy()
    live = [member("m1")]
    stats = reweight(live, _as_controller_books(_runtime_books(25)), policy, "2026-02-01")[1]["m1"]
    assert stats["days"] == 24 and stats["volatility"] > 0
    with pytest.raises(ValueError, match="virtual_book_history_missing"):
        reweight(live, _as_controller_books(_runtime_books(2)), policy, "2026-02-01")


def test_runtime_book_not_since_inception_is_refused_by_the_existing_rule():
    policy = load_policy()
    for snapshot in (_runtime_books(25, drop_joined=True),                       # admission unknown
                     _runtime_books(25, joined_at="2025-12-01T00:00:00+00:00")):  # history starts late
        [only] = snapshot
        assert only["since_inception"] is False
        with pytest.raises(ValueError, match="virtual_book_high_water_history_missing"):
            reweight([member("m1")], _as_controller_books(snapshot), policy, "2026-02-01")


def test_legacy_book_without_the_field_reads_as_not_since_inception():
    from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks
    snapshot = _runtime_books(3)
    legacy = MemberVirtualBooks()
    legacy.observe(binding_id="b", envelope_checksum="env", symbol="2330.TW",
                   event_time="2026-01-02T05:30:00+00:00", price=100, source_ref="r", bar_frequency="1d",
                   members={"m1": {"artifact_id": "a1", "version": "1.0.0", "checksum": "sum", "virtual_only": False,
                                   "view": {"direction": "LONG", "strength": 1}}})
    for book in legacy.books.values():
        book.pop("admitted_at", None)  # as written by the previous runtime version
    [legacy_snapshot] = legacy.snapshot()
    assert legacy_snapshot["since_inception"] is False and snapshot[0]["since_inception"] is True



# ---- pool admission time carried end to end (POOL-MEMBER-JOINED-AT-CARRY-20261126) ---------------
# Real segments only: a pool version member as the persona owner serves it, the BFF publisher's
# envelope build (schema validated), the paper signal producer's member views, the runtime member
# virtual book and the reweight.  No segment after the pool version is handed joined_at or
# since_inception; on a dev without the carry the reweight refuses with
# virtual_book_high_water_history_missing.
HOSTED_JOINED_AT = "2026-11-08T18:10:02Z"  # persona-ee7112a829d38b9135ac members[0], read 2026-11-26


def _published_envelope(joined_at=HOSTED_JOINED_AT):
    from types import SimpleNamespace
    from services.control_plane.bff.persona_pool_publisher import PersonaPoolPublisher
    from services.registry.strategy_artifact import strategy_artifact_checksum, validate_strategy_artifact
    from services.registry.test_portfolio_envelope import member_artifact

    artifact = member_artifact()
    checksum = strategy_artifact_checksum(artifact)
    entry = {"registry_id": artifact["artifact_id"], "version": artifact["version"], "checksum": checksum,
             "owner_tenant": "t", "artifact_state": "approved", "metadata": {"strategy_artifact": artifact}}
    publisher = PersonaPoolPublisher(
        coordinator=SimpleNamespace(transport=SimpleNamespace(get=lambda *_: entry)), pool_store=None)
    # Pool version member shape of the persona owner (strategy-pool/versions/<n>).
    member = {"artifact_ref": artifact["artifact_id"], "checksum": checksum, "stage": "active",
              "risk_weight": 1.0, "baseline_ref": "apv-baseline", **({"joined_at": joined_at} if joined_at else {})}
    envelope, pins = publisher._build_envelope(
        "t", SimpleNamespace(strategy_id="persona-pool"), {"pool_version": 6, "members": [member]},
        "artifact-persona-pool-chain-v6", "1.6.0", 6, "sha256:" + "0" * 64,
        {"artifact_id": "artifact-persona-pool-chain-v5"})
    validate_strategy_artifact(envelope)
    return envelope, pins, member


def _producer_books(envelope, first_day="2026-11-09", sessions=8):
    from datetime import datetime
    from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks
    from services.execution.lean_runtime.paper_signal_producer import CurrentArtifactStrategy
    from services.execution.lean_runtime.test_portfolio_target_positions import portfolio_binding

    books, snapshot, closes = MemberVirtualBooks(), [], [100.0, 101.0]
    day = date.fromisoformat(first_day)
    for i in range(sessions):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        closes = closes + [closes[-1] * (1 + (.02 if i % 3 else -.015))]
        binding = portfolio_binding(envelope, closes=closes)
        # A one-underlying envelope is decided from the binding's single market input.
        [row] = binding.pop("market_inputs").values()
        binding["market_input"] = {**row, "event_time": f"{day}T05:30:00Z", "observed_at": f"{day}T06:00:00Z"}
        output = CurrentArtifactStrategy()(binding, f"{day}T06:00:00Z")
        metadata = output["metadata"]
        # paper_runtime._handle_order_event feeds the producer's member views to the book as is.
        snapshot = books.observe(
            binding_id=binding["binding_id"], envelope_checksum=binding["artifact_checksum"],
            symbol=output["symbol"], event_time=f"{day}T05:30:00+00:00", price=closes[-1],
            open_price=closes[-2], source_ref="recorded:" + day.isoformat(),
            members=metadata["portfolio_member_views"], bar_frequency=metadata["portfolio_bar_frequency"])
        day += timedelta(days=1)
    return books, snapshot


def test_pool_joined_at_reaches_the_book_and_the_reweight_through_real_segments():
    envelope, pins, member = _published_envelope()
    _, [book] = _producer_books(envelope)
    # The reweight is the decisive segment: without the carry it raises
    # virtual_book_high_water_history_missing here.
    stats = reweight([member], {book["member_id"]: {**book, **book["net_of_costs"]}},
                     load_policy(), "2026-11-20")[1][member["artifact_ref"]]
    assert stats["days"] == len(book["returns"]) and stats["volatility"] > 0
    [pinned] = envelope["strategy_logic"]["members"]
    assert pinned["joined_at"] == HOSTED_JOINED_AT and pins[0]["joined_at"] == HOSTED_JOINED_AT
    assert book["admitted_at"] == HOSTED_JOINED_AT and book["since_inception"] is True


def test_envelope_published_before_the_carry_stays_valid_and_records_no_admission():
    envelope, pins, member = _published_envelope(joined_at=None)
    assert "joined_at" not in envelope["strategy_logic"]["members"][0]
    _, [book] = _producer_books(envelope)
    assert book["admitted_at"] is None and book["since_inception"] is False
    with pytest.raises(ValueError, match="virtual_book_high_water_history_missing"):
        reweight([member], {book["member_id"]: {**book, **book["net_of_costs"]}}, load_policy(), "2026-11-20")


def test_hosted_late_book_records_admission_but_stays_not_since_inception():
    # Hosted 2026-11-26: books start 2026-11-19, 11 days after admission; the gap is another task's.
    envelope, _, member = _published_envelope()
    _, [book] = _producer_books(envelope, first_day="2026-11-19", sessions=4)
    assert book["admitted_at"] == HOSTED_JOINED_AT and book["since_inception"] is False
    with pytest.raises(ValueError, match="virtual_book_high_water_history_missing"):
        reweight([member], {book["member_id"]: {**book, **book["net_of_costs"]}}, load_policy(), "2026-11-26")
