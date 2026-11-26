"""考评名额：报名门槛、确认期限、容量承诺与撤考。"""

from __future__ import annotations

from _fixture import ServiceTestCase, build_service


class SeatingTest(ServiceTestCase):
    def test_registration_requires_trade_minutes_at_request_time(self) -> None:
        # W30 没有任何 ship_pipe 经历，报名 E93 必须被拒
        outcome = self.service.submit_registration(
            {
                "id": "RX1",
                "worker": "W30",
                "seat": "E93",
                "occurred": "11:30",
                "action": "confirm",
            }
        )
        self.assertFalse(outcome.accepted)
        self.assertTrue(outcome.reason.startswith("practice_minutes_short"))

    def test_hold_does_not_promise_capacity(self) -> None:
        book = self.service.seating.books["E91"]
        # R3 已放掉 W17 的保留；容量 1 仍允许 W18 的保留意向存在
        self.assertNotIn("W17", book.holds)
        self.assertIn("W18", book.holds)
        self.assertEqual(book.confirms, {})

    def test_move_releases_only_unstarted_exam(self) -> None:
        # R3 在 E91 开考前放掉 R1 的保留
        book = self.service.seating.books["E91"]
        self.assertNotIn("W17", book.holds)
        self.assertEqual(book.released["W17"].id, "R1")
        # 迟到成绩 X1 仍可回溯原报名 R1
        result = next(r for r in self.service.results if r.id == "X1")
        original = next(r for r in self.service.registrations if r.id == result.registration)
        self.assertEqual(original.id, "R1")

    def test_capacity_never_promised_twice(self) -> None:
        # E92 容量 1，W17 已确认；第二个确认请求必须被拒，名额不再分出
        outcome = self.service.submit_registration(
            {
                "id": "RX2",
                "worker": "W18",
                "seat": "E92",
                "occurred": "11:30",
                "action": "confirm",
            }
        )
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "seat_capacity_exhausted")
        self.assertEqual(list(self.service.seating.books["E92"].confirms), ["W17"])

    def test_confirm_after_deadline_rejected(self) -> None:
        # E91 确认期限 11:00，11:05 才确认 -> 拒收
        outcome = self.service.submit_registration(
            {
                "id": "RX3",
                "worker": "W18",
                "seat": "E91",
                "occurred": "11:05",
                "action": "confirm",
            }
        )
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "confirm_deadline_passed")

    def test_move_after_exam_begins_rejected(self) -> None:
        service = build_service()
        outcome = service.submit_registration(
            {
                "id": "RX4",
                "worker": "W17",
                "seat": "E92",  # begins 12:30
                "occurred": "12:45",
                "action": "move",
            }
        )
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "exam_already_started")

    def test_holds_expire_at_confirm_deadline(self) -> None:
        book = self.service.seating.books["E91"]
        lapsed = book.sweep_expired(11 * 60)
        self.assertIn("R2", lapsed)
        self.assertIn("W18", book.expired_holds)

    def test_eligibility_evaluated_with_time_aware_experience(self) -> None:
        # 10:42 时 W17 已有 L4（90m，含 L5 冲突前），满足 90 分钟门槛
        r1 = self.service.seating.books["E91"].outcomes["R1"]
        self.assertTrue(r1.accepted)


if __name__ == "__main__":
    import unittest

    unittest.main()
