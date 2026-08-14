import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_LIB = REPO_ROOT / "deploy" / "lib"
if str(DEPLOY_LIB) not in sys.path:
    sys.path.insert(0, str(DEPLOY_LIB))

from casbot_deploy.checks import (
    CheckStatus,
    CommandResult,
    DeploymentInspector,
    DeploymentVerifier,
    RclpyGraphProbe,
    SubprocessCommandRunner,
)
from casbot_deploy.paths import DeploymentPaths
from casbot_deploy.vendor_gate import VendorGate
from tests.test_deployment_vendor_gate import VENDOR_SOURCE


FAKE_SECRET = "phase7-obviously-fake-api-key"


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.vendor_service_active = True
        self.yandex_service_active = False
        self.vendor_dialog_running = False
        self.legacy_vendor_dialog_running = False
        self.yandex_dialog_running = False
        self.extra_yandex_pids: set[str] = set()
        self.microphone_occupied = False
        self.import_failures: set[str] = set()
        self.unknown_commands: set[str] = set()
        self.yandex_pid = "4321"
        self.yandex_process = (
            "/opt/casbot-yandex-realtime/install/realtime_dialog/"
            "realtime_dialog_node --ros-args"
        )

    def run(self, args, *, timeout: float):
        command = tuple(str(value) for value in args)
        self.calls.append(command)
        if command[:2] == ("systemctl", "is-active"):
            service = command[-1]
            if service in self.unknown_commands:
                return CommandResult(command, 127, "", "fake command failure")
            active = (
                self.vendor_service_active
                if service == "lingze_robot.service"
                else self.yandex_service_active
            )
            return CommandResult(command, 0 if active else 3, "active\n" if active else "inactive\n", "")
        if command[:2] == ("systemctl", "show"):
            return CommandResult(command, 0, self.yandex_pid + "\n", "")
        if command and command[0] == "pgrep":
            if "pgrep" in self.unknown_commands:
                return CommandResult(command, 127, "", "fake command failure")
            if "/lingze/install/lingze_omni_s2s" in command[-1]:
                pids = ["100"] if self.vendor_dialog_running else []
            elif "/lingze/install/lingze_s2s" in command[-1]:
                pids = ["101"] if self.legacy_vendor_dialog_running else []
            else:
                pids = [self.yandex_pid] if self.yandex_dialog_running else []
                pids.extend(sorted(self.extra_yandex_pids))
            return CommandResult(
                command,
                0 if pids else 1,
                "".join(f"{pid}\n" for pid in pids),
                "",
            )
        if command and command[0] == "fuser":
            if "fuser" in self.unknown_commands:
                return CommandResult(command, 127, "", "fake command failure")
            return CommandResult(command, 0 if self.microphone_occupied else 1, "100" if self.microphone_occupied else "", "")
        if command and command[0] == "ps":
            return CommandResult(command, 0, self.yandex_process + "\n", "")
        if "--version" in command:
            return CommandResult(command, 0, "Python 3.10.12\n", "")
        if "-c" in command:
            program = command[command.index("-c") + 1]
            for dependency in self.import_failures:
                if dependency in program:
                    return CommandResult(command, 1, "", f"fake missing {dependency}")
            return CommandResult(command, 0, "", "")
        return CommandResult(command, 0, "", "")


class FakeGraphProbe:
    def __init__(self, nodes: list[str]) -> None:
        self.nodes = nodes
        self.error: Exception | None = None
        self.on_list = None

    def list_fully_qualified_nodes(self, *, timeout: float) -> list[str]:
        if self.error is not None:
            raise self.error
        if self.on_list is not None:
            self.on_list()
        return list(self.nodes)


class DeploymentChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = DeploymentPaths(self.root)
        self.runner = FakeRunner()
        self.namespace = "lzdl10823"
        self.dialog = f"/{self.namespace}/dialog_node"
        self.speaker = f"/{self.namespace}/audio_speaker_node"
        self.graph = FakeGraphProbe([self.speaker])
        self._create_static_files()
        self.inspector = DeploymentInspector(self.paths, self.runner, self.graph)
        self.verifier = DeploymentVerifier(self.paths, self.runner, self.graph)

    def _write(self, path: Path, text: str = "fixture\n", mode: int = 0o644) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.chmod(path, mode)

    def _create_static_files(self) -> None:
        self._write(self.paths.vendor_launch, VENDOR_SOURCE.decode())
        VendorGate(self.paths).apply(apply=True)
        self._write(
            self.paths.config,
            "/**:\n"
            "  ros__parameters:\n"
            '    speaker_pcm_format: "phase8-confirmed-value"\n',
        )
        self._write(
            self.paths.env,
            "\n".join(
                [
                    f"YANDEX_API_KEY={FAKE_SECRET}",
                    "YANDEX_FOLDER_ID=phase7-obviously-fake-folder",
                    "YANDEX_REALTIME_ENDPOINT=wss://ai.api.cloud.yandex.net/v1/realtime",
                    "YANDEX_MODEL_OR_AGENT=speech-realtime-260528",
                    "",
                ]
            ),
            mode=0o600,
        )
        self._write(
            self.paths.user_config,
            json.dumps(
                {
                    "namespace": self.namespace,
                    "robot_current_mode": "jijia",
                    "current_llm": "lingze_omni_s2s",
                }
            ),
        )
        for path in (
            self.paths.ros_setup,
            self.paths.vendor_setup,
            self.paths.venv_python,
            self.paths.project_install_setup,
            self.paths.project_executable,
            self.paths.project_package,
            self.paths.project_launch,
            self.paths.config,
            self.paths.colcon,
            self.paths.arecord,
            self.paths.capture_device,
        ):
            if not path.exists():
                self._write(path)
        os.chmod(self.paths.project_executable, 0o755)

    def assert_failed(self, report, name: str) -> None:
        result = report.by_name(name)
        self.assertEqual(result.status, CheckStatus.FAIL, report.render_text())
        self.assertFalse(report.ok)

    def _set_user_config(self, **updates: object) -> None:
        config = {
            "namespace": self.namespace,
            "robot_current_mode": "jijia",
            "current_llm": "lingze_omni_s2s",
        }
        config.update(updates)
        self.paths.user_config.write_text(json.dumps(config), encoding="utf-8")

    def test_jijia_with_each_supported_vendor_backend_passes_all_config_guards(self) -> None:
        for current_llm in ("lingze_omni_s2s", "lingze_s2s"):
            with self.subTest(current_llm=current_llm):
                self._set_user_config(current_llm=current_llm)
                reports = (
                    self.inspector.run("switch"),
                    self.inspector.run("service"),
                    self.inspector.run("rollback"),
                    self.verifier.verify("vendor-mode"),
                )
                for report in reports:
                    self.assertEqual(
                        report.by_name("robot_mode").status,
                        CheckStatus.PASS,
                        report.render_text(),
                    )
                    self.assertEqual(
                        report.by_name("current_llm").status,
                        CheckStatus.PASS,
                        report.render_text(),
                    )

    def test_business_debug_and_other_modes_fail_closed_everywhere(self) -> None:
        for robot_mode in ("business", "debug", "other", " jijia "):
            with self.subTest(robot_mode=robot_mode):
                self._set_user_config(robot_current_mode=robot_mode)
                reports = (
                    self.inspector.run("switch"),
                    self.inspector.run("service"),
                    self.inspector.run("rollback"),
                    self.verifier.verify("vendor-mode"),
                )
                for report in reports:
                    self.assert_failed(report, "robot_mode")

    def test_missing_or_malformed_user_config_fails_closed_everywhere(self) -> None:
        fixtures = (None, "{not-json")
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self._create_static_files()
                if fixture is None:
                    self.paths.user_config.unlink()
                else:
                    self.paths.user_config.write_text(fixture, encoding="utf-8")
                reports = (
                    self.inspector.run("switch"),
                    self.inspector.run("service"),
                    self.inspector.run("rollback"),
                    self.verifier.verify("vendor-mode"),
                )
                for report in reports:
                    self.assert_failed(report, "user_config")

    def test_empty_required_user_config_fields_fail_closed(self) -> None:
        for field in ("namespace", "robot_current_mode", "current_llm"):
            with self.subTest(field=field):
                self._set_user_config(**{field: ""})
                self.assert_failed(self.inspector.run("rollback"), "user_config")

    def test_unsupported_current_llm_is_rejected_everywhere(self) -> None:
        for current_llm in ("unsupported_backend", " lingze_s2s "):
            with self.subTest(current_llm=current_llm):
                self._set_user_config(current_llm=current_llm)
                reports = (
                    self.inspector.run("switch"),
                    self.inspector.run("service"),
                    self.inspector.run("rollback"),
                    self.verifier.verify("vendor-mode"),
                )
                for report in reports:
                    self.assert_failed(report, "current_llm")

    def test_config_change_during_graph_check_fails_service_and_verify(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()

        for target in ("service", "verify"):
            with self.subTest(target=target):
                self._set_user_config()
                self.graph.nodes = [self.speaker]

                def change_mode() -> None:
                    self._set_user_config(robot_current_mode="business")

                self.graph.on_list = change_mode
                report = (
                    self.inspector.run("service")
                    if target == "service"
                    else self.verifier.verify("transition")
                )
                self.assertFalse(report.ok, report.render_text())
                self.assertEqual(
                    report.by_name("robot_config_stable").status,
                    CheckStatus.FAIL,
                    report.render_text(),
                )

    def test_build_mode_checks_python_tools_overlays_imports_and_project_assets(self) -> None:
        report = self.inspector.run("build")
        self.assertTrue(report.ok, report.render_text())
        expected = {
            "python_3_10",
            "colcon",
            "ros2_setup",
            "vendor_overlay",
            "venv",
            "import_aiohttp",
            "import_rclpy",
            "import_lingze_msgs",
            "project_package",
            "project_install",
            "project_executable",
            "launch_file",
            "config_file",
        }
        self.assertTrue(expected <= {check.name for check in report.checks})

    def test_build_import_failures_are_individually_visible(self) -> None:
        for dependency, check_name in (
            ("aiohttp", "import_aiohttp"),
            ("rclpy", "import_rclpy"),
            ("lingze_msgs", "import_lingze_msgs"),
        ):
            with self.subTest(dependency=dependency):
                self.runner.import_failures = {dependency}
                self.assert_failed(self.inspector.run("build"), check_name)
        self.runner.import_failures.clear()

    def test_service_mode_passes_only_in_safe_transition_state(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        report = self.inspector.run("service")
        self.assertTrue(report.ok, report.render_text())
        self.assertEqual(report.by_name("dialog_node_count").status, CheckStatus.PASS)
        self.assertEqual(report.by_name("microphone_free").status, CheckStatus.PASS)

    def test_service_mode_rejects_missing_marker_or_gate(self) -> None:
        self.assert_failed(self.inspector.run("service"), "marker")
        self.paths.vendor_launch.write_bytes(VENDOR_SOURCE)
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.assert_failed(self.inspector.run("service"), "vendor_gate")

    def test_service_mode_rejects_symlink_marker_or_config(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        marker_target = self.root / "marker-target"
        marker_target.touch()
        self.paths.marker.symlink_to(marker_target)
        self.assert_failed(self.inspector.run("service"), "marker_security")

        self.paths.marker.unlink()
        self.paths.marker.touch()
        config_target = self.root / "config-target"
        config_target.write_text(
            '    speaker_pcm_format: "phase8-confirmed-value"\n',
            encoding="utf-8",
        )
        self.paths.config.unlink()
        self.paths.config.symlink_to(config_target)
        self.assert_failed(self.inspector.run("service"), "config_security")

    def test_service_mode_rejects_env_permission_variables_and_placeholders_without_leak(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        os.chmod(self.paths.env, 0o640)
        report = self.inspector.run("service")
        self.assert_failed(report, "env_permissions")

        os.chmod(self.paths.env, 0o600)
        self.paths.env.write_text("YANDEX_API_KEY=<replace_me>\n")
        report = self.inspector.run("service")
        self.assert_failed(report, "required_environment")
        rendered = report.render_text() + report.render_json()
        self.assertNotIn(FAKE_SECRET, rendered)
        self.assertNotIn("<replace_me>", rendered)

    def test_service_mode_rejects_unsafe_or_unexpected_environment_file(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        with self.paths.env.open("a", encoding="utf-8") as stream:
            stream.write("LD_PRELOAD=/tmp/not-allowed\n")
        self.assert_failed(self.inspector.run("service"), "env_syntax")

        self._create_static_files()
        os.chmod(self.paths.env.parent, 0o777)
        self.assert_failed(self.inspector.run("service"), "env_security")

        os.chmod(self.paths.env.parent, 0o755)
        target = self.root / "fake-secret-target"
        target.write_text(self.paths.env.read_text(encoding="utf-8"), encoding="utf-8")
        self.paths.env.unlink()
        self.paths.env.symlink_to(target)
        self.assert_failed(self.inspector.run("service"), "env_security")

    def test_service_mode_rejects_duplicate_environment_names(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        with self.paths.env.open("a", encoding="utf-8") as stream:
            stream.write("YANDEX_FOLDER_ID=duplicate-fake-folder\n")
        self.assert_failed(self.inspector.run("service"), "env_syntax")

    def test_service_mode_rejects_shell_export_environment_syntax(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        environment = self.paths.env.read_text(encoding="utf-8")
        self.paths.env.write_text(
            environment.replace("YANDEX_API_KEY=", "export YANDEX_API_KEY="),
            encoding="utf-8",
        )
        self.assert_failed(self.inspector.run("service"), "env_syntax")

    def test_service_mode_rejects_empty_speaker_format(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.paths.config.write_text('    speaker_pcm_format: ""\n')
        self.assert_failed(self.inspector.run("service"), "speaker_pcm_format")
        self.paths.config.write_text('    speaker_pcm_format: "   "\n')
        self.assert_failed(self.inspector.run("service"), "speaker_pcm_format")

    def test_service_mode_requires_speaker_format_under_ros_parameter_hierarchy(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        invalid_configs = (
            'speaker_pcm_format: "phase8-confirmed-value"\n',
            "/**:\n"
            "  ros__parameters:\n"
            '  speaker_pcm_format: "phase8-confirmed-value"\n',
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                self.paths.config.write_text(config, encoding="utf-8")
                self.assert_failed(
                    self.inspector.run("service"), "speaker_pcm_format"
                )

    def test_preflight_namespace_uses_same_ros_token_rule_as_launch(self) -> None:
        config = json.loads(self.paths.user_config.read_text(encoding="utf-8"))
        config["namespace"] = "bad-name"
        self.paths.user_config.write_text(json.dumps(config), encoding="utf-8")
        self.assert_failed(self.inspector.run("switch"), "namespace")

    def test_service_mode_rejects_vendor_process_duplicate_node_missing_speaker_and_busy_mic(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        cases = (
            ("vendor_dialog_absent", lambda: setattr(self.runner, "vendor_dialog_running", True)),
            ("dialog_node_count", lambda: self.graph.nodes.append(self.dialog)),
            ("speaker_node", lambda: self.graph.nodes.remove(self.speaker)),
            ("microphone_free", lambda: setattr(self.runner, "microphone_occupied", True)),
        )
        for check_name, mutate in cases:
            with self.subTest(check=check_name):
                self.setUp()
                mutate()
                self.assert_failed(self.inspector.run("service"), check_name)

    def test_preflight_and_verify_reject_orphan_yandex_dialog_process(self) -> None:
        self.runner.vendor_dialog_running = True
        self.runner.yandex_dialog_running = True
        self.graph.nodes = [self.dialog, self.speaker]
        self.assert_failed(self.inspector.run("switch"), "yandex_dialog_absent")
        self.assert_failed(self.verifier.verify("vendor-mode"), "mutual_exclusion")

    def test_preflight_rejects_alternate_vendor_backend_process(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.legacy_vendor_dialog_running = True
        self.assert_failed(self.inspector.run("service"), "vendor_dialog_absent")

    def test_switch_mode_requires_vendor_mode_but_defers_microphone_release(self) -> None:
        self.runner.vendor_dialog_running = True
        self.graph.nodes = [self.dialog, self.speaker]
        report = self.inspector.run("switch")
        self.assertTrue(report.ok, report.render_text())
        self.assertEqual(
            report.by_name("microphone_release_after_restart").status,
            CheckStatus.DEFERRED,
        )
        self.assertNotEqual(
            report.by_name("microphone_release_after_restart").status,
            CheckStatus.PASS,
        )

    def test_json_contains_explicit_statuses_and_no_secret(self) -> None:
        report = self.inspector.run("switch")
        payload = json.loads(report.render_json())
        self.assertEqual(payload["mode"], "switch")
        self.assertTrue({item["status"] for item in payload["checks"]} <= {"PASS", "FAIL", "DEFERRED"})
        self.assertNotIn(FAKE_SECRET, report.render_json())

    def test_graph_probe_failure_is_not_claimed_as_unique(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.graph.error = RuntimeError("graph unavailable")
        report = self.inspector.run("service")
        self.assert_failed(report, "dialog_node_count")
        self.assert_failed(report, "speaker_node")

    def test_command_failures_are_not_misreported_as_safe_absence(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.unknown_commands.add("fuser")
        self.assert_failed(self.inspector.run("service"), "microphone_free")

        self.runner.unknown_commands = {"casbot-yandex-dialog.service"}
        self.runner.vendor_dialog_running = True
        self.paths.marker.unlink()
        self.graph.nodes = [self.dialog, self.speaker]
        self.assert_failed(self.inspector.run("switch"), "yandex_service_stopped")

        self.runner.unknown_commands = {"pgrep"}
        report = self.verifier.verify("vendor-mode")
        self.assert_failed(report, "vendor_dialog")
        self.assertIn("UNKNOWN", report.by_name("vendor_dialog").detail)

    def test_verify_vendor_transition_and_yandex_modes(self) -> None:
        self.runner.vendor_dialog_running = True
        self.graph.nodes = [self.dialog, self.speaker]
        vendor = self.verifier.verify("vendor-mode")
        self.assertTrue(vendor.ok, vendor.render_text())

        self.runner.vendor_dialog_running = False
        self.graph.nodes = [self.speaker]
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        transition = self.verifier.verify("transition")
        self.assertTrue(transition.ok, transition.render_text())

        self.runner.yandex_service_active = True
        self.runner.yandex_dialog_running = True
        self.graph.nodes = [self.dialog, self.speaker]
        yandex = self.verifier.verify("yandex-mode")
        self.assertTrue(yandex.ok, yandex.render_text())
        self.assertEqual(yandex.by_name("yandex_process_owner").status, CheckStatus.PASS)

    def test_verify_rejects_dual_dialog_and_active_service_with_bad_graph(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.vendor_dialog_running = True
        self.runner.yandex_service_active = True
        self.runner.yandex_dialog_running = True
        self.graph.nodes = [self.dialog, self.dialog, self.speaker]
        self.assert_failed(self.verifier.verify("yandex-mode"), "mutual_exclusion")

        self.runner.vendor_dialog_running = False
        self.graph.nodes = [self.speaker]
        self.assert_failed(self.verifier.verify("yandex-mode"), "dialog_node_count")

    def test_verify_requires_exactly_one_target_node_and_process_ownership(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.yandex_service_active = True
        self.runner.yandex_dialog_running = True
        self.graph.nodes = [self.dialog, self.dialog, self.speaker]
        self.assert_failed(self.verifier.verify("yandex-mode"), "dialog_node_count")
        self.graph.nodes = [self.dialog, self.speaker]
        self.runner.yandex_process = "/usr/bin/something-unrelated"
        self.assert_failed(self.verifier.verify("yandex-mode"), "yandex_process_owner")

    def test_verify_rejects_extra_yandex_process_beside_service_main_pid(self) -> None:
        self.paths.marker.parent.mkdir(parents=True, exist_ok=True)
        self.paths.marker.touch()
        self.runner.yandex_service_active = True
        self.runner.yandex_dialog_running = True
        self.runner.extra_yandex_pids.add("9999")
        self.graph.nodes = [self.dialog, self.speaker]

        self.assert_failed(self.verifier.verify("yandex-mode"), "yandex_process_owner")


class RclpyGraphProbeTest(unittest.TestCase):
    def test_probe_does_not_stop_after_discovering_only_its_own_node(self) -> None:
        class FakeNode:
            def __init__(self) -> None:
                self.spin_count = 0
                self.destroyed = False

            def get_node_names_and_namespaces(self):
                values = [("casbot_yandex_deployment_probe", "/")]
                if self.spin_count >= 2:
                    values.append(("dialog_node", "/lzdl10823"))
                return values

            def destroy_node(self) -> None:
                self.destroyed = True

        node = FakeNode()

        def spin_once(_node, *, timeout_sec):
            del timeout_sec
            node.spin_count += 1

        fake_rclpy = types.SimpleNamespace(
            ok=lambda: True,
            create_node=lambda _name: node,
            spin_once=spin_once,
        )
        with mock.patch.dict(sys.modules, {"rclpy": fake_rclpy}):
            observed = RclpyGraphProbe().list_fully_qualified_nodes(timeout=0.002)

        self.assertGreaterEqual(node.spin_count, 2)
        self.assertIn("/lzdl10823/dialog_node", observed)
        self.assertTrue(node.destroyed)


class MissingRuntimePreflightTest(unittest.TestCase):
    def test_build_mode_reports_missing_runtime_as_fail_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = DeploymentPaths(Path(temporary))
            inspector = DeploymentInspector(
                paths,
                runner=SubprocessCommandRunner(),
                graph_probe=FakeGraphProbe([]),
                timeout=0.1,
                environ={},
            )
            report = inspector.run("build")

        self.assertFalse(report.ok)
        self.assertEqual(report.by_name("python_3_10").status, CheckStatus.FAIL)
        self.assertEqual(report.by_name("import_aiohttp").status, CheckStatus.FAIL)


if __name__ == "__main__":
    unittest.main()
