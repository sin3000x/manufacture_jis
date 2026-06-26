from __future__ import annotations

import io
from collections import defaultdict
import math
from pathlib import Path

from loguru import logger
import pandas as pd

from manufacture_jis.base import Concumption, Vehicle
from manufacture_jis.excel_utils import build_sheet_name_map, read_sheet

# Excel "排产信息" 表的列名映射：中文列名 → 英文字段名
SCHEDULE_COL_MAP = {
    "线体": "line",
    "任务令": "mfg_order",
    "原材料编码": "item_code",
    "计划量": "planned_qty",
    "开工时间": "start_time",
    "完工时间": "finish_time",
    "休息时间": "rest_time",
}

# Excel "SR拆分需求" 表的列名映射：中文列名 → 英文字段名
DEMAND_COL_MAP = {
    "需求ID": "demand_id",
    "物料编码": "item_code",
    "供应商名称": "supplier",
    "需求数量": "qty",
    "货位": "location",
}

# Excel "包规基表" 表的列名映射：中文列名 → 英文字段名
PACKING_COL_MAP = {
    "编码": "item_code",
    "包规": "pc_per_pallet",
}

# Excel "供应商作息&车规基表" 表的列名映射：中文列名 → 英文字段名
SUPPLIER_COL_MAP = {
    "供应商名称": "supplier",
    "车规（板/车）": "vehicle_capacity",
}


