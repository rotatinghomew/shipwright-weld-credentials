"""考评名额状态机。

报文动作语义：

- ``hold``：申请保留座位（报名意向，尚不是承诺）；
- ``confirm``：实承诺请求，中心在容量内、确认期限内才把名额许出；
- ``move``：学员放掉尚未开始的原考，释放其占用。

容量约束只在 ``confirm`` 时兑现：同一个名额不能承诺给两个人。
未确认的保留在 ``confirm_by`` 之后失效；考评开始（``begins``）后不允许再撤考。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Registration, Seat


@dataclass
class RegistrationOutcome:
    registration_id: str
    worker: str
    seat: str
    action: str
    occurred: int
    accepted: bool
    reason: str | None = None


@dataclass
class SeatBook:
    seat: Seat
    # 仍有效的保留与已承诺名额：学员号 -> 报名流水
    holds: dict[str, Registration] = field(default_factory=dict)
    confirms: dict[str, Registration] = field(default_factory=dict)
    released: dict[str, Registration] = field(default_factory=dict)
    expired_holds: dict[str, Registration] = field(default_factory=dict)
    outcomes: dict[str, RegistrationOutcome] = field(default_factory=dict)

    def active_holds(self, as_of: int) -> dict[str, Registration]:
        """到 ``as_of`` 仍未过期的保留。"""
        if as_of >= self.seat.confirm_by:
            return {}
        return dict(self.holds)

    def free_capacity(self, as_of: int) -> int:
        return self.seat.capacity - len(self.confirms)

    def apply(self, reg: Registration, eligible: bool, ineligible_reason: str | None) -> RegistrationOutcome:
        if reg.action == "hold":
            outcome = self._apply_hold(reg, eligible, ineligible_reason)
        elif reg.action == "confirm":
            outcome = self._apply_confirm(reg, eligible, ineligible_reason)
        elif reg.action == "move":
            outcome = self._apply_move(reg)
        else:
            outcome = RegistrationOutcome(
                reg.id, reg.worker, reg.seat, reg.action, reg.occurred, False, "unknown_action"
            )
        self.outcomes[reg.id] = outcome
        return outcome

    def _apply_hold(
        self, reg: Registration, eligible: bool, ineligible_reason: str | None
    ) -> RegistrationOutcome:
        if not eligible:
            return self._reject(reg, ineligible_reason or "trade_requirement_not_met")
        if reg.worker in self.confirms:
            return self._reject(reg, "seat_already_promised_to_worker")
        # 同一学员重复保留按幂等处理，以最早一条为准
        self.holds.setdefault(reg.worker, reg)
        return RegistrationOutcome(reg.id, reg.worker, reg.seat, reg.action, reg.occurred, True)

    def _apply_confirm(
        self, reg: Registration, eligible: bool, ineligible_reason: str | None
    ) -> RegistrationOutcome:
        if not eligible:
            return self._reject(reg, ineligible_reason or "trade_requirement_not_met")
        if reg.occurred > self.seat.confirm_by:
            return self._reject(reg, "confirm_deadline_passed")
        if reg.occurred >= self.seat.begins:
            return self._reject(reg, "exam_already_started")
        if reg.worker in self.confirms:
            return self._reject(reg, "duplicate_confirmation")
        if len(self.confirms) >= self.seat.capacity:
            # 名额已经许给别人，不能再次分出
            return self._reject(reg, "seat_capacity_exhausted")
        self.confirms[reg.worker] = reg
        self.holds.pop(reg.worker, None)
        return RegistrationOutcome(reg.id, reg.worker, reg.seat, reg.action, reg.occurred, True)

    def _apply_move(self, reg: Registration) -> RegistrationOutcome:
        if reg.occurred >= self.seat.begins:
            return self._reject(reg, "exam_already_started")
        original = self.holds.pop(reg.worker, None) or self.confirms.pop(reg.worker, None)
        if original is None:
            return self._reject(reg, "no_active_registration_to_release")
        self.released[reg.worker] = original
        return RegistrationOutcome(reg.id, reg.worker, reg.seat, reg.action, reg.occurred, True)

    @staticmethod
    def _reject(reg: Registration, reason: str) -> RegistrationOutcome:
        return RegistrationOutcome(
            reg.id, reg.worker, reg.seat, reg.action, reg.occurred, False, reason
        )

    def sweep_expired(self, as_of: int) -> list[str]:
        """确认期限到达后，未确认的保留失效，返回本次失效的报名号。"""
        if as_of < self.seat.confirm_by:
            return []
        lapsed: list[str] = []
        for worker, reg in list(self.holds.items()):
            self.expired_holds[worker] = reg
            del self.holds[worker]
            lapsed.append(reg.id)
        return lapsed


class SeatingRegistry:
    def __init__(self, seats: dict[str, Seat]):
        self.books: dict[str, SeatBook] = {sid: SeatBook(seat) for sid, seat in seats.items()}

    def apply(self, reg: Registration, eligible: bool, ineligible_reason: str | None) -> RegistrationOutcome:
        book = self.books.get(reg.seat)
        if book is None:
            return RegistrationOutcome(
                reg.id, reg.worker, reg.seat, reg.action, reg.occurred, False, "unknown_seat"
            )
        return book.apply(reg, eligible, ineligible_reason)

    def sweep(self, as_of: int) -> dict[str, list[str]]:
        return {sid: book.sweep_expired(as_of) for sid, book in self.books.items()}

    def is_promised(self, seat_id: str, worker: str, as_of: int) -> bool:
        book = self.books[seat_id]
        return worker in book.confirms
