"""ROS2 compatibility surface frozen from Phase 4 runtime evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InterfaceSpec:
    name: str
    type_name: str


@dataclass(frozen=True, slots=True)
class QosSpec:
    reliability: str
    durability: str
    depth: int
    reliability_durability_verified: bool = True
    depth_source: str = "implementation_policy"
    depth_vendor_verified: bool = False


NODE_NAME = "realtime_dialog_node"

# Relative names preserve the generic package while allowing the launch profile
# to resolve them under a device namespace such as ``/lzdl10823``.
DIALOG_START_SESSION = InterfaceSpec(
    "dialog/start_session", "std_srvs/srv/Trigger"
)
DIALOG_STOP_SESSION = InterfaceSpec(
    "dialog/stop_session", "std_srvs/srv/Trigger"
)
DIALOG_TEXT_INPUT = InterfaceSpec("dialog/text_input", "std_msgs/msg/String")
DIALOG_STATUS = InterfaceSpec("dialog/status", "std_msgs/msg/String")
DIALOG_TEXT_RESULT = InterfaceSpec("dialog/text_result", "std_msgs/msg/String")
DIALOG_SESSION_ACTIVE = InterfaceSpec(
    "dialog/session_active", "std_msgs/msg/Bool"
)
AUDIO_DIALOG_FLUSH = InterfaceSpec("audio/dialog_flush", "std_msgs/msg/Bool")
AUDIO_DIALOG_PLAY = InterfaceSpec(
    "audio/dialog_play", "lingze_msgs/msg/PcmAudioFrame"
)

STATUS_IDLE = "STATUS_IDLE"
STATUS_CONNECTING = "STATUS_CONNECTING"
STATUS_LISTENING = "STATUS_LISTENING"
STATUS_SPEAKING_TEXT = "STATUS_SPEAKING_TEXT"
STATUS_ERROR = "STATUS_ERROR"

# Reliability and durability are VERIFIED. Depths are project buffering policy;
# Phase 4 runtime evidence did not expose the vendor history depths.
AUDIO_PLAY_QOS = QosSpec("reliable", "volatile", 10)
AUDIO_FLUSH_QOS = QosSpec("reliable", "volatile", 10)
STATUS_QOS = QosSpec("reliable", "transient_local", 1)
TEXT_RESULT_QOS = QosSpec("reliable", "volatile", 10)
SESSION_ACTIVE_QOS = QosSpec("reliable", "transient_local", 1)


def _normalized_namespace(namespace: str) -> str:
    return namespace.strip().strip("/")


def resolve_topic_name(namespace: str, relative_name: str) -> str:
    if relative_name.startswith("/"):
        raise ValueError("application interface name must be relative")
    prefix = _normalized_namespace(namespace)
    return f"/{prefix}/{relative_name}" if prefix else f"/{relative_name}"


def resolve_node_name(namespace: str, node_name: str) -> str:
    value = node_name.strip().strip("/")
    if not value or "/" in value:
        raise ValueError("node_name must be one non-empty relative token")
    prefix = _normalized_namespace(namespace)
    return f"/{prefix}/{value}" if prefix else f"/{value}"


def session_active_for_status(status: str) -> bool:
    """PROJECT COMPATIBILITY SEMANTIC, not captured vendor exact timing."""
    mapping = {
        STATUS_IDLE: False,
        STATUS_CONNECTING: True,
        STATUS_LISTENING: True,
        STATUS_SPEAKING_TEXT: True,
        STATUS_ERROR: False,
    }
    try:
        return mapping[status]
    except KeyError as error:
        raise ValueError(f"unknown dialog status: {status}") from error
