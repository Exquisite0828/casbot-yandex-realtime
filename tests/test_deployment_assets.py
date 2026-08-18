import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = REPO_ROOT / "deploy"


class DeploymentAssetsTest(unittest.TestCase):
    def test_all_required_tools_exist_and_python_wrappers_share_one_library(self) -> None:
        tools = {
            "casbot-yandex-launch",
            "casbot-yandex-preflight",
            "casbot-yandex-switch",
            "casbot-yandex-rollback",
            "casbot-yandex-verify",
            "casbot-yandex-vendor-gate",
            "casbot-yandex-probe-dialog-metadata",
        }
        self.assertEqual(
            {path.name for path in (DEPLOY / "bin").iterdir() if path.is_file()},
            tools,
        )
        for name in tools:
            path = DEPLOY / "bin" / name
            self.assertTrue(os.access(path, os.X_OK), name)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Authorization", text)
            if name != "casbot-yandex-launch":
                self.assertIn("casbot_deploy.cli", text)

    def test_ros_dependent_control_tools_source_shared_runtime_environment(self) -> None:
        helper = DEPLOY / "lib" / "casbot-runtime-env"
        self.assertTrue(helper.is_file())
        helper_text = helper.read_text(encoding="utf-8")
        self.assertIn("/opt/tros/humble/setup.bash", helper_text)
        self.assertIn("/lingze/install/setup.bash", helper_text)
        self.assertIn("/opt/casbot-yandex-realtime/venv/bin/activate", helper_text)
        self.assertIn("/opt/casbot-yandex-realtime/install/setup.bash", helper_text)
        for name in (
            "casbot-yandex-preflight",
            "casbot-yandex-switch",
            "casbot-yandex-rollback",
            "casbot-yandex-verify",
            "casbot-yandex-probe-dialog-metadata",
        ):
            text = (DEPLOY / "bin" / name).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("#!/usr/bin/env bash"), name)
            self.assertIn("casbot-runtime-env", text)

    def test_systemd_unit_keeps_vendor_service_and_disables_restart(self) -> None:
        text = (DEPLOY / "systemd" / "casbot-yandex-dialog.service").read_text()
        for required in (
            "After=network-online.target lingze_robot.service",
            "Requires=lingze_robot.service",
            "PartOf=lingze_robot.service",
            "ConditionPathExists=/etc/casbot-yandex-realtime/external-dialog.enabled",
            "EnvironmentFile=/etc/casbot-yandex-realtime/yandex.env",
            "ExecStartPre=/opt/casbot-yandex-realtime/deploy/bin/casbot-yandex-preflight --mode service",
            "ExecStart=/opt/casbot-yandex-realtime/deploy/bin/casbot-yandex-launch",
            "KillSignal=SIGINT",
            "KillMode=control-group",
            "Restart=no",
            "WantedBy=multi-user.target",
        ):
            self.assertIn(required, text)
        self.assertNotIn("Conflicts=lingze_robot.service", text)
        self.assertNotIn("Restart=always", text)

    def test_production_config_contains_all_existing_parameters_without_guesses(self) -> None:
        existing = (
            REPO_ROOT / "src" / "realtime_dialog" / "config" / "casbot.example.yaml"
        ).read_text()
        deployment = (
            DEPLOY / "config" / "casbot-yandex.yaml.example"
        ).read_text()
        parameter = re.compile(r"^\s{4}([a-z][a-z0-9_]+):", re.MULTILINE)
        self.assertEqual(set(parameter.findall(deployment)), set(parameter.findall(existing)))
        self.assertIn('speaker_pcm_format: ""', deployment)
        self.assertIn('mic_device: "hw:0,0"', deployment)
        self.assertIn("barge_in_enabled: false", deployment)
        self.assertIn("microphone_resume_guard_ms: 500", deployment)
        self.assertIn("NOT a capture PASS", deployment)
        self.assertIn("Phase 8", deployment)

    def test_credential_and_requirements_templates_are_minimal(self) -> None:
        env = (DEPLOY / "config" / "yandex.env.example").read_text()
        self.assertIn("YANDEX_API_KEY=<replace_me>", env)
        self.assertIn("YANDEX_FOLDER_ID=<replace_me>", env)
        self.assertIn(
            "YANDEX_REALTIME_ENDPOINT=wss://ai.api.cloud.yandex.net/v1/realtime",
            env,
        )
        self.assertNotIn("phase6-obviously", env)
        requirements = (DEPLOY / "config" / "requirements.txt").read_text().splitlines()
        self.assertEqual(requirements, ["aiohttp>=3.8,<4"])

    def _write(self, path: Path, text: str = "# test fixture\n") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _prepare_launch_root(self, root: Path, user_config: str) -> Path:
        for logical in (
            "opt/tros/humble/setup.bash",
            "lingze/install/setup.bash",
            "opt/casbot-yandex-realtime/venv/bin/activate",
            "opt/casbot-yandex-realtime/install/setup.bash",
            "opt/casbot-yandex-realtime/install/realtime_dialog/lib/realtime_dialog/realtime_dialog_node",
            "etc/casbot-yandex-realtime/casbot-yandex.yaml",
            "opt/tros/humble/lib/hobot_shm/config/shm_fastdds.xml",
        ):
            self._write(root / logical)
        self._write(root / "lingze/config/user_config.json", user_config)
        capture = root / "ros2.args"
        executable = (
            root
            / "opt/casbot-yandex-realtime/install/realtime_dialog/lib/"
            "realtime_dialog/realtime_dialog_node"
        )
        executable.write_text(
            "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > \"$CAPTURE_FILE\"\n",
            encoding="utf-8",
        )
        os.chmod(executable, 0o755)
        return capture

    def test_launch_wrapper_loads_rooted_environment_and_resolves_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = self._prepare_launch_root(
                root,
                json.dumps(
                    {
                        "namespace": "from_config",
                        "robot_current_mode": "jijia",
                        "current_llm": "lingze_omni_s2s",
                    }
                ),
            )
            environment = dict(os.environ)
            environment.update(
                {
                    "CAPTURE_FILE": str(capture),
                    "CASBOT_ROS_NAMESPACE": "override_ns",
                    "YANDEX_API_KEY": "phase7-obviously-fake-secret-not-printed",
                }
            )
            result = subprocess.run(
                [str(DEPLOY / "bin" / "casbot-yandex-launch"), "--root", str(root)],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            args = capture.read_text()
            self.assertNotIn("run realtime_dialog realtime_dialog_node", args)
            self.assertIn("__node:=dialog_node", args)
            self.assertIn("__ns:=/override_ns", args)
            self.assertIn(str(root / "etc/casbot-yandex-realtime/casbot-yandex.yaml"), args)
            self.assertNotIn(environment["YANDEX_API_KEY"], result.stdout + result.stderr)

    def test_launch_wrapper_fails_closed_for_invalid_robot_config(self) -> None:
        invalid_configs = {
            "business": {
                "namespace": "lzdl10823",
                "robot_current_mode": "business",
                "current_llm": "lingze_omni_s2s",
            },
            "debug": {
                "namespace": "lzdl10823",
                "robot_current_mode": "debug",
                "current_llm": "lingze_omni_s2s",
            },
            "other": {
                "namespace": "lzdl10823",
                "robot_current_mode": "other",
                "current_llm": "lingze_omni_s2s",
            },
            "empty_mode": {
                "namespace": "lzdl10823",
                "robot_current_mode": "",
                "current_llm": "lingze_omni_s2s",
            },
            "unsupported_llm": {
                "namespace": "lzdl10823",
                "robot_current_mode": "jijia",
                "current_llm": "unsupported_backend",
            },
        }
        fixtures = {
            name: json.dumps(config) for name, config in invalid_configs.items()
        }
        fixtures["malformed_json"] = "{not-json"

        for name, fixture in fixtures.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                capture = self._prepare_launch_root(root, fixture)
                result = subprocess.run(
                    [str(DEPLOY / "bin" / "casbot-yandex-launch"), "--root", str(root)],
                    capture_output=True,
                    text=True,
                    check=False,
                    env={
                        **os.environ,
                        "CAPTURE_FILE": str(capture),
                        "CASBOT_ROS_NAMESPACE": "override_ns",
                        "YANDEX_API_KEY": "phase7-obviously-fake-secret-not-printed",
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(capture.exists())

    def test_launch_wrapper_fails_closed_when_user_config_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = self._prepare_launch_root(
                root,
                json.dumps(
                    {
                        "namespace": "lzdl10823",
                        "robot_current_mode": "jijia",
                        "current_llm": "lingze_omni_s2s",
                    }
                ),
            )
            (root / "lingze/config/user_config.json").unlink()
            result = subprocess.run(
                [str(DEPLOY / "bin" / "casbot-yandex-launch"), "--root", str(root)],
                capture_output=True,
                text=True,
                check=False,
                env={
                    **os.environ,
                    "CAPTURE_FILE": str(capture),
                    "YANDEX_API_KEY": "phase7-obviously-fake-secret-not-printed",
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(capture.exists())

    def test_launch_wrapper_fails_fast_when_an_environment_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [
                    str(DEPLOY / "bin" / "casbot-yandex-launch"),
                    "--root",
                    temporary,
                ],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "YANDEX_API_KEY": "phase7-obviously-fake-secret-not-printed"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing", result.stderr.lower())
            self.assertNotIn("phase7-obviously-fake-secret-not-printed", result.stderr)


if __name__ == "__main__":
    unittest.main()