class JISData:
    """JIS 输入数据加载与预处理。

    从 Excel 文件读取四张表（排产、需求、包规、供应商），校验数据完整性，
    将排产计划按消耗间隔拆分为小时级消耗对象，并为 CP-SAT 建模构建所有
    所需的索引映射（消耗↔车辆、供应商↔物料等）。
    """

    # 四张输入表的默认表名（通过 build_sheet_name_map 进行模糊匹配，兼容前缀变化）
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
        """初始化 JISData 并触发 Excel 加载。

        Args:
            path: Excel 文件路径或 BytesIO 流。
            num_vehicles_per_supplier: 每个供应商固定车辆数；None 时由
                vehicle_bounds 模块自动推算下界。
            arrival_lead_time: 物料最早在消耗时刻前多少小时到达（默认 8）。
            arrival_lag_time: 物料最晚在消耗时刻前多少小时到达（默认 3）。
            rest_times: 禁止到达的小时列表（24 小时制，如 [0,1,2] 表示凌晨
                0-2 点不可到达）。
            consumption_interval_hours: 将加工时长按此间隔拆分为多条消耗，
                默认 1 小时一条。
            max_vehicles_per_slot: 同一时槽最多可同时到达的车辆数（当前建模
                未直接使用，保留供扩展）。
        """
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

        # 消耗id -> 消耗对象；键格式为 "{任务令}_h{小时}" 或 "{任务令}_h{小时}-{子编号}"
        self.c2consumption: dict[str, Concumption] = {}
        # 车辆id -> 车辆对象；键格式为 "{供应商}_车次{序号}"
        self.v2vehicle: dict[str, Vehicle] = {}
        # 消耗id -> 可能配送该消耗的车辆id集合（由该物料所有候选供应商的车辆构成）
        self.c_to_v_set: dict[str, set[str]] = defaultdict(set)
        # 所有消耗到达时间上界的最大值，用于确定 CP 模型时间域上限
        self.t_max: int = 0
        # 出现在排产或需求中的全部物料编码集合
        self.item_set: set[str] = set()
        # 供应商 -> 该供应商所有车辆id集合
        self.s_to_v_set: dict[str, set[str]] = defaultdict(set)
        # 供应商 -> 该供应商负责配送的物料编码集合
        self.s_to_i_set: dict[str, set[str]] = defaultdict(set)
        # 物料编码 -> 可配送该物料的供应商集合
        self.i_to_s_set: dict[str, set[str]] = defaultdict(set)
        # 物料编码 -> 可配送该物料的车辆id集合（跨所有候选供应商）
        self.i_to_v_set: dict[str, set[str]] = defaultdict(set)
        # 物料编码 -> 每栈板件数（来自包规基表）
        self.pc_per_pallet: dict[str, int] = {}
        # (供应商, 物料编码) -> 该供应商需配送该物料的总需求件数
        self.si2qty: dict[tuple[str, str], int] = defaultdict(int)
        # 时间轴原点：最早开工日当天 0 点再减 1 天，保证所有小时偏移为正整数
        self.origin: pd.Timestamp | None = None

        # 任务令 -> 所属线体（用于结果输出时还原线体信息）
        self.mfg_order_to_line: dict[str, str] = {}
        # 物料编码 -> 货位（来自需求表，用于结果输出）
        self.item_to_location: dict[str, str] = {}

        # 预先构建 Excel 表名模糊匹配映射，兼容表名前缀数字变化
        self._sheet_map: dict[str, str] = build_sheet_name_map(path)

        self._load()

        # 去除休息时段后的可用到达时间列表，CP 模型的 arrival 变量域
        self.arrival_domain: list[int] = list(
            t for t in range(self.t_max + 1) if not self.is_rest_time(t)
        )

        if not self.arrival_domain:
            logger.error("没有可选择的到达时间")
            raise ValueError("没有可选择的到达时间")

    def __repr__(self) -> str:
        return f"JISData({self.path})"

    def is_rest_time(self, hour: int) -> bool:
        """判断给定小时偏移是否落在休息时段（按 24 小时制取模）。"""
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
        """按顺序加载全部四张表并构建建模所需的数据结构。

        加载顺序有依赖关系：
        1. 包规先于需求，因为需求校验依赖 pc_per_pallet。
        2. 供应商先于消耗拆分，因为拆分时需要车辆容量计算板数。
        3. 排产确定 origin 后才能计算小时偏移。
        """
        self._load_packaging()
        supplier_info = self._read_supplier_info()

        schedule_df = self._load_schedule()
        # origin = 最早开工日当天 0 点 - 1 天，确保所有小时偏移 ≥ 24
        self.origin = schedule_df["start_time"].min().normalize() - pd.Timedelta(days=1)
        self.mfg_order_to_line = schedule_df.set_index("mfg_order")["line"].to_dict()

        demand_df = self._load_demands()
        self.item_to_location = demand_df.set_index("item_code")["location"].to_dict()

        if demand_df.empty:
            return

        # 构建 supplier↔item 静态映射，并校验数据完整性
        schedule_item_codes = set(schedule_df["item_code"].astype(str).str.strip())
        self._build_item_supplier_mappings(
            demand_df, supplier_info, schedule_item_codes
        )
        # 按排产逐行拆分出小时级消耗对象
        self._build_consumptions_from_schedule(schedule_df, supplier_info)

        # 决定每个供应商的车辆数：固定值 or 由 vehicle_bounds 自动推算
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
        """从包规基表读取每个物料的每栈板件数，写入 pc_per_pallet 和 item_set。"""
        df = read_sheet(
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
        """从供应商基表读取车规，返回 {供应商名称: 车辆容量（板/车）} 字典。"""
        df = read_sheet(
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
        """根据每个供应商的车辆数上限，批量创建 Vehicle 对象并写入 v2vehicle 和 s_to_v_set。"""
        for supplier, capacity in supplier_info.items():
            n = counts.get(supplier, 0)
            for k in range(1, n + 1):
                vid = f"{supplier}_车次{k}"
                self.v2vehicle[vid] = Vehicle(
                    supplier=supplier, v=vid, capacity=capacity
                )
                self.s_to_v_set[supplier].add(vid)

    def _load_schedule(self) -> pd.DataFrame:
        """读取并清洗排产信息表，返回包含线体、任务令、物料、计划量、时间等字段的 DataFrame。"""
        df = read_sheet(
            self.path, self.SCHEDULE_SHEET, SCHEDULE_COL_MAP, self._sheet_map,
            fillna_map={"rest_time": 0},  # 休息时间缺失时填 0，表示不含休息
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
        """读取并清洗 SR 拆分需求表，返回含需求ID、物料、供应商、数量、货位的 DataFrame。"""
        df = read_sheet(self.path, self.DEMAND_SHEET, DEMAND_COL_MAP, self._sheet_map)
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
        """根据需求表构建物料↔供应商双向索引，并校验三类数据完整性。

        校验规则（任一失败即抛 ValueError）：
        - 需求表中的物料必须全部出现在排产信息中。
        - 需求表中的物料必须全部有包规记录。
        - 需求表中的供应商必须全部有车规记录。

        副作用（写入实例属性）：
        - s_to_i_set：供应商 → 物料集合
        - i_to_s_set：物料 → 供应商集合
        - si2qty：(供应商, 物料) → 总需求件数
        - item_set：全部物料编码
        """
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
        """将排产行拆分为小时级消耗对象，写入 c2consumption，并更新 t_max。

        拆分逻辑：
        1. 有效加工时间 = ceil(完工小时 - 开工小时 - 休息时间)。
        2. 按 consumption_interval_hours 将加工时间切成若干区间，每区间生成
           一条消耗；区间内的计划量按整数均分 + 末位补余数。
        3. 到达时间窗 = [消耗开始小时 - lead_time, 消耗开始小时 - lag_time]。
        4. 若一条消耗的需求超过单车容量（按最小供应商车规折算栈板数），则将该
           消耗拆分为多条子消耗（每辆车满载一栈板），子消耗 id 追加 "-{序号}"。

        副作用：
        - c2consumption：消耗id → Concumption 对象
        - t_max：所有消耗 arrival_ub 的最大值
        """
        interval_hours: int = self.consumption_interval_hours
        schedule_df["start_hour"] = self.datetime_to_hour(schedule_df["start_time"])
        schedule_df["finish_hour"] = self.datetime_to_hour(schedule_df["finish_time"])

        for s_row in schedule_df.itertuples(index=False):
            # 有效加工小时数（去除休息时间）
            processing_time: int = math.ceil(
                s_row.finish_hour - s_row.start_hour - s_row.rest_time
            )
            # 每小时均分的基础量和余数（余数分配到最后一小时）
            base_qty: int = s_row.planned_qty // processing_time
            remainder: int = s_row.planned_qty % processing_time

            # 含余数小时的总时长，用于按区间切分
            total_hours = processing_time + (1 if remainder > 0 else 0)
            num_intervals = math.ceil(total_hours / interval_hours)

            for i in range(num_intervals):
                interval_start = i * interval_hours
                interval_end = min((i + 1) * interval_hours, total_hours)

                # 累加该区间内各小时的消耗量
                interval_qty = 0
                for k in range(interval_start, interval_end):
                    if k < processing_time:
                        interval_qty += base_qty
                    else:
                        # 最后一个余数小时
                        interval_qty += remainder

                # 该消耗对应的加工开始时刻（相对 origin 的小时偏移）
                h = s_row.start_hour + interval_start
                arrival_lb = h - self.arrival_lead_time
                arrival_ub = h - self.arrival_lag_time

                # 判断是否需要拆成多条子消耗（单车装不下时）
                suppliers = self.i_to_s_set.get(s_row.item_code, set())
                ppp = self.pc_per_pallet.get(s_row.item_code)
                candidate_caps = [
                    supplier_info[s] for s in suppliers if s in supplier_info
                ]
                if ppp and candidate_caps:
                    min_cap = min(candidate_caps)
                    total_pallets = math.ceil(interval_qty / ppp)
                    # 需要几辆车才能装下所有栈板
                    n = math.ceil(total_pallets / min_cap)
                else:
                    n = 1

                if n <= 1:
                    # 单车即可配送，生成一条消耗
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
                    # 拆成 n 条子消耗，前 n-1 条满载，最后一条装余量
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
        """填充 i_to_v_set 和 c_to_v_set 两个反向索引。

        - i_to_v_set[item]：可配送该物料的所有车辆（来自所有候选供应商）。
        - c_to_v_set[cid]：可配送该消耗的所有车辆（等于其物料的候选车辆集合）。

        必须在 _create_vehicles 之后调用，否则 s_to_v_set 尚未填充。
        """
        for item, suppliers in self.i_to_s_set.items():
            for supplier in suppliers:
                self.i_to_v_set[item].update(self.s_to_v_set[supplier])
        for cid, consumption in self.c2consumption.items():
            self.c_to_v_set[cid].update(self.i_to_v_set[consumption.item])
