from pilotdb.pilot_engine.optimizer import (
    DEFAULT_HIGH_CARDINALITY_THRESHOLD,
    build_optimization_context,
    enumerate_table_subsets,
    identify_candidate_tables,
    generate_candidate_plans,
    solve_trust_region_plan,
)
from pilotdb.pilot_engine.sampling_plan import (
    SamplingPlan,
    choose_lowest_cost_plan,
    enumerate_sample_sets,
    scalar_rate_plan,
)


def test_scalar_rate_plan_wraps_legacy_rate():
    plan = scalar_rate_plan("lineitem", 0.05)
    assert plan.rate_for("lineitem") == 0.05
    assert plan.max_rate == 0.05
    assert not plan.is_exact()


def test_enumerate_sample_sets_three_tables():
    subsets = list(enumerate_sample_sets(["a", "b", "c"]))
    assert len(subsets) == 7
    assert ("a",) in subsets
    assert ("a", "b", "c") in subsets


def test_choose_lowest_cost_plan_ignores_missing_costs():
    plans = [
        SamplingPlan(rates={"a": 0.1}),
        SamplingPlan(rates={"a": 0.2}, estimated_cost=20.0),
        SamplingPlan(rates={"a": 0.3}, estimated_cost=10.0),
    ]
    best = choose_lowest_cost_plan(plans)
    assert best is not None
    assert best.estimated_cost == 10.0


def test_identify_candidate_tables_filters_small_tables():
    candidates = identify_candidate_tables(
        query_tables=["lineitem", "orders", "nation"],
        table_sizes={
            "lineitem": DEFAULT_HIGH_CARDINALITY_THRESHOLD + 1,
            "orders": DEFAULT_HIGH_CARDINALITY_THRESHOLD,
            "nation": 25,
        },
    )
    names = [candidate.name for candidate in candidates]
    assert names == ["lineitem", "orders"]


def test_enumerate_table_subsets_matches_candidate_count():
    candidates = identify_candidate_tables(
        query_tables=["a", "b", "c"],
        table_sizes={
            "a": DEFAULT_HIGH_CARDINALITY_THRESHOLD,
            "b": DEFAULT_HIGH_CARDINALITY_THRESHOLD,
            "c": DEFAULT_HIGH_CARDINALITY_THRESHOLD,
        },
    )
    subsets = enumerate_table_subsets(candidates)
    assert len(subsets) == 7


def test_build_optimization_context_contains_candidates():
    context = build_optimization_context(
        query_tables=["lineitem", "orders", "nation"],
        table_sizes={
            "lineitem": DEFAULT_HIGH_CARDINALITY_THRESHOLD + 1,
            "orders": 10,
            "nation": 25,
        },
        exact_cost=100.0,
    )
    assert context.exact_cost == 100.0
    assert len(context.candidate_tables) == 1
    assert context.candidate_tables[0].name == "lineitem"



def test_solve_trust_region_plan_returns_bounded_plan():
    plan = solve_trust_region_plan(
        subset=("lineitem", "orders"),
        table_sizes={"lineitem": 10_000_000, "orders": 2_000_000},
        min_rate=0.02,
        max_rate=0.1,
    )
    assert plan is not None
    assert set(plan.rates) == {"lineitem", "orders"}
    assert all(0.02 <= rate <= 0.1 for rate in plan.rates.values())
    assert plan.estimated_cost is not None


def test_generate_candidate_plans_enumerates_optimizer_subsets():
    """[F13] With 2 tables, paper §3.2 requires |S| candidates per subset:
    - (lineitem,) → 1 plan
    - (orders,) → 1 plan
    - (lineitem, orders) → 2 table-weighted + 1 uniform = 3 plans
    Total: 5 plans
    """
    context = build_optimization_context(
        query_tables=["lineitem", "orders"],
        table_sizes={"lineitem": 10_000_000, "orders": 2_000_000},
    )
    plans = generate_candidate_plans(
        context=context,
        table_sizes={"lineitem": 10_000_000, "orders": 2_000_000},
        min_rate=0.02,
        max_rate=0.1,
    )
    # 2 single-table + 3 multi-table = 5 total candidates
    assert len(plans) == 5, f"Expected 5 plans, got {len(plans)}"
    assert all(plan.estimated_cost is not None for plan in plans)

    # Verify multi-table plans have different reason suffixes
    multi_plans = [p for p in plans if len(p.rates) == 2]
    assert len(multi_plans) == 3
    reasons = [p.reason for p in multi_plans]
    assert any("primary=lineitem" in r for r in reasons)
    assert any("primary=orders" in r for r in reasons)
    assert any("uniform" in r for r in reasons)

