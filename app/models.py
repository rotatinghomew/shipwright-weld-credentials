"""交换报文事实与服务运行期记录的数据结构。

报文记录保留原始字符串字段（源标识可追溯），同时预解析出分钟数时间点；
运行期记录（资格、门禁结论、通知、审计轨迹）则记录服务自身做出的判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .timeutil import format_hm, parse_hm


@dataclass(frozen=True)
class Worker:
    id: str
    factory: str
    cohort: str


@dataclass(frozen=True)
class Policy:
    id: str
    trade: str
    required_minutes: int
    pass_score: int
    required_jobs: tuple[str, ...]

    @classmethod
    def from_row(cls, row: dict) -> "Policy":
        return cls(
            id=row["id"],
            trade=row["trade"],
            required_minutes=int(row["required_minutes"]),
            pass_score=int(row["pass_score"]),
            required_jobs=tuple(row["required_jobs"]),
        )


@dataclass(frozen=True)
class Mentor:
    """分厂颁发的带教时间窗：工种、学员组别与班组均为限定范围。"""

    id: str
    factory: str
    trade: str
    team: str
    cohort: str
    from_time: int
    to_time: int

    @classmethod
    def from_row(cls, row: dict) -> "Mentor":
        return cls(
            id=row["id"],
            factory=row["factory"],
            trade=row["trade"],
            team=row["team"],
            cohort=row["cohort"],
            from_time=parse_hm(row["from_time"]),
            to_time=parse_hm(row["to_time"]),
        )


@dataclass(frozen=True)
class Log:
    """分厂分段签到与带教签核。"""

    id: str
    factory: str
    worker: str
    mentor: str
    trade: str
    team: str
    start: int
    end: int
    signed_at: int

    @classmethod
    def from_row(cls, row: dict) -> "Log":
        return cls(
            id=row["id"],
            factory=row["factory"],
            worker=row["worker"],
            mentor=row["mentor"],
            trade=row["trade"],
            team=row["team"],
            start=parse_hm(row["start"]),
            end=parse_hm(row["end"]),
            signed_at=parse_hm(row["signed_at"]),
        )


@dataclass(frozen=True)
class Correction:
    """源签核更正；只有签核所属分厂可以撤正本厂记录。"""

    id: str
    target: str
    factory: str
    action: str
    arrived_at: int

    @classmethod
    def from_row(cls, row: dict) -> "Correction":
        return cls(
            id=row["id"],
            target=row["target"],
            factory=row["factory"],
            action=row["action"],
            arrived_at=parse_hm(row["arrived_at"]),
        )


@dataclass(frozen=True)
class Seat:
    id: str
    centre: str
    trade: str
    capacity: int
    confirm_by: int
    begins: int

    @classmethod
    def from_row(cls, row: dict) -> "Seat":
        return cls(
            id=row["id"],
            centre=row["centre"],
            trade=row["trade"],
            capacity=int(row["capacity"]),
            confirm_by=parse_hm(row["confirm_by"]),
            begins=parse_hm(row["begins"]),
        )


@dataclass(frozen=True)
class Registration:
    """报名流水：``hold`` 保留座位、``confirm`` 实承诺、``move`` 放掉未开始的原考。"""

    id: str
    worker: str
    seat: str
    occurred: int
    action: str

    @classmethod
    def from_row(cls, row: dict) -> "Registration":
        return cls(
            id=row["id"],
            worker=row["worker"],
            seat=row["seat"],
            occurred=parse_hm(row["occurred"]),
            action=row["action"],
        )


@dataclass(frozen=True)
class Result:
    """考评点成绩；``checked_in`` 为假时只是待核记录，不能据此发证。"""

    id: str
    seat: str
    registration: str
    checked_in: bool
    score: int | None
    received: int

    @classmethod
    def from_row(cls, row: dict) -> "Result":
        score = row.get("score")
        return cls(
            id=row["id"],
            seat=row["seat"],
            registration=row["registration"],
            checked_in=bool(row["checked_in"]),
            score=None if score is None else int(score),
            received=parse_hm(row["received"]),
        )


@dataclass(frozen=True)
class Order:
    """工位工单：``planned_start`` 是排班时间，``actual_start`` 来自开工记录。"""

    id: str
    team: str
    trade: str
    kind: str
    planned_start: int
    actual_start: int | None
    mentor: str | None
    worker: str

    @classmethod
    def from_row(cls, row: dict) -> "Order":
        actual = row.get("actual_start")
        return cls(
            id=row["id"],
            team=row["team"],
            trade=row["trade"],
            kind=row["kind"],
            planned_start=parse_hm(row["planned_start"]),
            actual_start=None if actual is None else parse_hm(actual),
            mentor=row.get("mentor"),
            worker=row["worker"],
        )


# --- 运行期记录 -----------------------------------------------------------


@dataclass
class LogAssessment:
    """单条签核在某个认定时点的状态。"""

    log_id: str
    factory: str
    trade: str
    team: str
    mentor_id: str
    segment: tuple[int, int]
    valid: bool
    excluded_reason: str | None = None
    voided_by: str | None = None
    # 该签核最终可计入的无争议时段（已扣除归属冲突段与失效部分）
    counted_segments: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Dispute:
    """跨厂归属冲突：同一时段被两家报工时，冲突段留给中心复核。"""

    id: str
    worker: str
    trade: str
    segment: tuple[int, int]
    factories: tuple[str, ...]
    log_ids: tuple[str, ...]
    raised_at: int
    status: str = "open"  # open / resolved
    resolved_at: int | None = None
    resolution: str | None = None


@dataclass
class GateDecision:
    """工单开工门禁结论，事实快照随结论一并留档。"""

    order_id: str
    evaluated_at: int
    allowed: bool
    reasons: list[str]
    qualification_id: str | None
    qualification_state: str | None
    mentor_required: bool
    mentor: dict | None
    facts: dict


@dataclass
class StopNotice:
    """更正影响在制工单时发给工位负责人的停工通知。"""

    id: str
    order_id: str
    team: str
    worker: str
    trade: str
    issued_at: int
    correction_id: str
    qualification_id: str
    message: str
    # 已实际开工的记录予以保留，不回删
    preserved_actual_start: int | None


@dataclass
class AuditEvent:
    """资格审计轨迹：开工用了哪些事实、后来哪项更正改变了用工决定。"""

    at: int
    category: str
    summary: str
    facts: dict
    worker: str | None = None
    trade: str | None = None
