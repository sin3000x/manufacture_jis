from __future__ import annotations

from collections import Counter, defaultdict
import math
from pathlib import Path

from loguru import logger
import pandas as pd

from manufacture_jis import RESULT_ROOT
from manufacture_jis.base import Vehicle
from manufacture_jis.jis_data import JISData


class JISResult:
    def __init__(self, data: JISData, vehicles: list[Vehicle]):
        self.data = data
        self.vehicles = vehicles
        self.result_df: pd.DataFrame = self._build_result_df()
        self.output_df: pd.DataFrame = self._build_output_df()
        self.issues: list[str] = self.check()
        if not self.issues:
            logger.info("求解结果通过检查!")
        else:
            for issue in self.issues:
                logger.error(issue)

    def check(self) -> list[str]:
        """检查求解结果是否满足业务约束。

        返回值是问题列表：空列表表示结果通过检查。
        """
        checks = (
            self._check_exactly_one_assign,
            self._check_vehicle_loads,
            self._check_arrival_domain,
            self._check_arrival_windows,
            self._check_loaded_pallets,
            self._check_vehicle_capacity,
            self._check_supplier_item_pallets,
        )

        issues: list[str] = []
        for check in checks:
            issues.extend(check())
        return issues

    def write_excel(self, path: str | Path = "") -> None:
        path = path or RESULT_ROOT / f"result_{self.data.path.stem}.xlsx"
        self.output_df.to_excel(path, index=False)

    def _check_exactly_one_assign(self) -> list[str]:
        issues: list[str] = []
        assigned_cids = Counter(
            load.cid for vehicle in self.vehicles for load in vehicle.loads
        )
        expected_cids = set(self.data.c2consumption)
        actual_cids = set(assigned_cids)

        for cid in sorted(expected_cids - actual_cids):
            issues.append(f"消耗 {cid} 未被任何车辆配送")
        for cid in sorted(actual_cids - expected_cids):
            issues.append(f"消耗 {cid} 不存在于输入数据中")
        for cid, count in assigned_cids.items():
            if count > 1:
                issues.append(f"消耗 {cid} 被分配了 {count} 次")
        return issues

    def _check_vehicle_loads(self) -> list[str]:
        issues: list[str] = []
        for vehicle in self.vehicles:
            for load in vehicle.loads:
                if vehicle.v not in self.data.c_to_v_set.get(load.cid, set()):
                    issues.append(f"车辆 {vehicle.v} 不能配送消耗 {load.cid}")
                if load.item not in vehicle.item_to_loaded_pallets:
                    issues.append(f"车辆 {vehicle.v} 缺少物料 {load.item} 的装载板数")
        return issues

    def _check_arrival_domain(self) -> list[str]:
        issues: list[str] = []
        allowed = set(self.data.arrival_domain)
        for vehicle in self.vehicles:
            if vehicle.arrival not in allowed:
                issues.append(
                    f"车辆 {vehicle.v} 到达时间 {vehicle.arrival} 不在可用时间域中"
                )
        return issues

    def _check_arrival_windows(self) -> list[str]:
        issues: list[str] = []
        for vehicle in self.vehicles:
            for load in vehicle.loads:
                if not (load.arrival_lb <= vehicle.arrival <= load.arrival_ub):
                    issues.append(
                        f"车辆 {vehicle.v} 到达时间 {vehicle.arrival} 不在消耗 {load.cid} 的窗口"
                        f" [{load.arrival_lb}, {load.arrival_ub}] 内"
                    )
        return issues

    def _check_loaded_pallets(self) -> list[str]:
        issues: list[str] = []
        for vehicle in self.vehicles:
            item_to_qty: dict[str, int] = defaultdict(int)
            for load in vehicle.loads:
                item_to_qty[load.item] += load.qty

            expected_items = set(item_to_qty)
            actual_items = set(vehicle.item_to_loaded_pallets)
            for item in sorted(actual_items - expected_items):
                if vehicle.item_to_loaded_pallets[item] != 0:
                    issues.append(f"车辆 {vehicle.v} 记录了未装载物料 {item} 的板数")

            for item, qty in item_to_qty.items():
                pc_per_pallet = self.data.pc_per_pallet[item]
                expected_pallets = math.ceil(qty / pc_per_pallet)
                actual_pallets = vehicle.item_to_loaded_pallets.get(item)
                if actual_pallets != expected_pallets:
                    issues.append(
                        f"车辆 {vehicle.v} 物料 {item} 装载板数不一致："
                        f"期望 {expected_pallets}，实际 {actual_pallets}"
                    )
        return issues

    def _check_vehicle_capacity(self) -> list[str]:
        issues: list[str] = []
        for vehicle in self.vehicles:
            total_loaded_pallets = sum(vehicle.item_to_loaded_pallets.values())
            if total_loaded_pallets > vehicle.capacity:
                issues.append(
                    f"车辆 {vehicle.v} 装载板数 {total_loaded_pallets} 超出车规 {vehicle.capacity}"
                )
        return issues

    def _check_supplier_item_pallets(self) -> list[str]:
        issues: list[str] = []
        actual: dict[tuple[str, str], int] = defaultdict(int)
        for vehicle in self.vehicles:
            for item, pallets in vehicle.item_to_loaded_pallets.items():
                actual[(vehicle.supplier, item)] += pallets

        expected = {
            (supplier, item): math.ceil(qty / self.data.pc_per_pallet[item])
            for (supplier, item), qty in self.data.si2qty.items()
        }
        all_keys = set(expected) | set(actual)
        for key in sorted(all_keys):
            exp = expected.get(key, 0)
            act = actual.get(key, 0)
            if act < exp:
                supplier, item = key
                issues.append(
                    f"供应商 {supplier} 的物料 {item} 总板数不足：期望 {exp}，实际 {act}"
                )
        return issues

    def _build_output_df(self) -> pd.DataFrame:
        df: pd.DataFrame = self.result_df.groupby(
            by=["supplier", "v", "item", "mfg_order"],
            as_index=False,
        ).agg(
            qty=("qty", "sum"),
            pc_per_pallet=("pc_per_pallet", "first"),
            loaded_pallets=("loaded_pallets", "first"),
            total_loaded_pallets=("total_loaded_pallets", "first"),
            capacity=("capacity", "first"),
            arrival=("arrival", "first"),
        )

        df["arrival"] = self.data.hour_to_datetime(df["arrival"]).dt.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        df["装载率"] = df["total_loaded_pallets"] / df["capacity"]
        df["装载率"] = df["装载率"].apply(lambda x: f"{x:.2%}")
        df = df.rename(
            columns={
                "supplier": "供应商",
                "v": "车次",
                "mfg_order": "任务令",
                "item": "物料编码",
                "qty": "任务令满足量",
                "pc_per_pallet": "包规",
                "loaded_pallets": "装载板数",
                "total_loaded_pallets": "总装载板数",
                "capacity": "车规",
                "arrival": "到达时间",
            },
        )

        # 删除一些同维度下的重复值
        df["s_group"] = df.groupby("供应商").ngroup()
        df["sv_group"] = df.groupby(["供应商", "车次"]).ngroup()
        df["svi_group"] = df.groupby(["供应商", "车次", "物料编码"]).ngroup()
        merge_dict = {
            "s_group": ["供应商"],
            "sv_group": ["车次", "总装载板数", "车规", "装载率", "到达时间"],
            "svi_group": ["物料编码", "包规", "装载板数"],
        }
        for merge_by, to_merge in merge_dict.items():
            mask = df.duplicated(subset=merge_by, keep="first")
            df.loc[mask, to_merge] = None

        # 删除车次编号中的供应商前缀
        df["车次"] = df["车次"].str.split("_").str[-1]

        df["装载量"] = df["装载板数"] * df["包规"]
        df["线体"] = df["任务令"].map(self.data.mfg_order_to_line)
        df["货位"] = df["物料编码"].map(self.data.item_to_location)
        df = df[
            [
                "供应商",
                "车次",
                "物料编码",
                "任务令",
                "线体",
                "货位",
                "任务令满足量",
                "包规",
                "装载板数",
                "装载量",
                "总装载板数",
                "车规",
                "装载率",
                "到达时间",
            ]
        ]

        return df

    def _build_result_df(self) -> pd.DataFrame:
        df: pd.DataFrame = pd.DataFrame(
            [
                (
                    vehicle.supplier,
                    vehicle.v,
                    load.cid,
                    load.mfg_order,
                    load.consumption_time,
                    load.item,
                    load.qty,
                    vehicle.item_to_loaded_pallets[load.item],
                    sum(vehicle.item_to_loaded_pallets.values()),
                    vehicle.capacity,
                    load.arrival_lb,
                    load.arrival_ub,
                    vehicle.arrival,
                )
                for vehicle in self.vehicles
                for load in vehicle.loads
            ],
            columns=[
                "supplier",
                "v",
                "cid",
                "mfg_order",
                "consumption_time",
                "item",
                "qty",
                "loaded_pallets",
                "total_loaded_pallets",
                "capacity",
                "arrival_lb",
                "arrival_ub",
                "arrival",
            ],
        )
        df["pc_per_pallet"] = df["item"].map(self.data.pc_per_pallet)
        df = df.sort_values(by=["supplier", "v", "item", "cid"])
        return df
