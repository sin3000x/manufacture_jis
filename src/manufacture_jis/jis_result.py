import pandas as pd
from manufacture_jis.jis_data import JISData, hour_to_datetime
from manufacture_jis.base import Vehicle


class JISResult:
    def __init__(self, data: JISData, vehicles: list[Vehicle]):
        self.data = data
        self.vehicles = vehicles
        self.result_df: pd.DataFrame = self._build_result_df()
        self.output_df: pd.DataFrame = self._build_output_df()

    def _build_output_df(self) -> pd.DataFrame:
        df: pd.DataFrame = self.result_df.groupby(
            by=["supplier", "v", "item", "mfg_order"],
            as_index=False,
        ).agg(
            qty=("qty", "sum"),
            pc_per_pallet=("pc_per_pallet", "first"),
            loaded_pallets=("loaded_pallets", "sum"),
            total_loaded_pallets=("total_loaded_pallets", "first"),
            capacity=("capacity", "first"),
            arrival=("arrival", "first"),
        )
        df["到达时间"] = hour_to_datetime(df["arrival"], self.data.origin).dt.strftime(
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
            },
        )
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
