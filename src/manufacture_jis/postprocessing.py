"""Postprocessing utilities for optimizer assignments."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from manufacture_jis.schemas import HourlyDemand, ScheduleRow, TruckRule


def build_schedule_rows(
    hourly_demands: list[HourlyDemand],
    truck_rules: list[TruckRule],
    assignments: dict[str, tuple[int, datetime]],
) -> list[ScheduleRow]:
    """Expand internal demand-to-truck assignments into output rows."""

    capacity_by_supplier = {rule.supplier: rule.truck_capacity_pallets for rule in truck_rules}
    total_pallets_by_truck: dict[tuple[str, int], int] = defaultdict(int)
    for demand in hourly_demands:
        truck_idx, _ = assignments[demand.demand_key]
        total_pallets_by_truck[(demand.supplier, truck_idx)] += demand.pallets

    rows: list[ScheduleRow] = []
    for demand in hourly_demands:
        truck_idx, arrival_time = assignments[demand.demand_key]
        total_pallets = total_pallets_by_truck[(demand.supplier, truck_idx)]
        capacity = capacity_by_supplier[demand.supplier]
        rows.append(
            ScheduleRow(
                supplier=demand.supplier,
                truck_no=f"{demand.supplier}-T{truck_idx + 1:03d}",
                item_code=demand.item_code,
                mfg_order=demand.mfg_order,
                line=demand.line,
                location=demand.location,
                demand_id=demand.demand_id,
                fulfilled_qty=demand.demand_qty,
                pack_size=demand.pack_size,
                pallets=demand.pallets,
                total_pallets=total_pallets,
                truck_capacity_pallets=capacity,
                load_rate=total_pallets / capacity,
                planned_arrival_time=arrival_time,
            )
        )
    return sorted(rows, key=lambda row: (row.supplier, row.truck_no, row.planned_arrival_time, row.mfg_order))


def summarize_trucks(rows: list[ScheduleRow]) -> dict[str, int]:
    """Return total pallets per output truck number."""

    totals: dict[str, int] = defaultdict(int)
    seen_lines: set[tuple[str, str, str, datetime]] = set()
    for row in rows:
        line_key = (row.truck_no, row.demand_id, row.mfg_order, row.planned_arrival_time)
        if line_key not in seen_lines:
            totals[row.truck_no] = row.total_pallets
            seen_lines.add(line_key)
    return dict(totals)
