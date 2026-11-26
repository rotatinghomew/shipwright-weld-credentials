"""更正影响：在制工单停工通知、记录保留与审计轨迹。"""

from __future__ import annotations

from _fixture import ServiceTestCase, build_service


class CorrectionImpactTest(ServiceTestCase):
    def test_stop_notice_issued_for_started_order(self) -> None:
        notices = [n for n in self.service.notices if n.order_id == "O11"]
        self.assertEqual(len(notices), 1)
        notice = notices[0]
        self.assertEqual(notice.issued_at, 16 * 60)  # C5 到达时
        self.assertEqual(notice.correction_id, "C5")
        # 已实际开工的记录保留，不回删
        self.assertEqual(notice.preserved_actual_start, 14 * 60 + 35)
        self.assertIn("实操分钟缺 60 分钟", notice.message)

    def test_unstarted_order_gets_no_stop_notice(self) -> None:
        # O13 没有 actual_start，只在门禁处拦截，不发停工通知
        self.assertFalse(any(n.order_id == "O13" for n in self.service.notices))

    def test_unrelated_trade_gets_no_notice(self) -> None:
        # O12 是 pipe，plate 撤正与它无关
        self.assertFalse(any(n.order_id == "O12" for n in self.service.notices))

    def test_no_notice_when_alternative_basis_survives(self) -> None:
        # 在报文副本中补入一条在撤正前生效、不与任何跨厂时段重叠的签核，
        # 验证“更正当时仍有其他有效依据”不会产生停工通知
        import json
        from pathlib import Path

        from app.service import TrainingService

        packet = json.loads(
            (Path(__file__).resolve().parents[1] / "data" / "training_20261201.json").read_text(
                encoding="utf-8"
            )
        )
        packet["logs"].append(
            {
                "id": "L40",
                "factory": "F-B",
                "worker": "W17",
                "mentor": "M2",
                "trade": "ship_plate",
                "team": "B2",
                "start": "11:00",
                "end": "12:30",
                "signed_at": "12:32",
            }
        )
        service = TrainingService()
        service.load_packet(packet)

        # C4(15:20) 到达后：L40 与 L5/L8 同厂并集，仍满足 90 分钟下限
        self.assertEqual(service.certification("W17", "ship_plate", "15:21").state, "active")
        self.assertFalse(any(n.order_id == "O11" for n in service.notices))
        # C5(16:00) 到达后：L40 单独 90 分钟无争议，资格仍 active，始终不发停工通知
        self.assertEqual(service.certification("W17", "ship_plate", "16:01").state, "active")
        self.assertFalse(any(n.order_id == "O11" for n in service.notices))

    def test_audit_trace_shows_facts_and_state_transition(self) -> None:
        trace = self.service.qualification_trace("W17", "ship_plate")
        categories = [event["category"] for event in trace["timeline"]]
        self.assertIn("dispute_raised", categories)
        self.assertIn("correction_applied", categories)
        self.assertIn("qualification_state_changed", categories)
        self.assertIn("stop_notice_issued", categories)
        # 状态最终落到 paused，且最后一次变更事件记录了 from/to
        changes = [
            e for e in trace["timeline"] if e["category"] == "qualification_state_changed"
        ]
        final_change = changes[-1]["facts"]
        self.assertEqual(final_change["to_state"], "paused")
        self.assertIn(final_change["from_state"], ("active", "pending"))
        # 开工时用了哪些事实可回看
        gate = next(g for g in trace["gate_decisions"] if g["order_id"] == "O11")
        self.assertTrue(gate["allowed"])
        self.assertIn("evidence_log_ids", gate["facts"]["qualification"])

    def test_time_travel_replays_history(self) -> None:
        # 同一资格在不同时点的状态可回放
        self.assertEqual(
            self.service.certification("W17", "ship_plate", "14:35").state, "active"
        )
        self.assertEqual(
            self.service.certification("W17", "ship_plate", "15:30").state, "active"
        )
        self.assertEqual(
            self.service.certification("W17", "ship_plate", "16:01").state, "paused"
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
