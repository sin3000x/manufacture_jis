# AGENTS.md

## 项目概述

manufacture-jis 是一个 JIS（Just-In-Sequence）物料配送优化系统。输入 Excel 中的排产计划和供应商需求，用 CP-SAT 求解器决定哪些车次在什么时间送达哪些物料，最小化用车数量。

## 技术栈

- Python 3.11，`uv` 管理依赖，`pytest` 测试
- 包布局：`src/manufacture_jis/`，`pyproject.toml` 含 `pythonpath = ["src"]`
- pandas + openpyxl 读 Excel，OR-Tools CP-SAT 求解

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
