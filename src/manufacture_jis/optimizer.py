"""CP-SAT optimizer for supplier truck arrival scheduling."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from ortools.sat.python import cp_model

from manufacture_jis.config import OptimizerConfig
from manufacture_jis.postprocessing import SchedulePostProcessor
from manufacture_jis.preprocessing import HourlyDemandBuilder
from manufacture_jis.schemas import (
    HourlyDemand,
    ManufacturingPlan,
    PackRule,
    ScheduleInputData,
    ScheduleRow,
    SplitDemand,
    TruckRule,
)


class InfeasibleScheduleError(RuntimeError):
    """Raised when CP-SAT cannot find a feasible delivery schedule."""


@dataclass(frozen=True)
class ScheduleAssignment:
    truck_index: int
    arrival_time: datetime


class ScheduleCPSATModel:
    """Internal CP-SAT model wrapper."""

    def __init__(self, hourly_demands: list[HourlyDemand], truck_rules: list[TruckRule], config: OptimizerConfig) -> None:
        self.hourly_demands = hourly_demands
        self.truck_rules = truck_rules
        self.config = config
        self.capacity_by_supplier = {rule.supplier: rule.truck_capacity_pallets for rule in truck_rules}
        self.demands_by_supplier: dict[str, list[HourlyDemand]] = defaultdict(list)
        for demand in hourly_demands:
            if demand.supplier not in self.capacity_by_supplier:
                raise ValueError(f"missing truck rule for supplier={demand.supplier}")
            self.demands_by_supplier[demand.supplier].append(demand)

        self.model = cp_model.CpModel()
        self.truck_used: dict[tuple[str, int], cp_model.IntVar] = {}
        self.assign: dict[tuple[str, int], cp_model.IntVar] = {}
        self.arrival_choice: dict[tuple[str, int, datetime], cp_model.IntVar] = {}
        self.truck_candidates_by_supplier: dict[str, list[int]] = {}
        self.all_arrival_times_by_supplier: dict[str, list[datetime]] = {}

    def solve(self) -> dict[str, ScheduleAssignment]:
        self.build_model()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.config.solver_time_limit_seconds
        solver.parameters.num_search_workers = self.config.num_search_workers
        status = solver.Solve(self.model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise InfeasibleScheduleError(f"no feasible schedule found; cp-sat status={solver.StatusName(status)}")
        return self.extract_assignments(solver)

    def build_model(self) -> None:
        for supplier, demands in self.demands_by_supplier.items():
            self._build_supplier_model(supplier, demands)
        self._set_objective()

    def _build_supplier_model(self, supplier: str, demands: list[HourlyDemand]) -> None:
        capacity = self.capacity_by_supplier[supplier]
        total_pallets = sum(demand.pallets for demand in demands)
        truck_count = HourlyDemandBuilder.required_truck_upper_bound(total_pallets, capacity)
        self.truck_candidates_by_supplier[supplier] = list(range(truck_count))
        arrival_times = sorted(
            {
                arrival_time
                for demand in demands
                for arrival_time in self.input_builder.allowed_arrival_times(demand.consumption_time)
            }
        )
        if not arrival_times:
            raise ValueError(f"no feasible arrival times for supplier={supplier}")
        self.all_arrival_times_by_supplier[supplier] = arrival_times

        for truck_idx in range(truck_count):
            used_var = self.model.NewBoolVar(f"used[{supplier},{truck_idx}]")
            self.truck_used[(supplier, truck_idx)] = used_var
            choices: list[cp_model.IntVar] = []
            for arrival_time in arrival_times:
                choice_var = self.model.NewBoolVar(f"arrival[{supplier},{truck_idx},{arrival_time.isoformat()}]")
                self.arrival_choice[(supplier, truck_idx, arrival_time)] = choice_var
                choices.append(choice_var)
            self.model.Add(sum(choices) == used_var)

            assigned_pallets: list[cp_model.LinearExpr] = []
            for demand in demands:
                assign_var = self.model.NewBoolVar(f"assign[{demand.demand_key},{truck_idx}]")
                self.assign[(demand.demand_key, truck_idx)] = assign_var
                self.model.Add(assign_var <= used_var)
                assigned_pallets.append(demand.pallets * assign_var)

                feasible_choice_vars = [
                    self.arrival_choice[(supplier, truck_idx, arrival_time)]
                    for arrival_time in arrival_times
                    if arrival_time in self.input_builder.allowed_arrival_times(demand.consumption_time)
                ]
                if not feasible_choice_vars:
                    raise ValueError(f"no feasible truck arrival for demand_key={demand.demand_key}")
                self.model.Add(assign_var <= sum(feasible_choice_vars))

            self.model.Add(sum(assigned_pallets) <= capacity * used_var)

        for demand in demands:
            self.model.Add(sum(self.assign[(demand.demand_key, truck_idx)] for truck_idx in range(truck_count)) == 1)

    def _set_objective(self) -> None:
        used_terms = list(self.truck_used.values())
        load_terms = [
            demand.pallets * self.assign[(demand.demand_key, truck_idx)]
            for supplier, demands in self.demands_by_supplier.items()
            for demand in demands
            for truck_idx in self.truck_candidates_by_supplier[supplier]
        ]
        max_pallets = sum(demand.pallets for demand in self.hourly_demands)
        primary_weight = max(1, max_pallets * self.config.maximize_load_weight + 1)
        self.model.Minimize(primary_weight * sum(used_terms) - self.config.maximize_load_weight * sum(load_terms))

    def extract_assignments(self, solver: cp_model.CpSolver) -> dict[str, ScheduleAssignment]:
        assignments: dict[str, ScheduleAssignment] = {}
        for supplier, demands in self.demands_by_supplier.items():
            for demand in demands:
                for truck_idx in self.truck_candidates_by_supplier[supplier]:
                    if solver.BooleanValue(self.assign[(demand.demand_key, truck_idx)]):
                        selected_arrival = next(
                            arrival_time
                            for arrival_time in self.all_arrival_times_by_supplier[supplier]
                            if solver.BooleanValue(self.arrival_choice[(supplier, truck_idx, arrival_time)])
                        )
                        assignments[demand.demand_key] = ScheduleAssignment(truck_idx, selected_arrival)
                        break
        return assignments


class ScheduleOptimizer:
    """Object-oriented wrapper around preprocessing, model building, and solving."""

    def __init__(self, config: OptimizerConfig | None = None) -> None:
        self.config = config or OptimizerConfig()
        self.input_builder = HourlyDemandBuilder(self.config)
        self.model_factory = ScheduleCPSATModel
        self.post_processor = SchedulePostProcessor()

    def optimize_schedule(
        self,
        plans: list[ManufacturingPlan],
        split_demands: list[SplitDemand],
        pack_rules: list[PackRule],
        truck_rules: list[TruckRule],
    ) -> list[ScheduleRow]:
        input_data = ScheduleInputData(plans, split_demands, pack_rules, truck_rules)
        return self.optimize_input_data(input_data)

    def optimize_input_data(self, input_data: ScheduleInputData) -> list[ScheduleRow]:
        hourly_demands = self.build_hourly_demands(input_data)
        return self.optimize_hourly_demands(hourly_demands, input_data.truck_rules)

    def build_hourly_demands(self, input_data: ScheduleInputData) -> list[HourlyDemand]:
        return self.input_builder.build(input_data)

    def optimize_hourly_demands(
        self,
        hourly_demands: list[HourlyDemand],
        truck_rules: list[TruckRule],
    ) -> list[ScheduleRow]:
        if not hourly_demands:
            return []

        problem = self.model_factory(hourly_demands, truck_rules, self.config)
        assignments = problem.solve()
        return self.post_processor.build_schedule_rows(hourly_demands, truck_rules, assignments)


def optimize_schedule(
    plans: list[ManufacturingPlan],
    split_demands: list[SplitDemand],
    pack_rules: list[PackRule],
    truck_rules: list[TruckRule],
    config: OptimizerConfig | None = None,
) -> list[ScheduleRow]:
    return ScheduleOptimizer(config).optimize_schedule(plans, split_demands, pack_rules, truck_rules)


def optimize_hourly_demands(
    hourly_demands: list[HourlyDemand],
    truck_rules: list[TruckRule],
    config: OptimizerConfig | None = None,
) -> list[ScheduleRow]:
    return ScheduleOptimizer(config).optimize_hourly_demands(hourly_demands, truck_rules)
