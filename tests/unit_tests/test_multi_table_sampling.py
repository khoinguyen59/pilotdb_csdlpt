from pilotdb.pilot_engine.multi_table_sampling import (
    apply_sampling_plan_template,
    build_empty_sampling_args,
    sampling_format_args,
    sampling_key,
    sampling_placeholder,
    sampled_rate_for_output,
)
from pilotdb.pilot_engine.sampling_plan import SamplingPlan


def test_sampling_placeholder_is_stable():
    assert sampling_placeholder("lineitem") == "{sampling_method_lineitem}"
    assert sampling_placeholder("schema.lineitem") == "{sampling_method_schema_lineitem}"


def test_sampling_format_args_uses_percent_rates():
    plan = SamplingPlan(rates={"lineitem": 0.05})
    args = sampling_format_args(plan, "duckdb")
    assert args["sampling_method_lineitem"] == "TABLESAMPLE SYSTEM(5.0%)"


def test_apply_sampling_plan_template_supports_per_table_placeholder():
    plan = SamplingPlan(rates={"lineitem": 0.05})
    query = "SELECT * FROM lineitem {sampling_method_lineitem}"
    rendered = apply_sampling_plan_template(query, plan, "postgres")
    assert rendered == "SELECT * FROM lineitem TABLESAMPLE SYSTEM (5.0)"


def test_apply_sampling_plan_template_keeps_legacy_placeholder():
    plan = SamplingPlan(rates={"lineitem": 0.05})
    query = "SELECT * FROM lineitem {sampling_method}"
    rendered = apply_sampling_plan_template(query, plan, "duckdb")
    assert rendered == "SELECT * FROM lineitem TABLESAMPLE SYSTEM(5.0%)"


def test_build_empty_sampling_args():
    args = build_empty_sampling_args({"lineitem": [], "orders": []})
    assert args == {"sampling_method_lineitem": "", "sampling_method_orders": ""}



def test_sampling_key_sanitizes_table_names():
    assert sampling_key("schema.lineitem") == "sampling_method_schema_lineitem"



def test_sampled_rate_for_output_multiplies_vector_rates():
    plan = SamplingPlan(rates={"lineitem": 0.1, "orders": 0.2})
    assert sampled_rate_for_output(plan) == 0.020000000000000004
