from __future__ import annotations

import io
from collections import defaultdict
import math
from pathlib import Path

from loguru import logger
import pandas as pd

from manufacture_jis.base import Concumption, Vehicle

SCHEDULE_COL_MAP = {
    "线体": "line",
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
    "货位": "location",
}

PACKING_COL_MAP = {
    "编码": "item_code",
    "包规": "pc_per_pallet",
}

SUPPLIER_COL_MAP = {
    "供应商名称": "supplier",
    "车规（板/车）": "vehicle_capacity",
}


def _build_sheet_name_map(path) -> dict[str, str]:
    """strip 后的名称 -> Excel 中的实际 sheet 名（兼容首尾空格）。"""
    with pd.ExcelFile(path) as xl:
        return {name.strip(): name for name in xl.sheet_names}


def _read_sheet(
    path,
    sheet_name: str,
    col_map: dict[str, str],
    sheet_map: dict[str, str],
    fillna_map: dict[str, object] | None = None,
) -> pd.DataFrame:
    actual = sheet_map.get(sheet_name.strip())
    if actual is None:
        logger.error(f"工作表 '{sheet_name}' 不存在，可用: {list(sheet_map)}")
        raise ValueError(f"工作表 '{sheet_name}' 不存在，可用: {list(sheet_map)}")
    df = pd.read_excel(path, sheet_name=actual)
    if df.empty:
        return df
    available = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(columns=available)[list(available.values())]
    if fillna_map:
        df = df.fillna({k: v for k, v in fillna_map.items() if k in df.columns})
    df = df.dropna()
    logger.info(f"读取 {sheet_name} 表，行数: {len(df)}")
    return df.reset_index(drop=True)


