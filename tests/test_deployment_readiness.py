import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_LIB = REPO_ROOT / "deploy" / "lib"
if str(DEPLOY_LIB) not in sys.path:
    sys.path.insert(0, str(DEPLOY_LIB))

from casbot_deploy import cli
from casbot_deploy.checks import CheckReport, CheckResult, CheckStatus, CommandResult
from casbot_deploy.operations import (
    ReadinessError,
    RollbackController,
    SwitchController,
    TransactionResult,
)
from casbot_deploy.paths import DeploymentPaths
from tests.test_deployment_switching import StateRunner


FAKE_SECRET = "phase8f-obviously-fake-api-key"


def passing_report(mode: str) -> CheckReport:
    return CheckReport(
        mode,
        (CheckResult("ready", CheckStatus.PASS, "complete snapshot"),),
    )


def failing_report(mode: str, name: str, detail: str = "not ready") -> CheckReport:
    return CheckReport(mode, (CheckResult(name, CheckStatus.FAIL, detail),))


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.value += duration


class SequencedVerifier:
    def __init__(self, **sequences: list[CheckReport]) -> None:
        self.sequences = {name: list(values) for name, values in sequences.items()}
        self.calls: list[str] = []

    def verify(self, mode: str) -> CheckReport:
        self.calls.append(mode)
        values = self.sequences.get(mode)
        if not values:
            return passing_report(mode)
        if len(values) > 1:
            return values.pop(0)
        return values[0]


class SequencedPreflight:
    def __init__(self, **sequences: list[CheckReport]) -> None:
        self.sequences = {name: list(values) for name, values in sequences.items()}
        self.calls: list[str] = []

    def run(self, mode: str) -> CheckReport:
        self.calls.append(mode)
        values = self.sequences.get(mode)
        if not values:
            return passing_report(mode)
        if len(values) > 1:
            return values.pop(0)
        return values[0]


class SecretFailureRunner(StateRunner):
    def run(self, args, *, timeout: float):
        command = tuple(str(value) for value in args)
        if command == ("systemctl", "start", "casbot-yandex-dialog.service"):
            self.calls.append((command, timeout, self.paths.marker.exists()))
            return CommandResult(command, 1, "", FAKE_SECRET)
        return super().run(args, timeout=timeout)


class DeploymentReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = DeploymentPaths(Path(self.temporary.name))
        self.paths.user_config.parent.mkdir(parents=True, exist_ok=True)
        self.paths.user_config.write_text(
            json.dumps(
                {
                    "namespace": "lzdl10823",
                    "robot_current_mode": "jijia",
                    "current_llm": "lingze_omni_s2s",
                }
            ),
            encoding="utf-8",
        )
        self.runner = StateRunner(self.paths)
        self.clock = FakeClock()

    def _switch(
        self,
        verifier: SequencedVerifier | None = None,
        preflight: SequencedPreflight | None = None,
        *,
        runner: StateRunner | None = None,
        timeout: float = 3.0,
    ) -> tuple[SwitchController, SequencedVerifier, SequencedPreflight]:
        actual_verifier = verifier or SequencedVerifier()
        actual_preflight = preflight or SequencedPreflight()
        controller = SwitchController(
            self.paths,
            runner or self.runner,
            actual_preflight,
            actual_verifier,
            timeout=timeout,
            probe_timeout=0.25,
            poll_interval=1.0,
            stable_passes=2,
            sleeper=self.clock.sleep,
            monotonic=self.clock.monotonic,
        )
        return controller, actual_verifier, actual_preflight

    def _rollback(
        self,
        verifier: SequencedVerifier | None = None,
        preflight: SequencedPreflight | None = None,
        *,
        timeout: float = 3.0,
    ) -> tuple[RollbackController, SequencedVerifier, SequencedPreflight]:
        actual_verifier = verifier or SequencedVerifier()
        actual_preflight = preflight or SequencedPreflight()
        controller = RollbackController(
            self.paths,
            self.runner,
            actual_verifier,
            preflight=actual_preflight,
            timeout=timeout,
            probe_timeout=0.25,
            poll_interval=1.0,
            stable_passes=2,
            sleeper=self.clock.sleep,
            monotonic=self.clock.monotonic,
        )
        return controller, actual_verifier, actual_preflight

    def _enter_yandex_state(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.vendor_dialog_running = False
        self.runner.yandex_service_active = True
        self.runner.yandex_dialog_running = True

    def test_default_components_receive_probe_timeout_not_stage_timeout(self) -> None:
        switch = SwitchController(
            self.paths,
            self.runner,
            timeout=30.0,
            probe_timeout=2.0,
        )
        rollback = RollbackController(
            self.paths,
            self.runner,
            timeout=30.0,
            probe_timeout=2.0,
        )

        for controller in (switch, rollback):
            self.assertEqual(controller.timeout, 30.0)
            self.assertEqual(controller.probe_timeout, 2.0)
            self.assertEqual(controller.verifier.timeout, 2.0)
            self.assertEqual(controller.preflight.timeout, 2.0)
            self.assertNotEqual(controller.verifier.timeout, controller.timeout)

    def test_cli_propagates_readiness_policy_to_switch_and_rollback(self) -> None:
        arguments = [
            "--root",
            str(self.paths.root),
            "--timeout",
            "41",
            "--probe-timeout",
            "3",
            "--poll-interval",
            "0.75",
            "--stable-passes",
            "3",
        ]
        for entrypoint, class_name in (
            (cli.switch_main, "SwitchController"),
            (cli.rollback_main, "RollbackController"),
        ):
            with self.subTest(entrypoint=entrypoint.__name__), mock.patch.object(
                cli, class_name
            ) as controller_class:
                controller_class.return_value.run.return_value = TransactionResult(
                    True, False, "dry-run"
                )
                self.assertEqual(entrypoint(arguments), 0)
                controller_class.assert_called_once_with(
                    self.paths,
                    timeout=41.0,
                    probe_timeout=3.0,
                    poll_interval=0.75,
                    stable_passes=3,
                )

    def test_transition_transient_failure_settles_without_rollback(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [
                    failing_report("transition", "dialog_node_count"),
                    passing_report("transition"),
                    passing_report("transition"),
                ]
            }
        )
        preflight = SequencedPreflight()
        switch = SwitchController(
            self.paths,
            self.runner,
            preflight,
            verifier,
            timeout=0.1,
            poll_interval=0,
        )

        result = switch.run(apply=True, maintenance_window=True)

        self.assertTrue(result.success, result.message)
        self.assertFalse(result.rollback_attempted)
        self.assertEqual(verifier.calls.count("transition"), 3)
        self.assertIn(
            ("systemctl", "start", "casbot-yandex-dialog.service"),
            [call[0] for call in self.runner.calls],
        )

    def test_transition_requires_consecutive_stable_passes(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [
                    passing_report("transition"),
                    failing_report("transition", "speaker_node"),
                    passing_report("transition"),
                    passing_report("transition"),
                ]
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertTrue(result.success, result.message)
        self.assertEqual(verifier.calls.count("transition"), 4)

    def test_transition_timeout_is_bounded_and_rolls_back_once(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [
                    failing_report(
                        "transition",
                        "dialog_node_count",
                        "expected 0; observed 1",
                    )
                ]
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertTrue(result.rollback_attempted)
        self.assertTrue(result.rollback_success, result.message)
        self.assertEqual(verifier.calls.count("transition"), 4)
        self.assertIn("transition readiness timed out", result.message)
        self.assertIn("FAIL dialog_node_count: expected 0; observed 1", result.message)

    def test_readiness_deadline_reserves_time_for_each_probe(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [
                    failing_report("transition", "dialog_node_count")
                ]
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        with self.assertRaises(ReadinessError):
            switch._wait_for_verification("transition")

        self.assertEqual(verifier.calls.count("transition"), 4)
        self.assertLessEqual(
            self.clock.value + switch.probe_timeout,
            switch.timeout,
        )

    def test_robot_config_drift_is_an_immediate_hard_failure(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [
                    failing_report("transition", "robot_config_stable"),
                    passing_report("transition"),
                ]
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertEqual(verifier.calls.count("transition"), 1)
        self.assertIn("hard failure", result.message)

    def test_dual_dialog_is_an_immediate_hard_failure(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [
                    failing_report("transition", "mutual_exclusion", "both active"),
                    passing_report("transition"),
                ]
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertEqual(verifier.calls.count("transition"), 1)
        self.assertIn("FAIL mutual_exclusion: both active", result.message)

    def test_service_preflight_transient_failure_can_recover(self) -> None:
        preflight = SequencedPreflight(
            service=[
                failing_report("service", "microphone_free"),
                passing_report("service"),
            ]
        )
        switch, _verifier, preflight = self._switch(preflight=preflight)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertTrue(result.success, result.message)
        self.assertEqual(preflight.calls.count("service"), 2)

    def test_service_preflight_static_failure_is_hard(self) -> None:
        preflight = SequencedPreflight(
            service=[
                failing_report("service", "env_security"),
                passing_report("service"),
            ]
        )
        switch, _verifier, preflight = self._switch(preflight=preflight)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertEqual(preflight.calls.count("service"), 1)
        self.assertIn("FAIL env_security", result.message)

    def test_yandex_mode_transient_failure_settles(self) -> None:
        verifier = SequencedVerifier(
            **{
                "yandex-mode": [
                    failing_report("yandex-mode", "yandex_process_owner"),
                    passing_report("yandex-mode"),
                    passing_report("yandex-mode"),
                ]
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertTrue(result.success, result.message)
        self.assertEqual(verifier.calls.count("yandex-mode"), 3)

    def test_yandex_mode_timeout_triggers_automatic_rollback(self) -> None:
        verifier = SequencedVerifier(
            **{
                "yandex-mode": [
                    failing_report("yandex-mode", "yandex_process_owner")
                ]
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertTrue(result.rollback_attempted)
        self.assertTrue(result.rollback_success, result.message)
        self.assertEqual(verifier.calls.count("yandex-mode"), 4)
        self.assertIn("yandex-mode readiness timed out", result.message)

    def test_vendor_dialog_reappearing_in_yandex_mode_is_hard(self) -> None:
        verifier = SequencedVerifier(
            **{
                "yandex-mode": [
                    failing_report("yandex-mode", "vendor_dialog"),
                    passing_report("yandex-mode"),
                ]
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertEqual(verifier.calls.count("yandex-mode"), 1)

    def test_unknown_process_ownership_is_an_immediate_hard_failure(self) -> None:
        verifier = SequencedVerifier(
            **{
                "yandex-mode": [
                    failing_report(
                        "yandex-mode",
                        "yandex_process_owner",
                        "UNKNOWN process ownership",
                    ),
                    passing_report("yandex-mode"),
                ]
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertEqual(verifier.calls.count("yandex-mode"), 1)
        self.assertIn("hard failure", result.message)

    def test_automatic_rollback_waits_for_vendor_mode_to_settle(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [failing_report("transition", "marker")],
                "vendor-mode": [
                    failing_report("vendor-mode", "vendor_dialog"),
                    passing_report("vendor-mode"),
                    passing_report("vendor-mode"),
                ],
            }
        )
        preflight = SequencedPreflight()
        switch = SwitchController(
            self.paths,
            self.runner,
            preflight,
            verifier,
            timeout=0.1,
            poll_interval=0,
        )

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertTrue(result.rollback_success, result.message)
        self.assertEqual(verifier.calls.count("vendor-mode"), 3)
        self.assertIn("vendor mode restored", result.message)

    def test_automatic_rollback_timeout_reports_marker_absent_final_state(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [failing_report("transition", "marker")],
                "vendor-mode": [
                    failing_report(
                        "vendor-mode",
                        "dialog_node_count",
                        "expected 1; observed 0",
                    )
                ],
            }
        )
        switch, verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertFalse(result.rollback_success)
        self.assertIn("automatic rollback not proven", result.message)
        self.assertIn("marker=absent", result.message)
        self.assertIn("vendor_service=active", result.message)
        self.assertIn("yandex_service=inactive", result.message)
        self.assertIn("FAIL dialog_node_count: expected 1; observed 0", result.message)
        self.assertNotIn("retain the marker", result.message)

    def test_unproven_yandex_exit_keeps_present_marker_and_blocks_vendor_restart(self) -> None:
        self.runner.yandex_dialog_running = True
        self.runner.preserve_yandex_process_on_stop = True
        verifier = SequencedVerifier(
            **{"transition": [failing_report("transition", "mutual_exclusion")]}
        )
        switch, _verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertFalse(result.rollback_success)
        self.assertTrue(self.paths.marker.exists())
        self.assertIn("marker=present", result.message)
        self.assertIn("retain the marker", result.message)
        restart_calls = [
            call[0]
            for call in self.runner.calls
            if call[0] == ("systemctl", "restart", "lingze_robot.service")
        ]
        self.assertEqual(len(restart_calls), 1)

    def test_normal_rollback_waits_for_transition_to_settle(self) -> None:
        self._enter_yandex_state()
        verifier = SequencedVerifier(
            **{
                "transition": [
                    failing_report("transition", "dialog_node_count"),
                    passing_report("transition"),
                    passing_report("transition"),
                ]
            }
        )
        rollback, verifier, _preflight = self._rollback(verifier)

        result = rollback.run(apply=True, maintenance_window=True)

        self.assertTrue(result.success, result.message)
        self.assertEqual(verifier.calls.count("transition"), 3)

    def test_normal_rollback_waits_for_vendor_mode_to_settle(self) -> None:
        self._enter_yandex_state()
        verifier = SequencedVerifier(
            **{
                "vendor-mode": [
                    failing_report("vendor-mode", "speaker_node"),
                    passing_report("vendor-mode"),
                    passing_report("vendor-mode"),
                ]
            }
        )
        rollback, verifier, _preflight = self._rollback(verifier)

        result = rollback.run(apply=True, maintenance_window=True)

        self.assertTrue(result.success, result.message)
        self.assertEqual(verifier.calls.count("vendor-mode"), 3)

    def test_readiness_error_preserves_last_full_report(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [
                    CheckReport(
                        "transition",
                        (
                            CheckResult("marker", CheckStatus.PASS, "expected state"),
                            CheckResult(
                                "dialog_node_count",
                                CheckStatus.FAIL,
                                "expected 0; observed 1",
                            ),
                            CheckResult("speaker_node", CheckStatus.PASS, "present"),
                        ),
                    )
                ]
            }
        )
        switch, _verifier, _preflight = self._switch(verifier)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertIn("last report:\nmode=transition result=FAIL", result.message)
        self.assertIn("PASS marker: expected state", result.message)
        self.assertIn("FAIL dialog_node_count: expected 0; observed 1", result.message)
        self.assertIn("PASS speaker_node: present", result.message)

    def test_all_readiness_waits_use_injected_clock_without_real_sleep(self) -> None:
        verifier = SequencedVerifier(
            **{
                "transition": [
                    failing_report("transition", "microphone_free"),
                    passing_report("transition"),
                    passing_report("transition"),
                ]
            }
        )
        switch, _verifier, _preflight = self._switch(verifier)

        with mock.patch(
            "casbot_deploy.operations.time.sleep",
            side_effect=AssertionError("real sleep used"),
        ):
            result = switch.run(apply=True, maintenance_window=True)

        self.assertTrue(result.success, result.message)
        self.assertGreaterEqual(len(self.clock.sleeps), 3)
        self.assertLessEqual(self.clock.value, 9.0)

    def test_failure_message_does_not_include_command_stderr_secret(self) -> None:
        runner = SecretFailureRunner(self.paths)
        switch, _verifier, _preflight = self._switch(runner=runner)

        result = switch.run(apply=True, maintenance_window=True)

        self.assertFalse(result.success)
        self.assertNotIn(FAKE_SECRET, result.message)


if __name__ == "__main__":
    unittest.main()
