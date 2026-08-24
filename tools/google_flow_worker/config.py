from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path


def _default_base_dir() -> Path:
    if platform.system().lower() == "windows":
        root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "PingooGoogleFlow"
    return Path("/opt/pingoo-google-flow")


def _default_host() -> str:
    if platform.system().lower() == "windows":
        return "127.0.0.1"
    return "172.20.0.1"


@dataclass(frozen=True)
class FlowWorkerConfig:
    base_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("PINGOO_FLOW_BASE_DIR", str(_default_base_dir()))
        )
    )
    profile_dir: Path | None = None
    downloads_dir: Path | None = None
    logs_dir: Path | None = None
    flow_url: str = os.getenv(
        "PINGOO_FLOW_URL",
        "https://labs.google/fx/tools/flow",
    )
    pingoo_upload_url: str = os.getenv(
        "PINGOO_UPLOAD_URL",
        "http://127.0.0.1:18080/api/v1/video_materials",
    )
    host: str = field(
        default_factory=lambda: os.getenv("PINGOO_FLOW_WORKER_HOST", _default_host())
    )
    port: int = field(
        default_factory=lambda: int(os.getenv("PINGOO_FLOW_WORKER_PORT", "8767"))
    )
    generation_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("FLOW_GENERATION_TIMEOUT_SECONDS", "900"))
    )
    max_auto_flow_scenes: int = field(
        default_factory=lambda: int(os.getenv("MAX_AUTO_FLOW_SCENES", "2"))
    )
    browser_executable: str = field(
        default_factory=lambda: (
            os.getenv("PINGOO_CHROME_EXECUTABLE")
            or os.getenv("PINGOO_CHROMIUM_EXECUTABLE", "")
        )
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_dir", self.profile_dir or self.base_dir / "profile")
        object.__setattr__(self, "downloads_dir", self.downloads_dir or self.base_dir / "downloads")
        object.__setattr__(self, "logs_dir", self.logs_dir or self.base_dir / "logs")


def get_config() -> FlowWorkerConfig:
    return FlowWorkerConfig()


def ensure_runtime_dirs(config: FlowWorkerConfig | None = None) -> None:
    cfg = config or get_config()
    for directory in (
        cfg.base_dir,
        cfg.profile_dir,
        cfg.downloads_dir,
        cfg.logs_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