class JISData:
    """JIS 输入数据。

    当前版本支持从样例 Excel 中读取四张表：
    """

    SCHEDULE_SHEET = "01排产信息 (输入)"
    DEMAND_SHEET = "02输入 (SR拆分)"
    PACKING_SHEET = "03包规基表"
    SUPPLIER_SHEET = "04供应商作息&车规基表"

    def __init__(
        self,
        path: str | Path | io.BytesIO,
        num_vehicles_per_supplier: int | None = None,
        arrival_lead_time: int = 8,
        arrival_lag_time: int = 3,
        rest_times: list[int] | None = None,
        consumption_interval_hours: int = 1,
        max_vehicles_per_slot: int = 1,
    ):
        self.path = Path(path)
        self.num_vehicles_per_supplier = num_vehicles_per_supplier
        self.arrival_lead_time = arrival_lead_time
        self.arrival_lag_time = arrival_lag_time
        self.rest_times = rest_times or []
        self.consumption_interval_hours = consumption_interval_hours
        self.max_vehicles_per_slot = max_vehicles_per_slot
        logger.info(
            f"T-{self.arrival_lead_time} ~ T-{self.arrival_lag_time}, "
            f"rest_times: {self.rest_times}, "
            f"consumption_interval_hours: {self.consumption_interval_hours}, "
            f"max_vehicles_per_slot: {self.max_vehicles_per_slot}"
        )

        # 消耗id -> 消耗
        self.c2consumption: dict[str, Concumption] = {}
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
        # 时间轴原点：最早开工日当天 0 点再减 1 天
        self.origin: pd.Timestamp | None = None

        # 任务令 -> 线体
        self.mfg_order_to_line: dict[str, str] = {}
        # 物料编码 -> 货位
        self.item_to_location: dict[str, str] = {}

        self._sheet_map: dict[str, str] = _build_sheet_name_map(path)

        self._load()

        self.arrival_domain: list[int] = list(
            t for t in range(self.t_max + 1) if not self.is_rest_time(t)
        )

        if not self.arrival_domain:
            logger.error("没有可选择的到达时间")
            raise ValueError("没有可选择的到达时间")

    def __repr__(self) -> str:
        return f"JISData({self.path})"

    def is_rest_time(self, hour: int) -> bool:
        return (hour % 24) in self.rest_times

    def datetime_to_hour(self, dt: pd.Series) -> pd.Series:
        """将 datetime Series 转为相对 origin 的小时偏移（整型）。"""
        if self.origin is None:
            logger.error("origin 尚未初始化")
            raise ValueError("origin 尚未初始化")
        return ((dt - self.origin).dt.total_seconds() // 3600).astype(int)

    def hour_to_datetime(self, hours: pd.Series) -> pd.Series:
        """将小时偏移 Series 转回 datetime。"""
        if self.origin is None:
            logger.error("origin 尚未初始化")
            raise ValueError("origin 尚未初始化")
        return self.origin + pd.to_timedelta(hours, unit="h")

    def _load(self) -> None:
        self._load_packaging()
        supplier_info = self._read_supplier_info()

        schedule_df = self._load_schedule()
        self.origin = schedule_df["start_time"].min().normalize() - pd.Timedelta(days=1)
        self.mfg_order_to_line = schedule_df.set_index("mfg_order")["line"].to_dict()

        demand_df = self._load_demands()
        self.item_to_location = demand_df.set_index("item_code")["location"].to_dict()

        if demand_df.empty:
            return

        schedule_item_codes = set(schedule_df["item_code"].astype(str).str.strip())
        self._build_item_supplier_mappings(
            demand_df, supplier_info, schedule_item_codes
        )
        self._build_consumptions_from_schedule(schedule_df, supplier_info)

        if self.num_vehicles_per_supplier is not None:
            counts = {s: self.num_vehicles_per_supplier for s in supplier_info}
        else:
            from manufacture_jis.vehicle_bounds import compute_vehicle_bounds

            counts = compute_vehicle_bounds(
                list(self.c2consumption.values()),
                self.s_to_i_set,
                supplier_info,
                self.pc_per_pallet,
                self.rest_times,
            )

        logger.info(f"Vehicle counts: {counts}")
        self._create_vehicles(supplier_info, counts)
        self._populate_vehicle_mappings()

    def _load_packaging(self) -> None:
        df = _read_sheet(
            self.path, self.PACKING_SHEET, PACKING_COL_MAP, self._sheet_map
        )
        if df.empty:
            return
        df["item_code"] = df["item_code"].astype(str).str.strip()
        df["pc_per_pallet"] = df["pc_per_pallet"].astype(int)
        for row in df.itertuples(index=False):
            self.pc_per_pallet[row.item_code] = row.pc_per_pallet
            self.item_set.add(row.item_code)

    def _read_supplier_info(self) -> dict[str, int]:
        df = _read_sheet(
            self.path, self.SUPPLIER_SHEET, SUPPLIER_COL_MAP, self._sheet_map
        )
        info: dict[str, int] = {}
        if df.empty:
            return info
        df["supplier"] = df["supplier"].astype(str).str.strip()
        df["vehicle_capacity"] = df["vehicle_capacity"].astype(int)
        for row in df.itertuples(index=False):
            info[row.supplier] = row.vehicle_capacity
        return info

    def _create_vehicles(
        self, supplier_info: dict[str, int], counts: dict[str, int]
    ) -> None:
        for supplier, capacity in supplier_info.items():
            n = counts.get(supplier, 0)
            for k in range(1, n + 1):
                vid = f"{supplier}_车次{k}"
                self.v2vehicle[vid] = Vehicle(
                    supplier=supplier, v=vid, capacity=capacity
                )
                self.s_to_v_set[supplier].add(vid)

    def _load_schedule(self) -> pd.DataFrame:
        df = _read_sheet(
            self.path, self.SCHEDULE_SHEET, SCHEDULE_COL_MAP, self._sheet_map,
            fillna_map={"rest_time": 0},
        )
        if df.empty:
            logger.error("排产信息为空")
            raise ValueError("排产信息为空")
        df["item_code"] = df["item_code"].astype(str).str.strip()
        df["mfg_order"] = df["mfg_order"].astype(str).str.strip()
        df["planned_qty"] = pd.to_numeric(df["planned_qty"], errors="raise").astype(int)
        df["start_time"] = pd.to_datetime(df["start_time"])
        df["finish_time"] = pd.to_datetime(df["finish_time"])
        df["rest_time"] = pd.to_numeric(df["rest_time"], errors="raise")
        return df

    def _load_demands(self) -> pd.DataFrame:
        df = _read_sheet(self.path, self.DEMAND_SHEET, DEMAND_COL_MAP, self._sheet_map)
        if df.empty:
            return df
        df["demand_id"] = df["demand_id"].astype(str).str.strip()
        df["item_code"] = df["item_code"].astype(str).str.strip()
        df["supplier"] = df["supplier"].astype(str).str.strip()
        df["qty"] = pd.to_numeric(df["qty"], errors="raise").astype(int)
        return df

    def _build_item_supplier_mappings(
        self,
        demand_df: pd.DataFrame,
        supplier_info: dict[str, int],
        schedule_item_codes: set[str],
    ) -> None:
        missing_schedule = set(demand_df["item_code"]) - schedule_item_codes
        if missing_schedule:
            logger.error(f"物料 {sorted(missing_schedule)} 在排产信息中不存在")
            raise ValueError(f"物料 {sorted(missing_schedule)} 在排产信息中不存在")

        missing_packaging = set(demand_df["item_code"]) - set(self.pc_per_pallet)
        if missing_packaging:
            logger.error(f"物料 {sorted(missing_packaging)} 缺少包规")
            raise ValueError(f"物料 {sorted(missing_packaging)} 缺少包规")

        missing_supplier = set(demand_df["supplier"]) - set(supplier_info)
        if missing_supplier:
            logger.error(f"供应商 {sorted(missing_supplier)} 缺少车规")
            raise ValueError(f"供应商 {sorted(missing_supplier)} 缺少车规")

        for d_row in demand_df.itertuples(index=False):
            self.s_to_i_set[d_row.supplier].add(d_row.item_code)
            self.i_to_s_set[d_row.item_code].add(d_row.supplier)
            self.si2qty[(d_row.supplier, d_row.item_code)] += d_row.qty
            self.item_set.add(d_row.item_code)

    def _build_consumptions_from_schedule(
        self, schedule_df: pd.DataFrame, supplier_info: dict[str, int]
    ) -> None:
        interval_hours: int = self.consumption_interval_hours
        schedule_df["start_hour"] = self.datetime_to_hour(schedule_df["start_time"])
        schedule_df["finish_hour"] = self.datetime_to_hour(schedule_df["finish_time"])

        for s_row in schedule_df.itertuples(index=False):
            processing_time: int = math.ceil(
                s_row.finish_hour - s_row.start_hour - s_row.rest_time
            )
            base_qty: int = s_row.planned_qty // processing_time
            remainder: int = s_row.planned_qty % processing_time

            total_hours = processing_time + (1 if remainder > 0 else 0)
            num_intervals = math.ceil(total_hours / interval_hours)

            for i in range(num_intervals):
                interval_start = i * interval_hours
                interval_end = min((i + 1) * interval_hours, total_hours)

                interval_qty = 0
                for k in range(interval_start, interval_end):
                    if k < processing_time:
                        interval_qty += base_qty
                    else:
                        interval_qty += remainder

                h = s_row.start_hour + interval_start
                arrival_lb = h - self.arrival_lead_time
                arrival_ub = h - self.arrival_lag_time

                suppliers = self.i_to_s_set.get(s_row.item_code, set())
                ppp = self.pc_per_pallet.get(s_row.item_code)
                candidate_caps = [
                    supplier_info[s] for s in suppliers if s in supplier_info
                ]
                if ppp and candidate_caps:
                    min_cap = min(candidate_caps)
                    total_pallets = math.ceil(interval_qty / ppp)
                    n = math.ceil(total_pallets / min_cap)
                else:
                    n = 1

                if n <= 1:
                    cid = f"{s_row.mfg_order}_h{h}"
                    self.c2consumption[cid] = Concumption(
                        cid=cid,
                        mfg_order=s_row.mfg_order,
                        consumption_time=h,
                        item=s_row.item_code,
                        qty=interval_qty,
                        arrival_lb=arrival_lb,
                        arrival_ub=arrival_ub,
                    )
                    self.t_max = max(self.t_max, arrival_ub)
                else:
                    full_load = min_cap * ppp
                    for j in range(n):
                        sub_qty = full_load if j < n - 1 else interval_qty - full_load * (n - 1)
                        cid = f"{s_row.mfg_order}_h{h}-{j + 1}"
                        self.c2consumption[cid] = Concumption(
                            cid=cid,
                            mfg_order=s_row.mfg_order,
                            consumption_time=h,
                            item=s_row.item_code,
                            qty=sub_qty,
                            arrival_lb=arrival_lb,
                            arrival_ub=arrival_ub,
                        )
                    self.t_max = max(self.t_max, arrival_ub)

    def _populate_vehicle_mappings(self) -> None:
        for item, suppliers in self.i_to_s_set.items():
            for supplier in suppliers:
                self.i_to_v_set[item].update(self.s_to_v_set[supplier])
        for cid, consumption in self.c2consumption.items():
            self.c_to_v_set[cid].update(self.i_to_v_set[consumption.item])
