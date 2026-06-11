import pandas as pd
import math
from ortools.sat.python import cp_model

from manufacture_jis.jis_data import JISData


class JISCPModel:
    def __init__(self, data: JISData):
        self.data = data
        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()

    def run(self):
        self.build()
        self.solve()
        self.extract_solution()

    def build(self):
        self._add_variables()
        self._add_constraints()
        self._set_objective()

    def solve(self) -> str:
        self.solver.parameters.log_search_progress = True
        self.solver.parameters.max_time_in_seconds = 120
        self.solver.parameters.num_search_workers = 8
        status = self.solver.solve(self.model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"Solver status: {status}")
        return self.solver.response_stats()

    def extract_solution(self):
        self.assign_sol_df: pd.DataFrame = pd.DataFrame(
            [
                (c, v)
                for (c, v), assign in self.assign.items()
                if self.solver.boolean_value(assign)
            ],
            columns=["c", "v"],
        )
        self.use_vehicle_sol_set: set[str] = set(
            v
            for v, use_vehicle in self.use_vehicle.items()
            if self.solver.boolean_value(use_vehicle)
        )
        self.arrival_sol_dict: dict[str, int] = {
            v: self.solver.value(self.arrival[v])
            for v in self.use_vehicle_sol_set
        }
        self.loaded_pallets_sol_df: pd.DataFrame = pd.DataFrame(
            [
                (v, i, self.solver.value(loaded_pallets))
                for (v, i), loaded_pallets in self.loaded_pallets.items()
            ],
            columns=["v", "i", "loaded_pallets"],
        )

    def _add_variables(self):
        data = self.data
        # 车辆v是否启用
        self.use_vehicle: dict[str, cp_model.BoolVar] = {
            v: self.model.new_bool_var(f"use_vehicle_{v}") for v in data.v2vehicle
        }
        # 消耗c是否被车辆v送
        self.assign: dict[tuple[str, str], cp_model.BoolVar] = {
            (c, v): self.model.new_bool_var(f"assign_{c}_{v}")
            for c in data.c2consumption
            for v in data.c_to_v_set[c]
        }
        # 车辆v到达时间
        self.arrival: dict[str, cp_model.IntVar] = {
            v: self.model.new_int_var(0, data.t_max, f"arrival_{v}")
            for v in data.v2vehicle
        }
        # 车辆v装载的物料i的板数
        self.loaded_pallets: dict[tuple[str, str], cp_model.IntVar] = {
            (v, i): self.model.new_int_var(
                0, data.v2vehicle[v].capacity, f"loaded_pallet_{v}"
            )
            for i in data.item_set
            for v in data.i_to_v_set[i]
        }

    def _add_constraints(self):
        self._constraint_exactly_one_assign()
        self._constraint_arrival()
        self._constraint_use_vehicle()
        self._constraint_loaded_pallets()
        self._constraint_vehicle_capacity()
        self._constraint_supplier_item_qty()
        self._break_symmetry()

    def _set_objective(self):
        """最小化启用车辆数量"""
        self.model.minimize(sum(self.use_vehicle.values()))

    def _constraint_exactly_one_assign(self):
        """每条消耗只能被一辆车送"""
        data = self.data
        for c in data.c2consumption:
            self.model.add_exactly_one(self.assign[(c, v)] for v in data.c_to_v_set[c])

    def _constraint_use_vehicle(self):
        """如果分配，那么这辆车就是被启用"""
        for (c, v), assign in self.assign.items():
            self.model.add_implication(assign, self.use_vehicle[v])

    def _constraint_arrival(self):
        """每辆车到达时间区间约束"""
        data = self.data
        for (c, v), assign in self.assign.items():
            consumption = data.c2consumption[c]
            self.model.add_linear_constraint(
                self.arrival[v], lb=consumption.arrival_lb, ub=consumption.arrival_ub
            ).only_enforce_if(assign)

    def _constraint_loaded_pallets(self):
        """记录每辆车装载的板数"""
        for (v, i), loaded_pallets in self.loaded_pallets.items():
            # 记录物料i的总量
            i_qty = sum(
                self.assign[(c, v)] * consumption.qty
                for c, consumption in self.data.c2consumption.items()
                if consumption.item == i and v in self.data.c_to_v_set[c]
            )
            pc_per_pallet: int = self.data.pc_per_pallet[i]

            # loaded_pallets = ceil(i_qty / pc_per_pallet)
            self.model.add((loaded_pallets - 1) * pc_per_pallet < i_qty)
            self.model.add(i_qty <= loaded_pallets * pc_per_pallet)

    def _constraint_vehicle_capacity(self):
        """车辆容量约束"""
        for v, vehicle in self.data.v2vehicle.items():
            self.model.add(
                sum(self.loaded_pallets.get((v, i), 0) for i in self.data.item_set)
                <= vehicle.capacity
            )

    def _constraint_supplier_item_qty(self):
        """供应商物料数量约束"""
        for s, v_set in self.data.s_to_v_set.items():
            for i in self.data.s_to_i_set[s]:
                self.model.add(
                    sum(self.loaded_pallets.get((v, i), 0) for v in v_set)
                    == math.ceil(self.data.si2qty[(s, i)] / self.data.pc_per_pallet[i])
                )

    def _break_symmetry(self):
        """打破对称性"""
        for s, v_set in self.data.s_to_v_set.items():
            sorted_v = sorted(v_set)
            for v1, v2 in zip(sorted_v, sorted_v[1:]):
                self.model.add_implication(self.use_vehicle[v2], self.use_vehicle[v1])
                self.model.add(self.arrival[v1] <= self.arrival[v2])
        
        for v, arrival in self.arrival.items():
            self.model.add(arrival == 0).only_enforce_if(~self.use_vehicle[v])
