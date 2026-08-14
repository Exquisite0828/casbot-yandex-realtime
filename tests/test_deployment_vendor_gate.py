import hashlib
import json
import os
from pathlib import Path
import py_compile
import stat
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_LIB = REPO_ROOT / "deploy" / "lib"
if str(DEPLOY_LIB) not in sys.path:
    sys.path.insert(0, str(DEPLOY_LIB))

from casbot_deploy.paths import DeploymentPaths
from casbot_deploy.vendor_gate import (
    GATE_BEGIN,
    GATE_END,
    DeploymentError,
    VendorGate,
    VendorGateStatus,
)


VENDOR_SOURCE = b'''import json

class LogInfo:
    def __init__(self, msg):
        self.msg = msg

def _read_current_llm() -> str:
    return "lingze_omni_s2s"

def _optional_node(package_name, executable, **kwargs):
    return package_name, executable, kwargs

def _dialog_backend_node():
    current_llm = _read_current_llm()
    backend_map = {
        "lingze_omni_s2s": "lingze_omni_s2s",
        "lingze_s2s": "lingze_s2s",
    }
    package_name = backend_map.get(current_llm)
    if not package_name:
        return LogInfo(msg=f"unknown {current_llm}")
    return _optional_node(
        package_name,
        "dialog_node",
        output="screen",
        parameters=[{"audio_output_mode": "topic"}],
    )
'''


class VendorGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = DeploymentPaths(self.root)
        self.paths.vendor_launch.parent.mkdir(parents=True)
        self.paths.vendor_launch.write_bytes(VENDOR_SOURCE)
        os.chmod(self.paths.vendor_launch, 0o640)
        self.gate = VendorGate(self.paths)

    def test_status_reports_missing_unpatched_patched_and_diverged(self) -> None:
        self.assertEqual(self.gate.status(), VendorGateStatus.UNPATCHED)
        self.paths.vendor_launch.unlink()
        self.assertEqual(self.gate.status(), VendorGateStatus.MISSING)
        self.paths.vendor_launch.write_bytes(VENDOR_SOURCE)
        self.gate.apply(apply=True)
        self.assertEqual(self.gate.status(), VendorGateStatus.PATCHED)
        self.paths.vendor_launch.write_bytes(
            self.paths.vendor_launch.read_bytes().replace(
                b"external dialog mode enabled",
                b"locally changed message",
            )
        )
        self.assertEqual(self.gate.status(), VendorGateStatus.DIVERGED)

    def test_apply_defaults_to_dry_run_and_writes_nothing(self) -> None:
        before = self.paths.vendor_launch.read_bytes()
        result = self.gate.apply(apply=False)
        self.assertTrue(result.success)
        self.assertFalse(result.changed)
        self.assertIn("DRY-RUN", result.message)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), before)
        self.assertFalse(self.paths.vendor_manifest.exists())
        self.assertFalse(self.paths.vendor_backup_dir.exists())

    def test_apply_creates_byte_backup_manifest_minimal_patch_and_preserves_mode(self) -> None:
        original_sha = hashlib.sha256(VENDOR_SOURCE).hexdigest()
        result = self.gate.apply(apply=True)
        self.assertTrue(result.success)
        self.assertTrue(result.changed)
        patched = self.paths.vendor_launch.read_bytes()
        text = patched.decode("utf-8")
        self.assertEqual(text.count(GATE_BEGIN), 1)
        self.assertEqual(text.count(GATE_END), 1)
        self.assertIn(str(self.paths.marker_logical), text)
        self.assertIn("current_llm = _read_current_llm()", text)
        self.assertIn('"lingze_s2s": "lingze_s2s"', text)
        self.assertEqual(stat.S_IMODE(self.paths.vendor_launch.stat().st_mode), 0o640)
        py_compile.compile(str(self.paths.vendor_launch), doraise=True)

        manifest = json.loads(self.paths.vendor_manifest.read_text(encoding="utf-8"))
        backup = self.root / manifest["backup_path"].lstrip("/")
        self.assertEqual(backup.read_bytes(), VENDOR_SOURCE)
        self.assertEqual(manifest["original_sha256"], original_sha)
        self.assertEqual(
            manifest["patched_sha256"], hashlib.sha256(patched).hexdigest()
        )
        self.assertEqual(manifest["original_mode"], 0o640)

    def test_apply_is_idempotent(self) -> None:
        first = self.gate.apply(apply=True)
        patched = self.paths.vendor_launch.read_bytes()
        manifest = self.paths.vendor_manifest.read_bytes()
        second = self.gate.apply(apply=True)
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), patched)
        self.assertEqual(self.paths.vendor_manifest.read_bytes(), manifest)

    def test_apply_rejects_missing_or_multiple_semantic_anchor(self) -> None:
        self.paths.vendor_launch.write_text("def unrelated():\n    return None\n")
        with self.assertRaisesRegex(DeploymentError, "exactly one"):
            self.gate.apply(apply=True)
        self.assertEqual(
            self.paths.vendor_launch.read_text(), "def unrelated():\n    return None\n"
        )

        duplicated = VENDOR_SOURCE + VENDOR_SOURCE
        self.paths.vendor_launch.write_bytes(duplicated)
        with self.assertRaisesRegex(DeploymentError, "exactly one"):
            self.gate.apply(apply=True)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), duplicated)

    def test_apply_rejects_missing_log_info_or_backend_selection_shape(self) -> None:
        missing_log_info = VENDOR_SOURCE.replace(b"class LogInfo:", b"class OtherAction:")
        self.paths.vendor_launch.write_bytes(missing_log_info)
        with self.assertRaisesRegex(DeploymentError, "LogInfo"):
            self.gate.apply(apply=True)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), missing_log_info)

        altered_backend = VENDOR_SOURCE.replace(
            b'        "lingze_s2s": "lingze_s2s",\n',
            b"",
        )
        self.paths.vendor_launch.write_bytes(altered_backend)
        with self.assertRaisesRegex(DeploymentError, "backend_map"):
            self.gate.apply(apply=True)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), altered_backend)

    def test_partial_or_unknown_gate_is_diverged_and_refused(self) -> None:
        altered = VENDOR_SOURCE.replace(
            b"    current_llm = _read_current_llm()",
            ("    " + GATE_BEGIN + "\n    current_llm = _read_current_llm()").encode(),
        )
        self.paths.vendor_launch.write_bytes(altered)
        self.assertEqual(self.gate.status(), VendorGateStatus.DIVERGED)
        with self.assertRaisesRegex(DeploymentError, "DIVERGED"):
            self.gate.apply(apply=True)

    def test_restore_is_dry_run_then_byte_identical_and_keeps_history(self) -> None:
        self.gate.apply(apply=True)
        manifest = json.loads(self.paths.vendor_manifest.read_text())
        backup = self.root / manifest["backup_path"].lstrip("/")
        patched = self.paths.vendor_launch.read_bytes()

        dry = self.gate.restore(apply=False)
        self.assertFalse(dry.changed)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), patched)

        restored = self.gate.restore(apply=True)
        self.assertTrue(restored.changed)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), VENDOR_SOURCE)
        self.assertEqual(stat.S_IMODE(self.paths.vendor_launch.stat().st_mode), 0o640)
        self.assertTrue(backup.exists())
        self.assertTrue(self.paths.vendor_manifest.exists())

    def test_restore_rejects_backup_or_current_hash_drift(self) -> None:
        self.gate.apply(apply=True)
        manifest = json.loads(self.paths.vendor_manifest.read_text())
        backup = self.root / manifest["backup_path"].lstrip("/")
        backup.write_bytes(b"not the original")
        with self.assertRaisesRegex(DeploymentError, "backup SHA-256"):
            self.gate.restore(apply=True)

        self.paths.vendor_launch.write_bytes(VENDOR_SOURCE)
        self.gate = VendorGate(self.paths)
        self.paths.vendor_manifest.unlink()
        self.gate.apply(apply=True)
        self.paths.vendor_launch.write_bytes(
            self.paths.vendor_launch.read_bytes() + b"\n# unrelated operator change\n"
        )
        with self.assertRaisesRegex(DeploymentError, "current target SHA-256"):
            self.gate.restore(apply=True)

    def test_restore_rejects_semantically_unpatched_but_byte_drifted_target(self) -> None:
        self.gate.apply(apply=True)
        drifted = VENDOR_SOURCE + b"\n# unrelated operator change\n"
        self.paths.vendor_launch.write_bytes(drifted)

        self.assertEqual(self.gate.status(), VendorGateStatus.UNPATCHED)
        with self.assertRaisesRegex(DeploymentError, "original SHA-256"):
            self.gate.restore(apply=True)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), drifted)

    def test_failed_atomic_target_replace_leaves_original_unchanged(self) -> None:
        before = self.paths.vendor_launch.read_bytes()

        def fail_target_replace(source: Path, target: Path) -> None:
            if target == self.paths.vendor_launch:
                raise OSError("scripted target replace failure")
            os.replace(source, target)

        gate = VendorGate(self.paths, replace=fail_target_replace)
        with self.assertRaisesRegex(OSError, "scripted target replace failure"):
            gate.apply(apply=True)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), before)

    def test_failed_target_directory_fsync_restores_original_bytes(self) -> None:
        calls = 0

        def fail_after_target_replace(_path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("scripted target directory fsync failure")

        with mock.patch(
            "casbot_deploy.vendor_gate._fsync_directory",
            side_effect=fail_after_target_replace,
        ):
            with self.assertRaisesRegex(OSError, "target directory fsync"):
                self.gate.apply(apply=True)

        self.assertEqual(self.paths.vendor_launch.read_bytes(), VENDOR_SOURCE)
        self.assertFalse(self.paths.vendor_manifest.exists())

    def test_apply_refuses_target_drift_before_atomic_replace(self) -> None:
        drifted = VENDOR_SOURCE + b"\n# concurrent operator change\n"

        def drift_after_manifest(source: Path, target: Path) -> None:
            os.replace(source, target)
            if target == self.paths.vendor_manifest:
                self.paths.vendor_launch.write_bytes(drifted)

        gate = VendorGate(self.paths, replace=drift_after_manifest)
        with self.assertRaisesRegex(DeploymentError, "drift"):
            gate.apply(apply=True)
        self.assertEqual(self.paths.vendor_launch.read_bytes(), drifted)


if __name__ == "__main__":
    unittest.main()
