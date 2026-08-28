"""Tests for RankingProjectionPort and EvolutionProjectionPort.

Validates:
1. RankingProjectionPort filtering/sorting for rankings, ranking formulas,
   persona league, rebalances, capital allocations, and containments
2. RankingProjectionPort's pure cross-dataset composition
   (build_persona_capital_ranking_view)
3. EvolutionProjectionPort program/decision reads and derived run/candidate
   projections
4. get_surface_status() behavior for missing/unavailable/empty/ok states
5. PersonaCapitalRuntimeDomainPort delegates to the ranking/evolution ports
"""

import sys
from pathlib import Path

BFF_DIR = Path(__file__).resolve().parent.parent
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

from domain_ports.persona_capital_runtime import (
    EvolutionProjectionPort,
    PersonaCapitalRuntimeDomainPort,
    RankingProjectionPort,
)


# ---------------------------------------------------------------------------
# 1. RankingProjectionPort
# ---------------------------------------------------------------------------

class TestRankingProjectionPortRankings:
    def test_list_and_get_rankings(self):
        rankings = [
            {"ranking_id": "rank-b", "status": "active"},
            {"ranking_id": "rank-a", "status": "draft"},
        ]
        port = RankingProjectionPort(rankings_reader=lambda: rankings)
        items = port.list_rankings()
        assert [i["ranking_id"] for i in items] == ["rank-a", "rank-b"]

        active = port.list_rankings(status="active")
        assert len(active) == 1
        assert active[0]["ranking_id"] == "rank-b"

        assert port.get_ranking("rank-a")["status"] == "draft"
        assert port.get_ranking("missing") is None
        assert port.get_ranking(None) is None

    def test_ranking_formulas(self):
        formulas = [
            {"formula_id": "rf-2", "status": "active"},
            {"formula_id": "rf-1", "status": "inactive"},
        ]
        port = RankingProjectionPort(ranking_formulas_reader=lambda: formulas)
        items = port.list_ranking_formulas()
        assert [i["formula_id"] for i in items] == ["rf-1", "rf-2"]
        active = port.list_ranking_formulas(status="active")
        assert len(active) == 1


class TestRankingProjectionPortPersonaLeague:
    def test_sorting_and_filtering(self):
        league = [
            {"persona_id": "persona-c", "rank": 3, "league_score": 0.5, "market_scope": ["us_equity"], "status": "active"},
            {"persona_id": "persona-a", "rank": 1, "league_score": 0.9, "market_scope": ["us_equity", "crypto"], "status": "active"},
            {"persona_id": "persona-b", "rank": 2, "league_score": 0.7, "market_scope": ["crypto"], "status": "paused"},
        ]
        port = RankingProjectionPort(persona_league_reader=lambda: league)
        ordered = port.list_persona_league()
        assert [i["persona_id"] for i in ordered] == ["persona-a", "persona-b", "persona-c"]

        crypto_only = port.list_persona_league(market_scope="crypto")
        assert {i["persona_id"] for i in crypto_only} == {"persona-a", "persona-b"}

        active_only = port.list_persona_league(status="active")
        assert {i["persona_id"] for i in active_only} == {"persona-a", "persona-c"}

        entry = port.get_persona_league_entry("persona-b")
        assert entry["rank"] == 2
        assert port.get_persona_league_entry("missing") is None
        assert port.get_persona_league_entry(None) is None

    def test_missing_rank_and_score_default(self):
        league = [{"persona_id": "persona-x"}]
        port = RankingProjectionPort(persona_league_reader=lambda: league)
        items = port.list_persona_league()
        assert items[0]["persona_id"] == "persona-x"


class TestRankingProjectionPortRebalances:
    def test_filtering_and_sorting(self):
        rebalances = [
            {"rebalance_id": "rb-1", "status": "pending", "capital_pool_id": "pool-a", "created_at": "2026-08-28T00:00:00Z"},
            {"rebalance_id": "rb-2", "status": "applied", "capital_pool_id": "pool-a", "created_at": "2026-08-28T00:05:00Z"},
            {"rebalance_id": "rb-3", "status": "pending", "capital_pool_id": "pool-b", "created_at": "2026-08-28T00:10:00Z"},
        ]
        port = RankingProjectionPort(rebalances_reader=lambda: rebalances)
        ordered = port.list_rebalances()
        assert [r["rebalance_id"] for r in ordered] == ["rb-3", "rb-2", "rb-1"]

        pending = port.list_rebalances(status="pending")
        assert len(pending) == 2

        pool_a = port.list_rebalances(pool_id="pool-a")
        assert len(pool_a) == 2

        assert port.get_rebalance("rb-2")["status"] == "applied"
        assert port.get_rebalance("missing") is None


