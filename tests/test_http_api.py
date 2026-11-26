"""HTTP 接口：角色鉴权、受理流程与查询裁剪。"""

from __future__ import annotations

import json
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from _fixture import PACKET_PATH, ServiceTestCase

from app.server import build_server
from app.service import TrainingService


class HttpTestBase(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.server = build_server(self.service, "127.0.0.1", 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def call(self, method: str, path: str, headers: dict | None = None, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urlopen(request, timeout=5) as reply:
                return reply.status, json.loads(reply.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


class AuthorizationTest(HttpTestBase):
    def test_factory_cannot_void_other_factory_log(self) -> None:
        status, payload = self.call(
            "POST",
            "/api/intake/corrections",
            {"X-Actor-Role": "factory", "X-Actor-Id": "F-B"},
            {
                "id": "CX",
                "target": "L4",
                "factory": "F-B",
                "action": "void",
                "arrived_at": "15:30",
            },
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "factory_mismatch")

    def test_worker_cannot_read_other_worker(self) -> None:
        status, payload = self.call(
            "GET",
            "/api/workers/W18/overview?as_of=16:30",
            {"X-Actor-Role": "worker", "X-Actor-Id": "W17"},
        )
        self.assertEqual(status, 403)

    def test_station_scoped_to_own_team(self) -> None:
        # O11 属 A7，B2 班组来查应被拒
        status, payload = self.call(
            "GET",
            "/api/stations/orders/O11",
            {"X-Actor-Role": "station", "X-Actor-Id": "B2"},
        )
        self.assertEqual(status, 403)

    def test_centre_only_endpoints(self) -> None:
        status, _ = self.call(
            "GET",
            "/api/centre/disputes",
            {"X-Actor-Role": "station", "X-Actor-Id": "A7"},
        )
        self.assertEqual(status, 403)

    def test_exam_point_owns_result_submission(self) -> None:
        # C-W 是 E91 的所属考评点；C-E 不能代交 E91 成绩
        status, payload = self.call(
            "POST",
            "/api/intake/results",
            {"X-Actor-Role": "exam_point", "X-Actor-Id": "C-E"},
            {
                "id": "XFOREIGN",
                "seat": "E91",
                "registration": "R1",
                "checked_in": True,
                "score": 90,
                "received": "13:30",
            },
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "forbidden")

        # 非考评点角色不能提交成绩
        status, _ = self.call(
            "POST",
            "/api/intake/results",
            {"X-Actor-Role": "factory", "X-Actor-Id": "F-A"},
            {
                "id": "XROLE",
                "seat": "E91",
                "registration": "R1",
                "checked_in": True,
                "score": 90,
                "received": "13:31",
            },
        )
        self.assertEqual(status, 403)


class IntakeAndQueryTest(HttpTestBase):
    def test_worker_overview(self) -> None:
        status, payload = self.call(
            "GET",
            "/api/workers/W17/overview?as_of=16:30",
            {"X-Actor-Role": "worker", "X-Actor-Id": "W17"},
        )
        self.assertEqual(status, 200)
        self.assertEqual({q["trade"] for q in payload["qualifications"]},
                         {"ship_plate", "ship_pipe"})
        plate = next(q for q in payload["qualifications"] if q["trade"] == "ship_plate")
        self.assertEqual(plate["state"], "paused")
        pipe = next(q for q in payload["qualifications"] if q["trade"] == "ship_pipe")
        self.assertEqual(pipe["state"], "active")
        # 学员能看到本人已计入经历与报名
        self.assertTrue(payload["experiences"])
        self.assertTrue(payload["registrations"])

    def test_station_order_view_explains_gate(self) -> None:
        status, payload = self.call(
            "GET",
            "/api/stations/orders/O12?as_of=14:45",
            {"X-Actor-Role": "station", "X-Actor-Id": "A7"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["decision"]["allowed"])
        self.assertTrue(payload["decision"]["mentor_required"])
        self.assertEqual(payload["authorization"]["qualification_id"], "Q-W17-ship_pipe")
        # 看不到完整实训流水
        self.assertNotIn("logs", payload["authorization"])

    def test_station_sees_stop_notice_after_correction(self) -> None:
        status, payload = self.call(
            "GET",
            "/api/stations/orders/O11?as_of=16:30",
            {"X-Actor-Role": "station", "X-Actor-Id": "A7"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["decision"]["allowed"])
        self.assertEqual(len(payload["stop_notices"]), 1)
        self.assertEqual(
            payload["stop_notices"][0]["preserved_actual_start"],
            "2026-12-01T14:35:00+08:00",
        )

    def test_centre_trace_endpoint(self) -> None:
        status, payload = self.call(
            "GET",
            "/api/centre/qualifications/W17/ship_plate/trace",
            {"X-Actor-Role": "centre", "X-Actor-Id": "CENTRE"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["current_state"], "paused")
        self.assertTrue(payload["timeline"])

    def test_centre_disputes_endpoint(self) -> None:
        status, payload = self.call(
            "GET",
            "/api/centre/disputes",
            {"X-Actor-Role": "centre", "X-Actor-Id": "CENTRE"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["disputes"])

    def test_intake_log_flow_over_http(self) -> None:
        row = {
            "id": "L77",
            "factory": "F-A",
            "worker": "W18",
            "mentor": "M1",
            "trade": "ship_plate",
            "team": "A7",
            "start": "10:00",
            "end": "11:00",
            "signed_at": "11:05",
        }
        status, payload = self.call(
            "POST",
            "/api/intake/logs",
            {"X-Actor-Role": "factory", "X-Actor-Id": "F-A"},
            row,
        )
        self.assertEqual(status, 201)
        self.assertTrue(payload["accepted"])

    def test_registration_rejected_over_capacity(self) -> None:
        status, payload = self.call(
            "POST",
            "/api/intake/registrations",
            {"X-Actor-Role": "centre", "X-Actor-Id": "CENTRE"},
            {
                "id": "RX9",
                "worker": "W18",
                "seat": "E92",
                "occurred": "11:30",
                "action": "confirm",
            },
        )
        self.assertEqual(status, 409)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["reason"], "seat_capacity_exhausted")

    def test_health(self) -> None:
        status, payload = self.call("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")


class ServerBootTest(unittest.TestCase):
    def test_server_boots_with_packet_file(self) -> None:
        service = TrainingService()
        service.load_packet(json.loads(PACKET_PATH.read_text(encoding="utf-8")))
        server = build_server(service, "127.0.0.1", 0)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/health", timeout=5) as reply:
                self.assertEqual(reply.status, 200)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    import unittest

    unittest.main()
