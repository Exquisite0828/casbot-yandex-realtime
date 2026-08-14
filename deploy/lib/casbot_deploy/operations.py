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
        poll_interval: float,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if poll_interval < 0:
            raise ValueError("poll_interval must not be negative")
        self.paths = paths
        self.runner = runner
        self.verifier = verifier
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._sleep = sleeper
        self._monotonic = monotonic

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
            ("systemctl", "is-active", service), timeout=self.timeout
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
        while True:
            if self._service_active(service) == active:
                return
            if self._monotonic() >= deadline:
                state = "active" if active else "inactive"
                raise DeploymentError(f"timed out waiting for {service} to become {state}")
            self._sleep(min(self.poll_interval, max(0.0, deadline - self._monotonic())))

    def _verify(self, mode: str) -> None:
        report = self.verifier.verify(mode)
        if not report.ok:
            raise DeploymentError(f"{mode} verification failed")

    def _require_yandex_dialog_absent(self) -> None:
        result = self.runner.run(
            ("pgrep", "-af", YANDEX_EXECUTABLE), timeout=self.timeout
        )
        if result.returncode == 1:
            return
        if result.returncode == 0:
            raise DeploymentError("Yandex dialog process is still running")
        raise DeploymentError(
            "unable to prove that the Yandex dialog process has exited; "
            f"status={result.returncode}"
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
        poll_interval: float = 0.5,
    ) -> None:
        actual_runner = runner or SubprocessCommandRunner()
        actual_verifier = verifier or DeploymentVerifier(paths, actual_runner)
        super().__init__(
            paths,
            actual_runner,
            actual_verifier,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        self.preflight = preflight or DeploymentInspector(paths, actual_runner)

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
            return TransactionResult(False, False, "switch preflight failed")

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
            self._verify("transition")
            service_preflight = self.preflight.run("service")
            if not service_preflight.ok:
                raise DeploymentError("Yandex service preflight failed")
            self._command("systemctl", "start", YANDEX_SERVICE)
            self._wait_service(YANDEX_SERVICE, active=True)
            self._verify("yandex-mode")
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
                f"CRITICAL: switch failed: {original}; automatic rollback failed: "
                f"{rollback_detail}. Manually stop {YANDEX_SERVICE} and prove all "
                "matching Yandex dialog PIDs are absent. Until then, retain the marker "
                f"and do not restart {VENDOR_SERVICE}. Only after that proof, remove "
                "the marker only after user_config again proves robot_current_mode=jijia "
                "and a supported current_llm; then restart "
                f"{VENDOR_SERVICE} and verify vendor-mode"
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
            return False, f"stop Yandex: {error}; marker retained to keep vendor gated"
        rollback_preflight = self.preflight.run("rollback")
        if not rollback_preflight.ok:
            return (
                False,
                "rollback preflight failed; marker retained and vendor restart not attempted",
            )
        try:
            config_snapshot = self._remove_marker_with_config_guard()
            self._require_config_unchanged_before_vendor_restart(config_snapshot)
        except Exception as error:
            return False, f"remove marker: {error}; vendor restart not attempted"
        errors: list[str] = []
        try:
            self._command("systemctl", "restart", VENDOR_SERVICE)
            self._wait_service(VENDOR_SERVICE, active=True)
        except Exception as error:
            errors.append(f"restart vendor: {error}")
        try:
            self._verify("vendor-mode")
        except Exception as error:
            errors.append(f"verify vendor-mode: {error}")
        return (not errors, "; ".join(errors) if errors else "vendor-mode verified")


class RollbackController(_TransactionBase):
    def __init__(
        self,
        paths: DeploymentPaths,
        runner: CommandRunner | None = None,
        verifier: Verifier | None = None,
        *,
        preflight: Preflight | None = None,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> None:
        actual_runner = runner or SubprocessCommandRunner()
        actual_verifier = verifier or DeploymentVerifier(paths, actual_runner)
        super().__init__(
            paths,
            actual_runner,
            actual_verifier,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        self.preflight = preflight or DeploymentInspector(paths, actual_runner)

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
            return TransactionResult(False, False, "rollback preflight failed")
        if not self.paths.marker.exists() and not self._service_active(YANDEX_SERVICE):
            report = self.verifier.verify("vendor-mode")
            if report.ok:
                return TransactionResult(True, False, "already in verified vendor-mode")

        try:
            self._command("systemctl", "stop", YANDEX_SERVICE)
            self._wait_service(YANDEX_SERVICE, active=False)
            self._require_yandex_dialog_absent()
            if self.paths.marker.exists():
                self._verify("transition")
            config_snapshot = self._remove_marker_with_config_guard()
            self._require_config_unchanged_before_vendor_restart(config_snapshot)
            self._command("systemctl", "restart", VENDOR_SERVICE)
            self._wait_service(VENDOR_SERVICE, active=True)
            self._verify("vendor-mode")
        except Exception as error:
            return TransactionResult(
                False,
                True,
                (
                    f"CRITICAL: rollback incomplete: {error}. Manually stop "
                    f"{YANDEX_SERVICE} and prove all matching Yandex dialog PIDs are "
                    "absent. Until then, retain the marker and do not restart "
                    f"{VENDOR_SERVICE}. Only after that proof and after user_config "
                    "again proves robot_current_mode=jijia and a supported current_llm, "
                    f"remove {self.paths.marker_logical}, restart {VENDOR_SERVICE}, "
                    "and verify vendor-mode"
                ),
            )
        return TransactionResult(
            True,
            True,
            "vendor-mode restored; perform vendor human voice acceptance",
        )
