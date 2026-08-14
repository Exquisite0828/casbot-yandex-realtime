"""Central deployment paths with a temporary-root mapping for tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeploymentPaths:
    root: Path = Path("/")

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())

    def resolve(self, logical: str | Path) -> Path:
        value = Path(logical)
        if not value.is_absolute():
            raise ValueError(f"deployment path must be absolute: {value}")
        if self.root == Path("/"):
            return value
        return self.root / value.relative_to("/")

    @property
    def is_real_root(self) -> bool:
        return self.root == Path("/")

    marker_logical = Path(
        "/etc/casbot-yandex-realtime/external-dialog.enabled"
    )
    config_logical = Path("/etc/casbot-yandex-realtime/casbot-yandex.yaml")
    env_logical = Path("/etc/casbot-yandex-realtime/yandex.env")
    vendor_launch_logical = Path(
        "/lingze/install/bringup/share/bringup/launch/launch/jijia.launch.py"
    )
    vendor_manifest_logical = Path(
        "/var/lib/casbot-yandex-realtime/vendor-gate-manifest.json"
    )
    vendor_backup_dir_logical = Path(
        "/var/lib/casbot-yandex-realtime/vendor-backups"
    )
    operation_state_dir_logical = Path(
        "/var/lib/casbot-yandex-realtime/operation-state"
    )
    workspace_logical = Path("/opt/casbot-yandex-realtime")
    project_executable_logical = Path(
        "/opt/casbot-yandex-realtime/install/realtime_dialog/lib/"
        "realtime_dialog/realtime_dialog_node"
    )

    @property
    def marker(self) -> Path:
        return self.resolve(self.marker_logical)

    @property
    def config(self) -> Path:
        return self.resolve(self.config_logical)

    @property
    def env(self) -> Path:
        return self.resolve(self.env_logical)

    @property
    def vendor_launch(self) -> Path:
        return self.resolve(self.vendor_launch_logical)

    @property
    def vendor_manifest(self) -> Path:
        return self.resolve(self.vendor_manifest_logical)

    @property
    def vendor_backup_dir(self) -> Path:
        return self.resolve(self.vendor_backup_dir_logical)

    @property
    def operation_state_dir(self) -> Path:
        return self.resolve(self.operation_state_dir_logical)

    @property
    def operation_lock(self) -> Path:
        return self.operation_state_dir / "operation.lock"

    @property
    def workspace(self) -> Path:
        return self.resolve(self.workspace_logical)

    @property
    def venv_python(self) -> Path:
        return self.workspace / "venv" / "bin" / "python"

    @property
    def project_install_setup(self) -> Path:
        return self.workspace / "install" / "setup.bash"

    @property
    def project_executable(self) -> Path:
        return self.resolve(self.project_executable_logical)

    @property
    def project_package(self) -> Path:
        return self.workspace / "src" / "realtime_dialog" / "package.xml"

    @property
    def project_launch(self) -> Path:
        return (
            self.workspace
            / "src"
            / "realtime_dialog"
            / "launch"
            / "casbot_realtime_dialog.launch.py"
        )

    @property
    def ros_setup(self) -> Path:
        tros = self.resolve("/opt/tros/humble/setup.bash")
        return tros if tros.exists() else self.resolve("/opt/ros/humble/setup.bash")

    @property
    def vendor_setup(self) -> Path:
        return self.resolve("/lingze/install/setup.bash")

    @property
    def user_config(self) -> Path:
        return self.resolve("/lingze/config/user_config.json")

    @property
    def colcon(self) -> Path:
        return self.resolve("/usr/local/bin/colcon")

    @property
    def system_python(self) -> Path:
        return self.resolve("/usr/bin/python3")

    @property
    def arecord(self) -> Path:
        return self.resolve("/usr/bin/arecord")

    @property
    def capture_device(self) -> Path:
        return self.resolve("/dev/snd/pcmC0D0c")
