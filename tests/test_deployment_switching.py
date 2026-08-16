import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_LIB = REPO_ROOT / "deploy" / "lib"
if str(DEPLOY_LIB) not in sys.path:
    sys.path.insert(0, str(DEPLOY_LIB))

from casbot_deploy.checks import (
    CheckReport,
    CheckResult,
    CheckStatus,
    CommandResult,
    DeploymentVerifier,
)
from casbot_deploy.operations import (
    DeploymentError,
    RollbackController,
    SwitchController,
)
from casbot_deploy.paths import DeploymentPaths
from casbot_deploy.robot_config import RobotConfigError, require_jijia_runtime


class StateRunner:
    def __init__(self, paths: DeploymentPaths) -> None:
        self.paths = paths
        self.vendor_service_active = True
        self.vendor_dialog_running = True
        self.yandex_service_active = False
        self.yandex_dialog_running = False
        self.preserve_yandex_process_on_stop = False
        self.calls: list[tuple[tuple[str, ...], float, bool]] = []
        self.fail_once: tuple[str, ...] | None = None
        self.always_fail: set[tuple[str, ...]] = set()

    def run(self, args, *, timeout: float):
        command = tuple(str(value) for value in args)
        self.calls.append((command, timeout, self.paths.marker.exists()))
        if command in self.always_fail or command == self.fail_once:
            if command == self.fail_once:
                self.fail_once = None
            return CommandResult(command, 1, "", "scripted failure")
        if command == ("systemctl", "stop", "casbot-yandex-dialog.service"):
            self.yandex_service_active = False
            if not self.preserve_yandex_process_on_stop:
                self.yandex_dialog_running = False
        elif command == ("systemctl", "restart", "lingze_robot.service"):
            self.vendor_service_active = True
            self.vendor_dialog_running = not self.paths.marker.exists()
        elif command == ("systemctl", "start", "casbot-yandex-dialog.service"):
            self.yandex_service_active = True
            self.yandex_dialog_running = True
        elif command[:2] == ("systemctl", "is-active"):
            service = command[-1]
            active = (
                self.vendor_service_active
                if service == "lingze_robot.service"
                else self.yandex_service_active
            )
            return CommandResult(command, 0 if active else 3, "active\n" if active else "inactive\n", "")
        elif command and command[0] == "pgrep":
            if "/lingze/install/lingze_omni_s2s" in command[-1]:
                running = self.vendor_dialog_running
                pid = "100"
            elif "/lingze/install/lingze_s2s" in command[-1]:
                running = False
                pid = "101"
            else:
                running = self.yandex_dialog_running
                pid = "4321"
            return CommandResult(command, 0 if running else 1, f"{pid}\n" if running else "", "")
        elif command and command[0] == "fuser":
            return CommandResult(command, 1, "", "")
        elif command[:2] == ("systemctl", "show"):
            return CommandResult(command, 0, "4321\n", "")
        elif command and command[0] == "ps":
            return CommandResult(
                command,
                0,
                "/opt/casbot-yandex-realtime/install/realtime_dialog/realtime_dialog_node\n",
                "",
            )
        return CommandResult(command, 0, "", "")


class StateGraph:
    def __init__(self, runner: StateRunner, namespace: str) -> None:
        self.runner = runner
        self.namespace = namespace
        self.hide_speaker = False
        self.force_dialog_count: int | None = None

    def list_fully_qualified_nodes(self, *, timeout: float):
        nodes = [] if self.hide_speaker else [f"/{self.namespace}/audio_speaker_node"]
        if self.force_dialog_count is None:
            count = int(self.runner.vendor_dialog_running) + int(
                self.runner.yandex_dialog_running
            )
        else:
            count = self.force_dialog_count
        nodes.extend([f"/{self.namespace}/dialog_node"] * count)
        return nodes


class FakePreflight:
    def __init__(self, paths: DeploymentPaths) -> None:
        self.paths = paths
        self.ok = True
        self.fail_modes: set[str] = set()
        self.calls: list[str] = []
        self.after_run = None

    def run(self, mode: str) -> CheckReport:
        self.calls.append(mode)
        config_ok = True
        try:
            require_jijia_runtime(self.paths.user_config)
        except RobotConfigError:
            config_ok = False
        report = (
            CheckReport(mode, ())
            if self.ok and config_ok and mode not in self.fail_modes
            else CheckReport(
                mode,
                (CheckResult("scripted", CheckStatus.FAIL, "failure"),),
            )
        )
        if self.after_run is not None:
            self.after_run(mode)
        return report


