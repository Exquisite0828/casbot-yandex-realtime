"""Bounded switch and rollback transactions with one automatic rollback."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Callable, Protocol

from .checks import (
    CheckReport,
    CommandRunner,
    DeploymentInspector,
    DeploymentVerifier,
    SubprocessCommandRunner,
    VENDOR_EXECUTABLES,
    VENDOR_SERVICE,
    YANDEX_EXECUTABLE,
    YANDEX_SERVICE,
)
from .paths import DeploymentPaths
from .robot_config import (
    RobotConfigError,
    RobotConfigSnapshot,
    require_jijia_runtime_snapshot,
)
from .vendor_gate import DeploymentError


@dataclass(frozen=True)
class TransactionResult:
    success: bool
    changed: bool
    message: str
    rollback_attempted: bool = False
    rollback_success: bool | None = None


class Preflight(Protocol):
    def run(self, mode: str) -> CheckReport: ...


class Verifier(Protocol):
    def verify(self, mode: str) -> CheckReport: ...


@dataclass(frozen=True)
class ReadinessPolicy:
    retryable_failures: frozenset[str]
    stable_passes: int


READINESS_POLICIES = {
    "transition": ReadinessPolicy(
        frozenset(
            {
                "vendor_dialog",
                "dialog_node_count",
                "speaker_node",
                "microphone_free",
            }
        ),
        stable_passes=2,
    ),
    "service": ReadinessPolicy(
        frozenset(
            {
                "vendor_dialog_absent",
                "dialog_node_count",
                "speaker_node",
                "microphone_free",
            }
        ),
        stable_passes=1,
    ),
    "yandex-mode": ReadinessPolicy(
        frozenset(
            {
                "yandex_dialog",
                "dialog_node_count",
                "speaker_node",
                "yandex_process_owner",
            }
        ),
        stable_passes=2,
    ),
    "vendor-mode": ReadinessPolicy(
        frozenset(
            {
                "vendor_dialog",
                "dialog_node_count",
                "speaker_node",
            }
        ),
        stable_passes=2,
    ),
}


class ReadinessError(DeploymentError):
    def __init__(
        self,
        mode: str,
        reason: str,
        report: CheckReport,
        *,
        stable_passes: int,
        required_stable_passes: int,
    ) -> None:
        self.mode = mode
        self.reason = reason
        self.report = report
        self.stable_passes = stable_passes
        self.required_stable_passes = required_stable_passes
        super().__init__(self._render())

    def _render(self) -> str:
        return (
            f"{self.mode} readiness {self.reason}; stable passes "
            f"{self.stable_passes}/{self.required_stable_passes}\n"
            f"last report:\n{self.report.render_text()}"
        )


@dataclass(frozen=True)
class RecoveryState:
    marker: str
    vendor_service: str
    yandex_service: str
    vendor_dialog: str
    yandex_dialog: str

    def render(self) -> str:
        return " ".join(
            (
                f"marker={self.marker}",
                f"vendor_service={self.vendor_service}",
                f"yandex_service={self.yandex_service}",
                f"vendor_dialog={self.vendor_dialog}",
                f"yandex_dialog={self.yandex_dialog}",
            )
        )


def resolve_probe_timeout(timeout: float, probe_timeout: float | None) -> float:
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    value = min(5.0, timeout / 2.0) if probe_timeout is None else probe_timeout
    if value <= 0:
        raise ValueError("probe_timeout must be greater than zero")
    if value >= timeout:
        raise ValueError("probe_timeout must be smaller than timeout")
    return value


class ReadinessWaiter:
    """Shared bounded polling for switch, rollback, and service startup."""

    def __init__(
        self,
        *,
        timeout: float,
        probe_timeout: float,
        poll_interval: float,
        stable_passes: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if probe_timeout <= 0:
            raise ValueError("probe_timeout must be greater than zero")
        if probe_timeout >= timeout:
            raise ValueError("probe_timeout must be smaller than timeout")
        if poll_interval < 0:
            raise ValueError("poll_interval must not be negative")
        if stable_passes < 1:
            raise ValueError("stable_passes must be at least one")
        self.timeout = timeout
        self.probe_timeout = probe_timeout
        self.poll_interval = poll_interval
        self.stable_passes = stable_passes
        self._sleep = sleeper
        self._monotonic = monotonic

    @staticmethod
    def _is_hard_failure(policy: ReadinessPolicy, report: CheckReport) -> bool:
        return any(
            check.name not in policy.retryable_failures
            or "UNKNOWN" in check.detail.upper()
            for check in report.failures
        )

    def wait_for_report(
        self,
        mode: str,
        probe: Callable[[str], CheckReport],
    ) -> CheckReport:
        try:
            policy = READINESS_POLICIES[mode]
        except KeyError as error:
            raise ValueError(f"unsupported readiness mode: {mode}") from error
        required_stable_passes = (
            policy.stable_passes if mode == "service" else self.stable_passes
        )
        deadline = self._monotonic() + self.timeout
        stable_passes = 0
        last_report: CheckReport | None = None
        while True:
            remaining = deadline - self._monotonic()
            if last_report is not None and remaining < self.probe_timeout:
                raise ReadinessError(
                    mode,
                    "timed out",
                    last_report,
                    stable_passes=stable_passes,
                    required_stable_passes=required_stable_passes,
                )
            report = probe(mode)
            last_report = report
            if report.ok:
                stable_passes += 1
                if stable_passes >= required_stable_passes:
                    return report
            else:
                stable_passes = 0
                if self._is_hard_failure(policy, report):
                    raise ReadinessError(
                        mode,
                        "hard failure",
                        report,
                        stable_passes=stable_passes,
                        required_stable_passes=required_stable_passes,
                    )
            remaining = deadline - self._monotonic()
            latest_next_probe = remaining - self.probe_timeout
            if latest_next_probe <= 0:
                raise ReadinessError(
                    mode,
                    "timed out",
                    report,
                    stable_passes=stable_passes,
                    required_stable_passes=required_stable_passes,
                )
            interval = self.poll_interval if self.poll_interval > 0 else 0.001
            self._sleep(min(interval, latest_next_probe))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class _TransactionBase:
    def __init__(
        self,
        paths: DeploymentPaths,
        runner: CommandRunner,
        verifier: Verifier,
        *,
        timeout: float,
        probe_timeout: float,
        poll_interval: float,
        stable_passes: int,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if poll_interval < 0:
            raise ValueError("poll_interval must not be negative")
        if stable_passes < 1:
            raise ValueError("stable_passes must be at least one")
        self.paths = paths
        self.runner = runner
        self.verifier = verifier
        self.timeout = timeout
        self.probe_timeout = probe_timeout
        self.poll_interval = poll_interval
        self.stable_passes = stable_passes
        self._sleep = sleeper
        self._monotonic = monotonic
        self._readiness = ReadinessWaiter(
            timeout=timeout,
            probe_timeout=probe_timeout,
            poll_interval=poll_interval,
            stable_passes=stable_passes,
            sleeper=sleeper,
            monotonic=monotonic,
        )

    def _wait_for_report(
        self,
        mode: str,
        probe: Callable[[str], CheckReport],
    ) -> CheckReport:
        return self._readiness.wait_for_report(mode, probe)

    def _wait_for_verification(self, mode: str) -> CheckReport:
        return self._wait_for_report(mode, self.verifier.verify)

    def _require_apply_authority(self, maintenance_window: bool) -> None:
        if not maintenance_window:
            raise DeploymentError("--maintenance-window is required with --apply")
        if self.paths.is_real_root and os.geteuid() != 0:
            raise DeploymentError("real-root apply requires root privileges")

    @contextmanager
    def _operation_lock(self):
        state_dir = self.paths.operation_state_dir
        created = False
        try:
            state_dir.mkdir(parents=True, mode=0o700)
            created = True
        except FileExistsError:
            pass
        metadata = state_dir.lstat()
        expected_uid = 0 if self.paths.is_real_root else os.geteuid()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_mode & 0o022
        ):
            raise DeploymentError("operation-state directory ownership or mode is unsafe")
        if created:
            os.chmod(state_dir, 0o700)

        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.paths.operation_lock, flags, 0o600)
        except OSError as error:
            raise DeploymentError("unable to open shared deployment operation lock") from error
        try:
            lock_metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(lock_metadata.st_mode)
                or lock_metadata.st_uid != expected_uid
                or lock_metadata.st_mode & 0o077
            ):
                raise DeploymentError("deployment operation lock ownership or mode is unsafe")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DeploymentError(
                    "another deployment operation is already in progress"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _command(self, *args: str) -> None:
        result = self.runner.run(args, timeout=self.timeout)
        if result.returncode != 0:
            raise DeploymentError(
                f"command failed with status {result.returncode}: {' '.join(args)}"
            )

    def _service_active(self, service: str) -> bool:
        result = self.runner.run(
            ("systemctl", "is-active", service), timeout=self.probe_timeout
        )
        if result.returncode == 0:
            return True
        if result.returncode == 3:
            return False
        raise DeploymentError(
            f"unable to determine service state for {service}; status={result.returncode}"
        )

    def _wait_service(self, service: str, *, active: bool) -> None:
        deadline = self._monotonic() + self.timeout
        attempted = False
        while True:
            remaining = deadline - self._monotonic()
            if attempted and remaining < self.probe_timeout:
                state = "active" if active else "inactive"
                raise DeploymentError(f"timed out waiting for {service} to become {state}")
            if self._service_active(service) == active:
                return
            attempted = True
            remaining = deadline - self._monotonic()
            latest_next_probe = remaining - self.probe_timeout
            if latest_next_probe <= 0:
                state = "active" if active else "inactive"
                raise DeploymentError(f"timed out waiting for {service} to become {state}")
            interval = self.poll_interval if self.poll_interval > 0 else 0.001
            self._sleep(min(interval, latest_next_probe))

    def _require_yandex_dialog_absent(self) -> None:
        result = self.runner.run(
            ("pgrep", "-af", YANDEX_EXECUTABLE), timeout=self.probe_timeout
        )
        if result.returncode == 1:
            return
        if result.returncode == 0:
            raise DeploymentError("Yandex dialog process is still running")
        raise DeploymentError(
            "unable to prove that the Yandex dialog process has exited; "
            f"status={result.returncode}"
        )

    def _probe_service_state(self, service: str) -> str:
        try:
            return "active" if self._service_active(service) else "inactive"
        except Exception:
            return "unknown"

    def _probe_dialog_state(self, executables: tuple[str, ...]) -> str:
        running = False
        for executable in executables:
            try:
                result = self.runner.run(
                    ("pgrep", "-af", executable), timeout=self.probe_timeout
                )
            except Exception:
                return "unknown"
            if result.returncode == 0:
                running = True
            elif result.returncode != 1:
                return "unknown"
        return "present" if running else "absent"

    def _recovery_state(self) -> RecoveryState:
        return RecoveryState(
            marker="present" if self.paths.marker.exists() else "absent",
            vendor_service=self._probe_service_state(VENDOR_SERVICE),
            yandex_service=self._probe_service_state(YANDEX_SERVICE),
            vendor_dialog=self._probe_dialog_state(VENDOR_EXECUTABLES),
            yandex_dialog=self._probe_dialog_state((YANDEX_EXECUTABLE,)),
        )

    @staticmethod
    def _recovery_guidance(state: RecoveryState) -> str:
        if state.yandex_dialog != "absent" or state.yandex_service != "inactive":
            reason = (
                "Yandex dialog absence is not proven"
                if state.yandex_dialog != "absent"
                else "Yandex service inactivity is not proven"
            )
            if state.marker == "present":
                return (
                    f"{reason}; retain the marker and do "
                    f"not restart {VENDOR_SERVICE}"
                )
            return (
                f"{reason} and marker is absent; do not "
                f"restart {VENDOR_SERVICE} until safe gating is restored and all "
                "matching Yandex dialog PIDs are proven absent"
            )
        if state.marker == "present":
            return (
                "Yandex dialog is absent; remove the marker only after user_config "
                "again proves robot_current_mode=jijia and a supported current_llm, "
                f"then restart {VENDOR_SERVICE} and verify vendor-mode"
            )
        if (
            state.vendor_dialog == "present"
            and state.vendor_service == "active"
            and state.yandex_service == "inactive"
        ):
            return "vendor process appears restored; vendor-mode verification is still required"
        return (
            "marker is absent and Yandex dialog is absent; continue read-only "
            "diagnostics and prove vendor-mode before any further change"
        )

    def _recovery_failure_detail(self, detail: str) -> str:
        state = self._recovery_state()
        return (
            f"{detail}; final state: {state.render()}; "
            f"guidance: {self._recovery_guidance(state)}"
        )

    def _create_marker(self) -> bool:
        if self.paths.marker.exists():
            return False
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temp = tempfile.mkstemp(
            prefix=f".{self.paths.marker.name}.",
            dir=str(self.paths.marker.parent),
        )
        temp = Path(raw_temp)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(b"external dialog enabled\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, 0o644)
            os.replace(temp, self.paths.marker)
            _fsync_directory(self.paths.marker.parent)
            return True
        finally:
            if temp.exists():
                temp.unlink()

    def _remove_marker(self) -> bool:
        try:
            self.paths.marker.unlink()
        except FileNotFoundError:
            return False
        _fsync_directory(self.paths.marker.parent)
        return True

    def _safe_robot_config_snapshot(self) -> RobotConfigSnapshot:
        try:
            return require_jijia_runtime_snapshot(self.paths.user_config)
        except RobotConfigError as error:
            raise DeploymentError(f"user_config guard failed: {error}") from error

    def _restore_marker_after_config_change(self, detail: str) -> None:
        try:
            if not self.paths.marker.exists():
                self._create_marker()
        except Exception as error:
            raise DeploymentError(
                f"{detail}; CRITICAL: marker restoration failed"
            ) from error
        if not self.paths.marker.exists():
            raise DeploymentError(f"{detail}; CRITICAL: marker was not restored")
        raise DeploymentError(f"{detail}; marker restored and vendor restart blocked")

    def _remove_marker_with_config_guard(self) -> RobotConfigSnapshot:
        before = self._safe_robot_config_snapshot()
        removed = self._remove_marker()
        try:
            after = self._safe_robot_config_snapshot()
            if after.sha256 != before.sha256:
                raise DeploymentError("user_config changed while removing marker")
        except DeploymentError as error:
            if removed:
                self._restore_marker_after_config_change(str(error))
            raise
        return after

    def _require_config_unchanged_before_vendor_restart(
        self, expected: RobotConfigSnapshot
    ) -> None:
        try:
            current = self._safe_robot_config_snapshot()
            if current.sha256 != expected.sha256:
                raise DeploymentError("user_config changed before vendor restart")
        except DeploymentError as error:
            self._restore_marker_after_config_change(str(error))


class SwitchController(_TransactionBase):
    def __init__(
        self,
        paths: DeploymentPaths,
        runner: CommandRunner | None = None,
        preflight: Preflight | None = None,
        verifier: Verifier | None = None,
        *,
        timeout: float = 30.0,
        probe_timeout: float | None = None,
        poll_interval: float = 0.5,
        stable_passes: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        actual_runner = runner or SubprocessCommandRunner()
        actual_probe_timeout = resolve_probe_timeout(timeout, probe_timeout)
        actual_verifier = verifier or DeploymentVerifier(
            paths, actual_runner, timeout=actual_probe_timeout
        )
        super().__init__(
            paths,
            actual_runner,
            actual_verifier,
            timeout=timeout,
            probe_timeout=actual_probe_timeout,
            poll_interval=poll_interval,
            stable_passes=stable_passes,
            sleeper=sleeper,
            monotonic=monotonic,
        )
        self.preflight = preflight or DeploymentInspector(
            paths, actual_runner, timeout=actual_probe_timeout
        )

    @staticmethod
    def plan() -> tuple[str, ...]:
        return (
            "preflight switch",
            "stop Yandex service",
            "atomically create external-dialog marker",
            "restart vendor main service",
            "verify transition",
            "preflight Yandex service start",
            "start Yandex service",
            "verify yandex-mode",
        )

    def run(
        self, *, apply: bool = False, maintenance_window: bool = False
    ) -> TransactionResult:
        if not apply:
            return TransactionResult(
                True,
                False,
                "DRY-RUN: " + " -> ".join(self.plan()),
            )
        self._require_apply_authority(maintenance_window)
        with self._operation_lock():
            return self._run_apply()

    def _run_apply(self) -> TransactionResult:
        preflight = self.preflight.run("switch")
        if not preflight.ok:
            return TransactionResult(
                False,
                False,
                f"switch preflight failed\n{preflight.render_text()}",
            )

        try:
            self._command("systemctl", "stop", YANDEX_SERVICE)
            self._wait_service(YANDEX_SERVICE, active=False)
        except Exception as error:
            return TransactionResult(
                False,
                False,
                f"switch failed before marker creation: {error}",
            )
        try:
            marker_created = self._create_marker()
        except Exception as error:
            if self.paths.marker.exists():
                return self._fail_with_automatic_rollback(error)
            return TransactionResult(
                False,
                False,
                f"switch failed before marker creation completed: {error}",
            )
        if not marker_created:
            return TransactionResult(False, False, "switch refused: marker already exists")

        try:
            self._command("systemctl", "restart", VENDOR_SERVICE)
            self._wait_service(VENDOR_SERVICE, active=True)
            self._wait_for_verification("transition")
            self._wait_for_report("service", self.preflight.run)
            self._command("systemctl", "start", YANDEX_SERVICE)
            self._wait_service(YANDEX_SERVICE, active=True)
            self._wait_for_verification("yandex-mode")
        except Exception as error:
            return self._fail_with_automatic_rollback(error)
        return TransactionResult(True, True, "Yandex mode established; human acceptance required")

    def _fail_with_automatic_rollback(self, error: BaseException) -> TransactionResult:
        rollback_success, rollback_detail = self._automatic_rollback()
        original = str(error) or type(error).__name__
        if rollback_success:
            message = (
                f"switch failed: {original}; vendor mode restored by one automatic rollback"
            )
        else:
            message = (
                f"CRITICAL: switch failed: {original}; automatic rollback not proven: "
                f"{rollback_detail}"
            )
        return TransactionResult(
            False,
            True,
            message,
            rollback_attempted=True,
            rollback_success=rollback_success,
        )

    def _automatic_rollback(self) -> tuple[bool, str]:
        try:
            self._command("systemctl", "stop", YANDEX_SERVICE)
            self._wait_service(YANDEX_SERVICE, active=False)
            self._require_yandex_dialog_absent()
        except Exception as error:
            return False, self._recovery_failure_detail(f"stop Yandex: {error}")
        rollback_preflight = self.preflight.run("rollback")
        if not rollback_preflight.ok:
            return (
                False,
                self._recovery_failure_detail(
                    "rollback preflight failed; vendor restart not attempted\n"
                    f"{rollback_preflight.render_text()}"
                ),
            )
        try:
            config_snapshot = self._remove_marker_with_config_guard()
            self._require_config_unchanged_before_vendor_restart(config_snapshot)
        except Exception as error:
            return False, self._recovery_failure_detail(
                f"remove marker: {error}; vendor restart not attempted"
            )
        errors: list[str] = []
        try:
            self._command("systemctl", "restart", VENDOR_SERVICE)
            self._wait_service(VENDOR_SERVICE, active=True)
        except Exception as error:
            errors.append(f"restart vendor: {error}")
        try:
            self._wait_for_verification("vendor-mode")
        except Exception as error:
            errors.append(f"verify vendor-mode: {error}")
        if errors:
            return False, self._recovery_failure_detail("; ".join(errors))
        return True, "vendor-mode verified"


class RollbackController(_TransactionBase):
    def __init__(
        self,
        paths: DeploymentPaths,
        runner: CommandRunner | None = None,
        verifier: Verifier | None = None,
        *,
        preflight: Preflight | None = None,
        timeout: float = 30.0,
        probe_timeout: float | None = None,
        poll_interval: float = 0.5,
        stable_passes: int = 2,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        actual_runner = runner or SubprocessCommandRunner()
        actual_probe_timeout = resolve_probe_timeout(timeout, probe_timeout)
        actual_verifier = verifier or DeploymentVerifier(
            paths, actual_runner, timeout=actual_probe_timeout
        )
        super().__init__(
            paths,
            actual_runner,
            actual_verifier,
            timeout=timeout,
            probe_timeout=actual_probe_timeout,
            poll_interval=poll_interval,
            stable_passes=stable_passes,
            sleeper=sleeper,
            monotonic=monotonic,
        )
        self.preflight = preflight or DeploymentInspector(
            paths, actual_runner, timeout=actual_probe_timeout
        )

    @staticmethod
    def plan() -> tuple[str, ...]:
        return (
            "preflight rollback",
            "stop Yandex service",
            "verify transition",
            "atomically remove external-dialog marker",
            "restart vendor main service",
            "verify vendor-mode",
        )

    def run(
        self, *, apply: bool = False, maintenance_window: bool = False
    ) -> TransactionResult:
        if not apply:
            return TransactionResult(
                True,
                False,
                "DRY-RUN: " + " -> ".join(self.plan()),
            )
        self._require_apply_authority(maintenance_window)
        with self._operation_lock():
            return self._run_apply()

    def _run_apply(self) -> TransactionResult:
        preflight = self.preflight.run("rollback")
        if not preflight.ok:
            return TransactionResult(
                False,
                False,
                f"rollback preflight failed\n{preflight.render_text()}",
            )
        if not self.paths.marker.exists() and not self._service_active(YANDEX_SERVICE):
            report = self.verifier.verify("vendor-mode")
            if report.ok:
                return TransactionResult(True, False, "already in verified vendor-mode")

        try:
            self._command("systemctl", "stop", YANDEX_SERVICE)
            self._wait_service(YANDEX_SERVICE, active=False)
            self._require_yandex_dialog_absent()
            if self.paths.marker.exists():
                self._wait_for_verification("transition")
            config_snapshot = self._remove_marker_with_config_guard()
            self._require_config_unchanged_before_vendor_restart(config_snapshot)
            self._command("systemctl", "restart", VENDOR_SERVICE)
            self._wait_service(VENDOR_SERVICE, active=True)
            self._wait_for_verification("vendor-mode")
        except Exception as error:
            detail = self._recovery_failure_detail(str(error))
            return TransactionResult(
                False,
                True,
                f"CRITICAL: rollback incomplete: {detail}",
            )
        return TransactionResult(
            True,
            True,
            "vendor-mode restored; perform vendor human voice acceptance",
        )
