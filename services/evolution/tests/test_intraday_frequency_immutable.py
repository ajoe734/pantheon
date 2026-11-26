"""SD_INTRADAY_MINUTE_BARS §9 K8: frequency immutability and per-frequency trial counts."""
from __future__ import annotations

import pytest

from services.evolution.models import BarFrequencyMutationError, reject_bar_frequency_mutation
from services.evolution.program_service import ProgramService, ProgramValidationError
from services.evolution.seed_data import INTRADAY_SEARCH_DECLARATIONS
from services.research.alpha_replication.shared_stores import SharedFamilyTrialCounterStore
from services.research.replication.gate import FamilyTrialCounterStore, family_trial_key


@pytest.mark.parametrize(
    "payload",
    [
        {"bar_frequency": "5m"},
        {"payload": {"data_frequency": "1m"}},
        {"payload": {"nested": [{"Frequency": "5m"}]}},
        {"expression": "set bar_frequency=5m"},
        {"data_source": "tw_price_1d"},
    ],
)
def test_reject_bar_frequency_mutation(payload):
    with pytest.raises(BarFrequencyMutationError):
        reject_bar_frequency_mutation(payload)


def test_benign_mutation_allowed():
    reject_bar_frequency_mutation({"expression": "gaussian_perturbation", "rate": 0.05})


def _service(tmp_path):
    from services.evolution.program_store import JsonProgramStore

    return ProgramService(JsonProgramStore(tmp_path / "programs.json"))


def test_program_service_rejects_frequency_mutation_rule(tmp_path):
    svc = _service(tmp_path)
    prog, _ = svc.create_program(tenant_id="t", actor_id="a", name="intraday")
    pid = prog["program_id"]
    for payload in ({"bar_frequency": "5m"}, {"payload": {"frequency": "1m"}}, {"expression": "bar_frequency=5m"}):
        with pytest.raises(ProgramValidationError):
            svc.execute_action(tenant_id="t", actor_id="a", program_id=pid,
                               action_id="create_mutation_rule", payload=payload)
    assert svc.get_program(tenant_id="t", program_id=pid)["mutation_rules"] == []
    ok, _ = svc.execute_action(tenant_id="t", actor_id="a", program_id=pid,
                               action_id="create_mutation_rule", payload={"expression": "gaussian_perturbation"})
    assert len(ok["program"]["mutation_rules"]) == 1


def test_seed_declarations_fixed_source_frequency():
    assert {d["bar_frequency"] for d in INTRADAY_SEARCH_DECLARATIONS} == {"1m", "5m"}
    assert {d["source_dataset"] for d in INTRADAY_SEARCH_DECLARATIONS} == {"tw_price_1m"}


def test_trial_key_daily_unchanged_and_intraday_isolated():
    assert family_trial_key("fam") == "fam"
    assert family_trial_key("fam", "1d") == "fam"
    assert family_trial_key("fam", None) == "fam"
    assert family_trial_key("fam", "1m") == "fam@1m"
    assert family_trial_key("fam", "5m") == "fam@5m"


@pytest.mark.parametrize("shared", [False, True])
def test_multi_frequency_counts_isolated(tmp_path, shared):
    path = tmp_path / "counts.json"
    store = (SharedFamilyTrialCounterStore if shared else FamilyTrialCounterStore)(path)
    for _ in range(3):
        store.increment(family_trial_key("fam", "1m"))
    store.increment(family_trial_key("fam", "5m"))
    store.increment("fam")  # legacy daily semantics
    assert store.get(family_trial_key("fam", "1m")) == 3
    assert store.get(family_trial_key("fam", "5m")) == 1
    assert store.get("fam") == 1