class TestRankingProjectionPortAllocationsAndContainments:
    def test_capital_allocations_sorting_and_filters(self):
        allocations = [
            {"capital_pool_id": "pool-b", "persona_id": "persona-1", "sleeve_id": "sleeve-2"},
            {"capital_pool_id": "pool-a", "persona_id": "persona-2", "capital_sleeve_id": "sleeve-1"},
        ]
        port = RankingProjectionPort(capital_allocations_reader=lambda: allocations)
        items = port.list_capital_allocations()
        assert items[0]["capital_pool_id"] == "pool-a"

        filtered = port.list_capital_allocations(capital_pool_id="pool-b", persona_id="persona-1")
        assert len(filtered) == 1

    def test_containments(self):
        containments = [
            {"persona_id": "persona-1", "status": "active", "executed_at": "2026-08-28T00:00:00Z"},
            {"persona_id": "persona-1", "status": "released", "executed_at": "2026-08-28T01:00:00Z"},
        ]
        port = RankingProjectionPort(containments_reader=lambda: containments)
        ordered = port.list_containments(persona_id="persona-1")
        assert ordered[0]["status"] == "released"  # most recent first

        latest = port.get_persona_containment("persona-1")
        assert latest["status"] == "released"
        assert port.get_persona_containment(None) is None
        assert port.get_persona_containment("nobody") is None


class TestRankingProjectionPortComposition:
    def test_build_persona_capital_ranking_view_composes_independent_readers(self):
        league = [{"persona_id": "persona-1", "rank": 1, "league_score": 0.8}]
        allocations = [{"capital_pool_id": "pool-a", "persona_id": "persona-1", "sleeve_id": "sleeve-1"}]
        containments = [{"persona_id": "persona-1", "status": "active", "executed_at": "2026-08-28T00:00:00Z"}]
        port = RankingProjectionPort(
            persona_league_reader=lambda: league,
            capital_allocations_reader=lambda: allocations,
            containments_reader=lambda: containments,
        )
        view = port.build_persona_capital_ranking_view("persona-1")
        assert view["persona_id"] == "persona-1"
        assert view["league_entry"]["rank"] == 1
        assert len(view["capital_allocations"]) == 1
        assert view["active_containment"]["status"] == "active"
        assert view["is_contained"] is True

    def test_build_persona_capital_ranking_view_no_containment(self):
        port = RankingProjectionPort(
            persona_league_reader=lambda: [],
            capital_allocations_reader=lambda: [],
            containments_reader=lambda: [],
        )
        view = port.build_persona_capital_ranking_view("persona-none")
        assert view["league_entry"] is None
        assert view["capital_allocations"] == []
        assert view["active_containment"] is None
        assert view["is_contained"] is False

    def test_build_persona_capital_ranking_view_empty_persona_id(self):
        port = RankingProjectionPort()
        view = port.build_persona_capital_ranking_view(None)
        assert view["persona_id"] is None
        assert view["league_entry"] is None


