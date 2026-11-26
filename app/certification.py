"""资格条件汇总与状态认定。

一份资格由两个条件支撑，按工种分别认定：

- 经历条件：可计入实操分钟数达到政策下限；
- 考评条件：在已承诺的名额上实际签到，且成绩达到通过分数。

状态语义：

- ``active``：两个条件均满足，可用于开工与跨厂验真；
- ``paused``：依据有误或不再够用（如签核被撤），暂停依赖它的发证与用工，
  服务须指出缺的是哪个条件；
- ``pending``：经历已够但考评结论尚不能确认。其中要区分
  ``known_ineligible``（已知道不合格）与``awaiting_result``（答复未到），
  等待期间不得把同一个名额再次分出去。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .experience import ExperienceView
from .models import Policy, Registration, Result, Seat

CertState = Literal["active", "paused", "pending"]
ExamStatus = Literal[
    "passed",
    "failed",
    "pending_review",
    "awaiting_result",
    "unregistered",
]


@dataclass
class ExamAssessment:
    status: ExamStatus
    detail: str | None
    result_id: str | None = None
    registration_id: str | None = None
    seat_id: str | None = None
    score: int | None = None
    checked_in: bool | None = None
    # 迟到成绩仍能回溯原报名，但不能覆盖新考评、不能据此发证
    late: bool = False


@dataclass
class Condition:
    key: str
    satisfied: bool
    detail: dict


@dataclass
class CertificationView:
    qualification_id: str
    worker: str
    trade: str
    state: CertState
    as_of: int
    conditions: list[Condition]
    missing: list[dict]
    exam: ExamAssessment
    experience: ExperienceView
    evidence: dict
    # 暂停发证时依赖的更正号（如有）
    paused_by_corrections: tuple[str, ...] = field(default_factory=tuple)

    @property
    def experience_ok(self) -> bool:
        return self.conditions[0].satisfied

    @property
    def exam_ok(self) -> bool:
        return self.conditions[1].satisfied


def assess_exam(
    worker: str,
    policy: Policy,
    as_of: int,
    seats: dict[str, Seat],
    registrations: list[Registration],
    results: list[Result],
    promised_seats: dict[str, str],
) -> ExamAssessment:
    """根据报名与成绩判断考评条件。

    ``promised_seats`` 为 座位号 -> 已承诺学员号 的映射，由名额状态机提供。
    """
    worker_regs = {reg.id: reg for reg in registrations if reg.worker == worker}

    # 成绩按报名号回溯原报名；迟到成绩（原报名已 move 或名额另许）只留痕
    for result in sorted(results, key=lambda r: r.received):
        reg = worker_regs.get(result.registration)
        if reg is None:
            continue
        seat = seats.get(result.seat)
        if seat is None or seat.trade != policy.trade:
            continue
        still_promised = promised_seats.get(result.seat) == worker
        late = not still_promised
        base = dict(
            result_id=result.id,
            registration_id=reg.id,
            seat_id=result.seat,
            score=result.score,
            checked_in=result.checked_in,
            late=late,
        )
        if not result.checked_in:
            return ExamAssessment(
                status="pending_review",
                detail="result_without_checkin_under_manual_review",
                **base,
            )
        if result.score is None:
            return ExamAssessment(
                status="awaiting_result",
                detail="checked_in_but_score_missing",
                **base,
            )
        if result.score < policy.pass_score:
            # 已经知道不合格
            return ExamAssessment(status="failed", detail="score_below_pass", **base)
        if late:
            # 分数本身通过，但迟到成绩不能据此发证，也不能覆盖新考评
            return ExamAssessment(
                status="pending_review",
                detail="late_result_conflicts_with_attendance_manual_review",
                **base,
            )
        return ExamAssessment(status="passed", detail=None, **base)

    # 尚无任何成绩：判断是否存在仍在等待答复的承诺名额
    for seat_id, promised_worker in promised_seats.items():
        if promised_worker != worker:
            continue
        seat = seats[seat_id]
        if seat.trade != policy.trade:
            continue
        reg = next(
            (r for r in worker_regs.values() if r.seat == seat_id and r.action == "confirm"),
            None,
        )
        if reg is not None and as_of < seat.begins:
            return ExamAssessment(
                status="awaiting_result",
                detail="confirmed_seat_awaiting_exam",
                registration_id=reg.id,
                seat_id=seat_id,
            )
        if reg is not None:
            return ExamAssessment(
                status="awaiting_result",
                detail="exam_answered_not_yet_received",
                registration_id=reg.id,
                seat_id=seat_id,
            )

    return ExamAssessment(status="unregistered", detail="no_trade_registration")


def build_view(
    worker: str,
    policy: Policy,
    as_of: int,
    experience_view: ExperienceView,
    exam: ExamAssessment,
    voided_corrections: tuple[str, ...] = (),
) -> CertificationView:
    experience_ok = experience_view.counted_minutes >= policy.required_minutes
    exam_ok = exam.status == "passed"

    conditions = [
        Condition(
            key="practice_minutes",
            satisfied=experience_ok,
            detail={
                "trade": policy.trade,
                "counted_minutes": experience_view.counted_minutes,
                "required_minutes": policy.required_minutes,
                "factory_minutes": dict(experience_view.factory_minutes),
                "counted_segments": [
                    {"start": s[0], "end": s[1]} for s in experience_view.counted_segments
                ],
                "open_disputes": [
                    {
                        "id": d.id,
                        "segment": {"start": d.segment[0], "end": d.segment[1]},
                        "factories": list(d.factories),
                        "log_ids": list(d.log_ids),
                    }
                    for d in experience_view.open_disputes
                ],
            },
        ),
        Condition(
            key="trade_exam",
            satisfied=exam_ok,
            detail={
                "status": exam.status,
                "detail": exam.detail,
                "result_id": exam.result_id,
                "registration_id": exam.registration_id,
                "seat_id": exam.seat_id,
                "score": exam.score,
                "pass_score": policy.pass_score,
                "checked_in": exam.checked_in,
                "late": exam.late,
            },
        ),
    ]

    missing: list[dict] = []
    if not experience_ok:
        missing.append(
            {
                "condition": "practice_minutes",
                "shortfall_minutes": policy.required_minutes
                - experience_view.counted_minutes,
                "required_minutes": policy.required_minutes,
                "counted_minutes": experience_view.counted_minutes,
            }
        )
    if not exam_ok:
        missing.append(
            {
                "condition": "trade_exam",
                "status": exam.status,
                "detail": exam.detail,
            }
        )

    if experience_ok and exam_ok:
        state: CertState = "active"
    elif not experience_ok and voided_corrections:
        # 原有依据被撤正后不再够用：暂停依赖该资格的发证与用工
        state = "paused"
    else:
        # 经历尚未攒够、或考评暂不能确认（含已知不合格与等待答复）
        state = "pending"

    evidence = {
        "log_ids": list(experience_view.valid_log_ids),
        "excluded_logs": [
            {
                "log_id": a.log_id,
                "factory": a.factory,
                "reason": a.excluded_reason,
                "voided_by": a.voided_by,
            }
            for a in experience_view.assessments
            if not a.valid
        ],
        "registration_id": exam.registration_id,
        "result_id": exam.result_id,
        "seat_id": exam.seat_id,
    }

    return CertificationView(
        qualification_id=f"Q-{worker}-{policy.trade}",
        worker=worker,
        trade=policy.trade,
        state=state,
        as_of=as_of,
        conditions=conditions,
        missing=missing,
        exam=exam,
        experience=experience_view,
        evidence=evidence,
        paused_by_corrections=voided_corrections,
    )
