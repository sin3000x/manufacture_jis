"""Input and output validation helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from manufacture_jis.config import OptimizerConfig
from manufacture_jis.schemas import HourlyDemand, ManufacturingPlan, PackRule, ScheduleRow, SplitDemand, TruckRule


def validate_manufacturing_plans(plans: list[ManufacturingPlan]) -> None:
    for plan in plans:
        if plan.plan_qty <= 0:
            raise ValueError(f"plan_qty must be positive for mfg_order={plan.mfg_order}")
        duration_hours = int((plan.end_time - plan.start_time).total_seconds() // 3600) - plan.break_hours
        if duration_hours <= 0:
            raise ValueError(f"actual processing duration must be positive for mfg_order={plan.mfg_order}")


def validate_split_demands(split_demands: list[SplitDemand]) -> None:
    for demand in split_demands:
        if demand.demand_qty <= 0:
            raise ValueError(f"demand_qty must be positive for demand_id={demand.demand_id}")


def validate_pack_rules(pack_rules: list[PackRule]) -> None:
    for rule in pack_rules:
        if rule.pack_size <= 0:
            raise ValueError(f"pack_size must be positive for item_code={rule.item_code}")


def validate_truck_rules(truck_rules: list[TruckRule]) -> None:
    for rule in truck_rules:
        if rule.truck_capacity_pallets <= 0:
            raise ValueError(f"truck_capacity_pallets must be positive for supplier={rule.supplier}")


def validate_plan_and_split_totals(plans: list[ManufacturingPlan], split_demands: list[SplitDemand]) -> None:
    plan_total_by_item: dict[str, int] = defaultdict(int)
    split_total_by_item: dict[str, int] = defaultdict(int)
    for plan in plans:
        plan_total_by_item[plan.item_code] += plan.plan_qty
    for demand in split_demands:
        split_total_by_item[demand.item_code] += demand.demand_qty
    if dict(plan_total_by_item) != dict(split_total_by_item):
        raise ValueError("split demand totals must match manufacturing plan totals by item_code")


def validate_schedule(
    schedule: list[ScheduleRow],
    hourly_demands: list[HourlyDemand],
    config: OptimizerConfig,
) -> None:
    """Validate key output invariants required by AGENTS.md."""

    hourly_by_key = {demand.demand_key: demand for demand in hourly_demands}
    demand_qty_by_key = {demand.demand_key: demand.demand_qty for demand in hourly_demands}
    fulfilled_by_key: dict[str, int] = defaultdict(int)
    suppliers_by_truck: dict[str, set[str]] = defaultdict(set)
    total_pallets_by_truck: dict[str, int] = {}
    capacity_by_truck: dict[str, int] = {}

    for row in schedule:
        demand = hourly_by_key.get(_row_demand_key(row.planned_arrival_time, row))
        # The public output does not expose the internal demand key, so this
        # validation focuses on aggregate invariants and arrival feasibility.
        suppliers_by_truck[row.truck_no].add(row.supplier)
        total_pallets_by_truck[row.truck_no] = row.total_pallets
        capacity_by_truck[row.truck_no] = row.truck_capacity_pallets
        if row.planned_arrival_time.hour in config.forbidden_arrival_hours:
            raise ValueError(f"truck_no={row.truck_no} arrives in a forbidden hour")
        if row.total_pallets > row.truck_capacity_pallets:
            raise ValueError(f"truck_no={row.truck_no} exceeds truck capacity")
        if demand is not None:
            fulfilled_by_key[demand.demand_key] += row.fulfilled_qty

    for truck_no, suppliers in suppliers_by_truck.items():
        if len(suppliers) != 1:
            raise ValueError(f"truck_no={truck_no} contains multiple suppliers")
    for demand_key, fulfilled_qty in fulfilled_by_key.items():
        if fulfilled_qty != demand_qty_by_key[demand_key]:
            raise ValueError(f"demand_key={demand_key} fulfilled quantity mismatch")


def _row_demand_key(_: datetime, row: ScheduleRow) -> str:
    return f"{row.demand_id}|{row.mfg_order}|{row.item_code}|{row.line}|{row.planned_arrival_time.isoformat()}"
