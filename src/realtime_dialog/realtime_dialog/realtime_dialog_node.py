"""Thin ROS2 wrapper; all cloud I/O runs on ``AsyncioWorker``."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
import os
from queue import Empty, SimpleQueue
import threading
from typing import Any, Coroutine

from .adapters import PendingRobotAudioOutputAdapter, PendingRobotMicAdapter
from .dialog_controller import DialogController
from .ros_contract import (
    AUDIO_DIALOG_FLUSH,
    DIALOG_START_SESSION,
    DIALOG_STATUS,
    DIALOG_STOP_SESSION,
    DIALOG_TEXT_INPUT,
    DIALOG_TEXT_RESULT,
    NODE_NAME,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_QOS,
)
from .yandex_realtime_client import (
    CURRENT_ENDPOINT,
    DEFAULT_INSTRUCTIONS,
    PRIMARY_MODEL,
    RuntimeConfig,
    YandexRealtimeClient,
)


class AsyncioWorker:
    """Own an asyncio loop in a background thread and never block submitters."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="yandex-realtime-asyncio",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        self._loop.close()

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
        if self._closed:
            coroutine.close()
            raise RuntimeError("asyncio worker is closed")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)


class BackgroundCommandBridge:
    """The only path from a ROS callback to async controller commands."""

    def __init__(self, worker: AsyncioWorker, controller: DialogController) -> None:
        self._worker = worker
        self._controller = controller

    def start_session(self) -> Future[Any]:
        return self._worker.submit(self._controller.start_session())

    def stop_session(self) -> Future[Any]:
        return self._worker.submit(self._controller.stop_session())

    def text_input(self, text: str) -> Future[Any]:
        return self._worker.submit(self._controller.handle_text_input(text))


try:
    import rclpy  # type: ignore[import-not-found]
except ImportError:
    rclpy = None


ROS2_AVAILABLE = rclpy is not None


