"""只读/受理两用的 JSON HTTP 接口（标准库 ``http.server``）。

角色通过请求头声明：``X-Actor-Role`` 取 ``factory``（分厂）/ ``exam_point``（考评点）/
``centre``（实训中心）/ ``station``（工位）/ ``worker``（学员），
``X-Actor-Id`` 为对应业务标识（厂号、考评点号、班组或学员号）。
受理类接口会校验角色与报文归属；查询类接口按角色裁剪可见范围，
工位拿不到别家完整实训流水。
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .service import ServiceError, TrainingService


class _Handler(BaseHTTPRequestHandler):
    service: TrainingService

    server_version = "TrainingService/1.0"

    # --- 基础工具 -----------------------------------------------------
    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: str, message: str, status: int) -> None:
        self._send_json({"error": code, "message": message}, status)

    def _actor(self) -> tuple[str | None, str | None]:
        return self.headers.get("X-Actor-Role"), self.headers.get("X-Actor-Id")

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            raise ServiceError("empty_body", "请求体为空", HTTPStatus.BAD_REQUEST)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError("bad_json", f"JSON 解析失败: {exc}", HTTPStatus.BAD_REQUEST)
        if not isinstance(payload, dict):
            raise ServiceError("bad_payload", "请求体必须是 JSON 对象", HTTPStatus.BAD_REQUEST)
        return payload

    def _require_role(self, role: str) -> str:
        actor_role, actor_id = self._actor()
        if actor_role != role or not actor_id:
            raise ServiceError(
                "forbidden",
                f"该接口仅 {role} 可调用",
                HTTPStatus.FORBIDDEN,
            )
        return actor_id

    def _query(self) -> dict[str, str]:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        return {key: values[0] for key, values in query.items()}

    def log_message(self, fmt: str, *args: Any) -> None:  # 安静些
        return

    # --- 路由 ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - 标准库命名
        try:
            path = urlparse(self.path).path.rstrip("/")
            if path == "/api/health":
                self._send_json({"status": "ok"})
                return
            if path.startswith("/api/workers/") and path.endswith("/overview"):
                self._handle_worker_overview(path)
                return
            if path.startswith("/api/stations/orders/"):
                self._handle_station_order(path)
                return
            if path.startswith("/api/centre/qualifications/") and path.endswith("/trace"):
                self._handle_centre_trace(path)
                return
            if path == "/api/centre/disputes":
                self._require_role("centre")
                self._send_json(self.service.disputes_view())
                return
            self._send_error("not_found", f"未知路径 {path}", HTTPStatus.NOT_FOUND)
        except ServiceError as exc:
            self._send_error(exc.code, exc.message, exc.status)
        except Exception as exc:  # pragma: no cover - 防御性兜底
            self._send_error("internal_error", str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path.rstrip("/")
            if path == "/api/intake/logs":
                self._handle_log()
            elif path == "/api/intake/corrections":
                self._handle_correction()
            elif path == "/api/intake/registrations":
                self._handle_registration()
            elif path == "/api/intake/results":
                self._handle_result()
            elif path == "/api/intake/orders":
                self._handle_order()
            elif path.startswith("/api/centre/disputes/") and path.endswith("/resolve"):
                self._handle_dispute_resolve(path)
            else:
                self._send_error("not_found", f"未知路径 {path}", HTTPStatus.NOT_FOUND)
        except ServiceError as exc:
            self._send_error(exc.code, exc.message, exc.status)
        except Exception as exc:  # pragma: no cover
            self._send_error("internal_error", str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    # --- 受理接口 -----------------------------------------------------
    def _handle_log(self) -> None:
        actor_id = self._require_role("factory")
        row = self._read_body()
        if row.get("factory") != actor_id:
            raise ServiceError("forbidden", "分厂只能提交本厂签核", HTTPStatus.FORBIDDEN)
        log = self.service.submit_log(row)
        self._send_json({"accepted": True, "log_id": log.id}, HTTPStatus.CREATED)

    def _handle_correction(self) -> None:
        actor_id = self._require_role("factory")
        row = self._read_body()
        if row.get("factory") != actor_id:
            raise ServiceError("forbidden", "分厂只能撤正本厂签核", HTTPStatus.FORBIDDEN)
        correction = self.service.submit_correction(row)
        self._send_json({"accepted": True, "correction_id": correction.id}, HTTPStatus.CREATED)

    def _handle_registration(self) -> None:
        # 报名由实训中心统一向考评点提交，报名号用于事后回溯原申请
        self._require_role("centre")
        row = self._read_body()
        outcome = self.service.submit_registration(row)
        self._send_json(
            {
                "accepted": outcome.accepted,
                "registration_id": outcome.registration_id,
                "reason": outcome.reason,
            },
            HTTPStatus.CREATED if outcome.accepted else HTTPStatus.CONFLICT,
        )

    def _handle_result(self) -> None:
        actor_id = self._require_role("exam_point")
        row = self._read_body()
        seat = self.service.seats.get(row["seat"]) if self.service.seats else None
        if seat is not None and seat.centre != actor_id:
            raise ServiceError("forbidden", "成绩只能由所属考评点提交", HTTPStatus.FORBIDDEN)
        result = self.service.submit_result(row)
        self._send_json({"accepted": True, "result_id": result.id}, HTTPStatus.CREATED)

    def _handle_order(self) -> None:
        actor_id = self._require_role("station")
        row = self._read_body()
        if row.get("team") != actor_id:
            raise ServiceError("forbidden", "工位只能登记本班组工单", HTTPStatus.FORBIDDEN)
        order = self.service.register_order(row)
        self._send_json({"accepted": True, "order_id": order.id}, HTTPStatus.CREATED)

    def _handle_dispute_resolve(self, path: str) -> None:
        self._require_role("centre")
        dispute_id = path.split("/")[-2]
        body = self._read_body()
        dispute = self.service.resolve_dispute(
            dispute_id, body["at"], str(body.get("resolution", ""))
        )
        self._send_json({"dispute_id": dispute.id, "status": dispute.status})

    # --- 查询接口 -----------------------------------------------------
    def _as_of(self) -> str:
        value = self._query().get("as_of", "23:59")
        return value

    def _handle_worker_overview(self, path: str) -> None:
        actor_role, actor_id = self._actor()
        worker_id = path.split("/")[3]
        if actor_role != "worker" or actor_id != worker_id:
            if actor_role != "centre":
                raise ServiceError("forbidden", "学员只能查询本人信息", HTTPStatus.FORBIDDEN)
        payload = self.service.worker_overview(worker_id, self._as_of())
        self._send_json(payload)

    def _handle_station_order(self, path: str) -> None:
        actor_id = self._require_role("station")
        order_id = path.split("/")[-1]
        order = self.service.orders.get(order_id)
        if order is None:
            raise ServiceError("order_not_found", f"未知工单 {order_id}", HTTPStatus.NOT_FOUND)
        if order.team != actor_id:
            raise ServiceError("forbidden", "工位只能查询本班组工单", HTTPStatus.FORBIDDEN)
        self._send_json(self.service.station_order_view(order_id, self._as_of()))

    def _handle_centre_trace(self, path: str) -> None:
        self._require_role("centre")
        parts = path.split("/")
        # /api/centre/qualifications/{worker}/{trade}/trace
        worker_id, trade = parts[4], parts[5]
        self._send_json(self.service.qualification_trace(worker_id, trade))


def build_server(service: TrainingService, host: str = "127.0.0.1", port: int = 8050) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)


def serve(packet_path: str | None = None, host: str = "127.0.0.1", port: int = 8050) -> None:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="跨厂实训与开工资格服务")
    parser.add_argument("--data", default=packet_path, help="交换报文 JSON 路径")
    parser.add_argument("--host", default=host)
    parser.add_argument("--port", type=int, default=port)
    args = parser.parse_args()

    service = TrainingService()
    if args.data:
        packet = json.loads(Path(args.data).read_text(encoding="utf-8"))
        service.load_packet(packet)
    server = build_server(service, args.host, args.port)
    print(f"实训资格服务运行于 http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.server_close()


if __name__ == "__main__":  # pragma: no cover
    serve()
