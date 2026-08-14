"""Strict reversible patching of the installed vendor launch file."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import py_compile
import stat
import tempfile
from typing import Callable

from .paths import DeploymentPaths


GATE_BEGIN = "# BEGIN CASBOT_YANDEX_EXTERNAL_DIALOG_GATE"
GATE_END = "# END CASBOT_YANDEX_EXTERNAL_DIALOG_GATE"


class DeploymentError(RuntimeError):
    """A safe deployment precondition or integrity check failed."""


class VendorGateStatus(str, Enum):
    UNPATCHED = "UNPATCHED"
    PATCHED = "PATCHED"
    DIVERGED = "DIVERGED"
    MISSING = "MISSING"


@dataclass(frozen=True)
class OperationResult:
    success: bool
    changed: bool
    message: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class VendorGate:
    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        replace: Callable[[Path, Path], None] = os.replace,
    ) -> None:
        self.paths = paths
        self._replace = replace

    def _require_root_for_real_apply(self) -> None:
        if self.paths.is_real_root and os.geteuid() != 0:
            raise DeploymentError("real-root apply requires root privileges")

    def _load_manifest(self) -> dict[str, object]:
        try:
            value = json.loads(self.paths.vendor_manifest.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise DeploymentError("vendor gate manifest is missing") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DeploymentError("vendor gate manifest is invalid") from error
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise DeploymentError("vendor gate manifest schema is invalid")
        return value

    @staticmethod
    def _find_anchor_line(source: str) -> int:
        try:
            tree = ast.parse(source)
        except SyntaxError as error:
            raise DeploymentError(f"vendor launch is not valid Python: {error}") from error
        functions = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_dialog_backend_node"
        ]
        anchors: list[ast.Assign] = []
        for function in functions:
            for node in function.body:
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                call = node.value
                if (
                    isinstance(target, ast.Name)
                    and target.id == "current_llm"
                    and isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_read_current_llm"
                    and not call.args
                    and not call.keywords
                ):
                    anchors.append(node)
        if len(functions) != 1 or len(anchors) != 1:
            raise DeploymentError(
                "expected exactly one _dialog_backend_node current_llm anchor"
            )
        log_info_bound = any(
            (
                isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "LogInfo"
            )
            or (
                isinstance(node, ast.ImportFrom)
                and any(
                    (alias.asname or alias.name) == "LogInfo"
                    for alias in node.names
                )
            )
            for node in tree.body
        )
        if not log_info_bound:
            raise DeploymentError("vendor launch does not bind required LogInfo action")
        function = functions[0]
        backend_maps = [
            node.value
            for node in function.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "backend_map"
            and isinstance(node.value, ast.Dict)
        ]
        expected_backends = {
            "lingze_omni_s2s": "lingze_omni_s2s",
            "lingze_s2s": "lingze_s2s",
        }
        if len(backend_maps) != 1:
            raise DeploymentError("vendor launch backend_map shape is unsupported")
        backend_map: dict[str, str] = {}
        for key, value in zip(backend_maps[0].keys, backend_maps[0].values):
            if (
                not isinstance(key, ast.Constant)
                or not isinstance(key.value, str)
                or not isinstance(value, ast.Constant)
                or not isinstance(value.value, str)
            ):
                continue
            backend_map[key.value] = value.value
        if any(backend_map.get(key) != value for key, value in expected_backends.items()):
            raise DeploymentError("vendor launch backend_map does not contain expected dialogs")
        dialog_calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_optional_node"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "dialog_node"
        ]
        if len(dialog_calls) != 1:
            raise DeploymentError("vendor dialog selection call shape is unsupported")
        return anchors[0].lineno

    def _render_patch(self, original: bytes) -> bytes:
        try:
            source = original.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DeploymentError("vendor launch must be UTF-8 text") from error
        line_number = self._find_anchor_line(source)
        lines = source.splitlines(keepends=True)
        anchor = lines[line_number - 1]
        indent = anchor[: len(anchor) - len(anchor.lstrip())]
        if not indent:
            raise DeploymentError("vendor launch anchor must be inside a function")
        marker = str(self.paths.marker_logical)
        gate_lines = [
            f"{indent}{GATE_BEGIN}\n",
            f"{indent}external_dialog_flag = {marker!r}\n",
            f"{indent}if __import__('os').path.exists(external_dialog_flag):\n",
            f"{indent}    return LogInfo(\n",
            f"{indent}        msg='[bringup] external dialog mode enabled; skip vendor dialog_node'\n",
            f"{indent}    )\n",
            f"{indent}{GATE_END}\n",
        ]
        lines[line_number - 1 : line_number - 1] = gate_lines
        patched = "".join(lines).encode("utf-8")
        try:
            compile(patched, str(self.paths.vendor_launch_logical), "exec")
        except SyntaxError as error:
            raise DeploymentError(f"patched vendor launch is invalid: {error}") from error
        return patched

    def status(self) -> VendorGateStatus:
        target = self.paths.vendor_launch
        if not target.exists():
            return VendorGateStatus.MISSING
        try:
            data = target.read_bytes()
            source = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return VendorGateStatus.DIVERGED
        begin_count = source.count(GATE_BEGIN)
        end_count = source.count(GATE_END)
        if begin_count == 0 and end_count == 0:
            try:
                self._find_anchor_line(source)
            except DeploymentError:
                return VendorGateStatus.DIVERGED
            return VendorGateStatus.UNPATCHED
        if begin_count != 1 or end_count != 1:
            return VendorGateStatus.DIVERGED
        try:
            manifest = self._load_manifest()
        except DeploymentError:
            return VendorGateStatus.DIVERGED
        return (
            VendorGateStatus.PATCHED
            if sha256_bytes(data) == manifest.get("patched_sha256")
            else VendorGateStatus.DIVERGED
        )

    def _atomic_write(
        self,
        target: Path,
        data: bytes,
        *,
        mode: int,
        uid: int | None = None,
        gid: int | None = None,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temp = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=str(target.parent)
        )
        temp = Path(raw_temp)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, mode)
            current = temp.stat()
            if uid is not None and gid is not None and (
                current.st_uid != uid or current.st_gid != gid
            ):
                os.chown(temp, uid, gid)
            self._replace(temp, target)
            _fsync_directory(target.parent)
        finally:
            if temp.exists():
                temp.unlink()

    def _py_compile_bytes(self, data: bytes) -> None:
        target = self.paths.vendor_launch
        descriptor, raw_source = tempfile.mkstemp(
            suffix=".py", prefix=".casbot-gate-check.", dir=str(target.parent)
        )
        source = Path(raw_source)
        bytecode = source.with_suffix(".pyc")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            py_compile.compile(str(source), cfile=str(bytecode), doraise=True)
        except py_compile.PyCompileError as error:
            raise DeploymentError(f"patched vendor launch failed py_compile: {error}") from error
        finally:
            if source.exists():
                source.unlink()
            if bytecode.exists():
                bytecode.unlink()

    def apply(self, *, apply: bool = False) -> OperationResult:
        status = self.status()
        if status is VendorGateStatus.PATCHED:
            return OperationResult(True, False, "vendor gate already PATCHED")
        if status is VendorGateStatus.MISSING:
            raise DeploymentError("vendor launch target is MISSING")
        if status is VendorGateStatus.DIVERGED:
            source = self.paths.vendor_launch.read_text(encoding="utf-8")
            if GATE_BEGIN not in source and GATE_END not in source:
                self._find_anchor_line(source)
            raise DeploymentError("vendor launch target is DIVERGED")
        original = self.paths.vendor_launch.read_bytes()
        patched = self._render_patch(original)
        if not apply:
            return OperationResult(
                True,
                False,
                f"DRY-RUN: patch {self.paths.vendor_launch_logical}",
            )
        self._require_root_for_real_apply()
        metadata = self.paths.vendor_launch.stat()
        original_mode = stat.S_IMODE(metadata.st_mode)
        original_sha = sha256_bytes(original)
        patched_sha = sha256_bytes(patched)
        self._py_compile_bytes(patched)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_logical = self.paths.vendor_backup_dir_logical / (
            f"jijia.launch.py.{timestamp}.{original_sha[:12]}.bak"
        )
        backup = self.paths.resolve(backup_logical)
        self._atomic_write(backup, original, mode=0o600)
        manifest = {
            "schema": 1,
            "target_path": str(self.paths.vendor_launch_logical),
            "backup_path": str(backup_logical),
            "original_sha256": original_sha,
            "patched_sha256": patched_sha,
            "original_mode": original_mode,
            "original_uid": metadata.st_uid,
            "original_gid": metadata.st_gid,
            "created_at_utc": timestamp,
        }
        manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        manifest_written = False
        try:
            self._atomic_write(
                self.paths.vendor_manifest, manifest_data, mode=0o600
            )
            manifest_written = True
            if sha256_bytes(self.paths.vendor_launch.read_bytes()) != original_sha:
                raise DeploymentError(
                    "vendor launch target drifted since semantic validation"
                )
            self._atomic_write(
                self.paths.vendor_launch,
                patched,
                mode=original_mode,
                uid=metadata.st_uid,
                gid=metadata.st_gid,
            )
            if sha256_bytes(self.paths.vendor_launch.read_bytes()) != patched_sha:
                raise DeploymentError("patched target SHA-256 verification failed")
        except BaseException as error:
            try:
                current_sha = sha256_bytes(self.paths.vendor_launch.read_bytes())
            except OSError:
                current_sha = ""
            if current_sha == patched_sha:
                try:
                    self._atomic_write(
                        self.paths.vendor_launch,
                        original,
                        mode=original_mode,
                        uid=metadata.st_uid,
                        gid=metadata.st_gid,
                    )
                except BaseException:
                    pass
                try:
                    current_sha = sha256_bytes(self.paths.vendor_launch.read_bytes())
                except OSError:
                    current_sha = ""
            if current_sha == original_sha:
                if manifest_written and self.paths.vendor_manifest.exists():
                    try:
                        self.paths.vendor_manifest.unlink()
                        _fsync_directory(self.paths.vendor_manifest.parent)
                    except OSError:
                        pass
                raise error
            raise DeploymentError(
                "vendor target drift detected or changed after replace failure; backup and "
                "manifest retained for manual recovery"
            ) from error
        return OperationResult(True, True, "vendor gate patched")

    def restore(self, *, apply: bool = False) -> OperationResult:
        status = self.status()
        if status is VendorGateStatus.UNPATCHED:
            if not self.paths.vendor_manifest.exists():
                return OperationResult(True, False, "vendor launch already UNPATCHED")
            manifest = self._load_manifest()
            current_sha = sha256_bytes(self.paths.vendor_launch.read_bytes())
            if current_sha != manifest.get("original_sha256"):
                raise DeploymentError(
                    "unpatched target does not match manifest original SHA-256"
                )
            return OperationResult(
                True,
                False,
                "vendor launch already restored to manifest original SHA-256",
            )
        if status is VendorGateStatus.MISSING:
            raise DeploymentError("vendor launch target is MISSING")
        manifest = self._load_manifest()
        backup_logical = Path(str(manifest["backup_path"]))
        backup = self.paths.resolve(backup_logical)
        try:
            original = backup.read_bytes()
        except FileNotFoundError as error:
            raise DeploymentError("vendor launch backup is missing") from error
        if sha256_bytes(original) != manifest.get("original_sha256"):
            raise DeploymentError("backup SHA-256 does not match manifest")
        current = self.paths.vendor_launch.read_bytes()
        if sha256_bytes(current) != manifest.get("patched_sha256"):
            raise DeploymentError("current target SHA-256 does not match manifest")
        if not apply:
            return OperationResult(
                True,
                False,
                f"DRY-RUN: restore {self.paths.vendor_launch_logical}",
            )
        self._require_root_for_real_apply()
        self._atomic_write(
            self.paths.vendor_launch,
            original,
            mode=int(manifest["original_mode"]),
            uid=int(manifest["original_uid"]),
            gid=int(manifest["original_gid"]),
        )
        if sha256_bytes(self.paths.vendor_launch.read_bytes()) != manifest.get(
            "original_sha256"
        ):
            raise DeploymentError("restored target SHA-256 verification failed")
        return OperationResult(True, True, "vendor launch restored")
