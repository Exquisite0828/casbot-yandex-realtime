"""Command-line entry points for the Phase 7 deployment control plane."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .checks import DeploymentInspector, DeploymentVerifier, ROS_TOKEN
from .metadata_probe import (
    ProbeExternalShutdown,
    ProbeTimeout,
    RclpyMetadataRuntime,
    format_metadata,
    probe_first_metadata,
)
from .operations import RollbackController, SwitchController
from .paths import DeploymentPaths
from .vendor_gate import DeploymentError, VendorGate, VendorGateStatus


def _paths(value: str) -> DeploymentPaths:
    return DeploymentPaths(Path(value))


def _print_error(error: BaseException) -> int:
    print(f"ERROR: {error}", file=sys.stderr)
    return 1


def vendor_gate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casbot-yandex-vendor-gate")
    parser.add_argument("--root", default="/")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("plan")
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--apply", action="store_true")
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args(argv)
    gate = VendorGate(_paths(arguments.root))
    try:
        if arguments.command == "status":
            status = gate.status()
            print(status.value)
            return 0 if status in {VendorGateStatus.UNPATCHED, VendorGateStatus.PATCHED} else 1
        if arguments.command == "plan":
            result = gate.apply(apply=False)
        elif arguments.command == "apply":
            result = gate.apply(apply=arguments.apply)
        else:
            result = gate.restore(apply=arguments.apply)
    except (DeploymentError, OSError) as error:
        return _print_error(error)
    print(result.message)
    return 0 if result.success else 1


def preflight_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casbot-yandex-preflight")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("build", "service", "switch", "rollback"),
    )
    parser.add_argument("--root", default="/")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=float, default=5.0)
    arguments = parser.parse_args(argv)
    try:
        report = DeploymentInspector(
            _paths(arguments.root), timeout=arguments.timeout
        ).run(arguments.mode)
    except Exception as error:
        return _print_error(error)
    print(report.render_json() if arguments.json else report.render_text())
    return 0 if report.ok else 1


def verify_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casbot-yandex-verify")
    parser.add_argument("mode", choices=("vendor-mode", "transition", "yandex-mode"))
    parser.add_argument("--root", default="/")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=float, default=5.0)
    arguments = parser.parse_args(argv)
    try:
        report = DeploymentVerifier(
            _paths(arguments.root), timeout=arguments.timeout
        ).verify(arguments.mode)
    except Exception as error:
        return _print_error(error)
    print(report.render_json() if arguments.json else report.render_text())
    return 0 if report.ok else 1


def switch_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casbot-yandex-switch")
    parser.add_argument("--root", default="/")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--maintenance-window", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    arguments = parser.parse_args(argv)
    try:
        result = SwitchController(
            _paths(arguments.root), timeout=arguments.timeout
        ).run(
            apply=arguments.apply,
            maintenance_window=arguments.maintenance_window,
        )
    except (DeploymentError, OSError) as error:
        return _print_error(error)
    print(result.message)
    if result.success and result.changed:
        print(
            "HUMAN ACCEPTANCE REQUIRED: call start_session; wait for STATUS_LISTENING; "
            "test Russian dialog, speaker, mouth, stop, interruption, flush, Web, other "
            "robot modules, Yandex usage, and original-cloud usage."
        )
    return 0 if result.success else 1


def rollback_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casbot-yandex-rollback")
    parser.add_argument("--root", default="/")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--maintenance-window", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    arguments = parser.parse_args(argv)
    try:
        result = RollbackController(
            _paths(arguments.root), timeout=arguments.timeout
        ).run(
            apply=arguments.apply,
            maintenance_window=arguments.maintenance_window,
        )
    except (DeploymentError, OSError) as error:
        return _print_error(error)
    print(result.message)
    if result.success and result.changed:
        print("HUMAN ACCEPTANCE REQUIRED: verify the vendor direct voice conversation.")
    return 0 if result.success else 1


def _resolve_namespace(paths: DeploymentPaths) -> str:
    override = os.environ.get("CASBOT_ROS_NAMESPACE", "").strip().strip("/")
    if override:
        value = override
    else:
        try:
            config = json.loads(paths.user_config.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DeploymentError("unable to read robot namespace") from error
        value = str(config.get("namespace") or "").strip().strip("/")
    if ROS_TOKEN.fullmatch(value) is None:
        raise DeploymentError("namespace must be one non-empty token")
    return value


def metadata_probe_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="casbot-yandex-probe-dialog-metadata")
    parser.add_argument("--root", default="/")
    parser.add_argument("--namespace")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)
    paths = _paths(arguments.root)
    try:
        namespace = (
            arguments.namespace.strip().strip("/")
            if arguments.namespace
            else _resolve_namespace(paths)
        )
        if ROS_TOKEN.fullmatch(namespace) is None:
            raise DeploymentError("namespace must be one non-empty token")
        metadata = probe_first_metadata(
            RclpyMetadataRuntime(),
            topic=f"/{namespace}/audio/dialog_play",
            timeout=arguments.timeout,
        )
    except KeyboardInterrupt:
        print("metadata probe interrupted", file=sys.stderr)
        return 130
    except (ProbeTimeout, ProbeExternalShutdown, DeploymentError, ImportError) as error:
        return _print_error(error)
    print(format_metadata(metadata, json_output=arguments.json))
    return 0
