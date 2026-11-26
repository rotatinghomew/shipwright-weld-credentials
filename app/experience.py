"""实操经历认定。

认定依据（见《实训与考评交换约定》及业务说明）：

1. 签核在认定时点未被本厂更正撤回；
2. 带教师时间窗真实存在，且工厂、工种、班组与签核一致，学员属于带教范围的组别；
3. 只有落入带教有效时间窗的签到时段可以计入；
4. 同一学员、同一工种在不同分厂的有效时段若发生重叠，归属冲突段一律不计入，
   留待中心复核；同厂重叠按并集处理，不重复计时；
5. 冲突一方的签核后来被撤回的，冲突解除，剩余无争议部分继续计入。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import segments as seg
from .models import Dispute, Log, LogAssessment, Mentor, Worker


@dataclass
class ExperienceView:
    worker_id: str
    trade: str
    as_of: int
    assessments: list[LogAssessment]
    counted_segments: list[tuple[int, int]]
    counted_minutes: int
    # 仍处于归属冲突、等待中心复核的时段
    open_disputes: list[Dispute]
    factory_minutes: dict[str, int]

    @property
    def valid_log_ids(self) -> tuple[str, ...]:
        return tuple(a.log_id for a in self.assessments if a.valid)


def _base_assessment(
    log: Log,
    worker: Worker,
    mentors: dict[str, Mentor],
    voided_by: dict[str, str],
) -> LogAssessment:
    correction_id = voided_by.get(log.id)
    if correction_id is not None:
        return LogAssessment(
            log_id=log.id,
            factory=log.factory,
            trade=log.trade,
            team=log.team,
            mentor_id=log.mentor,
            segment=(log.start, log.end),
            valid=False,
            excluded_reason="voided",
            voided_by=correction_id,
        )

    mentor = mentors.get(log.mentor)
    if mentor is None:
        reason = "mentor_not_found"
    elif mentor.factory != log.factory:
        reason = "mentor_factory_mismatch"
    elif mentor.trade != log.trade:
        reason = "mentor_trade_mismatch"
    elif mentor.team != log.team:
        reason = "mentor_team_mismatch"
    elif mentor.cohort != worker.cohort:
        reason = "cohort_outside_mentor_scope"
    else:
        reason = None

    if reason is not None:
        return LogAssessment(
            log_id=log.id,
            factory=log.factory,
            trade=log.trade,
            team=log.team,
            mentor_id=log.mentor,
            segment=(log.start, log.end),
            valid=False,
            excluded_reason=reason,
        )

    covered = seg.intersect((log.start, log.end), (mentor.from_time, mentor.to_time))
    if covered is None:
        return LogAssessment(
            log_id=log.id,
            factory=log.factory,
            trade=log.trade,
            team=log.team,
            mentor_id=log.mentor,
            segment=(log.start, log.end),
            valid=False,
            excluded_reason="outside_mentor_window",
        )
    return LogAssessment(
        log_id=log.id,
        factory=log.factory,
        trade=log.trade,
        team=log.team,
        mentor_id=log.mentor,
        segment=covered,
        valid=True,
    )


def recognize(
    worker_id: str,
    trade: str,
    as_of: int,
    workers: dict[str, Worker],
    mentors: dict[str, Mentor],
    logs: list[Log],
    voided_by: dict[str, str],
) -> ExperienceView:
    """在 ``as_of`` 时点重新认定某学员某工种的可计入经历。

    ``voided_by`` 为已生效的撤正映射：签核号 -> 更正号。
    结果完全由传入事实推导，便于任意时点回放。
    """
    worker = workers[worker_id]
    related = [
        log
        for log in logs
        if log.worker == worker_id
        and log.trade == trade
        # 回放口径：签核在其发出之后才成为有效事实
        and log.signed_at <= as_of
    ]
    assessments = [_base_assessment(log, worker, mentors, voided_by) for log in related]
    valid = [a for a in assessments if a.valid]

    # 跨厂重叠 = 归属冲突，整段从计时中扣除并生成复核事项
    open_disputes: list[Dispute] = []
    conflict_segments: list[tuple[int, int]] = []
    for i, left in enumerate(valid):
        for right in valid[i + 1 :]:
            if left.factory == right.factory:
                continue
            overlap = seg.intersect(left.segment, right.segment)
            if overlap is None:
                continue
            conflict_segments.append(overlap)
            factories = tuple(sorted({left.factory, right.factory}))
            open_disputes.append(
                Dispute(
                    id=f"D-{left.log_id}-{right.log_id}",
                    worker=worker_id,
                    trade=trade,
                    segment=overlap,
                    factories=factories,
                    log_ids=(left.log_id, right.log_id),
                    raised_at=min(
                        next(l.end for l in related if l.id == left.log_id),
                        next(l.end for l in related if l.id == right.log_id),
                    ),
                )
            )

    conflicts = seg.merge(conflict_segments)
    counted_union: list[tuple[int, int]] = []
    for assessment in valid:
        kept = seg.subtract([assessment.segment], conflicts)
        assessment.counted_segments = kept
        counted_union.extend(kept)
    counted_union = seg.merge(counted_union)

    factory_minutes: dict[str, int] = {}
    for assessment in valid:
        factory_minutes.setdefault(assessment.factory, 0)
        factory_minutes[assessment.factory] += sum(
            seg.length(s) for s in assessment.counted_segments
        )

    return ExperienceView(
        worker_id=worker_id,
        trade=trade,
        as_of=as_of,
        assessments=assessments,
        counted_segments=counted_union,
        counted_minutes=seg.union_length(counted_union),
        open_disputes=open_disputes,
        factory_minutes=factory_minutes,
    )
