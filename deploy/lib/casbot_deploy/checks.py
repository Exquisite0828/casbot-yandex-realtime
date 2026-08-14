"""Read-only deployment preflight and mutually-exclusive mode verification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Mapping, Protocol, Sequence

from .paths import DeploymentPaths
from .robot_config import (
    RobotConfigError,
    RobotConfigSnapshot,
    RobotRuntimeConfig,
    SUPPORTED_VENDOR_LLMS,
    load_robot_runtime_snapshot,
    require_jijia_runtime_snapshot,
)
from .vendor_gate import VendorGate, VendorGateStatus


VENDOR_SERVICE = "lingze_robot.service"
YANDEX_SERVICE = "casbot-yandex-dialog.service"
VENDOR_EXECUTABLES = (
    "/lingze/install/lingze_omni_s2s/lib/lingze_omni_s2s/dialog_node",
    "/lingze/install/lingze_s2s/lib/lingze_s2s/dialog_node",
)
YANDEX_EXECUTABLE = (
    str(DeploymentPaths.project_executable_logical)
)
REQUIRED_ENVIRONMENT = (
    "YANDEX_API_KEY",
    "YANDEX_FOLDER_ID",
    "YANDEX_REALTIME_ENDPOINT",
    "YANDEX_MODEL_OR_AGENT",
)
ROS_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENVIRONMENT_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    DEFERRED = "DEFERRED"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class CheckReport:
    mode: str
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(check.status is not CheckStatus.FAIL for check in self.checks)

    def by_name(self, name: str) -> CheckResult:
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(name)

    def render_text(self) -> str:
        lines = [f"mode={self.mode} result={'PASS' if self.ok else 'FAIL'}"]
        lines.extend(
            f"{check.status.value} {check.name}: {check.detail}"
            for check in self.checks
        )
        return "\n".join(lines)

    def render_json(self) -> str:
        return json.dumps(
            {
                "mode": self.mode,
                "ok": self.ok,
                "checks": [
                    {
                        "name": check.name,
                        "status": check.status.value,
                        "detail": check.detail,
                    }
                    for check in self.checks
                ],
            },
            sort_keys=True,
        )


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, args: Sequence[str], *, timeout: float) -> CommandResult: ...


class SubprocessCommandRunner:
    """Bounded shell-free command execution."""

    def run(self, args: Sequence[str], *, timeout: float) -> CommandResult:
        command = tuple(str(value) for value in args)
        completed = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
        return CommandResult(
            command,
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )


class GraphProbe(Protocol):
    def list_fully_qualified_nodes(self, *, timeout: float) -> list[str]: ...


class RclpyGraphProbe:
    """Use rclpy graph APIs so duplicate fully-qualified nodes stay visible."""

    def list_fully_qualified_nodes(self, *, timeout: float) -> list[str]:
        import rclpy

        already_initialized = rclpy.ok()
        if not already_initialized:
            rclpy.init(args=None)
        node = rclpy.create_node("casbot_yandex_deployment_probe")
        try:
            deadline = time.monotonic() + timeout
            observed: list[str] = []
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                rclpy.spin_once(node, timeout_sec=min(0.1, max(0.0, remaining)))
                observed = [
                    f"/{namespace.strip('/')}/{name}" if namespace.strip("/") else f"/{name}"
                    for name, namespace in node.get_node_names_and_namespaces()
                ]
            return observed
        finally:
            node.destroy_node()
            if not already_initialized and rclpy.ok():
                rclpy.shutdown()


def _pass(name: str, detail: str) -> CheckResult:
    return CheckResult(name, CheckStatus.PASS, detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name, CheckStatus.FAIL, detail)


def _deferred(name: str, detail: str) -> CheckResult:
    return CheckResult(name, CheckStatus.DEFERRED, detail)


def _expected_state(
    name: str,
    actual: bool | None,
    expected: bool,
    *,
    pass_detail: str,
    mismatch_detail: str,
) -> CheckResult:
    if actual is expected:
        return _pass(name, pass_detail)
    if actual is None:
        return _fail(name, "UNKNOWN: state could not be determined")
    return _fail(name, mismatch_detail)


def _file_check(name: str, path: Path) -> CheckResult:
    return _pass(name, "present") if path.is_file() else _fail(name, "missing")


def _executable_check(name: str, path: Path) -> CheckResult:
    return (
        _pass(name, "present and executable")
        if path.is_file() and os.access(path, os.X_OK)
        else _fail(name, "missing or not executable")
    )


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ENVIRONMENT_ASSIGNMENT.fullmatch(line)
        if match is None:
            raise ValueError("unsupported environment assignment syntax")
        name, raw_value = match.groups()
        if name in values:
            raise ValueError(f"duplicate environment variable: {name}")
        value = raw_value.strip()
        if value[:1] in {'"', "'"}:
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise ValueError(f"unterminated environment quote: {name}")
            value = value[1:-1]
        elif any(character.isspace() for character in value):
            raise ValueError(f"unquoted whitespace in environment variable: {name}")
        if "\\" in value or "\x00" in value:
            raise ValueError(f"unsupported environment escaping: {name}")
        values[name] = value.strip()
    return values


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return not value or "replace_me" in lowered or "<replace" in lowered or "changeme" in lowered


def _speaker_pcm_format(path: Path) -> str | None:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if (
        len(lines) < 3
        or any("\t" in line for line in lines)
        or lines[0] != "/**:"
        or lines[1] != "  ros__parameters:"
    ):
        return None
    parameter = re.compile(r"^ {4}([a-z][a-z0-9_]+):\s*(.*?)\s*$")
    values: dict[str, str] = {}
    for line in lines[2:]:
        match = parameter.fullmatch(line)
        if match is None:
            return None
        name, value = match.groups()
        if name in values:
            return None
        values[name] = value
    raw = values.get("speaker_pcm_format", "").strip()
    if len(raw) < 2 or raw[0] not in {'"', "'"} or raw[-1] != raw[0]:
        return None
    return raw[1:-1].strip() or None


def _secure_regular_file(
    paths: DeploymentPaths,
    name: str,
    path: Path,
    *,
    private: bool,
) -> CheckResult:
    expected_uid = 0 if paths.is_real_root else os.geteuid()
    try:
        metadata = path.lstat()
        parent = path.parent.lstat()
    except OSError:
        return _fail(name, "file or parent is unavailable")
    if not stat.S_ISREG(metadata.st_mode):
        return _fail(name, "must be a regular non-symlink file")
    if metadata.st_uid != expected_uid:
        return _fail(name, "file owner is not trusted")
    unsafe_file_bits = 0o077 if private else 0o022
    if metadata.st_mode & unsafe_file_bits:
        return _fail(name, "file mode is unsafe")
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != expected_uid
        or parent.st_mode & 0o022
    ):
        return _fail(name, "parent ownership or mode is unsafe")
    return _pass(name, "regular file with trusted owner, mode, and parent")


class _StateReader:
    def __init__(
        self,
        paths: DeploymentPaths,
        runner: CommandRunner,
        graph_probe: GraphProbe,
        *,
        timeout: float,
        environ: Mapping[str, str],
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.graph_probe = graph_probe
        self.timeout = timeout
        self.environ = environ

    def _returncode(self, args: Sequence[str]) -> int | None:
        try:
            return self.runner.run(args, timeout=self.timeout).returncode
        except (OSError, subprocess.SubprocessError):
            return None

    def service_active(self, service: str) -> bool | None:
        returncode = self._returncode(["systemctl", "is-active", service])
        if returncode == 0:
            return True
        if returncode == 3:
            return False
        return None

    def _process_pids(self, executable: str) -> set[int] | None:
        try:
            result = self.runner.run(
                ["pgrep", "-f", executable], timeout=self.timeout
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode == 1:
            return set()
        if result.returncode != 0:
            return None
        pids: set[int] = set()
        for line in result.stdout.splitlines():
            value = line.strip().split(maxsplit=1)[0] if line.strip() else ""
            if not value.isdigit() or int(value) <= 0:
                return None
            pids.add(int(value))
        return pids or None

    def vendor_dialog_pids(self) -> set[int] | None:
        combined: set[int] = set()
        for executable in VENDOR_EXECUTABLES:
            pids = self._process_pids(executable)
            if pids is None:
                return None
            combined.update(pids)
        return combined

    def vendor_dialog_running(self) -> bool | None:
        pids = self.vendor_dialog_pids()
        return None if pids is None else bool(pids)

    def yandex_dialog_pids(self) -> set[int] | None:
        return self._process_pids(YANDEX_EXECUTABLE)

    def yandex_dialog_running(self) -> bool | None:
        pids = self.yandex_dialog_pids()
        return None if pids is None else bool(pids)

    def microphone_free(self) -> bool | None:
        returncode = self._returncode(["fuser", str(self.paths.capture_device)])
        if returncode == 0:
            return False
        if returncode == 1:
            return True
        return None

    def nodes(self) -> tuple[list[str] | None, str | None]:
        try:
            return self.graph_probe.list_fully_qualified_nodes(
                timeout=self.timeout
            ), None
        except Exception as error:
            return None, type(error).__name__

    def robot_config_snapshot(
        self,
    ) -> tuple[RobotConfigSnapshot | None, str | None]:
        try:
            return load_robot_runtime_snapshot(self.paths.user_config), None
        except RobotConfigError as error:
            return None, str(error)

    def namespace(self, config: RobotRuntimeConfig | None = None) -> str | None:
        override = self.environ.get("CASBOT_ROS_NAMESPACE", "").strip().strip("/")
        if override:
            return override if ROS_TOKEN.fullmatch(override) else None
        if config is None:
            snapshot, _error = self.robot_config_snapshot()
            config = snapshot.config if snapshot is not None else None
        if config is None:
            return None
        return config.namespace if ROS_TOKEN.fullmatch(config.namespace) else None

    def yandex_process_owned(self) -> bool:
        try:
            result = self.runner.run(
                ["systemctl", "show", "-p", "MainPID", "--value", YANDEX_SERVICE],
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        pid = result.stdout.strip()
        if result.returncode != 0 or not pid.isdigit() or int(pid) <= 0:
            return False
        matching_pids = self.yandex_dialog_pids()
        if matching_pids != {int(pid)}:
            return False
        try:
            process = self.runner.run(
                ["ps", "-p", pid, "-o", "args="], timeout=self.timeout
            )
        except (OSError, subprocess.SubprocessError):
            return False
        command = process.stdout
        return (
            process.returncode == 0
            and "/opt/casbot-yandex-realtime/" in command
            and "realtime_dialog_node" in command
        )


class DeploymentInspector:
    def __init__(
        self,
        paths: DeploymentPaths,
        runner: CommandRunner | None = None,
        graph_probe: GraphProbe | None = None,
        *,
        timeout: float = 5.0,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.paths = paths
        self.runner = runner or SubprocessCommandRunner()
        self.graph_probe = graph_probe or RclpyGraphProbe()
        self.timeout = timeout
        self.environ = dict(os.environ if environ is None else environ)
        self.state = _StateReader(
            paths,
            self.runner,
            self.graph_probe,
            timeout=timeout,
            environ=self.environ,
        )

    def _run(self, args: Sequence[str]) -> CommandResult | None:
        try:
            return self.runner.run(args, timeout=self.timeout)
        except (OSError, subprocess.SubprocessError):
            return None

    def _python_import(self, module: str, statement: str | None = None) -> CheckResult:
        program = statement or f"import {module}"
        result = self._run([str(self.paths.venv_python), "-c", program])
        return (
            _pass(f"import_{module}", "importable")
            if result is not None and result.returncode == 0
            else _fail(f"import_{module}", "not importable")
        )

    def _build(self) -> CheckReport:
        version = self._run([str(self.paths.system_python), "--version"])
        checks = [
            _pass("python_3_10", "Python 3.10")
            if version is not None
            and version.returncode == 0
            and "Python 3.10." in (version.stdout + version.stderr)
            else _fail("python_3_10", "Python 3.10 required"),
            _file_check("colcon", self.paths.colcon),
            _file_check("ros2_setup", self.paths.ros_setup),
            _file_check("vendor_overlay", self.paths.vendor_setup),
            _file_check("venv", self.paths.venv_python),
            self._python_import("aiohttp"),
            self._python_import("rclpy"),
            self._python_import(
                "lingze_msgs",
                "from lingze_msgs.msg import PcmAudioFrame",
            ),
            _file_check("project_package", self.paths.project_package),
            _file_check("project_install", self.paths.project_install_setup),
            _executable_check("project_executable", self.paths.project_executable),
            _file_check("launch_file", self.paths.project_launch),
            _file_check("config_file", self.paths.config),
        ]
        return CheckReport("build", tuple(checks))

    def _config_checks(self) -> list[CheckResult]:
        checks = [
            _file_check("config_file", self.paths.config),
            _file_check("env_file", self.paths.env),
        ]
        checks.append(
            _secure_regular_file(
                self.paths,
                "config_security",
                self.paths.config,
                private=False,
            )
        )
        if self.paths.env.is_file():
            checks.append(
                _secure_regular_file(
                    self.paths,
                    "env_security",
                    self.paths.env,
                    private=True,
                )
            )
            mode = self.paths.env.stat().st_mode & 0o777
            checks.append(
                _pass("env_permissions", "group/other unreadable")
                if mode & 0o077 == 0
                else _fail("env_permissions", "must not allow group/other access")
            )
            try:
                values = _parse_env_file(self.paths.env)
                unexpected = sorted(set(values) - set(REQUIRED_ENVIRONMENT))
                if unexpected:
                    raise ValueError(
                        "unsupported environment variables: " + ", ".join(unexpected)
                    )
            except (OSError, UnicodeDecodeError, ValueError):
                values = {}
                syntax_valid = False
            else:
                syntax_valid = True
            checks.append(
                _pass("env_syntax", "strict supported environment subset")
                if syntax_valid
                else _fail("env_syntax", "malformed, duplicate, or unsupported variables")
            )
            invalid = [
                name
                for name in REQUIRED_ENVIRONMENT
                if _is_placeholder(values.get(name, ""))
            ]
            checks.append(
                _pass("required_environment", "required variables configured")
                if not invalid
                else _fail(
                    "required_environment",
                    "missing or placeholder variables: " + ", ".join(invalid),
                )
            )
        else:
            checks.extend(
                [
                    _fail("env_security", "env file missing or not a regular file"),
                    _fail("env_permissions", "env file missing"),
                    _fail("env_syntax", "env file missing"),
                    _fail("required_environment", "env file missing"),
                ]
            )
        if self.paths.config.is_file():
            try:
                speaker_format = _speaker_pcm_format(self.paths.config)
            except (OSError, UnicodeDecodeError):
                speaker_format = None
            checks.append(
                _pass("speaker_pcm_format", "configured")
                if speaker_format
                else _fail("speaker_pcm_format", "must be non-empty after Phase 8 evidence")
            )
        else:
            checks.append(_fail("speaker_pcm_format", "config file missing"))
        return checks

    def _robot_identity_checks(
        self,
    ) -> tuple[str | None, list[CheckResult], RobotConfigSnapshot | None]:
        snapshot, error = self.state.robot_config_snapshot()
        config = snapshot.config if snapshot is not None else None
        namespace = self.state.namespace(config)
        if config is None:
            return namespace, [
                _fail("user_config", error or "user_config validation failed"),
                _fail("namespace", "user_config validation failed"),
                _fail("robot_mode", "user_config validation failed"),
                _fail("current_llm", "user_config validation failed"),
            ], None
        return namespace, [
            _pass("user_config", "valid required fields"),
            _pass("namespace", "resolved")
            if namespace
            else _fail("namespace", "unable to resolve one namespace token"),
            _pass("robot_mode", "jijia")
            if config.robot_current_mode == "jijia"
            else _fail("robot_mode", "expected jijia"),
            _pass("current_llm", "supported vendor dialog backend")
            if config.current_llm in SUPPORTED_VENDOR_LLMS
            else _fail("current_llm", "unsupported vendor dialog backend"),
        ], snapshot

    def _robot_config_stability_check(
        self, initial: RobotConfigSnapshot | None
    ) -> CheckResult:
        if initial is None:
            return _fail("robot_config_stable", "initial user_config was invalid")
        try:
            current = require_jijia_runtime_snapshot(self.paths.user_config)
        except RobotConfigError:
            return _fail("robot_config_stable", "end-of-check user_config is invalid")
        if current.sha256 != initial.sha256:
            return _fail("robot_config_stable", "user_config changed during check")
        return _pass("robot_config_stable", "unchanged and safe at end of check")

    def _graph_checks(
        self, nodes: list[str] | None, namespace: str | None, *, expected_dialogs: int
    ) -> list[CheckResult]:
        if nodes is None or namespace is None:
            return [
                _fail("dialog_node_count", "ROS graph unavailable"),
                _fail("speaker_node", "ROS graph unavailable"),
            ]
        dialog = f"/{namespace}/dialog_node"
        speaker = f"/{namespace}/audio_speaker_node"
        count = nodes.count(dialog)
        return [
            _pass("dialog_node_count", f"exactly {expected_dialogs}")
            if count == expected_dialogs
            else _fail("dialog_node_count", f"expected {expected_dialogs}; observed {count}"),
            _pass("speaker_node", "present")
            if speaker in nodes
            else _fail("speaker_node", "missing"),
        ]

    def _service(self) -> CheckReport:
        namespace, robot_checks, robot_snapshot = self._robot_identity_checks()
        nodes, _graph_error = self.state.nodes()
        gate_status = VendorGate(self.paths).status()
        checks = [
            _pass("marker", "present") if self.paths.marker.is_file() else _fail("marker", "missing"),
            _secure_regular_file(
                self.paths,
                "marker_security",
                self.paths.marker,
                private=False,
            ),
            _pass("vendor_gate", "PATCHED")
            if gate_status is VendorGateStatus.PATCHED
            else _fail("vendor_gate", gate_status.value),
            _expected_state(
                "vendor_service",
                self.state.service_active(VENDOR_SERVICE),
                True,
                pass_detail="active",
                mismatch_detail="inactive",
            ),
            *self._config_checks(),
            _executable_check("project_executable", self.paths.project_executable),
            *robot_checks,
            _expected_state(
                "vendor_dialog_absent",
                self.state.vendor_dialog_running(),
                False,
                pass_detail="not running",
                mismatch_detail="vendor dialog is still running",
            ),
            _expected_state(
                "yandex_dialog_absent",
                self.state.yandex_dialog_running(),
                False,
                pass_detail="no orphan process",
                mismatch_detail="Yandex dialog process already exists",
            ),
            *self._graph_checks(nodes, namespace, expected_dialogs=0),
            _expected_state(
                "microphone_free",
                self.state.microphone_free(),
                True,
                pass_detail="capture device released",
                mismatch_detail="capture device occupied",
            ),
            self._python_import("aiohttp"),
            self._python_import("rclpy"),
            self._python_import(
                "lingze_msgs",
                "from lingze_msgs.msg import PcmAudioFrame",
            ),
        ]
        checks.append(self._robot_config_stability_check(robot_snapshot))
        return CheckReport("service", tuple(checks))

    def _switch(self) -> CheckReport:
        namespace, robot_checks, robot_snapshot = self._robot_identity_checks()
        nodes, _graph_error = self.state.nodes()
        gate_status = VendorGate(self.paths).status()
        checks = [
            _pass("marker", "absent before switch")
            if not self.paths.marker.exists()
            else _fail("marker", "already present"),
            _pass("vendor_gate", "PATCHED")
            if gate_status is VendorGateStatus.PATCHED
            else _fail("vendor_gate", gate_status.value),
            _expected_state(
                "vendor_service",
                self.state.service_active(VENDOR_SERVICE),
                True,
                pass_detail="active",
                mismatch_detail="inactive",
            ),
            _expected_state(
                "yandex_service_stopped",
                self.state.service_active(YANDEX_SERVICE),
                False,
                pass_detail="inactive",
                mismatch_detail="service is active",
            ),
            *self._config_checks(),
            _executable_check("project_executable", self.paths.project_executable),
            *robot_checks,
            _expected_state(
                "vendor_dialog_present",
                self.state.vendor_dialog_running(),
                True,
                pass_detail="running",
                mismatch_detail="not running",
            ),
            _expected_state(
                "yandex_dialog_absent",
                self.state.yandex_dialog_running(),
                False,
                pass_detail="no orphan process",
                mismatch_detail="Yandex dialog process already exists",
            ),
            *self._graph_checks(nodes, namespace, expected_dialogs=1),
            _deferred(
                "microphone_release_after_restart",
                "must be checked after marker-controlled vendor restart",
            ),
            self._python_import("aiohttp"),
            self._python_import("rclpy"),
            self._python_import(
                "lingze_msgs",
                "from lingze_msgs.msg import PcmAudioFrame",
            ),
        ]
        checks.append(self._robot_config_stability_check(robot_snapshot))
        return CheckReport("switch", tuple(checks))

    def _rollback(self) -> CheckReport:
        _namespace, robot_checks, robot_snapshot = self._robot_identity_checks()
        robot_checks.append(self._robot_config_stability_check(robot_snapshot))
        return CheckReport("rollback", tuple(robot_checks))

    def run(self, mode: str) -> CheckReport:
        if mode == "build":
            return self._build()
        if mode == "service":
            return self._service()
        if mode == "switch":
            return self._switch()
        if mode == "rollback":
            return self._rollback()
        raise ValueError(f"unsupported preflight mode: {mode}")


class DeploymentVerifier:
    def __init__(
        self,
        paths: DeploymentPaths,
        runner: CommandRunner | None = None,
        graph_probe: GraphProbe | None = None,
        *,
        timeout: float = 5.0,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.paths = paths
        self.runner = runner or SubprocessCommandRunner()
        self.graph_probe = graph_probe or RclpyGraphProbe()
        self.timeout = timeout
        self.state = _StateReader(
            paths,
            self.runner,
            self.graph_probe,
            timeout=timeout,
            environ=dict(os.environ if environ is None else environ),
        )

    def verify(self, mode: str) -> CheckReport:
        if mode not in {"vendor-mode", "transition", "yandex-mode"}:
            raise ValueError(f"unsupported verification mode: {mode}")
        inspector = DeploymentInspector(
            self.paths,
            self.runner,
            self.graph_probe,
            timeout=self.timeout,
            environ=self.state.environ,
        )
        namespace, robot_checks, robot_snapshot = inspector._robot_identity_checks()
        nodes, _graph_error = self.state.nodes()
        vendor_running = self.state.vendor_dialog_running()
        yandex_running = self.state.yandex_dialog_running()
        yandex_active = self.state.service_active(YANDEX_SERVICE)
        vendor_active = self.state.service_active(VENDOR_SERVICE)
        expected_dialogs = 0 if mode == "transition" else 1
        marker_expected = mode != "vendor-mode"
        vendor_expected = mode == "vendor-mode"
        yandex_expected = mode == "yandex-mode"
        checks = [
            *robot_checks,
            _pass("marker", "expected state")
            if self.paths.marker.exists() == marker_expected
            else _fail("marker", "unexpected marker state"),
            _expected_state(
                "vendor_service",
                vendor_active,
                True,
                pass_detail="active",
                mismatch_detail="inactive",
            ),
            _expected_state(
                "vendor_dialog",
                vendor_running,
                vendor_expected,
                pass_detail="expected state",
                mismatch_detail="unexpected process state",
            ),
            _expected_state(
                "yandex_service",
                yandex_active,
                yandex_expected,
                pass_detail="expected state",
                mismatch_detail="unexpected service state",
            ),
            _expected_state(
                "yandex_dialog",
                yandex_running,
                yandex_expected,
                pass_detail="expected process state",
                mismatch_detail="unexpected process state",
            ),
            _pass("mutual_exclusion", "at most one dialog implementation active")
            if vendor_running is not None
            and yandex_running is not None
            and not (vendor_running and yandex_running)
            else _fail(
                "mutual_exclusion",
                "UNKNOWN process state"
                if vendor_running is None or yandex_running is None
                else "vendor and Yandex dialogs are both active",
            ),
            *inspector._graph_checks(nodes, namespace, expected_dialogs=expected_dialogs),
        ]
        if mode == "transition":
            checks.append(
                _expected_state(
                    "microphone_free",
                    self.state.microphone_free(),
                    True,
                    pass_detail="capture device released",
                    mismatch_detail="capture device occupied",
                )
            )
        if mode == "yandex-mode":
            checks.append(
                _pass("yandex_process_owner", "service MainPID runs project executable")
                if self.state.yandex_process_owned()
                else _fail("yandex_process_owner", "service process ownership not proven")
            )
        checks.append(inspector._robot_config_stability_check(robot_snapshot))
        return CheckReport(mode, tuple(checks))
