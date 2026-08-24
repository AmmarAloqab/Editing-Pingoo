from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from .config import get_config, ensure_runtime_dirs
from .errors import FlowWorkerError
from .flow_browser import GoogleFlowBrowser
from .uploader import upload_material


config = get_config()
ensure_runtime_dirs(config)
app = FastAPI(title="Pingoo Google Flow Worker")


class FlowGenerateRequest(BaseModel):
    scene_id: int
    prompt: str
    aspect_ratio: str = "9:16"
    media_type: Literal["video", "image"] = "video"


@app.get("/health")
def health():
    return {"ok": True, "service": "pingoo-google-flow"}


@app.get("/flow/status")
def flow_status():
    return GoogleFlowBrowser(config).status()


@app.get("/flow/diagnostics")
def flow_diagnostics():
    return GoogleFlowBrowser(config).diagnostics()


@app.post("/flow/generate")
def flow_generate(request: FlowGenerateRequest):
    started = time.monotonic()
    downloaded: Path | None = None
    try:
        browser = GoogleFlowBrowser(config)
        downloaded = browser.generate_and_download(
            scene_id=request.scene_id,
            prompt=request.prompt,
            aspect_ratio=request.aspect_ratio,
            media_type=request.media_type,
        )
        material_url = upload_material(downloaded, config)
        return {
            "ok": True,
            "scene_id": request.scene_id,
            "source": "flow",
            "material_url": material_url,
            "generation_seconds": round(time.monotonic() - started, 2),
        }
    except FlowWorkerError as exc:
        return {
            "ok": False,
            "scene_id": request.scene_id,
            "error": exc.code,
            "message": str(exc),
        }
    finally:
        if downloaded and downloaded.exists():
            try:
                downloaded.unlink()
            except OSError:
                pass


def main() -> None:
    import uvicorn

    uvicorn.run(
        "tools.google_flow_worker.worker:app",
        host=config.host,
        port=config.port,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
