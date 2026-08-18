"""Thin ROS2 wrapper; cloud and microphone I/O stay off the executor."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass
import os
from queue import Empty, SimpleQueue
import threading
from typing import Any, Callable, Coroutine

from .adapters import (
    AdapterNotConfiguredError,
    ArecordMicAdapter,
    QueuedRobotAudioOutputAdapter,
    RobotAudioPacket,
    RobotFlushEvent,
    publish_pcm_audio_packet,
)
from .dialog_controller import ControllerResult, DialogController
from .ros_contract import (
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
    QosSpec,
    SESSION_ACTIVE_QOS,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_QOS,
    TEXT_RESULT_QOS,
    session_active_for_status,
)
from .yandex_realtime_client import (
    CURRENT_ENDPOINT,
    DEFAULT_INSTRUCTIONS,
    PRIMARY_MODEL,
    RuntimeConfig,
    YandexRealtimeClient,
    redact_text,
)


LINGZE_IMPORT_ERROR = (
    "lingze_msgs.msg.PcmAudioFrame is unavailable in the current environment; "
    "source the vendor overlay (normally /lingze/install/setup.bash) and retry"
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


class InitialSessionAutoStarter:
    """Submit one non-blocking start command during node initialization only."""

    def __init__(
        self,
        *,
        enabled: bool,
        submit: Callable[[], Future[Any]],
        watch: Callable[[Future[Any], str], object],
    ) -> None:
        self._enabled = enabled
        self._submit = submit
        self._watch = watch
        self._scheduled = False

    def schedule_once(self) -> Future[Any] | None:
        if not self._enabled or self._scheduled:
            return None
        self._scheduled = True
        future = self._submit()
        self._watch(future, "auto-start")
        return future


class FailureJournalReporter:
    """Sanitize one diagnostic before it reaches the ROS journal queue."""

    def __init__(
        self,
        sink: Callable[[str], object],
        *,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self._sink = sink
        self._secrets = secrets

    def report(self, prefix: str, reason: str) -> None:
        safe_reason = redact_text(str(reason), secrets=self._secrets)
        self._sink(f"{prefix}: {safe_reason}")


def observe_command_completion(
    future: Future[Any],
    *,
    command: str,
    last_error: str | None,
    failure_sink: Callable[[str, str], object],
    status_sink: Callable[[str], object],
) -> None:
    """Classify an async command result without logging benign input rejection."""
    try:
        result = future.result()
    except Exception as error:
        reason = str(error) or type(error).__name__
        failure_sink(
            "dialog command exception",
            f"{command} {type(error).__name__}: {reason}",
        )
        status_sink(STATUS_ERROR)
        return
    if not isinstance(result, ControllerResult) or result.success:
        return
    if last_error is not None and (
        last_error == result.message or last_error.startswith(f"{result.message};")
    ):
        return
    prefixes = {
        "auto-start": "auto-start session failed",
        "start": "start session failed",
        "stop": "stop session failed",
    }
    prefix = prefixes.get(command)
    if prefix is not None:
        failure_sink(prefix, result.message)


@dataclass(frozen=True, slots=True)
class RobotAdapterConfig:
    mic_backend: str
    mic_executable: str
    mic_device: str
    mic_source_sample_rate: int
    mic_channels: int
    mic_format: str
    mic_chunk_ms: int
    mic_queue_chunks: int
    speaker_pcm_format: str
    speaker_sample_rate: int
    speaker_channels: int
    speaker_queue_packets: int


@dataclass(frozen=True, slots=True)
class DialogBehaviorConfig:
    barge_in_enabled: bool
    microphone_resume_guard_ms: int
    auto_start_session: bool


def validate_robot_adapter_config(
    config: RobotAdapterConfig,
    *,
    yandex_input_sample_rate: int,
    yandex_output_sample_rate: int,
) -> None:
    """Fail fast rather than guessing any unresolved robot audio mapping."""
    if config.mic_backend != "arecord":
        raise AdapterNotConfiguredError("mic_backend must be 'arecord' in Phase 5")
    if not config.mic_device.strip():
        raise AdapterNotConfiguredError(
            "mic_device is required for the robot arecord backend"
        )
    if not config.speaker_pcm_format.strip():
        raise AdapterNotConfiguredError(
            "PcmAudioFrame.format is not configured; vendor runtime value is unknown"
        )
    if config.mic_channels != 1 or config.speaker_channels != 1:
        raise ValueError("mic_channels and speaker_channels must be 1")
    if config.mic_format != "S16_LE":
        raise ValueError("mic_format must be S16_LE")
    if config.mic_source_sample_rate <= 0:
        raise ValueError("mic_source_sample_rate must be greater than zero")
    if config.mic_chunk_ms <= 0:
        raise ValueError("mic_chunk_ms must be greater than zero")
    if config.mic_queue_chunks <= 0 or config.speaker_queue_packets <= 0:
        raise ValueError("audio queue limits must be greater than zero")
    if yandex_input_sample_rate <= 0 or yandex_output_sample_rate <= 0:
        raise ValueError("Yandex audio sample rates must be greater than zero")
    if config.speaker_sample_rate != yandex_output_sample_rate:
        raise AdapterNotConfiguredError(
            "speaker_sample_rate must equal yandex_output_sample_rate because "
            "Phase 5 performs no unverified speaker-side conversion"
        )


def drain_robot_audio_output(
    audio_output: QueuedRobotAudioOutputAdapter,
    *,
    publish_flush: Callable[[], object],
    publish_audio: Callable[[RobotAudioPacket], object],
) -> None:
    """Drain queued ROS output while preserving flush barriers and stale guards."""
    for event in audio_output.drain_events():
        if isinstance(event, RobotFlushEvent):
            publish_flush()
        elif isinstance(event, RobotAudioPacket):
            audio_output.publish_if_current(event, publish_audio)


try:
    import rclpy  # type: ignore[import-not-found]
except ImportError:
    rclpy = None


ROS2_AVAILABLE = rclpy is not None
LINGZE_MSGS_AVAILABLE = False
PcmAudioFrame = None


if ROS2_AVAILABLE:
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import Bool, String
    from std_srvs.srv import Trigger

    try:
        from lingze_msgs.msg import PcmAudioFrame as _PcmAudioFrame
    except ImportError:
        _PcmAudioFrame = None
    else:
        PcmAudioFrame = _PcmAudioFrame
        LINGZE_MSGS_AVAILABLE = True

    def _qos_profile(spec: QosSpec) -> Any:
        profile = QoSProfile(depth=spec.depth)
        profile.reliability = ReliabilityPolicy.RELIABLE
        profile.durability = (
            DurabilityPolicy.TRANSIENT_LOCAL
            if spec.durability == "transient_local"
            else DurabilityPolicy.VOLATILE
        )
        return profile

    class RealtimeDialogNode(Node):
        """CASBOT-compatible surface over the ROS-independent controller."""

        def __init__(self) -> None:
            if not LINGZE_MSGS_AVAILABLE:
                raise RuntimeError(LINGZE_IMPORT_ERROR)
            super().__init__(NODE_NAME)
            self._outbound: SimpleQueue[tuple[str, object]] = SimpleQueue()
            runtime_config = self._runtime_config()
            adapter_config = self._adapter_config()
            behavior_config = self._behavior_config()
            self._failure_reporter = FailureJournalReporter(
                lambda value: self._outbound.put(("failure", value)),
                secrets=(runtime_config.api_key,),
            )
            validate_robot_adapter_config(
                adapter_config,
                yandex_input_sample_rate=runtime_config.input_sample_rate,
                yandex_output_sample_rate=runtime_config.yandex_output_sample_rate,
            )
            self._speaker_pcm_format = adapter_config.speaker_pcm_format
            self._audio_output = QueuedRobotAudioOutputAdapter(
                channels=adapter_config.speaker_channels,
                max_audio_packets=adapter_config.speaker_queue_packets,
            )

            self._status_publisher = self.create_publisher(
                String, DIALOG_STATUS.name, _qos_profile(STATUS_QOS)
            )
            self._text_publisher = self.create_publisher(
                String, DIALOG_TEXT_RESULT.name, _qos_profile(TEXT_RESULT_QOS)
            )
            self._session_active_publisher = self.create_publisher(
                Bool,
                DIALOG_SESSION_ACTIVE.name,
                _qos_profile(SESSION_ACTIVE_QOS),
            )
            self._flush_publisher = self.create_publisher(
                Bool, AUDIO_DIALOG_FLUSH.name, _qos_profile(AUDIO_FLUSH_QOS)
            )
            self._audio_publisher = self.create_publisher(
                PcmAudioFrame,
                AUDIO_DIALOG_PLAY.name,
                _qos_profile(AUDIO_PLAY_QOS),
            )

            mic_adapter = ArecordMicAdapter(
                executable=adapter_config.mic_executable,
                device=adapter_config.mic_device,
                source_sample_rate=adapter_config.mic_source_sample_rate,
                target_sample_rate=runtime_config.input_sample_rate,
                channels=adapter_config.mic_channels,
                pcm_format=adapter_config.mic_format,
                chunk_ms=adapter_config.mic_chunk_ms,
            )
            self._worker = AsyncioWorker()
            self._controller = DialogController(
                client=YandexRealtimeClient(runtime_config),
                mic_adapter=mic_adapter,
                audio_output=self._audio_output,
                status_sink=self._enqueue_status,
                text_result_sink=lambda value: self._outbound.put(("text", value)),
                failure_sink=lambda reason: self._failure_reporter.report(
                    "dialog session failure", reason
                ),
                microphone_queue_chunks=adapter_config.mic_queue_chunks,
                barge_in_enabled=behavior_config.barge_in_enabled,
                microphone_resume_guard_ms=behavior_config.microphone_resume_guard_ms,
            )
            self._commands = BackgroundCommandBridge(
                self._worker, self._controller
            )
            self._auto_starter = InitialSessionAutoStarter(
                enabled=behavior_config.auto_start_session,
                submit=self._commands.start_session,
                watch=self._watch,
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
            self._enqueue_status(STATUS_IDLE)
            try:
                self._auto_starter.schedule_once()
            except RuntimeError as error:
                self._failure_reporter.report(
                    "auto-start session failed",
                    str(error) or type(error).__name__,
                )
                self._enqueue_status(STATUS_ERROR)

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
            self.declare_parameter("yandex_input_sample_rate", 24_000)
            self.declare_parameter("yandex_output_sample_rate", 24_000)
            self.declare_parameter("yandex_voice", "dasha")
            self.declare_parameter("yandex_vad_threshold", 0.5)
            self.declare_parameter("yandex_silence_ms", 500)
            self.declare_parameter("yandex_instructions", DEFAULT_INSTRUCTIONS)
            self.declare_parameter("yandex_connect_timeout", 15.0)
            self.declare_parameter("yandex_setup_timeout", 10.0)
            return RuntimeConfig.from_environment(
                endpoint=str(self.get_parameter("yandex_endpoint").value),
                model_or_uri=str(self.get_parameter("yandex_model").value),
                folder_id=(
                    str(self.get_parameter("yandex_folder_id").value).strip()
                    or os.environ.get("YANDEX_FOLDER_ID")
                    or None
                ),
                input_sample_rate=int(
                    self.get_parameter("yandex_input_sample_rate").value
                ),
                yandex_output_sample_rate=int(
                    self.get_parameter("yandex_output_sample_rate").value
                ),
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

        def _adapter_config(self) -> RobotAdapterConfig:
            defaults: dict[str, object] = {
                "mic_backend": "arecord",
                "mic_executable": "arecord",
                "mic_device": "",
                "mic_source_sample_rate": 16_000,
                "mic_channels": 1,
                "mic_format": "S16_LE",
                "mic_chunk_ms": 20,
                "mic_queue_chunks": 50,
                "speaker_pcm_format": "",
                "speaker_sample_rate": 24_000,
                "speaker_channels": 1,
                "speaker_queue_packets": 100,
            }
            for name, default in defaults.items():
                self.declare_parameter(name, default)
            return RobotAdapterConfig(
                mic_backend=str(self.get_parameter("mic_backend").value),
                mic_executable=str(self.get_parameter("mic_executable").value),
                mic_device=str(self.get_parameter("mic_device").value),
                mic_source_sample_rate=int(
                    self.get_parameter("mic_source_sample_rate").value
                ),
                mic_channels=int(self.get_parameter("mic_channels").value),
                mic_format=str(self.get_parameter("mic_format").value),
                mic_chunk_ms=int(self.get_parameter("mic_chunk_ms").value),
                mic_queue_chunks=int(
                    self.get_parameter("mic_queue_chunks").value
                ),
                speaker_pcm_format=str(
                    self.get_parameter("speaker_pcm_format").value
                ),
                speaker_sample_rate=int(
                    self.get_parameter("speaker_sample_rate").value
                ),
                speaker_channels=int(
                    self.get_parameter("speaker_channels").value
                ),
                speaker_queue_packets=int(
                    self.get_parameter("speaker_queue_packets").value
                ),
            )

        def _behavior_config(self) -> DialogBehaviorConfig:
            self.declare_parameter("barge_in_enabled", False)
            self.declare_parameter("microphone_resume_guard_ms", 500)
            self.declare_parameter("auto_start_session", False)
            return DialogBehaviorConfig(
                barge_in_enabled=bool(
                    self.get_parameter("barge_in_enabled").value
                ),
                microphone_resume_guard_ms=int(
                    self.get_parameter("microphone_resume_guard_ms").value
                ),
                auto_start_session=bool(
                    self.get_parameter("auto_start_session").value
                ),
            )

        def _enqueue_status(self, status: str) -> None:
            self._outbound.put(("status", status))
            self._outbound.put(
                ("session_active", session_active_for_status(status))
            )

        @staticmethod
        def _accept_scheduled(response: Any, message: str) -> Any:
            response.success = True
            response.message = message
            return response

        def _watch(self, future: Future[Any], command: str) -> None:
            future.add_done_callback(
                lambda completed: self._on_command_done(completed, command)
            )

        def _on_command_done(self, future: Future[Any], command: str) -> None:
            observe_command_completion(
                future,
                command=command,
                last_error=self._controller.last_error,
                failure_sink=self._failure_reporter.report,
                status_sink=self._enqueue_status,
            )

        def _on_start_session(self, _request: Any, response: Any) -> Any:
            try:
                self._watch(self._commands.start_session(), "start")
            except RuntimeError as error:
                self._failure_reporter.report(
                    "dialog command exception",
                    f"start {type(error).__name__}: {error}",
                )
                response.success = False
                response.message = "background worker unavailable"
                return response
            return self._accept_scheduled(response, "start scheduled")

        def _on_stop_session(self, _request: Any, response: Any) -> Any:
            try:
                self._watch(self._commands.stop_session(), "stop")
            except RuntimeError as error:
                self._failure_reporter.report(
                    "dialog command exception",
                    f"stop {type(error).__name__}: {error}",
                )
                response.success = False
                response.message = "background worker unavailable"
                return response
            return self._accept_scheduled(response, "stop scheduled")

        def _on_text_input(self, message: Any) -> None:
            try:
                self._watch(
                    self._commands.text_input(str(message.data)), "text-input"
                )
            except RuntimeError as error:
                self._failure_reporter.report(
                    "dialog command exception",
                    f"text-input {type(error).__name__}: {error}",
                )
                self._enqueue_status(STATUS_ERROR)

        def _drain_outbound(self) -> None:
            while True:
                try:
                    kind, value = self._outbound.get_nowait()
                except Empty:
                    break
                if kind == "status":
                    self._status_publisher.publish(String(data=str(value)))
                elif kind == "text":
                    self._text_publisher.publish(String(data=str(value)))
                elif kind == "session_active":
                    self._session_active_publisher.publish(Bool(data=bool(value)))
                elif kind == "failure":
                    self.get_logger().error(str(value))

            drain_robot_audio_output(
                self._audio_output,
                publish_flush=lambda: self._flush_publisher.publish(
                    Bool(data=True)
                ),
                publish_audio=self._publish_audio_packet,
            )

        def _publish_audio_packet(self, packet: RobotAudioPacket) -> None:
            publish_pcm_audio_packet(
                packet,
                speaker_pcm_format=self._speaker_pcm_format,
                clock=self.get_clock(),
                publisher=self._audio_publisher,
                message_type=PcmAudioFrame,
            )

        def shutdown_background(self) -> None:
            try:
                future = self._commands.stop_session()
                future.result(timeout=3)
            except Exception:
                pass
            self._worker.stop()

        def drain_shutdown_output(self) -> None:
            """Publish output already queued by shutdown after spin has ended."""
            self._drain_outbound()

        def enqueue_shutdown_flush(self) -> None:
            """Guarantee a local flush if background shutdown could not enqueue it."""
            self._audio_output.flush()

else:
    RealtimeDialogNode = None  # type: ignore[misc,assignment]


def shutdown_node(node: Any) -> None:
    """Stop background work, publish its final output, then destroy the node."""
    shutdown_error: Exception | None = None
    try:
        node.shutdown_background()
    except Exception as error:
        shutdown_error = error
    finally:
        node.enqueue_shutdown_flush()
        try:
            node.drain_shutdown_output()
        finally:
            node.destroy_node()
    if shutdown_error is not None:
        raise shutdown_error


def shutdown_runtime(
    node: Any | None,
    shutdown_ros: Callable[[], object],
) -> None:
    """Always release the ROS context after any node shutdown outcome."""
    try:
        if node is not None:
            shutdown_node(node)
    finally:
        shutdown_ros()


def main(args: list[str] | None = None) -> None:
    if not ROS2_AVAILABLE:
        raise RuntimeError("ROS2 Humble/rclpy is required to launch realtime_dialog_node")
    if not LINGZE_MSGS_AVAILABLE:
        raise RuntimeError(LINGZE_IMPORT_ERROR)
    rclpy.init(args=args)
    node = None
    try:
        node = RealtimeDialogNode()
        rclpy.spin(node)
    finally:
        shutdown_runtime(node, rclpy.shutdown)
