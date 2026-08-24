from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from .config import FlowWorkerConfig
from .errors import PingooUploadFailed


def upload_material(path: Path, config: FlowWorkerConfig) -> str:
    if not path.exists() or path.stat().st_size <= 0:
        raise PingooUploadFailed(f"downloaded file is missing or empty: {path}")

    with path.open("rb") as handle:
        response = requests.post(
            config.pingoo_upload_url,
            files={"file": (path.name, handle)},
            timeout=120,
        )

    if response.status_code != 200:
        raise PingooUploadFailed(
            f"Pingoo upload failed: status={response.status_code}"
        )

    try:
        payload: dict[str, Any] = response.json()
    except ValueError as exc:
        raise PingooUploadFailed("Pingoo upload returned non-JSON response") from exc

    material_url = str((payload.get("data") or {}).get("file") or "").strip()
    if not material_url:
        raise PingooUploadFailed(f"Pingoo upload response missing data.file: {payload}")
    return material_url
