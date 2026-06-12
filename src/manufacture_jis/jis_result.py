from pathlib import Path
import pandas as pd
from manufacture_jis import RESULT_ROOT
from manufacture_jis.jis_data import JISData
from manufacture_jis.base import Vehicle


class JISResult:
    def __init__(self, data: JISData, vehicles: list[Vehicle]):
        self.data = data
        self.vehicles = vehicles
        self.result_df: pd.DataFrame = self._build_result_df()
        self.output_df: pd.DataFrame = self._build_output_df()

    def write_excel(self, path: str | Path = '') -> None:
        path = path or RESULT_ROOT / f'result_{self.data.path.stem}.xlsx'
        self.output_df.to_excel(path, index=False)

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
                "qty": "满足量",
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
        df["车次"] = df["车次"].str.split("_").str[-1]

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
