import pandas as pd
from manufacture_jis.jis_data import JISData
from manufacture_jis.base import Vehicle


class JISResult:
    def __init__(self, data: JISData, vehicles: list[Vehicle]):
        self.data = data
        self.vehicles = vehicles
        self.result_df: pd.DataFrame = self._build_result_df()

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
                "arrival_lb",
                "arrival_ub",
                "arrival",
            ],
        )
        df['pc_per_pallet'] = df['item'].map(self.data.pc_per_pallet)
        return df