if ROS2_AVAILABLE:
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import Bool, String
    from std_srvs.srv import Trigger

    class RealtimeDialogNode(Node):
        """Vendor-compatible surface over the ROS-independent controller."""

        def __init__(self) -> None:
            super().__init__(NODE_NAME)
            self._outbound: SimpleQueue[tuple[str, object]] = SimpleQueue()
            config = self._runtime_config()
            self._worker = AsyncioWorker()
            audio_output = PendingRobotAudioOutputAdapter(
                lambda: self._outbound.put(("flush", True))
            )
            self._controller = DialogController(
                client=YandexRealtimeClient(config),
                mic_adapter=PendingRobotMicAdapter(),
                audio_output=audio_output,
                status_sink=lambda value: self._outbound.put(("status", value)),
                text_result_sink=lambda value: self._outbound.put(("text", value)),
            )
            self._commands = BackgroundCommandBridge(
                self._worker, self._controller
            )

            status_qos = QoSProfile(depth=STATUS_QOS.depth)
            status_qos.reliability = ReliabilityPolicy.RELIABLE
            status_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
            self._status_publisher = self.create_publisher(
                String, DIALOG_STATUS.name, status_qos
            )
            self._text_publisher = self.create_publisher(
                String, DIALOG_TEXT_RESULT.name, 10
            )
            self._flush_publisher = self.create_publisher(
                Bool, AUDIO_DIALOG_FLUSH.name, 10
            )
            self.create_service(
                Trigger, DIALOG_START_SESSION.name, self._on_start_session
            )
            self.create_service(
                Trigger, DIALOG_STOP_SESSION.name, self._on_stop_session
            )
            self.create_subscription(
                String, DIALOG_TEXT_INPUT.name, self._on_text_input, 10
            )
            self.create_timer(0.02, self._drain_outbound)
            self._outbound.put(("status", STATUS_IDLE))

        def _runtime_config(self) -> RuntimeConfig:
            self.declare_parameter(
                "yandex_endpoint",
                os.environ.get("YANDEX_REALTIME_ENDPOINT", CURRENT_ENDPOINT),
            )
            self.declare_parameter(
                "yandex_model",
                os.environ.get("YANDEX_MODEL_OR_AGENT", PRIMARY_MODEL),
            )
            self.declare_parameter(
                "yandex_folder_id", os.environ.get("YANDEX_FOLDER_ID", "")
            )
            self.declare_parameter("yandex_sample_rate", 24_000)
            self.declare_parameter("yandex_voice", "dasha")
            self.declare_parameter("yandex_vad_threshold", 0.5)
            self.declare_parameter("yandex_silence_ms", 500)
            self.declare_parameter("yandex_instructions", DEFAULT_INSTRUCTIONS)
            self.declare_parameter("yandex_connect_timeout", 15.0)
            self.declare_parameter("yandex_setup_timeout", 10.0)
            return RuntimeConfig.from_environment(
                endpoint=str(self.get_parameter("yandex_endpoint").value),
                model_or_uri=str(self.get_parameter("yandex_model").value),
                folder_id=str(self.get_parameter("yandex_folder_id").value) or None,
                sample_rate=int(self.get_parameter("yandex_sample_rate").value),
                voice=str(self.get_parameter("yandex_voice").value),
                vad_threshold=float(
                    self.get_parameter("yandex_vad_threshold").value
                ),
                silence_ms=int(self.get_parameter("yandex_silence_ms").value),
                instructions=str(
                    self.get_parameter("yandex_instructions").value
                ),
                connect_timeout=float(
                    self.get_parameter("yandex_connect_timeout").value
                ),
                setup_timeout=float(
                    self.get_parameter("yandex_setup_timeout").value
                ),
            )

        @staticmethod
        def _accept_scheduled(response: Any, message: str) -> Any:
            response.success = True
            response.message = message
            return response

        def _watch(self, future: Future[Any]) -> None:
            future.add_done_callback(self._on_command_done)

        def _on_command_done(self, future: Future[Any]) -> None:
            try:
                future.result()
            except Exception:
                # Detailed transport errors are normalized/redacted below the
                # ROS boundary.  Only the public state is emitted here.
                self._outbound.put(("status", STATUS_ERROR))

        def _on_start_session(self, _request: Any, response: Any) -> Any:
            try:
                self._watch(self._commands.start_session())
            except RuntimeError:
                response.success = False
                response.message = "background worker unavailable"
                return response
            return self._accept_scheduled(response, "start scheduled")

        def _on_stop_session(self, _request: Any, response: Any) -> Any:
            try:
                self._watch(self._commands.stop_session())
            except RuntimeError:
                response.success = False
                response.message = "background worker unavailable"
                return response
            return self._accept_scheduled(response, "stop scheduled")

        def _on_text_input(self, message: Any) -> None:
            try:
                self._watch(self._commands.text_input(str(message.data)))
            except RuntimeError:
                self._outbound.put(("status", STATUS_ERROR))

        def _drain_outbound(self) -> None:
            while True:
                try:
                    kind, value = self._outbound.get_nowait()
                except Empty:
                    return
                if kind == "status":
                    self._status_publisher.publish(String(data=str(value)))
                elif kind == "text":
                    self._text_publisher.publish(String(data=str(value)))
                elif kind == "flush":
                    self._flush_publisher.publish(Bool(data=bool(value)))

        def shutdown_background(self) -> None:
            try:
                future = self._commands.stop_session()
                future.result(timeout=3)
            except Exception:
                pass
            self._worker.stop()

else:
    RealtimeDialogNode = None  # type: ignore[misc,assignment]


def main(args: list[str] | None = None) -> None:
    if not ROS2_AVAILABLE:
        raise RuntimeError("ROS2 Humble/rclpy is required to launch realtime_dialog_node")
    rclpy.init(args=args)
    node = RealtimeDialogNode()
    try:
        rclpy.spin(node)
    finally:
        node.shutdown_background()
        node.destroy_node()
        rclpy.shutdown()
