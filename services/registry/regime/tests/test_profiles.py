from services.registry.regime.profiles import profile_for


def test_spec_regime_section_names_axis_and_horizon():
    p = profile_for({"strategy_spec": {"metadata": {"regime": {"primary_axis": "volatility", "horizon": "short"}}}})
    assert (p.primary_axis, p.horizon, p.source) == ("volatility", "short", "spec_regime")


def test_macro_is_never_primary_and_falls_back_to_the_default():
    p = profile_for({"regime": {"primary_axis": "macro"}})
    assert p.primary_axis != "macro" and p.note == "macro_primary_fallback_to_default"
    assert (p.primary_axis, p.horizon) == ("trend", "medium")


def test_alpha_template_applicable_regimes_pick_one_axis():
    p = profile_for({"applicable_regimes": ["risk_on", "risk_off"]})
    assert (p.primary_axis, p.source) == ("risk_appetite", "alpha_template")
    mixed = profile_for({"applicable_regimes": ["risk_on", "trending_up"]})
    assert mixed.source == "default"  # two axes named: no single primary, so the default applies


def test_family_default_and_holding_horizon():
    p = profile_for({"family": "mean_reversion"})
    assert (p.primary_axis, p.horizon, p.source) == ("volatility", "short", "family_default")
    assert profile_for({"holding_horizon_sessions": 60}).horizon == "medium"
    assert profile_for({"holding_horizon_sessions": 5}).horizon == "short"


def test_no_information_gets_the_default():
    p = profile_for(None)
    assert (p.primary_axis, p.horizon, p.source) == ("trend", "medium", "default")
    assert profile_for({"regime": {"primary_axis": "bogus"}}).note.startswith("unknown_primary_axis_ignored")