class TestRankingProjectionPortSurfaceStatus:
    def test_all_unavailable(self):
        port = RankingProjectionPort()
        status = port.get_surface_status()
        assert status["status"] == "unavailable"
        for surface in status["surfaces"].values():
            assert surface["status"] == "unavailable"

    def test_mixed_ok_and_missing_is_degraded(self):
        port = RankingProjectionPort(rankings_reader=lambda: [{"ranking_id": "r-1"}])
        status = port.get_surface_status()
        assert status["status"] == "degraded"
        assert status["surfaces"]["rankings"]["status"] == "ok"
        assert status["surfaces"]["rebalances"]["status"] == "unavailable"

    def test_reader_raises_marks_unavailable(self):
        def boom():
            raise RuntimeError("boom")
        port = RankingProjectionPort(rankings_reader=boom)
        status = port.get_surface_status()
        assert status["surfaces"]["rankings"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# 2. EvolutionProjectionPort
# ---------------------------------------------------------------------------

class TestEvolutionProjectionPort:
    def test_list_and_get_programs(self):
        programs = [
            {"program_id": "prog-1", "status": "active", "created_at": "2026-08-28T00:00:00Z"},
            {"program_id": "prog-2", "status": "paused", "created_at": "2026-08-28T01:00:00Z"},
        ]
        port = EvolutionProjectionPort(evolution_programs_reader=lambda: programs)
        ordered = port.list_evolution_programs()
        assert [p["program_id"] for p in ordered] == ["prog-2", "prog-1"]

        active = port.list_evolution_programs(status="active")
        assert len(active) == 1

        assert port.get_evolution_program("prog-1")["status"] == "active"
        assert port.get_evolution_program("missing") is None
        assert port.get_evolution_program(None) is None

    def test_list_evolution_decisions_with_status_filter(self):
        decisions = [
            {"decision_id": "dec-1", "status": "pending", "program_id": "prog-1"},
            {"decision_id": "dec-2", "status": "resolved", "program_id": "prog-1"},
        ]
        port = EvolutionProjectionPort(evolution_decisions_reader=lambda: decisions)
        assert len(port.list_evolution_decisions()) == 2
        pending = port.list_evolution_decisions(status="pending")
        assert len(pending) == 1
        assert pending[0]["decision_id"] == "dec-1"

    def test_derived_program_runs(self):
        programs = [{"program_id": "prog-1", "status": "active", "created_at": "2026-08-28T00:00:00Z"}]
        decisions = [
            {
                "decision_id": "dec-1",
                "program_id": "prog-1",
                "status": "resolved",
                "created_at": "2026-08-28T00:00:00Z",
                "resolved_at": "2026-08-28T00:10:00Z",
                "score": 0.9,
                "artifact_ref": "artifact-1",
            },
            {
                "decision_id": "dec-2",
                "program_id": "prog-other",
                "status": "resolved",
            },
        ]
        port = EvolutionProjectionPort(
            evolution_programs_reader=lambda: programs,
            evolution_decisions_reader=lambda: decisions,
        )
        runs = port.list_evolution_program_runs("prog-1")
        assert len(runs) == 1
        assert runs[0]["run_id"] == "dec-1"
        assert runs[0]["program_id"] == "prog-1"
        assert runs[0]["status"] == "resolved"
        assert runs[0]["score"] == 0.9

        # Program does not exist -> empty
        assert port.list_evolution_program_runs("prog-missing") == []
        assert port.list_evolution_program_runs(None) == []

    def test_derived_program_candidates(self):
        programs = [{"program_id": "prog-1", "status": "active", "created_at": "2026-08-28T00:00:00Z"}]
        decisions = [
            {"decision_id": "dec-1", "program_id": "prog-1", "status": "pending", "score": 0.5, "created_at": "2026-08-28T00:00:00Z"},
            {"decision_id": "dec-2", "program_id": "prog-1", "status": "resolved", "score": 0.9},
        ]
        port = EvolutionProjectionPort(
            evolution_programs_reader=lambda: programs,
            evolution_decisions_reader=lambda: decisions,
        )
        candidates = port.list_evolution_program_candidates("prog-1")
        assert len(candidates) == 1
        assert candidates[0]["candidate_id"] == "dec-1"
        assert candidates[0]["status"] == "pending"

        assert port.list_evolution_program_candidates("prog-missing") == []

    def test_surface_status(self):
        port = EvolutionProjectionPort()
        status = port.get_surface_status()
        assert status["status"] == "unavailable"

        port2 = EvolutionProjectionPort(
            evolution_programs_reader=lambda: [{"program_id": "prog-1"}],
            evolution_decisions_reader=lambda: [],
        )
        status2 = port2.get_surface_status()
        assert status2["status"] == "degraded"
        assert status2["surfaces"]["evolution_programs"]["status"] == "ok"
        assert status2["surfaces"]["evolution_decisions"]["status"] == "degraded"


# ---------------------------------------------------------------------------
# 3. Combined facade delegation to ranking/evolution ports
# ---------------------------------------------------------------------------

class TestCombinedFacadeRankingEvolutionDelegation:
    def test_combined_facade_delegates_ranking_and_evolution(self):
        port = PersonaCapitalRuntimeDomainPort(
            ranking_port=RankingProjectionPort(
                rankings_reader=lambda: [{"ranking_id": "rank-1"}],
                persona_league_reader=lambda: [{"persona_id": "persona-1", "rank": 1}],
                rebalances_reader=lambda: [{"rebalance_id": "rb-1", "created_at": "2026-08-28T00:00:00Z"}],
                capital_allocations_reader=lambda: [{"capital_pool_id": "pool-1", "persona_id": "persona-1"}],
                containments_reader=lambda: [{"persona_id": "persona-1", "status": "active"}],
            ),
            evolution_port=EvolutionProjectionPort(
                evolution_programs_reader=lambda: [{"program_id": "prog-1", "created_at": "2026-08-28T00:00:00Z"}],
                evolution_decisions_reader=lambda: [{"decision_id": "dec-1", "program_id": "prog-1", "status": "resolved"}],
            ),
        )

        assert len(port.list_rankings()) == 1
        assert port.get_ranking("rank-1")["ranking_id"] == "rank-1"
        assert len(port.list_persona_league()) == 1
        assert port.get_persona_league_entry("persona-1")["rank"] == 1
        assert len(port.list_rebalances()) == 1
        assert port.get_rebalance("rb-1")["rebalance_id"] == "rb-1"
        assert len(port.list_capital_allocations()) == 1
        assert len(port.list_containments()) == 1
        assert port.get_persona_containment("persona-1")["status"] == "active"

        view = port.build_persona_capital_ranking_view("persona-1")
        assert view["league_entry"]["rank"] == 1

        assert len(port.list_evolution_programs()) == 1
        assert port.get_evolution_program("prog-1")["program_id"] == "prog-1"
        assert len(port.list_evolution_decisions()) == 1
        runs = port.list_evolution_program_runs("prog-1")
        assert len(runs) == 1
        assert runs[0]["run_id"] == "dec-1"
        assert port.list_evolution_program_candidates("prog-1") == []

        surface = port.get_surface_status()
        # ranking_formulas_reader was not injected, so overall ranking status
        # reflects that one missing surface as degraded, not unavailable.
        assert surface["ranking"]["status"] == "degraded"
        assert surface["ranking"]["surfaces"]["rankings"]["status"] == "ok"
        assert surface["ranking"]["surfaces"]["ranking_formulas"]["status"] == "unavailable"
        assert surface["evolution"]["status"] == "ok"
