from pathlib import Path
import time
import unittest
import xml.etree.ElementTree as ET

from realtime_dialog.realtime_dialog_node import (
    AsyncioWorker,
    BackgroundCommandBridge,
    LINGZE_MSGS_AVAILABLE,
    ROS2_AVAILABLE,
    RobotAdapterConfig,
    drain_robot_audio_output,
    shutdown_node,
    shutdown_runtime,
    validate_robot_adapter_config,
)
from realtime_dialog.adapters import QueuedRobotAudioOutputAdapter
from realtime_dialog.adapters import AdapterNotConfiguredError
from realtime_dialog.ros_contract import (
    AUDIO_DIALOG_FLUSH,
    AUDIO_DIALOG_PLAY,
    AUDIO_FLUSH_QOS,
    AUDIO_PLAY_QOS,
    DIALOG_SESSION_ACTIVE,
    DIALOG_START_SESSION,
    DIALOG_STATUS,
    DIALOG_STOP_SESSION,
    DIALOG_TEXT_INPUT,
    DIALOG_TEXT_RESULT,
    NODE_NAME,
    SESSION_ACTIVE_QOS,
    STATUS_CONNECTING,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_LISTENING,
    STATUS_QOS,
    STATUS_SPEAKING_TEXT,
    TEXT_RESULT_QOS,
    resolve_node_name,
    resolve_topic_name,
    session_active_for_status,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "realtime_dialog"


class RosContractTest(unittest.TestCase):
    def test_all_application_contract_names_are_relative(self) -> None:
        self.assertEqual(NODE_NAME, "realtime_dialog_node")
        specs = {
            DIALOG_START_SESSION.name: DIALOG_START_SESSION.type_name,
            DIALOG_STOP_SESSION.name: DIALOG_STOP_SESSION.type_name,
            DIALOG_TEXT_INPUT.name: DIALOG_TEXT_INPUT.type_name,
            DIALOG_STATUS.name: DIALOG_STATUS.type_name,
            DIALOG_TEXT_RESULT.name: DIALOG_TEXT_RESULT.type_name,
            DIALOG_SESSION_ACTIVE.name: DIALOG_SESSION_ACTIVE.type_name,
            AUDIO_DIALOG_FLUSH.name: AUDIO_DIALOG_FLUSH.type_name,
            AUDIO_DIALOG_PLAY.name: AUDIO_DIALOG_PLAY.type_name,
        }
        self.assertTrue(all(not name.startswith("/") for name in specs))
        self.assertEqual(
            specs,
            {
                "dialog/start_session": "std_srvs/srv/Trigger",
                "dialog/stop_session": "std_srvs/srv/Trigger",
                "dialog/text_input": "std_msgs/msg/String",
                "dialog/status": "std_msgs/msg/String",
                "dialog/text_result": "std_msgs/msg/String",
                "dialog/session_active": "std_msgs/msg/Bool",
                "audio/dialog_flush": "std_msgs/msg/Bool",
                "audio/dialog_play": "lingze_msgs/msg/PcmAudioFrame",
            },
        )

    def test_casbot_namespace_and_node_resolution(self) -> None:
        self.assertEqual(
            resolve_topic_name("lzdl10823", DIALOG_STATUS.name),
            "/lzdl10823/dialog/status",
        )
        self.assertEqual(
            resolve_topic_name("/lzdl10823/", AUDIO_DIALOG_PLAY.name),
            "/lzdl10823/audio/dialog_play",
        )
        self.assertEqual(
            resolve_node_name("lzdl10823", "dialog_node"),
            "/lzdl10823/dialog_node",
        )
        self.assertEqual(resolve_topic_name("", DIALOG_STATUS.name), "/dialog/status")

    def test_verified_reliability_and_durability_with_policy_depths(self) -> None:
        expected = {
            AUDIO_PLAY_QOS: ("reliable", "volatile", 10),
            AUDIO_FLUSH_QOS: ("reliable", "volatile", 10),
            STATUS_QOS: ("reliable", "transient_local", 1),
            TEXT_RESULT_QOS: ("reliable", "volatile", 10),
            SESSION_ACTIVE_QOS: ("reliable", "transient_local", 1),
        }
        for spec, values in expected.items():
            self.assertEqual(
                (spec.reliability, spec.durability, spec.depth), values
            )
            self.assertTrue(spec.reliability_durability_verified)
            self.assertEqual(spec.depth_source, "implementation_policy")
            self.assertFalse(spec.depth_vendor_verified)

    def test_session_active_project_compatibility_semantics(self) -> None:
        self.assertEqual(DIALOG_SESSION_ACTIVE.type_name, "std_msgs/msg/Bool")
        self.assertFalse(session_active_for_status(STATUS_IDLE))
        self.assertTrue(session_active_for_status(STATUS_CONNECTING))
        self.assertTrue(session_active_for_status(STATUS_LISTENING))
        self.assertTrue(session_active_for_status(STATUS_SPEAKING_TEXT))
        self.assertFalse(session_active_for_status(STATUS_ERROR))
        with self.assertRaisesRegex(ValueError, "unknown dialog status"):
            session_active_for_status("UNKNOWN")

    def test_ament_metadata_declares_vendor_and_launch_dependencies(self) -> None:
        package = ET.parse(PACKAGE_ROOT / "package.xml").getroot()
        self.assertEqual(package.findtext("name"), "realtime_dialog")
        dependencies = {element.text for element in package.findall("exec_depend")}
        self.assertTrue(
            {"rclpy", "std_msgs", "std_srvs", "lingze_msgs", "launch", "launch_ros"}
            <= dependencies
        )
        setup_text = (PACKAGE_ROOT / "setup.py").read_text(encoding="utf-8")
        self.assertIn(
            "realtime_dialog_node = realtime_dialog.realtime_dialog_node:main",
            setup_text,
        )
        self.assertIn("launch/*.launch.py", setup_text)
        self.assertIn("config/*.yaml", setup_text)
        self.assertTrue((PACKAGE_ROOT / "resource" / "realtime_dialog").exists())

    def test_casbot_launch_and_config_are_installed_sources(self) -> None:
        launch = PACKAGE_ROOT / "launch" / "casbot_realtime_dialog.launch.py"
        config = PACKAGE_ROOT / "config" / "casbot.example.yaml"
        self.assertTrue(launch.exists())
        self.assertTrue(config.exists())
        launch_text = launch.read_text(encoding="utf-8")
        config_text = config.read_text(encoding="utf-8")
        self.assertIn('default_value="lzdl10823"', launch_text)
        self.assertIn('default_value="dialog_node"', launch_text)
        self.assertIn("speaker_pcm_format: \"\"", config_text)
        self.assertIn("mic_device: \"hw:0,0\"", config_text)
        self.assertIn("barge_in_enabled: false", config_text)
        self.assertIn("microphone_resume_guard_ms: 500", config_text)
        self.assertNotIn("YANDEX_API_KEY", config_text)

    def test_ros_half_duplex_parameters_are_declared_and_propagated(self) -> None:
        node_source = (
            PACKAGE_ROOT / "realtime_dialog" / "realtime_dialog_node.py"
        ).read_text(encoding="utf-8")
        self.assertIn('declare_parameter("barge_in_enabled", False)', node_source)
        self.assertIn(
            'declare_parameter("microphone_resume_guard_ms", 500)',
            node_source,
        )
        self.assertIn("barge_in_enabled=behavior_config.barge_in_enabled", node_source)
        self.assertIn(
            "microphone_resume_guard_ms=behavior_config.microphone_resume_guard_ms",
            node_source,
        )

    def test_no_pending_adapter_exists_in_production_source(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PACKAGE_ROOT / "realtime_dialog").glob("*.py")
        )
        self.assertNotIn("PendingRobotMicAdapter", source)
        self.assertNotIn("PendingRobotAudioOutputAdapter", source)

    def test_robot_audio_config_requires_unknown_format_without_guessing(self) -> None:
        config = RobotAdapterConfig(
            mic_backend="arecord",
            mic_executable="arecord",
            mic_device="hw:0,0",
            mic_source_sample_rate=16_000,
            mic_channels=1,
            mic_format="S16_LE",
            mic_chunk_ms=20,
            mic_queue_chunks=50,
            speaker_pcm_format="",
            speaker_sample_rate=24_000,
            speaker_channels=1,
            speaker_queue_packets=100,
        )
        with self.assertRaisesRegex(
            AdapterNotConfiguredError,
            "PcmAudioFrame.format is not configured; vendor runtime value is unknown",
        ):
            validate_robot_adapter_config(
                config,
                yandex_input_sample_rate=24_000,
                yandex_output_sample_rate=24_000,
            )

    def test_source_imports_real_vendor_message_and_api_key_is_not_parameter(self) -> None:
        node_source = (
            PACKAGE_ROOT / "realtime_dialog" / "realtime_dialog_node.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from lingze_msgs.msg import PcmAudioFrame", node_source)
        self.assertIn("/lingze/install/setup.bash", node_source)
        self.assertNotIn('declare_parameter("YANDEX_API_KEY"', node_source)

    def test_ros_availability_flags_are_environment_independent_booleans(self) -> None:
        self.assertIsInstance(ROS2_AVAILABLE, bool)
        self.assertIsInstance(LINGZE_MSGS_AVAILABLE, bool)
        if LINGZE_MSGS_AVAILABLE:
            self.assertTrue(ROS2_AVAILABLE)

    def test_background_worker_submits_without_blocking(self) -> None:
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

    def test_shutdown_drains_stop_flush_before_destroying_node(self) -> None:
        order: list[str] = []
        flush_messages: list[bool] = []
        audio_messages = []
        audio_output = QueuedRobotAudioOutputAdapter()
        audio_output.set_generation(1)

        class Node:
            def shutdown_background(self) -> None:
                order.append("shutdown")
                audio_output.flush()

            def enqueue_shutdown_flush(self) -> None:
                order.append("enqueue_flush")
                audio_output.flush()

            def drain_shutdown_output(self) -> None:
                order.append("drain")
                drain_robot_audio_output(
                    audio_output,
                    publish_flush=lambda: (
                        flush_messages.append(True),
                        order.append("flush_publish"),
                    ),
                    publish_audio=audio_messages.append,
                )

            def destroy_node(self) -> None:
                order.append("destroy")

        shutdown_node(Node())
        self.assertEqual(
            order,
            [
                "shutdown",
                "enqueue_flush",
                "drain",
                "flush_publish",
                "destroy",
            ],
        )
        self.assertEqual(flush_messages, [True])
        self.assertEqual(audio_messages, [])

    def test_shutdown_failure_still_flushes_and_destroys_node(self) -> None:
        order: list[str] = []

        class Node:
            def shutdown_background(self) -> None:
                order.append("shutdown")
                raise RuntimeError("background stop failed")

            def enqueue_shutdown_flush(self) -> None:
                order.append("enqueue_flush")

            def drain_shutdown_output(self) -> None:
                order.append("drain_flush")

            def destroy_node(self) -> None:
                order.append("destroy")

        with self.assertRaisesRegex(RuntimeError, "background stop failed"):
            shutdown_node(Node())
        self.assertEqual(
            order,
            ["shutdown", "enqueue_flush", "drain_flush", "destroy"],
        )

    def test_ros_shutdown_runs_even_when_node_shutdown_fails(self) -> None:
        order: list[str] = []

        class Node:
            def shutdown_background(self) -> None:
                order.append("shutdown")
                raise RuntimeError("node shutdown failed")

            def enqueue_shutdown_flush(self) -> None:
                order.append("enqueue_flush")

            def drain_shutdown_output(self) -> None:
                order.append("drain")

            def destroy_node(self) -> None:
                order.append("destroy")

        with self.assertRaisesRegex(RuntimeError, "node shutdown failed"):
            shutdown_runtime(Node(), lambda: order.append("rclpy_shutdown"))
        self.assertEqual(
            order,
            [
                "shutdown",
                "enqueue_flush",
                "drain",
                "destroy",
                "rclpy_shutdown",
            ],
        )


if __name__ == "__main__":
    unittest.main()