class RecordingVerifier:
    def __init__(self, verifier: DeploymentVerifier) -> None:
        self.verifier = verifier
        self.calls: list[str] = []
        self.fail_modes: set[str] = set()
        self.after_verify = None

    def verify(self, mode: str):
        self.calls.append(mode)
        report = self.verifier.verify(mode)
        if self.after_verify is not None:
            self.after_verify(mode)
        if mode not in self.fail_modes:
            return report
        checks = tuple(
            check
            for check in report.checks
            if check.name != "speaker_node"
        )
        return CheckReport(
            mode,
            checks + (CheckResult("scripted_failure", CheckStatus.FAIL, "scripted"),),
        )


class DeploymentSwitchingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = DeploymentPaths(Path(self.temporary.name))
        self.namespace = "lzdl10823"
        self.paths.user_config.parent.mkdir(parents=True, exist_ok=True)
        self.paths.user_config.write_text(
            json.dumps(
                {
                    "namespace": self.namespace,
                    "robot_current_mode": "jijia",
                    "current_llm": "lingze_omni_s2s",
                }
            ),
            encoding="utf-8",
        )
        self.paths.capture_device.parent.mkdir(parents=True, exist_ok=True)
        self.paths.capture_device.touch()
        self.runner = StateRunner(self.paths)
        self.graph = StateGraph(self.runner, self.namespace)
        self.verifier = RecordingVerifier(
            DeploymentVerifier(self.paths, self.runner, self.graph)
        )
        self.preflight = FakePreflight(self.paths)
        self.switch = SwitchController(
            self.paths,
            self.runner,
            self.preflight,
            self.verifier,
            timeout=0.1,
            poll_interval=0,
        )
        self.rollback = RollbackController(
            self.paths,
            self.runner,
            self.verifier,
            timeout=0.1,
            poll_interval=0,
        )

    def test_switch_default_dry_run_has_zero_writes_and_zero_commands(self) -> None:
        result = self.switch.run(apply=False, maintenance_window=False)
        self.assertTrue(result.success)
        self.assertFalse(result.changed)
        self.assertIn("DRY-RUN", result.message)
        self.assertFalse(self.paths.marker.exists())
        self.assertFalse(self.paths.operation_state_dir.exists())
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.preflight.calls, [])

    def test_switch_apply_requires_explicit_maintenance_window(self) -> None:
        with self.assertRaisesRegex(DeploymentError, "maintenance-window"):
            self.switch.run(apply=True, maintenance_window=False)
        self.assertFalse(self.paths.marker.exists())
        self.assertEqual(self.runner.calls, [])

    def test_switch_creates_marker_then_verifies_transition_before_yandex_start(self) -> None:
        result = self.switch.run(apply=True, maintenance_window=True)
        self.assertTrue(result.success, result.message)
        self.assertTrue(self.paths.marker.exists())
        self.assertEqual(self.preflight.calls, ["switch", "service"])
        self.assertEqual(
            self.verifier.calls,
            ["transition", "transition", "yandex-mode", "yandex-mode"],
        )
        commands = [call[0] for call in self.runner.calls]
        restart_index = commands.index(("systemctl", "restart", "lingze_robot.service"))
        start_index = commands.index(("systemctl", "start", "casbot-yandex-dialog.service"))
        self.assertLess(restart_index, start_index)
        self.assertTrue(self.runner.calls[restart_index][2])
        self.assertEqual(
            list(self.paths.marker.parent.glob(".external-dialog.enabled.*")), []
        )

    def test_mode_change_after_transition_stops_with_marker_retained(self) -> None:
        def change_mode_after_transition(mode: str) -> None:
            if mode != "transition":
                return
            self.paths.user_config.write_text(
                json.dumps(
                    {
                        "namespace": self.namespace,
                        "robot_current_mode": "business",
                        "current_llm": "lingze_omni_s2s",
                    }
                ),
                encoding="utf-8",
            )

        self.verifier.after_verify = change_mode_after_transition

        result = self.switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertTrue(result.rollback_attempted)
        self.assertFalse(result.rollback_success)
        self.assertIn("CRITICAL", result.message)
        self.assertTrue(self.paths.marker.exists())
        self.assertEqual(self.preflight.calls, ["switch", "rollback"])
        commands = [call[0] for call in self.runner.calls]
        self.assertNotIn(("systemctl", "start", "casbot-yandex-dialog.service"), commands)
        self.assertEqual(
            commands.count(("systemctl", "restart", "lingze_robot.service")),
            1,
        )

    def test_automatic_rollback_rechecks_config_after_rollback_preflight(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.vendor_dialog_running = False

        def change_after_rollback_preflight(mode: str) -> None:
            if mode == "rollback":
                self.paths.user_config.write_text(
                    json.dumps(
                        {
                            "namespace": self.namespace,
                            "robot_current_mode": "business",
                            "current_llm": "lingze_omni_s2s",
                        }
                    ),
                    encoding="utf-8",
                )

        self.preflight.after_run = change_after_rollback_preflight

        success, detail = self.switch._automatic_rollback()

        self.assertFalse(success)
        self.assertIn("user_config", detail)
        self.assertTrue(self.paths.marker.exists())
        self.assertFalse(
            any(
                call[0] == ("systemctl", "restart", "lingze_robot.service")
                for call in self.runner.calls
            )
        )

    def test_automatic_rollback_restores_marker_if_config_changes_after_removal(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.vendor_dialog_running = False
        original_remove = self.switch._remove_marker

        def remove_then_change_mode() -> bool:
            removed = original_remove()
            self.paths.user_config.write_text(
                json.dumps(
                    {
                        "namespace": self.namespace,
                        "robot_current_mode": "business",
                        "current_llm": "lingze_omni_s2s",
                    }
                ),
                encoding="utf-8",
            )
            return removed

        with mock.patch.object(
            self.switch,
            "_remove_marker",
            side_effect=remove_then_change_mode,
        ):
            success, detail = self.switch._automatic_rollback()

        self.assertFalse(success)
        self.assertIn("marker restored", detail)
        self.assertTrue(self.paths.marker.exists())
        self.assertFalse(
            any(
                call[0] == ("systemctl", "restart", "lingze_robot.service")
                for call in self.runner.calls
            )
        )

    def test_automatic_rollback_rechecks_config_immediately_before_vendor_restart(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.vendor_dialog_running = False
        guarded_remove = self.switch._remove_marker_with_config_guard

        def guarded_remove_then_change_mode():
            snapshot = guarded_remove()
            self.paths.user_config.write_text(
                json.dumps(
                    {
                        "namespace": self.namespace,
                        "robot_current_mode": "business",
                        "current_llm": "lingze_omni_s2s",
                    }
                ),
                encoding="utf-8",
            )
            return snapshot

        with mock.patch.object(
            self.switch,
            "_remove_marker_with_config_guard",
            side_effect=guarded_remove_then_change_mode,
        ):
            success, detail = self.switch._automatic_rollback()

        self.assertFalse(success)
        self.assertIn("marker restored", detail)
        self.assertTrue(self.paths.marker.exists())
        self.assertFalse(
            any(
                call[0] == ("systemctl", "restart", "lingze_robot.service")
                for call in self.runner.calls
            )
        )

    def test_marker_post_replace_fsync_failure_triggers_one_automatic_rollback(self) -> None:
        calls = 0

        def fail_first_directory_fsync(_path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("scripted marker directory fsync failure")

        with mock.patch(
            "casbot_deploy.operations._fsync_directory",
            side_effect=fail_first_directory_fsync,
        ):
            result = self.switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertTrue(result.rollback_attempted)
        self.assertTrue(result.rollback_success, result.message)
        self.assertFalse(self.paths.marker.exists())
        self.assertTrue(self.runner.vendor_dialog_running)

    def test_switch_preflight_failure_does_not_create_marker_or_rollback(self) -> None:
        self.preflight.ok = False
        result = self.switch.run(apply=True, maintenance_window=True)
        self.assertFalse(result.success)
        self.assertFalse(result.rollback_attempted)
        self.assertFalse(self.paths.marker.exists())
        self.assertEqual(self.verifier.calls, [])

    def test_switch_refuses_unknown_service_state_before_marker(self) -> None:
        self.runner.always_fail.add(
            ("systemctl", "is-active", "casbot-yandex-dialog.service")
        )
        result = self.switch.run(apply=True, maintenance_window=True)
        self.assertFalse(result.success)
        self.assertFalse(result.rollback_attempted)
        self.assertFalse(self.paths.marker.exists())

    def test_each_post_marker_hard_failure_attempts_rollback_once(self) -> None:
        scenarios = (
            (("systemctl", "restart", "lingze_robot.service"), None),
            (None, "transition"),
            (("systemctl", "start", "casbot-yandex-dialog.service"), None),
            (None, "yandex-mode"),
        )
        for failed_command, failed_mode in scenarios:
            with self.subTest(command=failed_command, mode=failed_mode):
                self.setUp()
                if failed_command is not None:
                    self.runner.fail_once = failed_command
                if failed_mode is not None:
                    self.verifier.fail_modes.add(failed_mode)
                result = self.switch.run(apply=True, maintenance_window=True)
                self.assertFalse(result.success)
                self.assertTrue(result.rollback_attempted)
                self.assertTrue(result.rollback_success, result.message)
                self.assertFalse(self.paths.marker.exists())
                self.assertTrue(self.runner.vendor_dialog_running)
                self.assertIn("vendor mode restored", result.message)
                restart_calls = [
                    call
                    for call, _timeout, _marker in self.runner.calls
                    if call == ("systemctl", "restart", "lingze_robot.service")
                ]
                self.assertLessEqual(len(restart_calls), 2)

    def test_automatic_rollback_failure_is_critical_and_keeps_original_error(self) -> None:
        self.verifier.fail_modes.add("transition")
        self.runner.always_fail.add(
            ("systemctl", "restart", "lingze_robot.service")
        )
        result = self.switch.run(apply=True, maintenance_window=True)
        self.assertFalse(result.success)
        self.assertTrue(result.rollback_attempted)
        self.assertFalse(result.rollback_success)
        self.assertIn("CRITICAL", result.message)
        self.assertIn("systemctl restart lingze_robot.service", result.message)

    def test_automatic_rollback_never_ungates_vendor_when_yandex_stop_fails(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.yandex_service_active = True
        self.runner.yandex_dialog_running = True
        self.runner.always_fail.add(
            ("systemctl", "stop", "casbot-yandex-dialog.service")
        )

        success, detail = self.switch._automatic_rollback()

        self.assertFalse(success)
        self.assertIn("stop Yandex", detail)
        self.assertTrue(self.paths.marker.exists())
        self.assertFalse(
            any(
                call[0] == ("systemctl", "restart", "lingze_robot.service")
                for call in self.runner.calls
            )
        )

    def test_switch_never_calls_kill_or_pkill_and_all_commands_are_bounded(self) -> None:
        result = self.switch.run(apply=True, maintenance_window=True)
        self.assertTrue(result.success)
        commands = [call[0] for call in self.runner.calls]
        self.assertFalse(any(command[0] in {"kill", "pkill"} for command in commands))
        self.assertTrue(all(timeout > 0 for _command, timeout, _marker in self.runner.calls))

    def _enter_yandex_mode(self) -> None:
        result = self.switch.run(apply=True, maintenance_window=True)
        self.assertTrue(result.success, result.message)
        self.runner.calls.clear()
        self.verifier.calls.clear()
        self.preflight.calls.clear()

    def test_rollback_default_dry_run_has_zero_writes(self) -> None:
        self._enter_yandex_mode()
        before_calls = list(self.runner.calls)
        result = self.rollback.run(apply=False, maintenance_window=False)
        self.assertTrue(result.success)
        self.assertFalse(result.changed)
        self.assertTrue(self.paths.marker.exists())
        self.assertEqual(self.runner.calls, before_calls)

    def test_switch_and_rollback_refuse_apply_while_shared_operation_lock_is_held(self) -> None:
        self.paths.operation_state_dir.mkdir(parents=True)
        descriptor = os.open(
            self.paths.operation_lock,
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

        for controller in (self.switch, self.rollback):
            with self.subTest(controller=type(controller).__name__):
                before_calls = list(self.runner.calls)
                with self.assertRaisesRegex(DeploymentError, "operation.*progress"):
                    controller.run(apply=True, maintenance_window=True)
                self.assertEqual(self.runner.calls, before_calls)
                self.assertFalse(self.paths.marker.exists())

    def test_rollback_normal_order_restores_vendor_mode_without_gate_restore(self) -> None:
        self._enter_yandex_mode()
        result = self.rollback.run(apply=True, maintenance_window=True)
        self.assertTrue(result.success, result.message)
        self.assertFalse(self.paths.marker.exists())
        self.assertFalse(self.runner.yandex_service_active)
        self.assertTrue(self.runner.vendor_dialog_running)
        self.assertEqual(
            self.verifier.calls,
            ["transition", "transition", "vendor-mode", "vendor-mode"],
        )
        commands = [call[0] for call in self.runner.calls]
        self.assertLess(
            commands.index(("systemctl", "stop", "casbot-yandex-dialog.service")),
            commands.index(("systemctl", "restart", "lingze_robot.service")),
        )
        self.assertFalse(any("vendor-gate" in " ".join(command) for command in commands))

    def test_rollback_rejects_unsupported_current_llm_before_any_command(self) -> None:
        self._enter_yandex_mode()
        self.paths.user_config.write_text(
            json.dumps(
                {
                    "namespace": self.namespace,
                    "robot_current_mode": "jijia",
                    "current_llm": "unsupported_backend",
                }
            ),
            encoding="utf-8",
        )

        result = self.rollback.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertFalse(result.changed)
        self.assertIn("rollback preflight failed", result.message)
        self.assertEqual(self.runner.calls, [])
        self.assertTrue(self.paths.marker.exists())

    def test_rollback_restores_marker_when_config_changes_after_transition_verify(self) -> None:
        self._enter_yandex_mode()

        def change_after_transition(mode: str) -> None:
            if mode == "transition":
                self.paths.user_config.write_text(
                    json.dumps(
                        {
                            "namespace": self.namespace,
                            "robot_current_mode": "business",
                            "current_llm": "lingze_omni_s2s",
                        }
                    ),
                    encoding="utf-8",
                )

        self.verifier.after_verify = change_after_transition

        result = self.rollback.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertTrue(self.paths.marker.exists())
        self.assertFalse(
            any(
                call[0] == ("systemctl", "restart", "lingze_robot.service")
                for call in self.runner.calls
            )
        )

    def test_rollback_is_idempotent_when_already_vendor_mode(self) -> None:
        result = self.rollback.run(apply=True, maintenance_window=True)
        self.assertTrue(result.success)
        self.assertFalse(result.changed)
        self.assertEqual(self.verifier.calls, ["vendor-mode"])
        self.assertFalse(
            any(call[0][:2] == ("systemctl", "restart") for call in self.runner.calls)
        )

    def test_rollback_requires_maintenance_window_for_apply(self) -> None:
        self._enter_yandex_mode()
        with self.assertRaisesRegex(DeploymentError, "maintenance-window"):
            self.rollback.run(apply=True, maintenance_window=False)

    def test_rollback_does_not_restart_vendor_while_orphan_yandex_process_exists(self) -> None:
        self.runner.vendor_dialog_running = False
        self.runner.yandex_dialog_running = True
        self.runner.preserve_yandex_process_on_stop = True

        result = self.rollback.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertIn("CRITICAL", result.message)
        self.assertIn("marker=absent", result.message)
        self.assertIn("Yandex dialog absence is not proven", result.message)
        self.assertIn("matching Yandex dialog PIDs are proven absent", result.message)
        self.assertNotIn("retain the marker", result.message)
        self.assertFalse(
            any(
                call[0] == ("systemctl", "restart", "lingze_robot.service")
                for call in self.runner.calls
            )
        )


if __name__ == "__main__":
    unittest.main()
