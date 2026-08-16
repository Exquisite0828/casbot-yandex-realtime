import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = REPO_ROOT / "deploy"


class DeploymentRuntimeEnvironmentTest(unittest.TestCase):
    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _run_bash(
        self,
        script: str,
        *arguments: Path,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script, "phase8c-test", *(str(value) for value in arguments)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def _prepare_launch_root(
        self,
        root: Path,
        *,
        vendor_setup: str | None = None,
    ) -> Path:
        self._write(
            root / "opt/tros/humble/setup.bash",
            ': "${AMENT_TRACE_SETUP_FILES}"\n'
            'export PHASE8C_SETUP_ORDER="ros"\n',
        )
        self._write(
            root / "lingze/install/setup.bash",
            vendor_setup
            or 'export PHASE8C_SETUP_ORDER="${PHASE8C_SETUP_ORDER}:vendor"\n',
        )
        self._write(
            root / "opt/casbot-yandex-realtime/venv/bin/activate",
            'export PHASE8C_SETUP_ORDER="${PHASE8C_SETUP_ORDER}:venv"\n',
        )
        self._write(
            root / "opt/casbot-yandex-realtime/install/setup.bash",
            'export PHASE8C_SETUP_ORDER="${PHASE8C_SETUP_ORDER}:project"\n',
        )
        self._write(
            root / "etc/casbot-yandex-realtime/casbot-yandex.yaml",
            "/**:\n  ros__parameters:\n    speaker_pcm_format: \"pcm_s16le\"\n",
        )
        self._write(
            root / "opt/tros/humble/lib/hobot_shm/config/shm_fastdds.xml",
            "<profiles/>\n",
        )
        self._write(
            root / "lingze/config/user_config.json",
            json.dumps(
                {
                    "namespace": "lzdl10823",
                    "robot_current_mode": "jijia",
                    "current_llm": "lingze_omni_s2s",
                }
            ),
        )
        capture = root / "launch-capture.txt"
        executable = (
            root
            / "opt/casbot-yandex-realtime/install/realtime_dialog/lib/"
            "realtime_dialog/realtime_dialog_node"
        )
        self._write(
            executable,
            "#!/usr/bin/env bash\n"
            "{\n"
            "  printf 'order=%s\\n' \"${PHASE8C_SETUP_ORDER:-missing}\"\n"
            "  printf 'args=%s\\n' \"$*\"\n"
            "} > \"$CAPTURE_FILE\"\n",
        )
        os.chmod(executable, 0o755)
        return capture

    def test_helper_sources_nounset_incompatible_setup_and_restores_on(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup = Path(temporary) / "setup.bash"
            self._write(
                setup,
                ': "${AMENT_TRACE_SETUP_FILES}"\n'
                'export PHASE8C_SETUP_EFFECT="preserved"\n',
            )
            environment = dict(os.environ)
            environment.pop("AMENT_TRACE_SETUP_FILES", None)
            result = self._run_bash(
                "set -euo pipefail\n"
                "source \"$1\"\n"
                "casbot_source_setup_file \"$2\"\n"
                "[[ \"$PHASE8C_SETUP_EFFECT\" == preserved ]]\n"
                "case \"$-\" in *u*) ;; *) exit 91 ;; esac\n"
                "printf 'nounset=on effect=%s\\n' \"$PHASE8C_SETUP_EFFECT\"\n",
                DEPLOY / "lib" / "casbot-runtime-env",
                setup,
                environment=environment,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "nounset=on effect=preserved\n")
        self.assertNotIn("unbound variable", result.stderr)

    def test_helper_preserves_nounset_off_and_environment_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup = Path(temporary) / "setup.bash"
            self._write(setup, 'export PHASE8C_SETUP_EFFECT="preserved"\n')
            result = self._run_bash(
                "set -eo pipefail\n"
                "source \"$1\"\n"
                "casbot_source_setup_file \"$2\"\n"
                "case \"$-\" in *u*) exit 92 ;; esac\n"
                "printf 'nounset=off effect=%s\\n' \"$PHASE8C_SETUP_EFFECT\"\n",
                DEPLOY / "lib" / "casbot-runtime-env",
                setup,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "nounset=off effect=preserved\n")

    def test_helper_propagates_setup_failure_and_restores_nounset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup = Path(temporary) / "setup.bash"
            self._write(
                setup,
                "printf 'setup-stderr-sentinel\\n' >&2\n"
                'export PHASE8C_FAILURE_EFFECT="preserved"\n'
                "return 37\n",
            )
            result = self._run_bash(
                "set -euo pipefail\n"
                "source \"$1\"\n"
                "if casbot_source_setup_file \"$2\"; then rc=0; else rc=$?; fi\n"
                "[[ \"$rc\" -eq 37 ]]\n"
                "[[ \"$PHASE8C_FAILURE_EFFECT\" == preserved ]]\n"
                "case \"$-\" in *u*) ;; *) exit 93 ;; esac\n"
                "printf 'rc=%s nounset=on\\n' \"$rc\"\n",
                DEPLOY / "lib" / "casbot-runtime-env",
                setup,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "rc=37 nounset=on\n")
        self.assertIn("setup-stderr-sentinel", result.stderr)

    def test_preflight_wrapper_survives_nounset_incompatible_ros_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write(
                root / "opt/tros/humble/setup.bash",
                ': "${AMENT_TRACE_SETUP_FILES}"\n'
                'export PHASE8C_ROS_SETUP_EFFECT="preserved"\n',
            )
            environment = dict(os.environ)
            environment.pop("AMENT_TRACE_SETUP_FILES", None)

            result = subprocess.run(
                [
                    str(DEPLOY / "bin" / "casbot-yandex-preflight"),
                    "--root",
                    str(root),
                    "--mode",
                    "build",
                ],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

        self.assertNotIn("unbound variable", result.stderr)
        self.assertIn("mode=build result=FAIL", result.stdout)

    def test_launch_wrapper_sources_all_setups_and_direct_execs_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = self._prepare_launch_root(root)
            environment = dict(os.environ)
            environment.pop("AMENT_TRACE_SETUP_FILES", None)
            environment.update(
                {
                    "CAPTURE_FILE": str(capture),
                    "YANDEX_API_KEY": "phase8c-obviously-fake-secret",
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
            captured = capture.read_text(encoding="utf-8")

        self.assertIn("order=ros:vendor:venv:project", captured)
        self.assertIn("__node:=dialog_node", captured)
        self.assertIn("__ns:=/lzdl10823", captured)
        self.assertIn(str(root / "etc/casbot-yandex-realtime/casbot-yandex.yaml"), captured)
        self.assertNotIn("run realtime_dialog realtime_dialog_node", captured)
        self.assertNotIn("unbound variable", result.stderr)
        self.assertNotIn(environment["YANDEX_API_KEY"], result.stdout + result.stderr)

    def test_launch_wrapper_propagates_setup_failure_without_exec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = self._prepare_launch_root(
                root,
                vendor_setup=(
                    "printf 'vendor-setup-failure\\n' >&2\n"
                    "return 37\n"
                ),
            )
            environment = dict(os.environ)
            environment.pop("AMENT_TRACE_SETUP_FILES", None)
            environment["CAPTURE_FILE"] = str(capture)

            result = subprocess.run(
                [str(DEPLOY / "bin" / "casbot-yandex-launch"), "--root", str(root)],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertIn("vendor-setup-failure", result.stderr)
        self.assertFalse(capture.exists())

    def test_all_external_setup_source_points_use_one_shared_helper(self) -> None:
        runtime = (DEPLOY / "lib" / "casbot-runtime-env").read_text(encoding="utf-8")
        launch = (DEPLOY / "bin" / "casbot-yandex-launch").read_text(encoding="utf-8")
        self.assertIn("casbot_source_setup_file()", runtime)
        self.assertEqual(runtime.count('source "$casbot_setup_file"'), 1)
        for direct_source in (
            'source "$tros_setup"',
            'source "$ros_setup"',
            'source "$vendor_setup"',
            'source "$venv_activate"',
            'source "$project_setup"',
            'source "$selected_ros_setup"',
        ):
            self.assertNotIn(direct_source, runtime + launch)
        self.assertIn('source "$deploy_dir/lib/casbot-runtime-env"', launch)
        self.assertEqual(launch.count("casbot_source_setup_file"), 4)

        for name in (
            "casbot-yandex-preflight",
            "casbot-yandex-switch",
            "casbot-yandex-rollback",
            "casbot-yandex-verify",
            "casbot-yandex-probe-dialog-metadata",
        ):
            wrapper = (DEPLOY / "bin" / name).read_text(encoding="utf-8")
            self.assertIn('source "$deploy_dir/lib/casbot-runtime-env"', wrapper)
            self.assertIn('casbot_source_runtime "$@"', wrapper)
            self.assertNotIn("set +u", wrapper)

        vendor_gate = (DEPLOY / "bin" / "casbot-yandex-vendor-gate").read_text(
            encoding="utf-8"
        )
        self.assertTrue(vendor_gate.startswith("#!/usr/bin/env python3"))
        self.assertNotIn("source ", vendor_gate)


if __name__ == "__main__":
    unittest.main()
