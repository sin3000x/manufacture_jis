from datetime import datetime

from manufacture_jis.optimizer import optimize_schedule
from manufacture_jis.schemas import ManufacturingPlan, PackRule, SplitDemand, TruckRule


def test_optimizer_respects_capacity_and_minimizes_small_case_truck_count() -> None:
    plans = [
        ManufacturingPlan(
            line="L1",
            mfg_order="MO1",
            item_code="A",
            plan_qty=20,
            break_hours=0,
            start_time=datetime(2026, 6, 10, 15),
            end_time=datetime(2026, 6, 10, 16),
        ),
        ManufacturingPlan(
            line="L1",
            mfg_order="MO2",
            item_code="B",
            plan_qty=10,
            break_hours=0,
            start_time=datetime(2026, 6, 10, 16),
            end_time=datetime(2026, 6, 10, 17),
        ),
    ]
    split_demands = [
        SplitDemand(item_code="A", supplier="S1", location="L-A", demand_qty=20, demand_id="D-A"),
        SplitDemand(item_code="B", supplier="S1", location="L-B", demand_qty=10, demand_id="D-B"),
    ]
    pack_rules = [PackRule(item_code="A", pack_size=10), PackRule(item_code="B", pack_size=10)]
    truck_rules = [TruckRule(supplier="S1", truck_capacity_pallets=3)]

    rows = optimize_schedule(plans, split_demands, pack_rules, truck_rules)

    assert {row.truck_no for row in rows} == {"S1-T001"}
    assert {row.total_pallets for row in rows} == {3}
    assert all(row.total_pallets <= row.truck_capacity_pallets for row in rows)
    assert all(row.planned_arrival_time.hour not in {1, 2, 7, 12, 13, 18} for row in rows)


def test_optimizer_does_not_mix_suppliers() -> None:
    plans = [
        ManufacturingPlan(
            line="L1",
            mfg_order="MO1",
            item_code="A",
            plan_qty=10,
            break_hours=0,
            start_time=datetime(2026, 6, 10, 15),
            end_time=datetime(2026, 6, 10, 16),
        ),
        ManufacturingPlan(
            line="L1",
            mfg_order="MO2",
            item_code="B",
            plan_qty=10,
            break_hours=0,
            start_time=datetime(2026, 6, 10, 15),
            end_time=datetime(2026, 6, 10, 16),
        ),
    ]
    split_demands = [
        SplitDemand(item_code="A", supplier="S1", location="L-A", demand_qty=10, demand_id="D-A"),
        SplitDemand(item_code="B", supplier="S2", location="L-B", demand_qty=10, demand_id="D-B"),
    ]
    pack_rules = [PackRule(item_code="A", pack_size=10), PackRule(item_code="B", pack_size=10)]
    truck_rules = [
        TruckRule(supplier="S1", truck_capacity_pallets=2),
        TruckRule(supplier="S2", truck_capacity_pallets=2),
    ]

    rows = optimize_schedule(plans, split_demands, pack_rules, truck_rules)

    supplier_by_truck = {}
    for row in rows:
        supplier_by_truck.setdefault(row.truck_no, set()).add(row.supplier)
    assert all(len(suppliers) == 1 for suppliers in supplier_by_truck.values())
    assert {row.supplier for row in rows} == {"S1", "S2"}
