from __future__ import annotations

import io
from collections import defaultdict
import math
from pathlib import Path

import pandas as pd

from manufacture_jis.base import HourlyConcumption, Vehicle

SCHEDULE_COL_MAP = {
    "任务令": "mfg_order",
    "原材料编码": "item_code",
    "计划量": "planned_qty",
    "开工时间": "start_time",
    "完工时间": "finish_time",
    "休息时间": "rest_time",
}

DEMAND_COL_MAP = {
    "需求ID": "demand_id",
    "物料编码": "item_code",
    "供应商名称": "supplier",
    "需求数量": "qty",
}

PACKING_COL_MAP = {
    "编码": "item_code",
    "包规": "pc_per_pallet",
}

SUPPLIER_COL_MAP = {
    "供应商名称": "supplier",
    "车规（板/车）": "vehicle_capacity",
}


def _read_sheet(path, sheet_name: str, col_map: dict[str, str]) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet_name)
    if df.empty:
        return df
    available = {k: v for k, v in col_map.items() if k in df.columns}
    return df.rename(columns=available)[list(available.values())].dropna()


class JISData:
    """JIS 输入数据。

    当前版本支持从样例 Excel 中读取四张表：
    1. `01排产信息（输入）`
    2. `02输入（SR拆分）`
    3. `03包规基表`
    4. `04供应商作息&车规基表`
    """

    SCHEDULE_SHEET = "01排产信息（输入）"
    DEMAND_SHEET = "02输入（SR拆分）"
    PACKING_SHEET = "03包规基表"
    SUPPLIER_SHEET = "04供应商作息&车规基表"

    def __init__(
        self,
        path: str | Path | io.BytesIO,
        num_vehicles_per_supplier: int = 5,
        arrival_lead_time: int = 8,
        arrival_lag_time: int = 3,
    ):
        self.path = path
        self.num_vehicles_per_supplier = num_vehicles_per_supplier
        self.arrival_lead_time = arrival_lead_time
        self.arrival_lag_time = arrival_lag_time

        # 消耗id -> 消耗
        self.c2consumption: dict[str, HourlyConcumption] = {}
        # 车辆id -> 车辆
        self.v2vehicle: dict[str, Vehicle] = {}
        # 消耗id -> 可能送这条消耗的车辆id集合。
        self.c_to_v_set: dict[str, set[str]] = defaultdict(set)
        # 最晚的时间，建模用
        self.t_max: int = 0
        # 物料编码集合，建模用
        self.item_set: set[str] = set()
        # 供应商 -> 车辆集合
        self.s_to_v_set: dict[str, set[str]] = defaultdict(set)
        # 供应商 -> 物料集合
        self.s_to_i_set: dict[str, set[str]] = defaultdict(set)
        # 物料编码 -> 可能送这条物料的供应商集合。
        self.i_to_s_set: dict[str, set[str]] = defaultdict(set)
        # 物料编码 -> 可能送这条物料的车辆集合。
        self.i_to_v_set: dict[str, set[str]] = defaultdict(set)
        # 包规：每个栈板有多少个物料
        self.pc_per_pallet: dict[str, int] = {}
        # (供应商, 物料编码) -> 总数
        self.si2qty: dict[tuple[str, str], int] = defaultdict(int)

        self._load()

    def _load(self) -> None:
        self._load_packaging()
        self._load_suppliers()
        schedule_df = self._load_schedule()
        demand_df = self._load_demands()
        self._build_consumptions(schedule_df, demand_df)

    def _load_packaging(self) -> None:
        df = _read_sheet(self.path, self.PACKING_SHEET, PACKING_COL_MAP)
        if df.empty:
            return
        df["item_code"] = df["item_code"].astype(str).str.strip()
        df["pc_per_pallet"] = df["pc_per_pallet"].astype(int)
        for row in df.itertuples(index=False):
            self.pc_per_pallet[row.item_code] = row.pc_per_pallet
            self.item_set.add(row.item_code)

    def _load_suppliers(self) -> None:
        df = _read_sheet(self.path, self.SUPPLIER_SHEET, SUPPLIER_COL_MAP)
        if df.empty:
            return
        df["supplier"] = df["supplier"].astype(str).str.strip()
        df["vehicle_capacity"] = df["vehicle_capacity"].astype(int)
        for row in df.itertuples(index=False):
            for k in range(1, self.num_vehicles_per_supplier + 1):
                vid = f"{row.supplier}_车次{k}"
                self.v2vehicle[vid] = Vehicle(
                    supplier=row.supplier, v=vid, capacity=row.vehicle_capacity
                )
                self.s_to_v_set[row.supplier].add(vid)

    def _load_schedule(self) -> pd.DataFrame:
        df = _read_sheet(self.path, self.SCHEDULE_SHEET, SCHEDULE_COL_MAP)
        if df.empty:
            raise ValueError("排产信息为空")
        df["item_code"] = df["item_code"].astype(str).str.strip()
        df["mfg_order"] = df["mfg_order"].astype(str).str.strip()
        df["planned_qty"] = pd.to_numeric(df["planned_qty"], errors="raise").astype(int)
        df["start_time"] = pd.to_datetime(df["start_time"])
        df["finish_time"] = pd.to_datetime(df["finish_time"])
        df["rest_time"] = pd.to_numeric(df["rest_time"], errors="raise")
        return df

    def _load_demands(self) -> pd.DataFrame:
        df = _read_sheet(self.path, self.DEMAND_SHEET, DEMAND_COL_MAP)
        if df.empty:
            return df
        df["demand_id"] = df["demand_id"].astype(str).str.strip()
        df["item_code"] = df["item_code"].astype(str).str.strip()
        df["supplier"] = df["supplier"].astype(str).str.strip()
        df["qty"] = pd.to_numeric(df["qty"], errors="raise").astype(int)
        return df

    def _build_consumptions(
        self, schedule_df: pd.DataFrame, demand_df: pd.DataFrame
    ) -> None:
        if demand_df.empty:
            return

        missing_schedule = set(demand_df["item_code"]) - set(schedule_df["item_code"])
        if missing_schedule:
            raise ValueError(f"物料 {sorted(missing_schedule)[0]} 在排产信息中不存在")

        missing_packaging = set(demand_df["item_code"]) - set(self.pc_per_pallet)
        if missing_packaging:
            raise ValueError(f"物料 {sorted(missing_packaging)[0]} 缺少包规")

        missing_supplier = set(demand_df["supplier"]) - set(self.s_to_v_set)
        if missing_supplier:
            raise ValueError(f"供应商 {sorted(missing_supplier)[0]} 缺少车规")

        # 从需求构建 supplier ↔ item 的静态关联
        for d_row in demand_df.itertuples(index=False):
            vehicles = self.s_to_v_set[d_row.supplier]
            self.s_to_i_set[d_row.supplier].add(d_row.item_code)
            self.i_to_s_set[d_row.item_code].add(d_row.supplier)
            self.i_to_v_set[d_row.item_code].update(vehicles)
            self.si2qty[(d_row.supplier, d_row.item_code)] += d_row.qty
            self.item_set.add(d_row.item_code)

        origin = schedule_df["start_time"].min().normalize() - pd.Timedelta(days=1)
        schedule_df["start_hour"] = (
            (schedule_df["start_time"] - origin).dt.total_seconds() // 3600
        ).astype(int)
        schedule_df["finish_hour"] = (
            (schedule_df["finish_time"] - origin).dt.total_seconds() // 3600
        ).astype(int)

        # 按排产展开为小时级消耗，每个小时一条
        for s_row in schedule_df.itertuples(index=False):
            processing_time: int = math.ceil(
                s_row.finish_hour - s_row.start_hour - s_row.rest_time
            )
            base_qty: int = s_row.planned_qty // processing_time
            remainder: int = s_row.planned_qty % processing_time

            for k in range(processing_time):
                h = s_row.start_hour + k
                cid = f"{s_row.mfg_order}_h{h}"
                hourly_qty = base_qty + (1 if k < remainder else 0)
                arrival_lb = h - self.arrival_lead_time
                arrival_ub = h - self.arrival_lag_time

                self.c2consumption[cid] = HourlyConcumption(
                    cid=cid,
                    mfg_order=s_row.mfg_order,
                    consumption_time=h,
                    item=s_row.item_code,
                    qty=hourly_qty,
                    arrival_lb=arrival_lb,
                    arrival_ub=arrival_ub,
                )
                self.c_to_v_set[cid].update(self.i_to_v_set[s_row.item_code])
                self.t_max = max(self.t_max, arrival_ub)
