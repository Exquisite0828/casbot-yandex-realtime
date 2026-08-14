"""Read one vendor audio frame's metadata without touching its payload."""

from __future__ import annotations

import json
import time
from typing import Callable, Protocol


class ProbeTimeout(RuntimeError):
    pass


class ProbeExternalShutdown(RuntimeError):
    pass


class ProbeRuntime(Protocol):
    def initialize(self) -> bool: ...

    def subscribe(self, topic: str, callback: Callable[[object], None]) -> None: ...

    def ok(self) -> bool: ...

    def spin_once(self, timeout: float) -> None: ...

    def destroy(self) -> None: ...

    def shutdown(self) -> None: ...


def extract_metadata(message: object) -> dict[str, object]:
    """Intentionally access only the three Phase 8 integration fields."""
    return {
        "sample_rate": int(getattr(message, "sample_rate")),
        "channels": int(getattr(message, "channels")),
        "format": str(getattr(message, "format")),
    }


def format_metadata(metadata: dict[str, object], *, json_output: bool) -> str:
    safe = {
        "sample_rate": int(metadata["sample_rate"]),
        "channels": int(metadata["channels"]),
        "format": str(metadata["format"]),
    }
    if json_output:
        return json.dumps(safe, sort_keys=True)
    return "\n".join(f"{name}={safe[name]}" for name in safe)


def probe_first_metadata(
    runtime: ProbeRuntime,
    *,
    topic: str,
    timeout: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    metadata: dict[str, object] | None = None

    def receive(message: object) -> None:
        nonlocal metadata
        if metadata is None:
            metadata = extract_metadata(message)

    initialized_here = runtime.initialize()
    try:
        runtime.subscribe(topic, receive)
        deadline = monotonic() + timeout
        while metadata is None:
            if not runtime.ok():
                raise ProbeExternalShutdown("rclpy context shut down before metadata arrived")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ProbeTimeout("timed out waiting for the first dialog audio frame")
            runtime.spin_once(min(0.1, remaining))
        return metadata
    finally:
        runtime.destroy()
        if initialized_here and runtime.ok():
            runtime.shutdown()


class RclpyMetadataRuntime:
    """ROS2 Humble runtime boundary; imports are delayed until Phase 8 use."""

    def __init__(self) -> None:
        import rclpy
        from lingze_msgs.msg import PcmAudioFrame
        from rclpy.executors import ExternalShutdownException
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

        self._rclpy = rclpy
        self._message_type = PcmAudioFrame
        self._external_shutdown = ExternalShutdownException
        qos = QoSProfile(depth=1)
        qos.reliability = ReliabilityPolicy.RELIABLE
        qos.durability = DurabilityPolicy.VOLATILE
        self._qos = qos
        self._node = None

    def initialize(self) -> bool:
        initialized_here = not self._rclpy.ok()
        if initialized_here:
            self._rclpy.init(args=None)
        self._node = self._rclpy.create_node(
            "casbot_yandex_dialog_metadata_probe"
        )
        return initialized_here

    def subscribe(self, topic: str, callback: Callable[[object], None]) -> None:
        if self._node is None:
            raise RuntimeError("metadata probe runtime is not initialized")
        self._node.create_subscription(
            self._message_type,
            topic,
            callback,
            self._qos,
        )

    def ok(self) -> bool:
        return bool(self._rclpy.ok())

    def spin_once(self, timeout: float) -> None:
        try:
            self._rclpy.spin_once(self._node, timeout_sec=timeout)
        except self._external_shutdown as error:
            raise ProbeExternalShutdown("ROS context shut down") from error

    def destroy(self) -> None:
        if self._node is not None:
            self._node.destroy_node()
            self._node = None

    def shutdown(self) -> None:
        self._rclpy.shutdown()
