"""Preprocessing for manufacturing plans and supplier split demands."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

from manufacture_jis.config import OptimizerConfig
from manufacture_jis.schemas import HourlyDemand, ManufacturingPlan, PackRule, ScheduleInputData, SplitDemand, TruckRule
from manufacture_jis.validation import (
    validate_manufacturing_plans,
    validate_pack_rules,
    validate_plan_and_split_totals,
    validate_split_demands,
    validate_truck_rules,
)


@dataclass(frozen=True)
class HourlyAllocationContext:
    plan: ManufacturingPlan
    split: SplitDemand
    pack_size: int
    demand_key: str
    demand_qty: int
    consumption_time: datetime
    earliest_arrival_time: datetime
    latest_arrival_time: datetime


class HourlyDemandBuilder:
    """Build validated hourly demand records from raw input data."""

    def __init__(self, config: OptimizerConfig | None = None) -> None:
        self.config = config or OptimizerConfig()

    def build(self, input_data: ScheduleInputData) -> list[HourlyDemand]:
        return self.build_hourly_demands(
            input_data.plans,
            input_data.split_demands,
            input_data.pack_rules,
            input_data.truck_rules,
        )

    def ceil_div(self, value: int, divisor: int) -> int:
        if divisor <= 0:
            raise ValueError("divisor must be positive")
        return -(-value // divisor)

    def split_plan_to_hourly_quantities(self, plan: ManufacturingPlan) -> list[tuple[datetime, int]]:
        duration_hours = int((plan.end_time - plan.start_time).total_seconds() // 3600) - plan.break_hours
        if duration_hours <= 0:
            raise ValueError(f"actual processing duration must be positive for mfg_order={plan.mfg_order}")
        base_qty = plan.plan_qty // duration_hours
        remainder = plan.plan_qty % duration_hours
        buckets: list[tuple[datetime, int]] = []
        for offset in range(duration_hours):
            qty = base_qty + (remainder if offset == duration_hours - 1 else 0)
            buckets.append((plan.start_time + timedelta(hours=offset), qty))
        return buckets

    def allowed_arrival_times(self, consumption_time: datetime) -> list[datetime]:
        arrivals: list[datetime] = []
        for offset in range(self.config.arrival_window_start_hours, self.config.arrival_window_end_hours + 1):
            arrival_time = consumption_time + timedelta(hours=offset)
            if arrival_time.hour not in self.config.forbidden_arrival_hours:
                arrivals.append(arrival_time)
        return arrivals

    def build_hourly_demands(
        self,
        plans: list[ManufacturingPlan],
        split_demands: list[SplitDemand],
        pack_rules: list[PackRule],
        truck_rules: list[TruckRule],
    ) -> list[HourlyDemand]:
        validate_manufacturing_plans(plans)
        validate_split_demands(split_demands)
        validate_pack_rules(pack_rules)
        validate_truck_rules(truck_rules)
        validate_plan_and_split_totals(plans, split_demands)

        pack_size_by_item = {rule.item_code: rule.pack_size for rule in pack_rules}
        truck_capacity_by_supplier = {rule.supplier: rule.truck_capacity_pallets for rule in truck_rules}
        splits_by_item: dict[str, deque[SplitDemand]] = defaultdict(deque)
        for split in split_demands:
            if split.item_code not in pack_size_by_item:
                raise ValueError(f"missing pack rule for item_code={split.item_code}")
            if split.supplier not in truck_capacity_by_supplier:
                raise ValueError(f"missing truck rule for supplier={split.supplier}")
            splits_by_item[split.item_code].append(split)

        remaining_by_demand_id = {split.demand_id: split.demand_qty for split in split_demands}
        hourly_demands: list[HourlyDemand] = []
        sequence = 0

        for plan in plans:
            if plan.item_code not in pack_size_by_item:
                raise ValueError(f"missing pack rule for item_code={plan.item_code}")
            pack_size = pack_size_by_item[plan.item_code]
            for consumption_time, bucket_qty in self.split_plan_to_hourly_quantities(plan):
                remaining_bucket_qty = bucket_qty
                while remaining_bucket_qty > 0:
                    if not splits_by_item[plan.item_code]:
                        raise ValueError(f"insufficient split demand for item_code={plan.item_code}")
                    split = splits_by_item[plan.item_code][0]
                    alloc_qty = min(remaining_bucket_qty, remaining_by_demand_id[split.demand_id])
                    arrivals = self.allowed_arrival_times(consumption_time)
                    if not arrivals:
                        raise ValueError(
                            f"no feasible arrival time for mfg_order={plan.mfg_order} "
                            f"at consumption_time={consumption_time.isoformat()}"
                        )
                    sequence += 1
                    hourly_demands.append(
                        HourlyDemand(
                            demand_key=f"D{sequence:06d}",
                            supplier=split.supplier,
                            item_code=plan.item_code,
                            mfg_order=plan.mfg_order,
                            line=plan.line,
                            location=split.location,
                            demand_id=split.demand_id,
                            demand_qty=alloc_qty,
                            pack_size=pack_size,
                            pallets=self.ceil_div(alloc_qty, pack_size),
                            consumption_time=consumption_time,
                            earliest_arrival_time=min(arrivals),
                            latest_arrival_time=max(arrivals),
                        )
                    )
                    remaining_bucket_qty -= alloc_qty
                    remaining_by_demand_id[split.demand_id] -= alloc_qty
                    if remaining_by_demand_id[split.demand_id] == 0:
                        splits_by_item[plan.item_code].popleft()

        if any(quantity != 0 for quantity in remaining_by_demand_id.values()):
            raise ValueError("unused split demand remains after hourly allocation")
        return hourly_demands

    def required_truck_upper_bound(self, total_pallets: int, capacity: int) -> int:
        return max(1, ceil(total_pallets / capacity))


def build_hourly_demands(
    plans: list[ManufacturingPlan],
    split_demands: list[SplitDemand],
    pack_rules: list[PackRule],
    truck_rules: list[TruckRule],
    config: OptimizerConfig | None = None,
) -> list[HourlyDemand]:
    return HourlyDemandBuilder(config).build_hourly_demands(plans, split_demands, pack_rules, truck_rules)


def allowed_arrival_times(consumption_time: datetime, config: OptimizerConfig) -> list[datetime]:
    return HourlyDemandBuilder(config).allowed_arrival_times(consumption_time)


def required_truck_upper_bound(total_pallets: int, capacity: int) -> int:
    return max(1, ceil(total_pallets / capacity))
