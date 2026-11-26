"""开工门禁：资格状态、工单类别与现场带教。"""

from __future__ import annotations

from _fixture import ServiceTestCase, build_service


class GateTest(ServiceTestCase):
    def test_independent_seam_order_opens_with_active_certificate(self) -> None:
        # O11：seam 可独立开工，14:35 时 plate 资格 active
        gate = self.service.gates["O11"]
        self.assertTrue(gate.allowed)
        self.assertFalse(gate.mentor_required)
        self.assertEqual(gate.qualification_state, "active")

    def test_pipe_order_requires_on_site_mentor_in_scope(self) -> None:
        # O12：pipe 须现场带教，M3 同班组且时间窗覆盖 14:45
        gate = self.service.gates["O12"]
        self.assertTrue(gate.allowed)
        self.assertTrue(gate.mentor_required)
        self.assertTrue(gate.mentor["valid"])
        self.assertTrue(gate.mentor["window"]["covers_start"])

    def test_pipe_order_blocked_without_mentor(self) -> None:
        import dataclasses

        service = build_service()
        service.orders["O12"] = dataclasses.replace(service.orders["O12"], mentor=None)
        gate = service.evaluate_order("O12", "14:45")
        self.assertFalse(gate.allowed)
        self.assertIn("on_site_mentor_required_but_none_assigned", gate.reasons)

    def test_mentor_outside_window_blocks_order(self) -> None:
        # M3 窗 08:00-17:30；构造 18:00 开工的 pipe 工单
        service = build_service()
        service.register_order(
            {
                "id": "O99",
                "team": "A7",
                "trade": "ship_pipe",
                "kind": "pipe",
                "planned_start": "18:00",
                "actual_start": "18:00",
                "mentor": "M3",
                "worker": "W17",
            }
        )
        gate = service.evaluate_order("O99", "18:00")
        self.assertFalse(gate.allowed)
        self.assertIn("mentor_invalid:window_covers_start", gate.reasons)

    def test_gate_uses_actual_start_facts_not_planned_schedule(self) -> None:
        # O11 排班 14:20 显示可上岗；撤正 15:20/16:00 后再查应被拦
        view = self.service.station_order_view("O11", "16:30")
        self.assertFalse(view["decision"]["allowed"])
        self.assertIn("qualification_paused", view["decision"]["reasons"])
        # 但正式门禁结论是开工当时（14:35）做出的，留档为放行
        record = view["gate_record_at_actual_start"]
        self.assertTrue(record["allowed"])
        self.assertEqual(record["evaluated_at"], "2026-12-01T14:35:00+08:00")

    def test_unstarted_order_blocked_when_certificate_paused(self) -> None:
        # O13 无 actual_start，16:10 预览即拦截
        gate = self.service.evaluate_order("O13", "16:10")
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.reasons, ["qualification_paused"])

    def test_unrelated_trade_orders_remain_valid(self) -> None:
        # plate 撤正不影响 pipe 的 O12
        gate = self.service.evaluate_order("O12", "16:30")
        self.assertTrue(gate.allowed)
        self.assertEqual(gate.qualification_state, "active")

    def test_station_cannot_see_other_factory_log_details(self) -> None:
        view = self.service.station_order_view("O11", "14:35")
        # 只给授权依据索引，没有任何完整流水明细
        self.assertNotIn("logs", view["authorization"])
        self.assertEqual(
            set(view["authorization"]["evidence_index"]),
            {"log_ids", "registration_id", "result_id"},
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
