from __future__ import annotations

import math
from dataclasses import dataclass

from manufacture_jis.base import Concumption


@dataclass
class _VehicleState:
    feasible_lb: int
    feasible_ub: int
    item_to_qty: dict[str, int]
    total_pallets: int


def _has_valid_arrival(lb: int, ub: int, rest_times: list[int]) -> bool:
    for h in range(lb, ub + 1):
        if (h % 24) not in rest_times:
            return True
    return False


def _greedy_pack(
    consumptions: list[Concumption],
    capacity: int,
    pc_per_pallet: dict[str, int],
    rest_times: list[int],
) -> int:
    vehicles: list[_VehicleState] = []

    for c in consumptions:
        pc = pc_per_pallet[c.item]
        c_pallets = math.ceil(c.qty / pc)
        assigned = False

        for v in vehicles:
            new_lb = max(v.feasible_lb, c.arrival_lb)
            new_ub = min(v.feasible_ub, c.arrival_ub)
            if new_lb > new_ub:
                continue
            if not _has_valid_arrival(new_lb, new_ub, rest_times):
                continue

            old_qty = v.item_to_qty.get(c.item, 0)
            new_qty = old_qty + c.qty
            delta = math.ceil(new_qty / pc) - math.ceil(old_qty / pc)

            if v.total_pallets + delta <= capacity:
                v.feasible_lb = new_lb
                v.feasible_ub = new_ub
                v.item_to_qty[c.item] = new_qty
                v.total_pallets += delta
                assigned = True
                break

        if not assigned:
            vehicles.append(
                _VehicleState(
                    feasible_lb=c.arrival_lb,
                    feasible_ub=c.arrival_ub,
                    item_to_qty={c.item: c.qty},
                    total_pallets=c_pallets,
                )
            )

    return len(vehicles)


def compute_vehicle_bounds(
    consumptions: list[Concumption],
    s_to_i_set: dict[str, set[str]],
    supplier_capacities: dict[str, int],
    pc_per_pallet: dict[str, int],
    rest_times: list[int],
) -> dict[str, int]:
    """为每个供应商计算所需车辆数的启发式上界。

    对每个供应商分别运行贪心装箱算法，尝试两种消费排序策略，
    取较小值作为该供应商的车辆上界。
    """
    bounds: dict[str, int] = {}

    for supplier, items in s_to_i_set.items():
        cs = [c for c in consumptions if c.item in items]
        if not cs:
            bounds[supplier] = 0
            continue

        capacity = supplier_capacities[supplier]
        count_edf = _greedy_pack(
            sorted(cs, key=lambda c: c.arrival_ub), capacity, pc_per_pallet, rest_times
        )
        count_esf = _greedy_pack(
            sorted(cs, key=lambda c: c.arrival_lb), capacity, pc_per_pallet, rest_times
        )
        bounds[supplier] = max(min(count_edf, count_esf), 1)

    return bounds
