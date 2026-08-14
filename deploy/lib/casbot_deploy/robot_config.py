"""Fail-closed parsing for the vendor robot runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys


SUPPORTED_VENDOR_LLMS = frozenset({"lingze_omni_s2s", "lingze_s2s"})


class RobotConfigError(ValueError):
    """A sanitized robot configuration failure safe for operator output."""


@dataclass(frozen=True)
class RobotRuntimeConfig:
    namespace: str
    robot_current_mode: str
    current_llm: str


@dataclass(frozen=True)
class RobotConfigSnapshot:
    config: RobotRuntimeConfig
    sha256: str


def load_robot_runtime_snapshot(path: Path) -> RobotConfigSnapshot:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise RobotConfigError("user_config is missing or unreadable") from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RobotConfigError("user_config is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise RobotConfigError("user_config root must be a JSON object")

    values: dict[str, str] = {}
    for field in ("namespace", "robot_current_mode", "current_llm"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RobotConfigError(f"user_config field {field} must be a non-empty string")
        values[field] = value
    namespace = values["namespace"].strip().strip("/")
    if not namespace:
        raise RobotConfigError("user_config field namespace must be a non-empty string")
    return RobotConfigSnapshot(
        config=RobotRuntimeConfig(
            namespace=namespace,
            robot_current_mode=values["robot_current_mode"],
            current_llm=values["current_llm"],
        ),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_robot_runtime_config(path: Path) -> RobotRuntimeConfig:
    return load_robot_runtime_snapshot(path).config


def require_jijia_runtime_snapshot(path: Path) -> RobotConfigSnapshot:
    snapshot = load_robot_runtime_snapshot(path)
    config = snapshot.config
    if config.robot_current_mode != "jijia":
        raise RobotConfigError("robot_current_mode must be jijia")
    if config.current_llm not in SUPPORTED_VENDOR_LLMS:
        raise RobotConfigError("current_llm is not a supported vendor dialog backend")
    return snapshot


def require_jijia_runtime(path: Path) -> RobotRuntimeConfig:
    return require_jijia_runtime_snapshot(path).config


def launch_guard_main(raw_path: str) -> int:
    try:
        config = require_jijia_runtime(Path(raw_path))
    except RobotConfigError as error:
        print(f"ERROR: user_config validation failed: {error}", file=sys.stderr)
        return 1
    print(config.namespace)
    return 0
