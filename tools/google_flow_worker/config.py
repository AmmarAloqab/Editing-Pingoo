from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(os.getenv("PINGOO_FLOW_BASE_DIR", "/opt/pingoo-google-flow"))


@dataclass(frozen=True)
class FlowWorkerConfig:
    base_dir: Path = BASE_DIR
    profile_dir: Path = BASE_DIR / "profile"
    downloads_dir: Path = BASE_DIR / "downloads"
    logs_dir: Path = BASE_DIR / "logs"
    flow_url: str = os.getenv(
        "PINGOO_FLOW_URL",
        "https://labs.google/fx/tools/flow",
    )
    pingoo_upload_url: str = os.getenv(
        "PINGOO_UPLOAD_URL",
        "http://127.0.0.1:18080/api/v1/video_materials",
    )
    host: str = os.getenv("PINGOO_FLOW_WORKER_HOST", "172.18.0.1")
    port: int = int(os.getenv("PINGOO_FLOW_WORKER_PORT", "8767"))
    generation_timeout_seconds: int = int(
        os.getenv("FLOW_GENERATION_TIMEOUT_SECONDS", "900")
    )
    max_auto_flow_scenes: int = int(os.getenv("MAX_AUTO_FLOW_SCENES", "2"))
    browser_executable: str = os.getenv("PINGOO_CHROMIUM_EXECUTABLE", "")


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
