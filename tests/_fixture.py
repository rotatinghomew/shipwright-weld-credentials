"""测试夹具：装载合成报文并提供构造服务的便捷函数。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.service import TrainingService  # noqa: E402

PACKET_PATH = ROOT / "data" / "training_20261201.json"


def load_packet() -> dict:
    return json.loads(PACKET_PATH.read_text(encoding="utf-8"))


def build_service() -> TrainingService:
    service = TrainingService()
    service.load_packet(load_packet())
    return service


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.service = build_service()
