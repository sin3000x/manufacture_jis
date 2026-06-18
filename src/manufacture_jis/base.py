from dataclasses import dataclass, field


@dataclass
class Concumption:
    """一条任务令的消耗。假设这条消耗只在一辆车上，这是符合业务认知的。"""
    cid: str  # 这条消耗的id
    mfg_order: str  # 任务令
    consumption_time: int  # 开始消耗的时间点
    item: str  # 物料编码
    qty: int  # 消耗数量
    arrival_lb: int  # 最早到达时间
    arrival_ub: int  # 最晚到达时间


@dataclass
class Vehicle:
    """车辆"""
    supplier: str  # 供应商
    v: str  # 车辆id
    capacity: int  # 车规：可以装载的最大板数

    # 装载的小时级消耗
    loads: list[Concumption] = field(default_factory=list)
    # 到达时间
    arrival: int = 0
    # 装载板数
    item_to_loaded_pallets: dict[str, int] = field(default_factory=dict)
