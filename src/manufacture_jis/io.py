"""High-level XLSX input/output helpers for the scheduling pipeline."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from manufacture_jis.optimizer import ScheduleOptimizer
from manufacture_jis.schemas import ManufacturingPlan, PackRule, ScheduleOutputData, SplitDemand, TruckRule

INPUT_SHEETS = {
    "production_plan": "01排产信息（输入）",
    "split_result": "02输入（SR拆分）",
    "package_rules": "03包规基表",
    "truck_rules": "04供应商作息&车规基表",
}

OUTPUT_COLUMNS = [
    "供应商",
    "车次",
    "物料编码",
    "任务令",
    "线体",
    "货位",
    "需求ID",
    "满足数量",
    "包规",
    "板数",
    "总板数",
    "车规",
    "装载率",
    "计划到货时间",
]

XlsxSource = str | Path | BytesIO | BinaryIO


class ExcelScheduleIO:
    """Load scheduling inputs from Excel and export optimized results."""

    def __init__(self, optimizer: ScheduleOptimizer | None = None) -> None:
        self.optimizer = optimizer or ScheduleOptimizer()

    def load_plans(self, source: XlsxSource) -> list[ManufacturingPlan]:
        frame = self.load_sheet_frame(source, INPUT_SHEETS["production_plan"])
        plans: list[ManufacturingPlan] = []
        for row in frame.itertuples(index=False):
            start_time = pd.to_datetime(row[5]).to_pydatetime()
            end_time = pd.to_datetime(row[6]).to_pydatetime()
            plans.append(
                ManufacturingPlan(
                    line=str(row[0]),
                    mfg_order=str(row[1]),
                    item_code=str(row[2]),
                    plan_qty=int(row[3]),
                    break_hours=int(float(row[4])),
                    start_time=start_time,
                    end_time=end_time,
                )
            )
        return plans

    def load_split_demands(self, source: XlsxSource) -> list[SplitDemand]:
        frame = self.load_sheet_frame(source, INPUT_SHEETS["split_result"])
        return [
            SplitDemand(
                item_code=str(row[0]),
                supplier=str(row[1]),
                location=str(row[2]),
                demand_qty=int(row[3]),
                demand_id=str(row[4]),
            )
            for row in frame.itertuples(index=False)
        ]

    def load_pack_rules(self, source: XlsxSource) -> list[PackRule]:
        frame = self.load_sheet_frame(source, INPUT_SHEETS["package_rules"])
        return [PackRule(item_code=str(row[0]), pack_size=int(row[1])) for row in frame.itertuples(index=False)]

    def load_truck_rules(self, source: XlsxSource) -> list[TruckRule]:
        frame = self.load_sheet_frame(source, INPUT_SHEETS["truck_rules"])
        return [
            TruckRule(supplier=str(row[0]), truck_capacity_pallets=int(row[1]))
            for row in frame.itertuples(index=False)
        ]

    def load_sheet_frame(self, source: XlsxSource, sheet_name: str) -> pd.DataFrame:
        return pd.read_excel(source, sheet_name=sheet_name)

    def load_input_data(self, source: XlsxSource) -> tuple[list[ManufacturingPlan], list[SplitDemand], list[PackRule], list[TruckRule]]:
        return (
            self.load_plans(source),
            self.load_split_demands(source),
            self.load_pack_rules(source),
            self.load_truck_rules(source),
        )

    def schedule_dataframe_from_xlsx(self, source: XlsxSource) -> pd.DataFrame:
        plans, split_demands, pack_rules, truck_rules = self.load_input_data(source)
        rows = self.optimizer.optimize_schedule(plans, split_demands, pack_rules, truck_rules)
        return self.schedule_rows_to_dataframe(rows)

    def schedule_rows_to_dataframe(self, rows: list) -> pd.DataFrame:
        frame = pd.DataFrame(
            [
                {
                    "供应商": row.supplier,
                    "车次": row.truck_no,
                    "物料编码": row.item_code,
                    "任务令": row.mfg_order,
                    "线体": row.line,
                    "货位": row.location,
                    "需求ID": row.demand_id,
                    "满足数量": row.fulfilled_qty,
                    "包规": row.pack_size,
                    "板数": row.pallets,
                    "总板数": row.total_pallets,
                    "车规": row.truck_capacity_pallets,
                    "装载率": row.load_rate,
                    "计划到货时间": row.planned_arrival_time,
                }
                for row in rows
            ],
            columns=OUTPUT_COLUMNS,
        )
        return frame


def schedule_dataframe_from_xlsx(source: XlsxSource) -> pd.DataFrame:
    return ExcelScheduleIO().schedule_dataframe_from_xlsx(source)
