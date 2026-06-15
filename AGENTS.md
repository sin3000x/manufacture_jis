# AGENTS.md

## 项目概述

manufacture-jis 是一个 JIS（Just-In-Sequence）物料配送优化系统。输入 Excel 中的排产计划和供应商需求，用 CP-SAT 求解器决定哪些车次在什么时间送达哪些物料，最小化用车数量。

## 技术栈

- Python 3.11，`uv` 管理依赖和运行命令，`pytest` 测试
- 包布局：`src/manufacture_jis/`，`pyproject.toml` 含 `pythonpath = ["src"]`
- pandas + openpyxl 读 Excel，OR-Tools CP-SAT 求解
- 启动脚本：`PYTHONPATH=src uv run uvicorn manufacture_jis.web.app:app --host 0.0.0.0 --port 8502`

## 核心概念

| 概念 | 数据类 | 含义 |
|---|---|---|
| HourlyConsumption | `base.py` | 一个任务令在某个加工小时的物料消耗，含到达时间窗 `[arrival_lb, arrival_ub]` |
| Vehicle | `base.py` | 一辆卡车，属于某供应商，有容量（板/车） |

## 数据流

```
Excel (4张表) → JISData（加载、校验、按排产拆小时级消耗）→ JISCPModel（CP-SAT 建模求解）→ JISResult（提取解）
```

**JISData** 是核心加载器：
- **排产**驱动小时拆分：一个任务令的加工时间 = `ceil(完工-开工-休息)`，每个小时生成一条 HourlyConsumption，qty = `计划量 / 加工时间`（整数均分+余数）
- **需求**只用于构建 supplier↔item 的静态映射（`s_to_i_set`、`i_to_v_set`、`si2qty`），不参与消耗量计算
- `c_to_v_set[consumption]` = 该物料所有供应商的全部车辆，由 CP 模型决定具体分配
- 中文字段通过 `*_COL_MAP` 在读取时即时映射为英文，避免后续适配
- origin = 最早开工时间当天 0 点 - 1 天，保证到达时间非负
- 每个供应商默认 5 辆车，命名 `{供应商}_车次{1..N}`

## 建模要点

- 变量：`use_vehicle`（是否启用）、`assign`（消耗-车辆分配）、`arrival`（到达时间）、`loaded_pallets`（装载板数）
- 约束：每条消耗恰好一车、到达时间窗、车容量
- 目标：最小化启用车辆数
- 注意 `_constraint_loaded_pallets` 必须过滤 `v in data.c_to_v_set[c]`，否则跨供应商 KeyError

## 业务约束

以下约束与 `JISCPModel` 保持一致，也应由 `JISResult.check()` 做结果回验。`check()` 的职责是返回问题列表，不抛异常；空列表表示通过。

### 1. 消耗分配与车辆启用

- 每条小时级消耗 `HourlyConcumption` 必须且只能由一辆候选车辆配送，候选车辆来自 `c_to_v_set[cid]`。
- 车辆只要承接任意消耗就视为启用；启用车辆必须至少承接一条消耗。
- 结果对象中只保留被启用的车辆，但仍需保证每条消耗在结果里恰好出现一次。

### 2. 到达时间约束

- 同一车辆承接的所有消耗共享一个到达时间 `arrival`。
- 该到达时间必须同时落在该车每条消耗各自的到达时间窗 `[arrival_lb, arrival_ub]` 内。
- 到达时间只能取 `arrival_domain` 中的非休息时段。
- 【可选，结果对象中不体现】未启用车辆的到达时间在模型中被固定到时间域最后一个值，用于打破对称性。

### 3. 装载板数与车辆容量

- 每辆车按物料汇总装载量；同一物料的装载板数应为 `ceil(该车该物料总件数 / 包规)`。
- 车辆记录的 `item_to_loaded_pallets` 不应包含未实际装载的物料，除非对应板数为 0。
- 每辆车的总装载板数不得超过车辆车规 `capacity`。

### 4. 供应商-物料总量守恒

- 对每个供应商-物料组合，所有该供应商车辆上的该物料总板数不能少于 `ceil(需求数量 / 包规)`。

### 5. 对称性处理【可选，不做检查】

- 同一供应商的车次按自然序打破对称：后序车启用时，前序车也必须启用。
- 同一供应商的前序车到达时间不晚于后序车。

### 6. `JISResult.check()` 的约定

- `check()` 只负责校验，不负责修复。
- `check()` 返回 `list[str]`，每条字符串描述一个问题。
- 若返回空列表，则表示该结果满足当前业务约束。
