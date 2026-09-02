"""值标准化：T1 匹配前的可配置管线（去除空格、大小写、全半角、百分号/小数归一）。

验收标准 10：原值格式差异（13% vs 0.13、全半角、大小写、空格）不影响记忆命中。
"""
import re

from .config import settings

_FULL2HALF = {0xFF01 + i: chr(0x21 + i) for i in range(0x5E)}
_FULL2HALF[0x3000] = " "  # 全角空格
_FULL2HALF_TABLE = str.maketrans(_FULL2HALF)


def _trim(s: str) -> str:
    return s.strip()


def _full2half(s: str) -> str:
    return s.translate(_FULL2HALF_TABLE)


def _lower(s: str) -> str:
    return s.lower()


def _collapse_spaces(s: str) -> str:
    """去除所有空白字符（匹配键不看空格差异）。"""
    return re.sub(r"\s+", "", s)


def _percent_decimal(s: str) -> str:
    """百分号/小数归一：13% ≡ 13 ≡ 0.13 → '13'；13.5% → '13.5'。非数值原样返回。

    约定：未带百分号且含小数点、绝对值小于 1 的小数视为百分数的 1/100 形式（0.13 = 13%）。
    """
    t = s.strip()
    if not t:
        return s
    had_pct = "%" in t
    body = t.replace("%", "").strip()
    if not body:
        return s
    try:
        f = float(body)
    except ValueError:
        return s
    if not had_pct and "." in body and abs(f) < 1:
        f *= 100
    return format(round(f, 8), "g")


_STEPS = {
    "trim": _trim,
    "full2half": _full2half,
    "lower": _lower,
    "collapse_spaces": _collapse_spaces,
    "percent_decimal": _percent_decimal,
}


def normalize_value(value, steps: list[str] | None = None) -> str:
    """按配置管线标准化一个值，用于匹配比较（不改变存储原值）。"""
    if value is None:
        return ""
    s = str(value)
    for name in (steps or settings.normalize_steps):
        fn = _STEPS.get(name)
        if fn:
            s = fn(s)
    return s
