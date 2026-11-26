"""端到端业务规则：考评后发现依据有误、冲突成绩不覆盖新考评等。"""

from __future__ import annotations

from _fixture import ServiceTestCase, build_service


class EndToEndRuleTest(ServiceTestCase):
    def test_basis_error_after_exam_keeps_attendance_and_pauses(self) -> None:
        # E92 12:30 开考、X2 13:10 到达；C4/C5 在开考之后才撤正。
        # 已参加的考评不能被悄悄抹掉：X2 仍可查，发证被暂停且点明缺哪个条件
        cert = self.service.certification("W17", "ship_plate", "16:30")
        self.assertEqual(cert.state, "paused")
        self.assertEqual(cert.exam.status, "passed")  # 考评结论保留
        self.assertEqual(cert.exam.result_id, "X2")
        self.assertTrue(cert.exam.checked_in)
        missing = next(m for m in cert.missing if m["condition"] == "practice_minutes")
        self.assertEqual(missing["shortfall_minutes"], 60)
        self.assertEqual(cert.paused_by_corrections, ("C4", "C5"))

    def test_conflicting_late_result_does_not_overwrite_new_assessment(self) -> None:
        # X1（无签到、迟到）与 X2（实际签到通过）并存时，以 X2 为准；
        # X1 仍能通过原报名 R1 查到，但不参与发证
        cert = self.service.certification("W17", "ship_plate", "16:30")
        self.assertEqual(cert.exam.result_id, "X2")
        x1 = next(r for r in self.service.results if r.id == "X1")
        self.assertEqual(
            next(r.id for r in self.service.registrations if r.id == x1.registration),
            "R1",
        )
        # 名额没有因补证再次许给别人：E92 承诺始终是 W17 的 R4
        r4 = next(r for r in self.service.registrations if r.id == "R4")
        self.assertEqual(self.service.seating.books["E92"].confirms, {"W17": r4})

    def test_seat_never_reassigned_while_awaiting_result(self) -> None:
        # 答复未到期间，已满员名额上的新 confirm 请求一律被拒（名额不二次分配）
        service = build_service()
        # W18 凭 L7（120 分钟）在 10:50 实承诺 E91（容量 1，期限 11:00，开考 11:20）
        first = service.submit_registration(
            {
                "id": "RXP",
                "worker": "W18",
                "seat": "E91",
                "occurred": "10:50",
                "action": "confirm",
            }
        )
        self.assertTrue(first.accepted)
        # 10:55 时考评尚未开始、答复未到：属于 awaiting_result
        cert = service.certification("W18", "ship_plate", "10:55")
        self.assertEqual(cert.exam.status, "awaiting_result")
        self.assertEqual(cert.exam.seat_id, "E91")
        # W17 当时也满足工种门槛，但名额已许出，不能再次分配
        self.assertGreaterEqual(
            service.experience("W17", "ship_plate", "10:58").counted_minutes, 90
        )
        outcome = service.submit_registration(
            {
                "id": "RXW",
                "worker": "W17",
                "seat": "E91",
                "occurred": "10:58",
                "action": "confirm",
            }
        )
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "seat_capacity_exhausted")
        rxp = next(r for r in service.registrations if r.id == "RXP")
        self.assertEqual(service.seating.books["E91"].confirms, {"W18": rxp})

    def test_known_ineligible_vs_awaiting_are_distinguishable(self) -> None:
        # 已知不合格（failed）与暂时不能确认（awaiting_result）必须可区分
        service = build_service()
        awaiting = service.certification("W17", "ship_plate", "13:05")
        self.assertEqual(awaiting.exam.status, "awaiting_result")

        service2 = build_service()
        service2.submit_result(
            {
                "id": "XF",
                "seat": "E92",
                "registration": "R4",
                "checked_in": True,
                "score": 50,
                "received": "13:05",
            }
        )
        failed = service2.certification("W17", "ship_plate", "13:06")
        self.assertEqual(failed.exam.status, "failed")
        self.assertNotEqual(awaiting.exam.status, failed.exam.status)

    def test_worker_query_is_scoped_to_self(self) -> None:
        overview_w17 = self.service.worker_overview("W17", "16:30")
        overview_w18 = self.service.worker_overview("W18", "16:30")
        # 两人的报名集合互不可见对方记录
        w17_regs = {r["registration_id"] for r in overview_w17["registrations"]}
        w18_regs = {r["registration_id"] for r in overview_w18["registrations"]}
        self.assertIn("R1", w17_regs)
        self.assertIn("R2", w18_regs)
        self.assertTrue(w17_regs.isdisjoint(w18_regs))

    def test_full_timeline_states_for_worker17(self) -> None:
        expected = [
            ("10:41", "pending"),   # 只有 L4，分钟未达
            ("11:03", "pending"),   # 经历够了，考评还没结论
            ("14:35", "active"),    # X2 通过且分钟达标，O11 放行
            ("15:21", "active"),    # C4 撤 L4，剩余仍达标
            ("16:01", "paused"),    # C5 撤 L5，只剩 L8 30 分钟
        ]
        for at, state in expected:
            with self.subTest(at=at):
                self.assertEqual(
                    self.service.certification("W17", "ship_plate", at).state, state
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
