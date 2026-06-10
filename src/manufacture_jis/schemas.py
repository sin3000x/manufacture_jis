"""Typed records used by the scheduling pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ManufacturingPlan:
    line: str
    mfg_order: str
    item_code: str
    plan_qty: int
    break_hours: int
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class ScheduleInputData:
    plans: list[ManufacturingPlan]
    split_demands: list[SplitDemand]
    pack_rules: list[PackRule]
    truck_rules: list[TruckRule]


@dataclass(frozen=True)
class ScheduleOutputData:
    rows: list[ScheduleRow]


@dataclass(frozen=True)
class SplitDemand:
    item_code: str
    supplier: str
    location: str
    demand_qty: int
    demand_id: str


@dataclass(frozen=True)
class PackRule:
    item_code: str
    pack_size: int


@dataclass(frozen=True)
class TruckRule:
    supplier: str
    truck_capacity_pallets: int


@dataclass(frozen=True)
class HourlyDemand:
    demand_key: str
    supplier: str
    item_code: str
    mfg_order: str
    line: str
    location: str
    demand_id: str
    demand_qty: int
    pack_size: int
    pallets: int
    consumption_time: datetime
    earliest_arrival_time: datetime
    latest_arrival_time: datetime


@dataclass(frozen=True)
class ScheduleRow:
    supplier: str
    truck_no: str
    item_code: str
    mfg_order: str
    line: str
    location: str
    demand_id: str
    fulfilled_qty: int
    pack_size: int
    pallets: int
    total_pallets: int
    truck_capacity_pallets: int
    load_rate: float
    planned_arrival_time: datetime
