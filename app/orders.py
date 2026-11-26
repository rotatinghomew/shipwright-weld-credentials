"""开工门禁与带教认定。

工位必须在开工（``actual_start``）时现场检查资格与带教，即使班前排班
（``planned_start``）已经显示可以上岗：门禁结论只采用开工时点的事实快照。

- ``seam`` 类工单可独立开工；``pipe`` 类工单须现场带教；
- 带教凭本班组及当前有效范围认定：带教师的工厂/工种/班组与工单一致、
  时间窗覆盖开工时刻、学员组别在带教范围内；
- 资格处于 ``paused`` 或 ``pending`` 均不得开工，回执中写明被拦原因与所缺条件。
"""

from __future__ import annotations

from .certification import CertificationView
from .models import GateDecision, Mentor, Order, Policy, Worker
from .timeutil import format_hm

# 须现场带教的工单类别（《交换约定》：夹具中 pipe 须现场带教）
SUPERVISED_JOB_KINDS = frozenset({"pipe"})


def evaluate_gate(
    order: Order,
    evaluated_at: int,
    cert: CertificationView | None,
    policy: Policy | None,
    worker: Worker,
    mentors: dict[str, Mentor],
) -> GateDecision:
    reasons: list[str] = []
    mentor_info: dict | None = None
    mentor_required = order.kind in SUPERVISED_JOB_KINDS
    qualification_id = cert.qualification_id if cert else None
    qualification_state = cert.state if cert else None

    # 1. 工种政策与可承担工单类别
    if policy is None:
        reasons.append("no_policy_for_trade")
    elif order.kind not in policy.required_jobs:
        reasons.append(f"job_kind_{order.kind}_not_covered_by_qualification")

    # 2. 资格状态
    if cert is None:
        reasons.append("qualification_not_found")
    elif cert.state == "paused":
        reasons.append("qualification_paused")
    elif cert.state == "pending":
        reasons.append(f"qualification_pending:{cert.exam.status}")
    elif cert.state != "active":  # pragma: no cover - 防御性分支
        reasons.append(f"qualification_state_{cert.state}")

    # 3. 现场带教
    if mentor_required:
        if order.mentor is None:
            reasons.append("on_site_mentor_required_but_none_assigned")
        else:
            mentor_info = _check_mentor(order, evaluated_at, worker, mentors)
            if not mentor_info["valid"]:
                reasons.append(f"mentor_invalid:{mentor_info['reason']}")
    elif order.mentor is not None:
        # 独立上岗工单如安排了带教，也顺带核验并在回执中展示，不拦截
        mentor_info = _check_mentor(order, evaluated_at, worker, mentors)

    facts = {
        "order_id": order.id,
        "team": order.team,
        "trade": order.trade,
        "kind": order.kind,
        "planned_start": format_hm(order.planned_start),
        "actual_start": format_hm(evaluated_at),
        "worker": order.worker,
        "worker_factory": worker.factory,
        "worker_cohort": worker.cohort,
        "mentor_required": mentor_required,
        "qualification": _cert_fact(cert),
        "missing_conditions": list(cert.missing) if cert else [],
    }

    return GateDecision(
        order_id=order.id,
        evaluated_at=evaluated_at,
        allowed=not reasons,
        reasons=reasons,
        qualification_id=qualification_id,
        qualification_state=qualification_state,
        mentor_required=mentor_required,
        mentor=mentor_info,
        facts=facts,
    )


def _check_mentor(
    order: Order, at: int, worker: Worker, mentors: dict[str, Mentor]
) -> dict:
    mentor = mentors.get(order.mentor or "")
    if mentor is None:
        return {"mentor_id": order.mentor, "valid": False, "reason": "mentor_not_found"}
    checks = {
        "team_match": mentor.team == order.team,
        "trade_match": mentor.trade == order.trade,
        "cohort_in_scope": mentor.cohort == worker.cohort,
        "window_covers_start": mentor.from_time <= at < mentor.to_time,
    }
    failed = next((name for name, ok in checks.items() if not ok), None)
    return {
        "mentor_id": mentor.id,
        "factory": mentor.factory,
        "team": mentor.team,
        "trade": mentor.trade,
        "cohort": mentor.cohort,
        "window": {
            "from": format_hm(mentor.from_time),
            "to": format_hm(mentor.to_time),
            "covers_start": checks["window_covers_start"],
        },
        "valid": failed is None,
        "reason": failed,
    }


def _cert_fact(cert: CertificationView | None) -> dict | None:
    if cert is None:
        return None
    return {
        "qualification_id": cert.qualification_id,
        "state": cert.state,
        "exam_status": cert.exam.status,
        "counted_minutes": cert.experience.counted_minutes,
        "evidence_log_ids": list(cert.evidence["log_ids"]),
        "result_id": cert.exam.result_id,
    }
