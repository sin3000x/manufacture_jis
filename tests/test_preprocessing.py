from datetime import datetime

import pytest

from manufacture_jis.config import OptimizerConfig
from manufacture_jis.preprocessing import allowed_arrival_times, build_hourly_demands, ceil_div, split_plan_to_hourly_quantities
from manufacture_jis.schemas import ManufacturingPlan, PackRule, SplitDemand, TruckRule


def test_split_plan_to_hourly_quantities_preserves_total() -> None:
    plan = ManufacturingPlan(
        line="L1",
        mfg_order="MO1",
        item_code="A",
        plan_qty=10,
        break_hours=0,
        start_time=datetime(2026, 6, 10, 8),
        end_time=datetime(2026, 6, 10, 11),
    )

    buckets = split_plan_to_hourly_quantities(plan)

    assert [qty for _, qty in buckets] == [3, 3, 4]
    assert sum(qty for _, qty in buckets) == plan.plan_qty


def test_ceil_div_for_pack_rule_rounding() -> None:
    assert ceil_div(1, 10) == 1
    assert ceil_div(10, 10) == 1
    assert ceil_div(11, 10) == 2


def test_allowed_arrival_times_filters_forbidden_hours_and_window() -> None:
    config = OptimizerConfig(forbidden_arrival_hours=frozenset({1, 2, 7, 12, 13, 18}))
    arrivals = allowed_arrival_times(datetime(2026, 6, 10, 15), config)

    assert datetime(2026, 6, 10, 7) not in arrivals
    assert datetime(2026, 6, 10, 12) not in arrivals
    assert arrivals == [datetime(2026, 6, 10, 8), datetime(2026, 6, 10, 9), datetime(2026, 6, 10, 10), datetime(2026, 6, 10, 11)]


def test_build_hourly_demands_connects_split_pack_and_time_window() -> None:
    plans = [
        ManufacturingPlan(
            line="L1",
            mfg_order="MO1",
            item_code="A",
            plan_qty=10,
            break_hours=0,
            start_time=datetime(2026, 6, 10, 15),
            end_time=datetime(2026, 6, 10, 16),
        )
    ]
    split_demands = [SplitDemand(item_code="A", supplier="S1", location="LOC1", demand_qty=10, demand_id="D1")]
    pack_rules = [PackRule(item_code="A", pack_size=6)]
    truck_rules = [TruckRule(supplier="S1", truck_capacity_pallets=10)]

    demands = build_hourly_demands(plans, split_demands, pack_rules, truck_rules)

    assert len(demands) == 1
    assert demands[0].pallets == 2
    assert demands[0].earliest_arrival_time == datetime(2026, 6, 10, 8)
    assert demands[0].latest_arrival_time == datetime(2026, 6, 10, 11)


def test_build_hourly_demands_rejects_mismatched_plan_and_split_totals() -> None:
    plans = [
        ManufacturingPlan(
            line="L1",
            mfg_order="MO1",
            item_code="A",
            plan_qty=10,
            break_hours=0,
            start_time=datetime(2026, 6, 10, 15),
            end_time=datetime(2026, 6, 10, 16),
        )
    ]
    split_demands = [SplitDemand(item_code="A", supplier="S1", location="LOC1", demand_qty=9, demand_id="D1")]

    with pytest.raises(ValueError, match="split demand totals"):
        build_hourly_demands(
            plans,
            split_demands,
            [PackRule(item_code="A", pack_size=6)],
            [TruckRule(supplier="S1", truck_capacity_pallets=10)],
        )
