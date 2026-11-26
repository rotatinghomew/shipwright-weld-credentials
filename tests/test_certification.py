"""资格认定：成绩状态、暂停发证、更正后的其他有效依据。"""

from __future__ import annotations

from _fixture import ServiceTestCase, build_service


class CertificationTest(ServiceTestCase):
    def test_active_when_minutes_and_exam_pass(self) -> None:
        cert = self.service.certification("W17", "ship_plate", "14:35")
        self.assertEqual(cert.state, "active")
        self.assertEqual(cert.exam.status, "passed")
        self.assertEqual(cert.exam.result_id, "X2")
        self.assertGreaterEqual(cert.experience.counted_minutes, 90)

    def test_no_checkin_result_cannot_issue(self) -> None:
        # X1：checked_in=false，即使分数 83 也只是待核记录
        from app import certification as cm

        policy = self.service.policy_by_trade["ship_plate"]
        promised, seats, regs, results = self.service._seat_snapshot(13 * 60 + 20)
        only_x1 = [r for r in results if r.id == "X1"]
        exam = cm.assess_exam("W17", policy, 800, seats, regs, only_x1, {})
        self.assertEqual(exam.status, "pending_review")
        self.assertFalse(exam.checked_in)
        self.assertEqual(exam.registration_id, "R1")

    def test_late_result_traces_original_registration_but_not_used(self) -> None:
        # R1 已 move，X1 迟到；最终资格依据的是 E92/R4/X2，而不是 X1
        cert = self.service.certification("W17", "ship_plate", "16:30")
        self.assertEqual(cert.exam.result_id, "X2")
        self.assertEqual(cert.evidence["registration_id"], "R4")
        x1 = next(r for r in self.service.results if r.id == "X1")
        self.assertEqual(
            next(r for r in self.service.registrations if r.id == x1.registration).id,
            "R1",
        )

    def test_known_failure_distinct_from_awaiting(self) -> None:
        # 构造已知不合格：实际签到但分数低于通过线
        service = build_service()
        service.submit_result(
            {
                "id": "XFAIL",
                "seat": "E92",
                "registration": "R4",
                "checked_in": True,
                "score": 60,
                "received": "13:05",
            }
        )
        cert = service.certification("W17", "ship_plate", "13:06")
        self.assertEqual(cert.state, "pending")
        self.assertEqual(cert.exam.status, "failed")
        self.assertTrue(any(m["condition"] == "trade_exam" for m in cert.missing))

    def test_awaiting_result_keeps_certificate_pending(self) -> None:
        # 13:05 成绩未到，E92 已确认：属于“暂时不能确认”而非不合格
        cert = self.service.certification("W17", "ship_plate", "13:05")
        self.assertEqual(cert.state, "pending")
        self.assertEqual(cert.exam.status, "awaiting_result")

    def test_correction_pauses_and_names_missing_condition(self) -> None:
        cert = self.service.certification("W17", "ship_plate", "16:01")
        self.assertEqual(cert.state, "paused")
        self.assertEqual(cert.paused_by_corrections, ("C4", "C5"))
        missing = next(m for m in cert.missing if m["condition"] == "practice_minutes")
        self.assertEqual(missing["shortfall_minutes"], 60)
        self.assertEqual(missing["counted_minutes"], 30)

    def test_other_valid_basis_keeps_certificate_active(self) -> None:
        # pipe 资格只依赖 L6 与 X3；plate 的撤正不影响它
        cert = self.service.certification("W17", "ship_pipe", "16:30")
        self.assertEqual(cert.state, "active")
        self.assertEqual(cert.evidence["log_ids"], ["L6"])

    def test_no_basis_at_all_is_pending_not_paused(self) -> None:
        cert = self.service.certification("W30", "ship_pipe", "16:30")
        self.assertEqual(cert.state, "pending")
        self.assertEqual(cert.exam.status, "unregistered")


if __name__ == "__main__":
    import unittest

    unittest.main()
