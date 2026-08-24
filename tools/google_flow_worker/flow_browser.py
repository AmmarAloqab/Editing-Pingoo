from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .config import FlowWorkerConfig, ensure_runtime_dirs
from .errors import (
    FlowAuthRequired,
    FlowDownloadFailed,
    FlowGenerationFailed,
    FlowGenerationTimeout,
    FlowUiChanged,
)


AUTH_TEXT_MARKERS = (
    "sign in",
    "تسجيل الدخول",
    "captcha",
    "verify it",
    "security check",
)


class GoogleFlowBrowser:
    def __init__(self, config: FlowWorkerConfig):
        self.config = config
        ensure_runtime_dirs(config)

    def _launch_context(self, headless: bool):
        kwargs = {
            "user_data_dir": str(self.config.profile_dir),
            "headless": headless,
            "accept_downloads": True,
            "downloads_path": str(self.config.downloads_dir),
            "viewport": {"width": 1280, "height": 900},
        }
        if self.config.browser_executable:
            kwargs["executable_path"] = self.config.browser_executable
        return sync_playwright().start(), kwargs

    def status(self) -> dict:
        try:
            with sync_playwright() as playwright:
                browser_type = playwright.chromium
                kwargs = {
                    "user_data_dir": str(self.config.profile_dir),
                    "headless": True,
                    "accept_downloads": True,
                    "downloads_path": str(self.config.downloads_dir),
                }
                if self.config.browser_executable:
                    kwargs["executable_path"] = self.config.browser_executable
                context = browser_type.launch_persistent_context(**kwargs)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.config.flow_url, wait_until="domcontentloaded", timeout=60000)
                authenticated = self._is_authenticated(page)
                title = page.title()
                url = page.url
                context.close()
                return {
                    "authenticated": authenticated,
                    "title": title,
                    "url": url,
                }
        except Exception as exc:
            return {
                "authenticated": False,
                "error": type(exc).__name__,
            }

    def _is_authenticated(self, page) -> bool:
        text = ""
        try:
            text = page.locator("body").inner_text(timeout=10000).lower()
        except Exception:
            pass
        if any(marker in text for marker in AUTH_TEXT_MARKERS):
            return False
        return "flow" in page.url.lower() or "flow" in page.title().lower()

    def assert_authenticated(self) -> None:
        status = self.status()
        if not status.get("authenticated"):
            raise FlowAuthRequired("Google Flow authenticated session required")

    def generate_and_download(
        self,
        *,
        scene_id: int,
        prompt: str,
        aspect_ratio: str = "9:16",
        media_type: str = "video",
    ) -> Path:
        started = time.monotonic()
        timeout_ms = self.config.generation_timeout_seconds * 1000
        with sync_playwright() as playwright:
            kwargs = {
                "user_data_dir": str(self.config.profile_dir),
                "headless": True,
                "accept_downloads": True,
                "downloads_path": str(self.config.downloads_dir),
                "viewport": {"width": 1280, "height": 900},
            }
            if self.config.browser_executable:
                kwargs["executable_path"] = self.config.browser_executable
            context = playwright.chromium.launch_persistent_context(**kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(self.config.flow_url, wait_until="domcontentloaded", timeout=60000)
                if not self._is_authenticated(page):
                    raise FlowAuthRequired("Google Flow authenticated session required")

                self._submit_prompt(page, prompt, aspect_ratio, media_type)
                download_path = self._wait_for_download(page, scene_id, timeout_ms)
                elapsed = time.monotonic() - started
                if elapsed > self.config.generation_timeout_seconds:
                    raise FlowGenerationTimeout("Google Flow generation timed out")
                if not download_path.exists() or download_path.stat().st_size <= 0:
                    raise FlowDownloadFailed("Google Flow download empty")
                return download_path
            except (FlowAuthRequired, FlowUiChanged, FlowGenerationFailed, FlowGenerationTimeout, FlowDownloadFailed):
                self._capture_debug(page, f"scene-{scene_id}")
                raise
            finally:
                context.close()

    def _submit_prompt(self, page, prompt: str, aspect_ratio: str, media_type: str) -> None:
        try:
            prompt_box = page.get_by_role("textbox").first
            prompt_box.fill(prompt, timeout=30000)
        except PlaywrightTimeoutError as exc:
            raise FlowUiChanged("Could not find Flow prompt textbox") from exc

        for label in (media_type, "Video", "Generate video"):
            try:
                page.get_by_text(label, exact=False).first.click(timeout=3000)
                break
            except Exception:
                continue

        if aspect_ratio:
            try:
                page.get_by_text(aspect_ratio, exact=False).first.click(timeout=3000)
            except Exception:
                pass

        for button_name in ("Generate", "Create", "Submit"):
            try:
                page.get_by_role("button", name=button_name).click(timeout=5000)
                return
            except Exception:
                continue
        raise FlowUiChanged("Could not find Flow generate button")

    def _wait_for_download(self, page, scene_id: int, timeout_ms: int) -> Path:
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            try:
                with page.expect_download(timeout=5000) as download_info:
                    for name in ("Download", "Export", "Save"):
                        try:
                            page.get_by_role("button", name=name).first.click(timeout=3000)
                            break
                        except Exception:
                            continue
                download = download_info.value
                suffix = Path(download.suggested_filename or "").suffix or ".mp4"
                target = self.config.downloads_dir / f"flow-scene-{scene_id}-{int(time.time())}{suffix}"
                download.save_as(str(target))
                return target
            except PlaywrightTimeoutError:
                page.wait_for_timeout(5000)
        raise FlowGenerationTimeout("Timed out waiting for Google Flow download")

    def _capture_debug(self, page, prefix: str) -> None:
        timestamp = int(time.time())
        safe_prefix = "".join(ch for ch in prefix if ch.isalnum() or ch in "-_")
        try:
            (self.config.logs_dir / f"{safe_prefix}-{timestamp}.txt").write_text(
                f"title={page.title()}\nurl={page.url}\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        try:
            page.screenshot(
                path=str(self.config.logs_dir / f"{safe_prefix}-{timestamp}.png"),
                full_page=True,
            )
        except Exception:
            pass
