"""半开区间时段（含起点、不含终点）的基础运算。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

Segment = tuple[int, int]


def intersect(a: Segment, b: Segment) -> Segment | None:
    """两个时段的交集，无重叠返回 ``None``。"""
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    return (start, end) if start < end else None


def length(segment: Segment) -> int:
    return segment[1] - segment[0]


def merge(segments: Iterable[Segment]) -> list[Segment]:
    """合并互相邻接或重叠的时段，返回有序且互不重叠的列表。"""
    ordered = sorted((s for s in segments if s[1] > s[0]), key=lambda s: (s[0], s[1]))
    merged: list[Segment] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def union_length(segments: Iterable[Segment]) -> int:
    """并集总分钟数，重叠部分只算一次。"""
    return sum(length(segment) for segment in merge(segments))


def subtract(group: Sequence[Segment], removed: Sequence[Segment]) -> list[Segment]:
    """从一组时段里扣除另一组时段覆盖的部分。"""
    result: list[Segment] = []
    for start, end in merge(group):
        cursor = start
        for cut_start, cut_end in merge(removed):
            if cut_end <= cursor:
                continue
            if cut_start >= end:
                break
            if cut_start > cursor:
                result.append((cursor, min(cut_start, end)))
            cursor = max(cursor, cut_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return merge(result)
