from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

from .config import get_config, ensure_runtime_dirs
from .errors import FlowWorkerError
from .flow_browser import GoogleFlowBrowser
from .uploader import upload_material


config = get_config()
ensure_runtime_dirs(config)
app = FastAPI(title="Pingoo Google Flow Worker")

_jobs_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pingoo-flow")


class FlowGenerateRequest(BaseModel):
    scene_id: int
    prompt: str
    aspect_ratio: str = "9:16"
    media_type: Literal["video", "image"] = "video"


def _now() -> float:
    return round(time.time(), 3)


def _safe_log(event: str, *, job_id: str, scene_id: int, code: str | None = None) -> None:
    suffix = f" code={code}" if code else ""
    print(f"{event} job_id={job_id} scene={scene_id}{suffix}", flush=True)


def _set_job(job_id: str, **updates) -> dict:
    with _jobs_lock:
        current = dict(_jobs.get(job_id) or {})
        current.update(updates)
        current["updated_at"] = _now()
        _jobs[job_id] = current
        return dict(current)


def _get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _job_payload(job: dict) -> dict:
    payload = {
        "ok": job.get("state") != "failed",
        "job_id": job.get("job_id"),
        "scene_id": job.get("scene_id"),
        "state": job.get("state"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }
    if job.get("state") == "completed":
        payload.update(
            {
                "source": "flow",
                "material_url": job.get("material_url") or "",
                "generation_seconds": job.get("generation_seconds") or 0,
            }
        )
    if job.get("state") == "failed":
        payload.update(
            {
                "error": job.get("error_code") or "FLOW_WORKER_ERROR",
                "error_code": job.get("error_code") or "FLOW_WORKER_ERROR",
            }
        )
    return payload


def _flow_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    return str(code or "FLOW_WORKER_ERROR")


def _run_flow_job(job_id: str, request: FlowGenerateRequest) -> None:
    started = time.monotonic()
    downloaded: Path | None = None

    def log_event(event: str) -> None:
        _safe_log(event, job_id=job_id, scene_id=request.scene_id)

    try:
        _set_job(job_id, state="generating")
        log_event("FLOW_BROWSER_STARTED")
        browser = GoogleFlowBrowser(config)
        downloaded = browser.generate_and_download(
            scene_id=request.scene_id,
            prompt=request.prompt,
            aspect_ratio=request.aspect_ratio,
            media_type=request.media_type,
            log_event=log_event,
        )
        _set_job(job_id, state="uploading")
        log_event("FLOW_UPLOAD_STARTED")
        material_url = upload_material(downloaded, config)
        log_event("FLOW_UPLOAD_COMPLETED")
        _set_job(
            job_id,
            state="completed",
            material_url=material_url,
            generation_seconds=round(time.monotonic() - started, 2),
        )
        log_event("FLOW_JOB_COMPLETED")
    except FlowWorkerError as exc:
        code = _flow_error_code(exc)
        _set_job(job_id, state="failed", error_code=code)
        _safe_log("FLOW_JOB_FAILED", job_id=job_id, scene_id=request.scene_id, code=code)
    except Exception:
        code = "FLOW_WORKER_ERROR"
        _set_job(job_id, state="failed", error_code=code)
        _safe_log("FLOW_JOB_FAILED", job_id=job_id, scene_id=request.scene_id, code=code)
    finally:
        if downloaded and downloaded.exists():
            try:
                downloaded.unlink()
            except OSError:
                pass


@app.get("/health")
def health():
    return {"ok": True, "service": "pingoo-google-flow"}


@app.get("/flow/status")
def flow_status():
    return GoogleFlowBrowser(config).status()


@app.get("/flow/diagnostics")
def flow_diagnostics():
    return GoogleFlowBrowser(config).diagnostics()


@app.get("/flow/ui-inventory")
def flow_ui_inventory():
    return GoogleFlowBrowser(config).ui_inventory()


@app.post("/flow/generate")
def flow_generate(request: FlowGenerateRequest):
    job_id = uuid4().hex
    now = _now()
    job = {
        "ok": True,
        "job_id": job_id,
        "scene_id": request.scene_id,
        "state": "queued",
        "created_at": now,
        "updated_at": now,
    }
    with _jobs_lock:
        _jobs[job_id] = dict(job)
    _safe_log("FLOW_JOB_CREATED", job_id=job_id, scene_id=request.scene_id)
    _executor.submit(_run_flow_job, job_id, request)
    return _job_payload(job)


@app.get("/flow/jobs/{job_id}")
def flow_job_status(job_id: str):
    job = _get_job(job_id)
    if not job:
        return {"ok": False, "job_id": job_id, "state": "failed", "error": "FLOW_JOB_NOT_FOUND", "error_code": "FLOW_JOB_NOT_FOUND"}
    return _job_payload(job)


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
