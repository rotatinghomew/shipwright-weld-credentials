"""实训资格服务门面。

服务把两类判断接起来：考评员核实的实操达标判断，与工位负责人在开工时
对“这份资格今天能不能用于手头工单”的判断。所有事件按到达时点依次受理，
任意时点的认定都由当时仍有效的源事实重新推导，便于回放与审计。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import certification as cert_mod
from . import experience as exp_mod
from . import orders as order_mod
from . import seating as seat_mod
from .models import (
    AuditEvent,
    Correction,
    Dispute,
    GateDecision,
    Log,
    Mentor,
    Order,
    Policy,
    Registration,
    Result,
    Seat,
    StopNotice,
    Worker,
)
from .timeutil import format_hm, iso_text, parse_hm


class ServiceError(Exception):
    """业务受理失败；``code`` 供接口层映射状态码。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class IngestEvent:
    at: int
    kind: str
    source: str
    payload_id: str
    accepted: bool
    reason: str | None


@dataclass
class _CertRuntime:
    view: cert_mod.CertificationView
    history: list[AuditEvent] = field(default_factory=list)
    last_state: str | None = None


class TrainingService:
    def __init__(self) -> None:
        self.workers: dict[str, Worker] = {}
        self.policies: dict[str, Policy] = {}
        self.policy_by_trade: dict[str, Policy] = {}
        self.mentors: dict[str, Mentor] = {}
        self.seats: dict[str, Seat] = {}

        self.logs: list[Log] = []
        self._log_ids: set[str] = set()
        self.corrections: list[Correction] = []
        self.registrations: list[Registration] = []
        self.results: list[Result] = []
        self.orders: dict[str, Order] = {}

        # 签核号 -> 已生效更正号
        self.voided_by: dict[str, str] = {}
        self.seating: seat_mod.SeatingRegistry | None = None
        self.disputes: dict[str, Dispute] = {}
        self.gates: dict[str, GateDecision] = {}
        self.notices: list[StopNotice] = []
        self.audit: list[AuditEvent] = []
        self.ingest_log: list[IngestEvent] = []
        self._runtimes: dict[tuple[str, str], _CertRuntime] = {}

    # ------------------------------------------------------------------
    # 装载与事实索引
    # ------------------------------------------------------------------
    def load_packet(self, packet: dict[str, Any]) -> None:
        """按《交换约定》装载一整份快照并按时间顺序受理其中事件。"""
        for row in packet.get("workers", []):
            self.register_worker(row)
        for row in packet.get("policy", []):
            self.register_policy(row)
        for row in packet.get("mentors", []):
            self.register_mentor(row)
        for row in packet.get("seats", []):
            self.register_seat(row)
        self.seating = seat_mod.SeatingRegistry(self.seats)

        events: list[tuple[int, str, str, dict]] = []
        for row in packet.get("logs", []):
            events.append((parse_hm(row["signed_at"]), "log", row["factory"], row))
        for row in packet.get("corrections", []):
            events.append((parse_hm(row["arrived_at"]), "correction", row["factory"], row))
        for row in packet.get("registrations", []):
            events.append((parse_hm(row["occurred"]), "registration", "centre", row))
        for row in packet.get("results", []):
            events.append((parse_hm(row["received"]), "result", self.seats[row["seat"]].centre, row))
        for row in packet.get("orders", []):
            order = Order.from_row(row)
            self.orders[order.id] = order
            if order.actual_start is not None:
                events.append((order.actual_start, "gate", order.team, row))
        events.sort(key=lambda item: (item[0], item[1], item[2]))
        for at, kind, source, row in events:
            if kind == "log":
                self.submit_log(row)
            elif kind == "correction":
                self.submit_correction(row)
            elif kind == "registration":
                self.submit_registration(row)
            elif kind == "result":
                self.submit_result(row)
            elif kind == "gate":
                self.evaluate_order(row["id"], at)

    def register_worker(self, row: dict) -> Worker:
        worker = Worker(id=row["id"], factory=row["factory"], cohort=row["cohort"])
        self.workers[worker.id] = worker
        return worker

    def register_policy(self, row: dict) -> Policy:
        policy = Policy.from_row(row)
        self.policies[policy.id] = policy
        self.policy_by_trade[policy.trade] = policy
        return policy

    def register_mentor(self, row: dict) -> Mentor:
        mentor = Mentor.from_row(row)
        self.mentors[mentor.id] = mentor
        return mentor

    def register_seat(self, row: dict) -> Seat:
        seat = Seat.from_row(row)
        self.seats[seat.id] = seat
        return seat

    # ------------------------------------------------------------------
    # 分厂：签到签核与撤正
    # ------------------------------------------------------------------
    def submit_log(self, row: dict) -> Log:
        log = Log.from_row(row)
        if log.id in self._log_ids:
            raise ServiceError("duplicate_log", f"签核 {log.id} 已存在", 409)
        if log.factory not in {w.factory for w in self.workers.values()}:
            raise ServiceError("unknown_factory", f"未知分厂 {log.factory}", 422)
        if log.worker not in self.workers:
            raise ServiceError("unknown_worker", f"未知学员 {log.worker}", 422)
        if log.end <= log.start:
            raise ServiceError("bad_segment", "签核时段必须含起点不含终点", 422)
        self.logs.append(log)
        self._log_ids.add(log.id)
        self.ingest_log.append(
            IngestEvent(log.signed_at, "log", log.factory, log.id, True, None)
        )
        self._record(
            log.signed_at,
            "log_received",
            f"分厂 {log.factory} 提交签核 {log.id}",
            {"log_id": log.id, "worker": log.worker, "trade": log.trade},
            worker=log.worker,
            trade=log.trade,
        )
        self._refresh_worker(log.worker, log.trade, log.signed_at, cause=f"log:{log.id}")
        return log

    def submit_correction(self, row: dict) -> Correction:
        correction = Correction.from_row(row)
        target = next((log for log in self.logs if log.id == correction.target), None)
        if target is None:
            raise ServiceError("target_not_found", f"未找到源签核 {correction.target}", 404)
        # 各厂只能撤正本厂的签核
        if correction.factory != target.factory:
            self.ingest_log.append(
                IngestEvent(
                    correction.arrived_at,
                    "correction_rejected",
                    correction.factory,
                    correction.id,
                    False,
                    "factory_mismatch",
                )
            )
            raise ServiceError(
                "factory_mismatch",
                f"只有 {target.factory} 可以撤正签核 {target.id}",
                403,
            )
        if correction.action != "void":
            raise ServiceError("unsupported_action", f"不支持的更正动作 {correction.action}", 422)
        self.corrections.append(correction)
        self.voided_by[target.id] = correction.id
        self.ingest_log.append(
            IngestEvent(
                correction.arrived_at, "correction", correction.factory, correction.id, True, None
            )
        )
        self._record(
            correction.arrived_at,
            "correction_applied",
            f"分厂 {correction.factory} 撤正签核 {target.id}",
            {"correction_id": correction.id, "target_log": target.id, "worker": target.worker},
            worker=target.worker,
            trade=target.trade,
        )
        # 冲突可能因一方撤正而解除；先同步复核事项再重算资格
        self._sync_disputes(correction.arrived_at)
        self._refresh_worker(
            target.worker,
            target.trade,
            correction.arrived_at,
            cause=f"correction:{correction.id}",
        )
        self._handle_correction_impact(correction, target)
        return correction

    # ------------------------------------------------------------------
    # 考评点：报名、成绩
    # ------------------------------------------------------------------
    def submit_registration(self, row: dict) -> seat_mod.RegistrationOutcome:
        if self.seating is None:
            raise ServiceError("seats_not_configured", "尚未配置考评名额", 500)
        reg = Registration.from_row(row)
        if reg.worker not in self.workers:
            raise ServiceError("unknown_worker", f"未知学员 {reg.worker}", 422)
        seat = self.seats.get(reg.seat)
        if seat is None:
            raise ServiceError("unknown_seat", f"未知考评名额 {reg.seat}", 422)

        # 考评点只接受报名时满足工种要求的学员
        view = self.experience(reg.worker, seat.trade, reg.occurred)
        policy = self.policy_by_trade[seat.trade]
        eligible = view.counted_minutes >= policy.required_minutes
        reason = None if eligible else (
            f"practice_minutes_short:{policy.required_minutes - view.counted_minutes}"
        )
        outcome = self.seating.apply(reg, eligible, reason)
        self.registrations.append(reg)
        self.ingest_log.append(
            IngestEvent(
                reg.occurred,
                f"registration_{reg.action}",
                seat.centre,
                reg.id,
                outcome.accepted,
                outcome.reason,
            )
        )
        self._record(
            reg.occurred,
            "registration_decided",
            f"报名 {reg.id}（{reg.action}）{'受理' if outcome.accepted else '拒收'}",
            {
                "registration_id": reg.id,
                "worker": reg.worker,
                "seat": reg.seat,
                "action": reg.action,
                "accepted": outcome.accepted,
                "reason": outcome.reason,
                "counted_minutes_at_request": view.counted_minutes,
            },
            worker=reg.worker,
            trade=seat.trade,
        )
        self._refresh_worker(reg.worker, seat.trade, reg.occurred, cause=f"registration:{reg.id}")
        return outcome

    def submit_result(self, row: dict) -> Result:
        result = Result.from_row(row)
        seat = self.seats.get(result.seat)
        if seat is None:
            raise ServiceError("unknown_seat", f"未知考评名额 {result.seat}", 422)
        reg = next((r for r in self.registrations if r.id == result.registration), None)
        if reg is None or reg.seat != result.seat:
            # 传回的报名号必须能定位中心当初提交的申请
            raise ServiceError(
                "registration_not_found",
                f"成绩 {result.id} 的报名号 {result.registration} 无法定位原报名",
                404,
            )
        if any(r.id == result.id for r in self.results):
            raise ServiceError("duplicate_result", f"成绩 {result.id} 已存在", 409)
        self.results.append(result)
        self.ingest_log.append(
            IngestEvent(result.received, "result", seat.centre, result.id, True, None)
        )
        note = "实际签到" if result.checked_in else "无实际签到，转人工复核"
        self._record(
            result.received,
            "result_received",
            f"考评点 {seat.centre} 送达成绩 {result.id}（{note}）",
            {
                "result_id": result.id,
                "seat": result.seat,
                "registration": result.registration,
                "checked_in": result.checked_in,
                "score": result.score,
            },
            worker=reg.worker,
            trade=seat.trade,
        )
        self._refresh_worker(reg.worker, seat.trade, result.received, cause=f"result:{result.id}")
        return result

    # ------------------------------------------------------------------
    # 中心：复核事项处置
    # ------------------------------------------------------------------
    def resolve_dispute(self, dispute_id: str, at: str | int, resolution: str) -> Dispute:
        at_min = parse_hm(at)
        dispute = self.disputes.get(dispute_id)
        if dispute is None:
            raise ServiceError("dispute_not_found", f"未知复核事项 {dispute_id}", 404)
        dispute.status = "resolved"
        dispute.resolved_at = at_min
        dispute.resolution = resolution
        self._record(
            at_min,
            "dispute_resolved",
            f"中心复核 {dispute_id}：{resolution}",
            {"dispute_id": dispute_id, "resolution": resolution},
            worker=dispute.worker,
            trade=dispute.trade,
        )
        self._refresh_worker(dispute.worker, dispute.trade, at_min, cause=f"dispute:{dispute_id}")
        return dispute

    # ------------------------------------------------------------------
    # 工位：开工门禁
    # ------------------------------------------------------------------
    def register_order(self, row: dict) -> Order:
        order = Order.from_row(row)
        self.orders[order.id] = order
        return order

    def evaluate_order(self, order_id: str, at: str | int | None = None) -> GateDecision:
        """开工时检查资格和带教；不传时间则使用工单的实际开工时间。"""
        order = self.orders.get(order_id)
        if order is None:
            raise ServiceError("order_not_found", f"未知工单 {order_id}", 404)
        at_min = order.actual_start if at is None else parse_hm(at)
        if at_min is None:
            # 尚未开工：按排班时点做预览，但不产生正式门禁结论
            at_min = order.planned_start
        worker = self.workers[order.worker]
        policy = self.policy_by_trade.get(order.trade)
        cert = self.certification(order.worker, order.trade, at_min)
        decision = order_mod.evaluate_gate(order, at_min, cert, policy, worker, self.mentors)
        self.gates[order_id] = decision
        self._record(
            at_min,
            "gate_evaluated",
            f"工单 {order_id} 门禁{'放行' if decision.allowed else '拦截'}",
            {
                "order_id": order_id,
                "allowed": decision.allowed,
                "reasons": decision.reasons,
                "qualification_id": decision.qualification_id,
            },
            worker=order.worker,
            trade=order.trade,
        )
        return decision

    # ------------------------------------------------------------------
    # 认定视图
    # ------------------------------------------------------------------
    def _void_map_at(self, as_of: int) -> dict[str, str]:
        """时点口径下已生效的撤正映射：签核号 -> 更正号。"""
        return {
            log_id: cid
            for log_id, cid in self.voided_by.items()
            if next(c.arrived_at for c in self.corrections if c.id == cid) <= as_of
        }

    def _seat_snapshot(self, as_of: int) -> tuple[dict[str, str], dict[str, Seat], list[Registration], list[Result]]:
        """还原某时点的名额状态：承诺映射、名额、当时已存在的报名与成绩。"""
        visible_regs = [r for r in self.registrations if r.occurred <= as_of]
        promised: dict[str, str] = {}
        if self.seating is not None:
            for seat_id, book in self.seating.books.items():
                for worker, reg in book.confirms.items():
                    if reg.occurred <= as_of:
                        still_held = True
                        for rel in visible_regs:
                            if (
                                rel.action == "move"
                                and rel.worker == worker
                                and rel.seat == seat_id
                                and rel.occurred > reg.occurred
                                and rel.occurred <= as_of
                            ):
                                still_held = False
                        if still_held:
                            promised[seat_id] = worker
        visible_results = [r for r in self.results if r.received <= as_of]
        return promised, self.seats, visible_regs, visible_results

    def experience(self, worker_id: str, trade: str, as_of: str | int) -> exp_mod.ExperienceView:
        if worker_id not in self.workers:
            raise ServiceError("unknown_worker", f"未知学员 {worker_id}", 404)
        as_of_min = parse_hm(as_of)
        view = exp_mod.recognize(
            worker_id,
            trade,
            as_of_min,
            self.workers,
            self.mentors,
            self.logs,
            self._void_map_at(as_of_min),
        )
        self._sync_disputes(as_of_min)
        return view

    def certification(self, worker_id: str, trade: str, as_of: str | int) -> cert_mod.CertificationView:
        as_of_min = parse_hm(as_of)
        self._sync_disputes(as_of_min)
        void_map = self._void_map_at(as_of_min)
        exp_view = exp_mod.recognize(
            worker_id,
            trade,
            as_of_min,
            self.workers,
            self.mentors,
            self.logs,
            void_map,
        )
        policy = self.policy_by_trade[trade]
        promised, seats, visible_regs, visible_results = self._seat_snapshot(as_of_min)
        exam = cert_mod.assess_exam(
            worker_id,
            policy,
            as_of_min,
            seats,
            visible_regs,
            visible_results,
            promised,
        )
        corrections = tuple(
            cid
            for log_id, cid in void_map.items()
            if any(log.id == log_id and log.worker == worker_id and log.trade == trade for log in self.logs)
        )
        return cert_mod.build_view(worker_id, policy, as_of_min, exp_view, exam, corrections)

    # ------------------------------------------------------------------
    # 查询：学员 / 工位 / 中心
    # ------------------------------------------------------------------
    def worker_overview(self, worker_id: str, as_of: str | int) -> dict:
        """学员查询：已计入的经历、报名和证书状态（只看本人）。"""
        if worker_id not in self.workers:
            raise ServiceError("unknown_worker", f"未知学员 {worker_id}", 404)
        as_of_min = parse_hm(as_of)
        trades = sorted(self.policy_by_trade)
        experiences = []
        qualifications = []
        for trade in trades:
            view = self.experience(worker_id, trade, as_of_min)
            cert = self.certification(worker_id, trade, as_of_min)
            experiences.append(self._experience_payload(view))
            qualifications.append(self._cert_payload(cert))
        return {
            "worker": worker_id,
            "factory": self.workers[worker_id].factory,
            "cohort": self.workers[worker_id].cohort,
            "as_of": iso_text(as_of_min),
            "experiences": experiences,
            "registrations": [
                {
                    "registration_id": reg.id,
                    "seat": reg.seat,
                    "trade": self.seats[reg.seat].trade,
                    "action": reg.action,
                    "occurred": iso_text(reg.occurred),
                    "accepted": self.seating.books[reg.seat].outcomes[reg.id].accepted,
                    "reason": self.seating.books[reg.seat].outcomes[reg.id].reason,
                }
                for reg in self.registrations
                if reg.worker == worker_id
            ],
            "qualifications": qualifications,
        }

    def station_order_view(self, order_id: str, as_of: str | int) -> dict:
        """工位查询：当前工单为何可开、需谁带教或为何被拦下。

        工位只取得本工单相关的授权依据索引，看不到别家完整实训流水。
        """
        order = self.orders.get(order_id)
        if order is None:
            raise ServiceError("order_not_found", f"未知工单 {order_id}", 404)
        as_of_min = parse_hm(as_of)
        cert = self.certification(order.worker, order.trade, as_of_min)
        worker = self.workers[order.worker]
        policy = self.policy_by_trade.get(order.trade)
        decision = order_mod.evaluate_gate(order, as_of_min, cert, policy, worker, self.mentors)
        gate_record = self.gates.get(order_id)
        return {
            "order": {
                "order_id": order.id,
                "team": order.team,
                "trade": order.trade,
                "kind": order.kind,
                "planned_start": iso_text(order.planned_start),
                "actual_start": None if order.actual_start is None else iso_text(order.actual_start),
            },
            "as_of": iso_text(as_of_min),
            "decision": {
                "allowed": decision.allowed,
                "reasons": decision.reasons,
                "mentor_required": decision.mentor_required,
                "mentor": decision.mentor,
            },
            # 授权依据只给索引与汇总，不给别家流水明细
            "authorization": {
                "worker": order.worker,
                "qualification_id": cert.qualification_id,
                "state": cert.state,
                "missing": cert.missing,
                "evidence_index": {
                    "log_ids": list(cert.evidence["log_ids"]),
                    "registration_id": cert.evidence["registration_id"],
                    "result_id": cert.evidence["result_id"],
                },
                "counted_minutes": cert.experience.counted_minutes,
                "required_minutes": self.policy_by_trade[order.trade].required_minutes,
            },
            "gate_record_at_actual_start": None
            if gate_record is None
            else {
                "evaluated_at": iso_text(gate_record.evaluated_at),
                "allowed": gate_record.allowed,
                "reasons": gate_record.reasons,
            },
            "stop_notices": [
                self._notice_payload(notice)
                for notice in self.notices
                if notice.order_id == order_id
            ],
        }

    def qualification_trace(self, worker_id: str, trade: str) -> dict:
        """中心回看：某份资格在开工时用了哪些事实、后来哪项更正改变了决定。"""
        current = self.certification(worker_id, trade, self._latest_minute())
        related_orders = [
            order
            for order in self.orders.values()
            if order.worker == worker_id and order.trade == trade
        ]
        return {
            "qualification_id": current.qualification_id,
            "worker": worker_id,
            "trade": trade,
            "current_state": current.state,
            "current": self._cert_payload(current),
            "timeline": [
                {
                    "at": iso_text(event.at),
                    "category": event.category,
                    "summary": event.summary,
                    "facts": event.facts,
                }
                for event in self.audit
                if event.worker == worker_id and event.trade == trade
            ],
            "gate_decisions": [
                {
                    "order_id": order.id,
                    "evaluated_at": iso_text(self.gates[order.id].evaluated_at),
                    "allowed": self.gates[order.id].allowed,
                    "reasons": self.gates[order.id].reasons,
                    "facts": self.gates[order.id].facts,
                }
                for order in related_orders
                if order.id in self.gates
            ],
            "stop_notices": [
                self._notice_payload(notice)
                for notice in self.notices
                if notice.worker == worker_id and notice.trade == trade
            ],
        }

    def disputes_view(self) -> dict:
        return {
            "disputes": [
                {
                    "id": d.id,
                    "worker": d.worker,
                    "trade": d.trade,
                    "segment": {"start": iso_text(d.segment[0]), "end": iso_text(d.segment[1])},
                    "factories": list(d.factories),
                    "log_ids": list(d.log_ids),
                    "raised_at": iso_text(d.raised_at),
                    "status": d.status,
                    "resolved_at": None if d.resolved_at is None else iso_text(d.resolved_at),
                    "resolution": d.resolution,
                }
                for d in self.disputes.values()
            ]
        }

    # ------------------------------------------------------------------
    # 内部机制
    # ------------------------------------------------------------------
    def _refresh_worker(self, worker_id: str, trade: str, at: int, cause: str) -> None:
        view = self.certification(worker_id, trade, at)
        runtime = self._runtimes.setdefault((worker_id, trade), _CertRuntime(view=view))
        runtime.view = view
        if runtime.last_state is not None and runtime.last_state != view.state:
            self._record(
                at,
                "qualification_state_changed",
                f"资格 {view.qualification_id} 状态 {runtime.last_state} -> {view.state}",
                {
                    "qualification_id": view.qualification_id,
                    "from_state": runtime.last_state,
                    "to_state": view.state,
                    "cause": cause,
                    "missing": view.missing,
                },
                runtime,
                worker=worker_id,
                trade=trade,
            )
        else:
            self._record(
                at,
                "qualification_reassessed",
                f"资格 {view.qualification_id} 重新认定为 {view.state}",
                {
                    "qualification_id": view.qualification_id,
                    "state": view.state,
                    "cause": cause,
                    "counted_minutes": view.experience.counted_minutes,
                    "exam_status": view.exam.status,
                    "missing": view.missing,
                },
                runtime,
                worker=worker_id,
                trade=trade,
            )
        runtime.last_state = view.state

    def _sync_disputes(self, as_of: int) -> None:
        """按当前有效事实同步跨厂复核事项；撤正使冲突解除的自动关闭。"""
        void_map = self._void_map_at(as_of)
        active: dict[str, Dispute] = {}
        for worker in self.workers:
            trades = {
                log.trade
                for log in self.logs
                if log.worker == worker and log.signed_at <= as_of
            }
            for trade in trades:
                view = exp_mod.recognize(
                    worker, trade, as_of, self.workers, self.mentors, self.logs, void_map
                )
                for dispute in view.open_disputes:
                    active[dispute.id] = dispute
        for dispute_id, dispute in list(self.disputes.items()):
            if dispute_id not in active and dispute.status == "open":
                dispute.status = "resolved"
                dispute.resolved_at = as_of
                dispute.resolution = "conflict_cleared_by_void"
        for dispute_id, dispute in active.items():
            existing = self.disputes.get(dispute_id)
            if existing is None:
                self.disputes[dispute_id] = dispute
                self._record(
                    dispute.raised_at,
                    "dispute_raised",
                    f"跨厂工时冲突 {dispute_id} 留待中心复核",
                    {
                        "dispute_id": dispute_id,
                        "worker": dispute.worker,
                        "trade": dispute.trade,
                        "segment": [format_hm(dispute.segment[0]), format_hm(dispute.segment[1])],
                        "factories": list(dispute.factories),
                        "log_ids": list(dispute.log_ids),
                    },
                    worker=dispute.worker,
                    trade=dispute.trade,
                )

    def _handle_correction_impact(self, correction: Correction, target: Log) -> None:
        """更正导致经历不再够用时：判断其他依据、暂停在制工单并通知工位。"""
        at = correction.arrived_at
        cert = self.certification(target.worker, target.trade, at)
        # 是否还有其他有效依据支撑：以更正受理后重新认定的当前状态为准，
        # 不依赖可能因“状态未变化而未更新”的运行时缓存
        alternative = cert.state == "active"
        for order in self.orders.values():
            if order.worker != target.worker or order.trade != target.trade:
                continue
            if order.actual_start is None:
                # 未开工的工单由门禁拦截，无需停工通知
                continue
            if alternative:
                continue
            notice = StopNotice(
                id=f"N-{order.id}-{correction.id}",
                order_id=order.id,
                team=order.team,
                worker=order.worker,
                trade=order.trade,
                issued_at=at,
                correction_id=correction.id,
                qualification_id=cert.qualification_id,
                message=(
                    f"资格 {cert.qualification_id} 依据变更后缺少："
                    + "；".join(self._missing_text(cert))
                    + "，停止继续安排受影响工作，已开工记录保留"
                ),
                preserved_actual_start=order.actual_start,
            )
            if not any(n.id == notice.id for n in self.notices):
                self.notices.append(notice)
                self._record(
                    at,
                    "stop_notice_issued",
                    f"通知工位 {order.team} 停止工单 {order.id} 的受影响工作",
                    {
                        "notice_id": notice.id,
                        "order_id": order.id,
                        "correction_id": correction.id,
                        "preserved_actual_start": format_hm(order.actual_start),
                        "missing": cert.missing,
                    },
                    worker=order.worker,
                    trade=order.trade,
                )

    @staticmethod
    def _missing_text(cert: cert_mod.CertificationView) -> list[str]:
        texts = []
        for item in cert.missing:
            if item["condition"] == "practice_minutes":
                texts.append(
                    f"实操分钟缺 {item['shortfall_minutes']} 分钟"
                    f"（已有 {item['counted_minutes']}/{item['required_minutes']}）"
                )
            else:
                texts.append(f"考评条件未满足：{item['status']}（{item['detail']}）")
        return texts

    def _latest_minute(self) -> int:
        candidates = [1440 - 1]
        candidates += [log.signed_at for log in self.logs]
        candidates += [c.arrived_at for c in self.corrections]
        candidates += [r.received for r in self.results]
        candidates += [o.actual_start for o in self.orders.values() if o.actual_start is not None]
        return max(candidates)

    def _record(
        self,
        at: int,
        category: str,
        summary: str,
        facts: dict,
        runtime: _CertRuntime | None = None,
        worker: str | None = None,
        trade: str | None = None,
    ) -> None:
        event = AuditEvent(
            at=at,
            category=category,
            summary=summary,
            facts=facts,
            worker=worker,
            trade=trade,
        )
        self.audit.append(event)
        if runtime is not None:
            runtime.history.append(event)

    def _experience_payload(self, view: exp_mod.ExperienceView) -> dict:
        return {
            "trade": view.trade,
            "counted_minutes": view.counted_minutes,
            "counted_segments": [
                {"start": iso_text(s[0]), "end": iso_text(s[1])} for s in view.counted_segments
            ],
            "factory_minutes": dict(view.factory_minutes),
            "logs": [
                {
                    "log_id": a.log_id,
                    "factory": a.factory,
                    "team": a.team,
                    "mentor": a.mentor_id,
                    "segment": {
                        "start": iso_text(a.segment[0]),
                        "end": iso_text(a.segment[1]),
                    },
                    "valid": a.valid,
                    "excluded_reason": a.excluded_reason,
                    "voided_by": a.voided_by,
                    "counted_segments": [
                        {"start": iso_text(s[0]), "end": iso_text(s[1])}
                        for s in a.counted_segments
                    ],
                }
                for a in view.assessments
            ],
            "open_disputes": [
                {
                    "id": d.id,
                    "segment": {
                        "start": iso_text(d.segment[0]),
                        "end": iso_text(d.segment[1]),
                    },
                    "factories": list(d.factories),
                }
                for d in view.open_disputes
            ],
        }

    def _cert_payload(self, cert: cert_mod.CertificationView) -> dict:
        return {
            "qualification_id": cert.qualification_id,
            "trade": cert.trade,
            "state": cert.state,
            "as_of": iso_text(cert.as_of),
            "missing": cert.missing,
            "exam": {
                "status": cert.exam.status,
                "detail": cert.exam.detail,
                "result_id": cert.exam.result_id,
                "registration_id": cert.exam.registration_id,
                "seat_id": cert.exam.seat_id,
                "score": cert.exam.score,
                "checked_in": cert.exam.checked_in,
                "late": cert.exam.late,
            },
            "evidence": cert.evidence,
            "paused_by_corrections": list(cert.paused_by_corrections),
        }

    def _notice_payload(self, notice: StopNotice) -> dict:
        return {
            "notice_id": notice.id,
            "order_id": notice.order_id,
            "team": notice.team,
            "issued_at": iso_text(notice.issued_at),
            "correction_id": notice.correction_id,
            "qualification_id": notice.qualification_id,
            "message": notice.message,
            "preserved_actual_start": None
            if notice.preserved_actual_start is None
            else iso_text(notice.preserved_actual_start),
        }
