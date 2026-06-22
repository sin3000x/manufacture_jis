from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import unicodedata

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet


def write_df_to_excel(
    df: pd.DataFrame,
    path: str | Path,
    merge_dict: dict[tuple[str, ...], list[str]] | None = None,
) -> None:
    """将 DataFrame 写入 xlsx，并对标题行加粗、所有单元格居中、按规则纵向合并单元格。

    写入流程：
      1. 用 pandas 将 df 写成 xlsx（无索引列）。
      2. 若未提供 merge_dict，直接返回，不做后处理。
      3. 否则用 openpyxl 重新打开文件，依次执行：
         - 自动调整列宽（上限 40）；
         - 标题行加粗，全表居中；
         - 按 merge_dict 对数据行做纵向合并。

    Args:
        df: 待写入的 DataFrame。若提供了 merge_dict，df 应已按 merge_dict 的
            key 列预先排好序，否则同组行不连续会导致合并结果不符合预期。
        path: 输出文件路径，扩展名应为 .xlsx；父目录须已存在。
        merge_dict: 合并规则，key 为分组依据列（tuple），value 为在该分组内需要
            纵向合并的目标列列表。同一 key 对应的行若值完全相同则会被合并为一格。
            示例：{("供应商",): ["供应商"], ("供应商", "车次"): ["车次", "物料"]}
            表示先按供应商合并"供应商"列，再按供应商+车次合并"车次"和"物料"列。
            传入 None 或空字典则跳过所有后处理，仅写原始数据。

    Returns:
        None。写入结果直接落盘到 path，不返回任何值。
    """
    path = Path(path)
    df.to_excel(path, index=False)

    if not merge_dict:
        return

    wb = load_workbook(path)
    ws = wb.active
    # 建立列名 → 列号（1-based）映射，供合并时定位目标列
    col_index = {name: i + 1 for i, name in enumerate(df.columns)}
    header_row = 1

    center = Alignment(horizontal="center", vertical="center")
    bold = Font(bold=True)

    normalized_merge_dict = _normalize_merge_dict(merge_dict)
    # 按 key 长度升序，确保父分组（短 key）先于子分组（长 key）合并，避免区域冲突
    ordered_keys = sorted(normalized_merge_dict, key=len)

    _apply_auto_width(ws)

    # 全表居中，标题行加粗
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = center
            if cell.row == header_row:
                cell.font = bold

    for group_keys in ordered_keys:
        target_cols = normalized_merge_dict[group_keys]
        # 过滤 df 中不存在的列，避免 KeyError
        group_cols = [col for col in group_keys if col in col_index]
        target_col_indices = [col_index[c] for c in target_cols if c in col_index]
        if not group_cols or not target_col_indices:
            continue

        for _, group in df.groupby(group_cols, sort=False):
            rows = group.index.tolist()
            if not rows:
                continue

            # DataFrame index 从 0 起，Excel 含标题行且从 1 起，故偏移 +2
            start_excel = rows[0] + header_row + 1
            end_excel = rows[-1] + header_row + 1
            for ci in target_col_indices:
                if start_excel != end_excel:
                    ws.merge_cells(
                        start_row=start_excel,
                        end_row=end_excel,
                        start_column=ci,
                        end_column=ci,
                    )
                # 合并后只有左上角单元格有效，需显式重设对齐
                ws.cell(row=start_excel, column=ci).alignment = center

    wb.save(path)


def _normalize_merge_dict(
    merge_dict: dict[tuple[str, ...] | Sequence[str], list[str]],
) -> dict[tuple[str, ...], list[str]]:
    """将 merge_dict 的 key 统一转为 tuple，并对 value 去重（保序）。

    Args:
        merge_dict: 原始合并规则，key 可为任意 Sequence[str]（list、tuple 均可）。

    Returns:
        key 全部为 tuple 的新字典；value 中重复列名已被移除，顺序不变。
    """
    normalized: dict[tuple[str, ...], list[str]] = {}
    for raw_group_keys, target_cols in merge_dict.items():
        group_keys = tuple(raw_group_keys)
        # 用列表而非 set 保留列的原始顺序
        seen: set[str] = set()
        dedup_targets = [c for c in target_cols if not (c in seen or seen.add(c))]  # type: ignore[func-returns-value]
        normalized[group_keys] = dedup_targets
    return normalized


def _apply_auto_width(ws: Worksheet) -> None:
    """按列内容最大显示宽度自动调整列宽，额外留 2 个字符余量，上限 40。

    Args:
        ws: 待调整的 openpyxl Worksheet 对象，直接在原对象上修改，无返回值。
    """
    for column_cells in ws.columns:
        col_letter = column_cells[0].column_letter
        width = max((_display_width(cell.value) for cell in column_cells), default=0)
        if width > 0:
            ws.column_dimensions[col_letter].width = min(width + 2, 40)


def _display_width(value: object) -> int:
    """计算值转为字符串后的终端/Excel 显示宽度。

    全角字符（CJK 汉字、全角标点等，east_asian_width 为 F/W/A）计为 2，
    其余 ASCII 及半角字符计为 1。

    Args:
        value: 任意值，None 视为空字符串（返回 0），其余类型调用 str() 转换。

    Returns:
        非负整数，表示该值在等宽字体下的显示列数。
    """
    if value is None:
        return 0
    width = 0
    for char in str(value):
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W", "A"} else 1
    return width
