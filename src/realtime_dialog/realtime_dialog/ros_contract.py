"""Vendor-documented ROS2 names without importing robot-specific messages."""

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
    provisional: bool


NODE_NAME = "realtime_dialog_node"

DIALOG_START_SESSION = InterfaceSpec(
    "/dialog/start_session", "std_srvs/srv/Trigger"
)
DIALOG_STOP_SESSION = InterfaceSpec(
    "/dialog/stop_session", "std_srvs/srv/Trigger"
)
DIALOG_TEXT_INPUT = InterfaceSpec("/dialog/text_input", "std_msgs/msg/String")
DIALOG_STATUS = InterfaceSpec("/dialog/status", "std_msgs/msg/String")
DIALOG_TEXT_RESULT = InterfaceSpec("/dialog/text_result", "std_msgs/msg/String")
AUDIO_DIALOG_FLUSH = InterfaceSpec("/audio/dialog_flush", "std_msgs/msg/Bool")

# Phase 4 must inspect the real message fields before this target gets a publisher.
AUDIO_DIALOG_PLAY = InterfaceSpec(
    "/audio/dialog_play", "lingze_msgs/msg/PcmAudioFrame"
)

STATUS_IDLE = "STATUS_IDLE"
STATUS_CONNECTING = "STATUS_CONNECTING"
STATUS_LISTENING = "STATUS_LISTENING"
STATUS_SPEAKING_TEXT = "STATUS_SPEAKING_TEXT"
STATUS_ERROR = "STATUS_ERROR"

STATUS_QOS = QosSpec(
    reliability="reliable",
    durability="transient_local",
    depth=1,
    provisional=True,
)
