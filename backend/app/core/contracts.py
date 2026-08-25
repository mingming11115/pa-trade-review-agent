from __future__ import annotations

import calendar
import re
from datetime import datetime


# 期货月份代码：1-12月对应不同的单字母代码
MONTH_CODES = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}
# 季度合约品种根代码
QUARTERLY_ROOTS = {"ES", "MES", "NQ", "MNQ"}
# 黄金品种根代码
GOLD_ROOTS = {"GC", "MGC"}
# 月度合约品种根代码
MONTHLY_ROOTS = {"CL", "MCL"}
# 所有支持的期货品种根代码
SUPPORTED_ROOTS = QUARTERLY_ROOTS | GOLD_ROOTS | MONTHLY_ROOTS
# 具体合约代码正则，格式如 ESH4、GCZ4 等
CONCRETE_SYMBOL = re.compile(r"^[A-Z0-9]+[FGHJKMNQUVXZ]\d$")


def resolve_contract_symbol(root: str, start: datetime, end: datetime) -> str:
    """根据品种根代码和起止时间，解析出具体合约代码。

    若输入已是具体合约代码则直接返回；否则根据品种类型选取合适的交割月份。
    """
    symbol = root.strip().upper()
    if CONCRETE_SYMBOL.fullmatch(symbol):
        return symbol
    if symbol not in SUPPORTED_ROOTS:
        raise ValueError(f"unsupported futures root: {symbol}")

    if symbol in QUARTERLY_ROOTS:
        month, year = _quarterly_contract(end)
    elif symbol in GOLD_ROOTS:
        month, year = _next_listed_month(end, (2, 4, 6, 8, 10, 12), strict=True)
    else:
        month, year = _add_month(end.year, end.month, 1)

    return f"{symbol}{MONTH_CODES[month]}{year % 10}"


def _quarterly_contract(anchor: datetime) -> tuple[int, int]:
    """选取季度合约月份：3/6/9/12月，若当月已过第三周五则顺延到下一个季度月。"""
    month, year = _next_listed_month(anchor, (3, 6, 9, 12), strict=False)
    if month == anchor.month:
        third_friday = calendar.Calendar().monthdatescalendar(year, month)
        expiry_day = [day for week in third_friday for day in week if day.weekday() == 4][2]
        if anchor.date() > expiry_day:
            return _next_listed_month(anchor, (3, 6, 9, 12), strict=True)
    return month, year


def _next_listed_month(
    anchor: datetime, listed_months: tuple[int, ...], *, strict: bool
) -> tuple[int, int]:
    """从给定的上市月份列表中，找到锚点时间之后的下一个上市月份。

    strict=True 时排除当月（即使当月是上市月也跳过）。
    """
    for month in listed_months:
        if month > anchor.month or (month == anchor.month and not strict):
            return month, anchor.year
    return listed_months[0], anchor.year + 1


def _add_month(year: int, month: int, offset: int) -> tuple[int, int]:
    """在给定年月基础上偏移指定月数，返回新的（月份, 年份）。"""
    absolute = year * 12 + month - 1 + offset
    return absolute % 12 + 1, absolute // 12
