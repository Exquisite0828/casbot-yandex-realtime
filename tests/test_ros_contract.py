from pathlib import Path
import time
import unittest
import xml.etree.ElementTree as ET

from realtime_dialog.realtime_dialog_node import (
    AsyncioWorker,
    BackgroundCommandBridge,
    ROS2_AVAILABLE,
)
from realtime_dialog.ros_contract import (
    AUDIO_DIALOG_FLUSH,
    AUDIO_DIALOG_PLAY,
    DIALOG_START_SESSION,
    DIALOG_STATUS,
    DIALOG_STOP_SESSION,
    DIALOG_TEXT_INPUT,
    DIALOG_TEXT_RESULT,
    NODE_NAME,
    STATUS_QOS,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "realtime_dialog"


class RosContractTest(unittest.TestCase):
    def test_vendor_contract_names_and_types_are_exact(self) -> None:
        self.assertEqual(NODE_NAME, "realtime_dialog_node")
        self.assertEqual(
            {
                DIALOG_START_SESSION.name: DIALOG_START_SESSION.type_name,
                DIALOG_STOP_SESSION.name: DIALOG_STOP_SESSION.type_name,
                DIALOG_TEXT_INPUT.name: DIALOG_TEXT_INPUT.type_name,
                DIALOG_STATUS.name: DIALOG_STATUS.type_name,
                DIALOG_TEXT_RESULT.name: DIALOG_TEXT_RESULT.type_name,
                AUDIO_DIALOG_FLUSH.name: AUDIO_DIALOG_FLUSH.type_name,
                AUDIO_DIALOG_PLAY.name: AUDIO_DIALOG_PLAY.type_name,
            },
            {
                "/dialog/start_session": "std_srvs/srv/Trigger",
                "/dialog/stop_session": "std_srvs/srv/Trigger",
                "/dialog/text_input": "std_msgs/msg/String",
                "/dialog/status": "std_msgs/msg/String",
                "/dialog/text_result": "std_msgs/msg/String",
                "/audio/dialog_flush": "std_msgs/msg/Bool",
                "/audio/dialog_play": "lingze_msgs/msg/PcmAudioFrame",
            },
        )

    def test_status_qos_is_a_provisional_vendor_documented_default(self) -> None:
        self.assertEqual(STATUS_QOS.reliability, "reliable")
        self.assertEqual(STATUS_QOS.durability, "transient_local")
        self.assertTrue(STATUS_QOS.provisional)

    def test_ament_python_metadata_and_executable(self) -> None:
        package = ET.parse(PACKAGE_ROOT / "package.xml").getroot()
        self.assertEqual(package.findtext("name"), "realtime_dialog")
        self.assertEqual(package.findtext("exec_depend"), "rclpy")
        setup_text = (PACKAGE_ROOT / "setup.py").read_text(encoding="utf-8")
        self.assertIn(
            "realtime_dialog_node = realtime_dialog.realtime_dialog_node:main",
            setup_text,
        )
        self.assertTrue((PACKAGE_ROOT / "resource" / "realtime_dialog").exists())

    def test_background_worker_submits_without_waiting_for_coroutine(self) -> None:
        worker = AsyncioWorker()

        async def delayed() -> str:
            import asyncio

            await asyncio.sleep(0.05)
            return "done"

        try:
            started = time.monotonic()
            future = worker.submit(delayed())
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.03)
            self.assertFalse(future.done())
            self.assertEqual(future.result(timeout=1), "done")
        finally:
            worker.stop()

    def test_ros_command_bridge_only_submits_coroutines(self) -> None:
        class Controller:
            async def start_session(self):
                return "start"

            async def stop_session(self):
                return "stop"

            async def handle_text_input(self, text: str):
                return text

        class Worker:
            def __init__(self) -> None:
                self.coroutines = []

            def submit(self, coroutine):
                self.coroutines.append(coroutine)
                return "scheduled"

        worker = Worker()
        bridge = BackgroundCommandBridge(worker, Controller())
        self.assertEqual(bridge.start_session(), "scheduled")
        self.assertEqual(bridge.stop_session(), "scheduled")
        self.assertEqual(bridge.text_input("Привет"), "scheduled")
        self.assertEqual(len(worker.coroutines), 3)
        for coroutine in worker.coroutines:
            coroutine.close()

    def test_local_environment_does_not_fake_ros2(self) -> None:
        self.assertFalse(ROS2_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
