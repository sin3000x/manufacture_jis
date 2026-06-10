"""CP-SAT optimizer for supplier truck arrival scheduling."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ortools.sat.python import cp_model

from manufacture_jis.config import OptimizerConfig
from manufacture_jis.preprocessing import allowed_arrival_times, build_hourly_demands, required_truck_upper_bound
from manufacture_jis.schemas import HourlyDemand, ManufacturingPlan, PackRule, ScheduleRow, SplitDemand, TruckRule
from manufacture_jis.postprocessing import build_schedule_rows


class InfeasibleScheduleError(RuntimeError):
    """Raised when CP-SAT cannot find a feasible delivery schedule."""


def optimize_schedule(
    plans: list[ManufacturingPlan],
    split_demands: list[SplitDemand],
    pack_rules: list[PackRule],
    truck_rules: list[TruckRule],
    config: OptimizerConfig | None = None,
) -> list[ScheduleRow]:
    """Run the full preprocessing, optimization, and postprocessing pipeline."""

    config = config or OptimizerConfig()
    hourly_demands = build_hourly_demands(plans, split_demands, pack_rules, truck_rules, config)
    return optimize_hourly_demands(hourly_demands, truck_rules, config)


def optimize_hourly_demands(
    hourly_demands: list[HourlyDemand],
    truck_rules: list[TruckRule],
    config: OptimizerConfig | None = None,
) -> list[ScheduleRow]:
    """Assign hourly demands to supplier-specific trucks and arrival times.

    Model summary:
    - one Boolean variable assigns each hourly demand to one candidate truck;
    - one Boolean variable assigns each used truck to exactly one feasible arrival hour;
    - a demand can be loaded on a truck only when the truck's arrival time is in
      that demand bucket's T-window;
    - objective minimizes truck count first, then rewards pallet utilization.
    """

    config = config or OptimizerConfig()
    if not hourly_demands:
        return []

    capacity_by_supplier = {rule.supplier: rule.truck_capacity_pallets for rule in truck_rules}
    demands_by_supplier: dict[str, list[HourlyDemand]] = defaultdict(list)
    for demand in hourly_demands:
        if demand.supplier not in capacity_by_supplier:
            raise ValueError(f"missing truck rule for supplier={demand.supplier}")
        demands_by_supplier[demand.supplier].append(demand)

    model = cp_model.CpModel()
    truck_used: dict[tuple[str, int], cp_model.IntVar] = {}
    assign: dict[tuple[str, int], cp_model.IntVar] = {}
    arrival_choice: dict[tuple[str, int, datetime], cp_model.IntVar] = {}
    truck_candidates_by_supplier: dict[str, list[int]] = {}
    all_arrival_times_by_supplier: dict[str, list[datetime]] = {}

    for supplier, demands in demands_by_supplier.items():
        capacity = capacity_by_supplier[supplier]
        total_pallets = sum(demand.pallets for demand in demands)
        truck_count = required_truck_upper_bound(total_pallets, capacity)
        truck_candidates_by_supplier[supplier] = list(range(truck_count))
        arrival_times = sorted(
            {
                arrival_time
                for demand in demands
                for arrival_time in allowed_arrival_times(demand.consumption_time, config)
            }
        )
        if not arrival_times:
            raise ValueError(f"no feasible arrival times for supplier={supplier}")
        all_arrival_times_by_supplier[supplier] = arrival_times

        for truck_idx in range(truck_count):
            used_var = model.NewBoolVar(f"used[{supplier},{truck_idx}]")
            truck_used[(supplier, truck_idx)] = used_var
            choices: list[cp_model.IntVar] = []
            for arrival_time in arrival_times:
                choice_var = model.NewBoolVar(f"arrival[{supplier},{truck_idx},{arrival_time.isoformat()}]")
                arrival_choice[(supplier, truck_idx, arrival_time)] = choice_var
                choices.append(choice_var)
            model.Add(sum(choices) == used_var)

            assigned_pallets: list[cp_model.LinearExpr] = []
            for demand in demands:
                assign_var = model.NewBoolVar(f"assign[{demand.demand_key},{truck_idx}]")
                assign[(demand.demand_key, truck_idx)] = assign_var
                model.Add(assign_var <= used_var)
                assigned_pallets.append(demand.pallets * assign_var)

                feasible_arrivals = set(allowed_arrival_times(demand.consumption_time, config))
                feasible_choice_vars = [
                    arrival_choice[(supplier, truck_idx, arrival_time)]
                    for arrival_time in arrival_times
                    if arrival_time in feasible_arrivals
                ]
                if not feasible_choice_vars:
                    raise ValueError(f"no feasible truck arrival for demand_key={demand.demand_key}")
                model.Add(assign_var <= sum(feasible_choice_vars))

            model.Add(sum(assigned_pallets) <= capacity * used_var)

        for demand in demands:
            model.Add(sum(assign[(demand.demand_key, truck_idx)] for truck_idx in range(truck_count)) == 1)

    used_terms = list(truck_used.values())
    load_terms = [
        demand.pallets * assign[(demand.demand_key, truck_idx)]
        for supplier, demands in demands_by_supplier.items()
        for demand in demands
        for truck_idx in truck_candidates_by_supplier[supplier]
    ]
    max_pallets = sum(demand.pallets for demand in hourly_demands)
    primary_weight = max(1, max_pallets * config.maximize_load_weight + 1)
    model.Minimize(primary_weight * sum(used_terms) - config.maximize_load_weight * sum(load_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.solver_time_limit_seconds
    solver.parameters.num_search_workers = config.num_search_workers
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise InfeasibleScheduleError(f"no feasible schedule found; cp-sat status={solver.StatusName(status)}")

    assignments: dict[str, tuple[int, datetime]] = {}
    for supplier, demands in demands_by_supplier.items():
        for demand in demands:
            for truck_idx in truck_candidates_by_supplier[supplier]:
                if solver.BooleanValue(assign[(demand.demand_key, truck_idx)]):
                    selected_arrival = next(
                        arrival_time
                        for arrival_time in all_arrival_times_by_supplier[supplier]
                        if solver.BooleanValue(arrival_choice[(supplier, truck_idx, arrival_time)])
                    )
                    assignments[demand.demand_key] = (truck_idx, selected_arrival)
                    break

    return build_schedule_rows(hourly_demands, truck_rules, assignments)
