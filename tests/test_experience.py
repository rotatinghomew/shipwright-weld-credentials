"""实操经历认定：带教范围、跨厂重叠、撤正与随身带走。"""

from __future__ import annotations

from _fixture import ServiceTestCase, build_service


class ExperienceRecognitionTest(ServiceTestCase):
    def test_mentor_scope_limits_counted_minutes(self) -> None:
        # M2 带教窗 09:15-14:00；L5(09:15-10:30) 完全在窗内
        view = self.service.experience("W17", "ship_plate", "10:40")
        l5 = next(a for a in view.assessments if a.log_id == "L5")
        self.assertTrue(l5.valid)
        self.assertEqual(l5.segment, (9 * 60 + 15, 10 * 60 + 30))

    def test_experience_carries_across_factories_without_double_count(self) -> None:
        # 截至 11:03：L4(F-A 08:00-09:30=90m)、L5(F-B 09:15-10:30=75m)、L8(F-B 30m)
        # L4 与 L5 跨厂重叠 09:15-09:30（15m，归属冲突整段不计），并集去重后：
        # 90 + 75 + 30 - 2*15 = 165
        view = self.service.experience("W17", "ship_plate", "11:03")
        self.assertEqual(view.counted_minutes, 165)
        # 两家厂的经历都计入（换厂可带走）
        self.assertEqual(set(view.factory_minutes), {"F-A", "F-B"})
        dispute = next(iter(view.open_disputes), None)
        self.assertIsNotNone(dispute)
        self.assertEqual(dispute.segment, (9 * 60 + 15, 9 * 60 + 30))
        self.assertEqual(dispute.factories, ("F-A", "F-B"))

    def test_same_factory_overlap_union_not_double_counted(self) -> None:
        # L5 与 L8 同属 F-B 且首尾相接（10:30），并集计时不重复
        view = self.service.experience("W17", "ship_plate", "11:03")
        fb_minutes = view.factory_minutes["F-B"]
        # L5 去掉冲突段后 60m，L8 30m
        self.assertEqual(fb_minutes, 90)

    def test_void_takes_effect_at_arrival_and_keeps_uncontested_minutes(self) -> None:
        before = self.service.experience("W17", "ship_plate", "15:19")
        self.assertIn("L4", before.valid_log_ids)
        after = self.service.experience("W17", "ship_plate", "15:21")
        l4 = next(a for a in after.assessments if a.log_id == "L4")
        self.assertFalse(l4.valid)
        self.assertEqual(l4.excluded_reason, "voided")
        self.assertEqual(l4.voided_by, "C4")
        # L4 撤正后 L5 仍在，L5 的无争议部分（去掉与 L4 的冲突段）继续计入
        self.assertIn("L5", after.valid_log_ids)
        self.assertEqual(after.counted_minutes, 105)

    def test_dispute_auto_resolves_when_one_side_voided(self) -> None:
        # C4(15:20) 先到，L4 失效后 L4-L5 冲突解除
        view_1521 = self.service.experience("W17", "ship_plate", "15:21")
        self.assertEqual(view_1521.open_disputes, [])
        dispute = self.service.disputes["D-L4-L5"]
        self.assertEqual(dispute.status, "resolved")
        self.assertEqual(dispute.resolution, "conflict_cleared_by_void")

    def test_both_voided_leaves_only_uncontested_log(self) -> None:
        view = self.service.experience("W17", "ship_plate", "16:01")
        self.assertEqual(view.counted_minutes, 30)  # 只剩 L8
        self.assertEqual(view.factory_minutes, {"F-B": 30})

    def test_out_of_scope_cohort_log_rejected(self) -> None:
        # W30 属 G2，M1 仅覆盖 G1；构造一条越组签核
        service = build_service()
        service.submit_log(
            {
                "id": "L99",
                "factory": "F-A",
                "worker": "W30",
                "mentor": "M1",
                "trade": "ship_plate",
                "team": "A7",
                "start": "08:00",
                "end": "09:00",
                "signed_at": "09:05",
            }
        )
        view = service.experience("W30", "ship_plate", "09:10")
        assessment = next(a for a in view.assessments if a.log_id == "L99")
        self.assertFalse(assessment.valid)
        self.assertEqual(assessment.excluded_reason, "cohort_outside_mentor_scope")
        self.assertEqual(view.counted_minutes, 0)

    def test_only_own_factory_may_void(self) -> None:
        from app.service import ServiceError

        with self.assertRaises(ServiceError) as ctx:
            self.service.submit_correction(
                {
                    "id": "CX",
                    "target": "L4",  # L4 属 F-A
                    "factory": "F-B",
                    "action": "void",
                    "arrived_at": "15:30",
                }
            )
        self.assertEqual(ctx.exception.code, "factory_mismatch")
        # 撤正在 15:30 到达，而 C4 15:20 才生效；故在 15:10 的时点视图里 L4 仍有效
        view = self.service.experience("W17", "ship_plate", "15:10")
        self.assertIn("L4", view.valid_log_ids)


if __name__ == "__main__":
    import unittest

    unittest.main()
