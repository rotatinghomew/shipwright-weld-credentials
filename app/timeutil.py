"""值班日时间口径。

所有参与方共用 UTC+08:00 的同一个值班日（见《实训与考评交换约定》），
报文里的 ``HH:MM`` 统一换算为当日零点起的分钟数，便于排序和时段运算；
对外展示时再还原为 ``HH:MM`` 或带时区的 ISO 字符串。时段一律含起点、不含终点。
"""

from __future__ import annotations

DUTY_DATE = "2026-12-01"
TIMEZONE_LABEL = "UTC+08:00"
ISO_TZ = "+08:00"


def parse_hm(value: str | int) -> int:
    """把 ``HH:MM`` 解析为分钟数；已经是整数的分钟数原样返回。"""
    if isinstance(value, int):
        return value
    hour_text, minute_text = str(value).split(":")
    hour, minute = int(hour_text), int(minute_text)
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError(f"非法时间: {value!r}")
    return hour * 60 + minute


def format_hm(minutes: int) -> str:
    """分钟数还原为 ``HH:MM``。"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def iso_text(minutes: int) -> str:
    """值班日某分钟对应的带时区 ISO-8601 文本。"""
    return f"{DUTY_DATE}T{format_hm(minutes)}:00{ISO_TZ}"
