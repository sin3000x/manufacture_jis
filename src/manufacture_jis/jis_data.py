import io
from pathlib import Path
from manufacture_jis.base import HourlyConcumption, Vehicle
from collections import defaultdict


class JISData:
    def __init__(self, path: str | Path | io.BytesIO):
        self.path = path
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
        # 包规：每个栈板有多少个物料
        self.pc_per_pallet: dict[str, int] = {}
        # (供应商, 物料编码) -> 总数
        self.si2qty: dict[tuple[str, str], int] = defaultdict(int)