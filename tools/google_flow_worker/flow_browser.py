from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

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
from .windows_chrome import find_chrome_executable


AUTH_TEXT_MARKERS = (
    "sign in",
    "تسجيل الدخول",
    "email or phone",
    "use your google account",
    "captcha",
    "verify it",
    "security check",
)

FLOW_UI_MARKERS = (
    "generate",
    "create",
    "prompt",
    "image",
    "video",
    "ingredients",
    "frames",
)


class GoogleFlowBrowser:
    def __init__(self, config: FlowWorkerConfig):
        self.config = config
        ensure_runtime_dirs(config)

    def _browser_executable(self) -> str:
        if self.config.browser_executable:
            return self.config.browser_executable
        return find_chrome_executable()

    def _context_kwargs(self, headless: bool | None = None) -> dict:
        kwargs = {
            "user_data_dir": str(self.config.profile_dir),
            "headless": self.config.headless if headless is None else headless,
            "accept_downloads": True,
            "downloads_path": str(self.config.downloads_dir),
            "viewport": {"width": 1280, "height": 900},
        }
        executable = self._browser_executable()
        if executable:
            kwargs["executable_path"] = executable
        return kwargs

    def status(self) -> dict:
        context = None
        page = None
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(**self._context_kwargs())
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.config.flow_url, wait_until="domcontentloaded", timeout=60000)
                return self._classify_page(page)
        except Exception as exc:
            return {
                "authenticated": False,
                "error_code": self._status_error_code(exc),
                "error": type(exc).__name__,
                "page_title": self._safe_title(page),
                "current_url": self._safe_url(page),
            }
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def _is_authenticated(self, page) -> bool:
        return bool(self._classify_page(page).get("authenticated"))

    def _classify_page(self, page) -> dict:
        title = self._safe_title(page)
        url = self._safe_url(page)
        text = self._safe_body_text(page)
        host = urlparse(url).netloc.lower()

        if "accounts.google.com" in host or any(marker in text for marker in AUTH_TEXT_MARKERS):
            return {
                "authenticated": False,
                "error_code": "FLOW_LOGIN_PAGE",
                "page_title": title,
                "current_url": url,
            }

        page_signal = f"{title} {url}".lower()
        has_flow_page = "flow" in page_signal or "labs.google" in host
        has_flow_ui = any(marker in text for marker in FLOW_UI_MARKERS)
        if has_flow_page and has_flow_ui:
            return {
                "authenticated": True,
                "error_code": "FLOW_UI_READY",
                "page_title": title,
                "current_url": url,
            }

        return {
            "authenticated": False,
            "error_code": "FLOW_UI_UNKNOWN",
            "page_title": title,
            "current_url": url,
        }

    def _safe_body_text(self, page) -> str:
        try:
            return page.locator("body").inner_text(timeout=10000).lower()
        except Exception:
            return ""

    def _safe_title(self, page) -> str:
        if not page:
            return ""
        try:
            return page.title()
        except Exception:
            return ""

    def _safe_url(self, page) -> str:
        if not page:
            return ""
        try:
            return page.url
        except Exception:
            return ""

    def _status_error_code(self, exc: Exception) -> str:
        if type(exc).__name__ == "Error":
            return "FLOW_BROWSER_ERROR"
        if isinstance(exc, PlaywrightTimeoutError):
            return "FLOW_STATUS_TIMEOUT"
        return "FLOW_STATUS_ERROR"

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
            context = playwright.chromium.launch_persistent_context(**self._context_kwargs())
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
