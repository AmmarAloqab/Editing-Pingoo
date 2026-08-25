from __future__ import annotations

import re
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

FLOW_AUTHENTICATED_CONTROLS = (
    "new project",
    "edit project",
    "delete project",
    "explore tools",
    "flow tv",
    "flow music",
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
                page.wait_for_timeout(2000)
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
        diagnostics = self._page_diagnostics(page)

        if "accounts.google.com" in host:
            return {
                "authenticated": False,
                "error_code": "AUTH_REQUIRED",
                "page_title": title,
                "current_url": url,
            }

        if diagnostics["has_sign_in"] and not diagnostics["has_google_account_button"]:
            return {
                "authenticated": False,
                "error_code": "AUTH_REQUIRED",
                "page_title": title,
                "current_url": url,
            }

        if any(marker in text for marker in AUTH_TEXT_MARKERS):
            return {
                "authenticated": False,
                "error_code": "FLOW_LOGIN_PAGE",
                "page_title": title,
                "current_url": url,
            }

        page_signal = f"{title} {url}".lower()
        has_flow_page = "flow" in page_signal or "labs.google" in host
        has_flow_ui = any(marker in text for marker in FLOW_UI_MARKERS)
        has_authenticated_controls = bool(diagnostics["detected_flow_controls"])
        if (
            has_flow_page
            and not diagnostics["has_sign_in"]
            and (diagnostics["has_google_account_button"] or has_authenticated_controls or has_flow_ui)
        ):
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

    def diagnostics(self) -> dict:
        context = None
        page = None
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(**self._context_kwargs())
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.config.flow_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
                return self._page_diagnostics(page)
        except Exception:
            return {
                "url": self._safe_url(page),
                "title": self._safe_title(page),
                "has_google_account_button": False,
                "has_sign_in": False,
                "detected_flow_controls": [],
            }
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def _page_diagnostics(self, page) -> dict:
        buttons = self._visible_role_texts(page, "button")
        links = self._visible_role_texts(page, "link")
        aria_labels = self._aria_labels(page)
        control_names = buttons + links + aria_labels
        detected_controls = []
        for value in control_names:
            lowered = value.lower()
            for marker in FLOW_AUTHENTICATED_CONTROLS:
                if marker in lowered and marker not in detected_controls:
                    detected_controls.append(marker)

        return {
            "url": self._safe_url(page),
            "title": self._safe_title(page),
            "has_google_account_button": self._has_google_account_button(page, aria_labels),
            "has_sign_in": self._has_sign_in(page, buttons, links),
            "detected_flow_controls": detected_controls,
        }

    def _visible_role_texts(self, page, role: str, limit: int = 40) -> list[str]:
        values = []
        try:
            locator = page.get_by_role(role)
            count = min(locator.count(), limit)
            for index in range(count):
                try:
                    text = locator.nth(index).inner_text(timeout=1000).strip()
                    if text and text not in values:
                        values.append(text)
                except Exception:
                    continue
        except Exception:
            pass
        return values

    def _aria_labels(self, page, limit: int = 80) -> list[str]:
        values = []
        try:
            locator = page.locator("[aria-label]")
            count = min(locator.count(), limit)
            for index in range(count):
                try:
                    label = locator.nth(index).get_attribute("aria-label", timeout=1000)
                    if label and label not in values:
                        values.append(label)
                except Exception:
                    continue
        except Exception:
            pass
        return values

    def _has_google_account_button(self, page, aria_labels: list[str]) -> bool:
        if any("google account" in label.lower() for label in aria_labels):
            return True
        try:
            pattern = re.compile(r"google account|account menu|profile", re.I)
            return page.get_by_role("button", name=pattern).count() > 0
        except Exception:
            return False

    def _has_sign_in(self, page, buttons: list[str], links: list[str]) -> bool:
        labels = [value.lower() for value in buttons + links]
        if any(label.strip() in {"sign in", "تسجيل الدخول"} for label in labels):
            return True
        try:
            pattern = re.compile(r"^sign in$|تسجيل الدخول", re.I)
            return (
                page.get_by_role("button", name=pattern).count() > 0
                or page.get_by_role("link", name=pattern).count() > 0
            )
        except Exception:
            return False

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
        log_event=None,
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

                self._submit_prompt(page, prompt, aspect_ratio, media_type, log_event=log_event)
                if log_event:
                    log_event("FLOW_GENERATION_WAITING")
                download_path = self._wait_for_download(page, scene_id, timeout_ms, log_event=log_event)
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

    def _submit_prompt(self, page, prompt: str, aspect_ratio: str, media_type: str, log_event=None) -> None:
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
                if log_event:
                    log_event("FLOW_PROMPT_SUBMITTED")
                return
            except Exception:
                continue
        raise FlowUiChanged("Could not find Flow generate button")

    def _wait_for_download(self, page, scene_id: int, timeout_ms: int, log_event=None) -> Path:
        deadline = time.monotonic() + (timeout_ms / 1000)
        download_started_logged = False
        generation_completed_logged = False
        while time.monotonic() < deadline:
            try:
                with page.expect_download(timeout=5000) as download_info:
                    for name in ("Download", "Export", "Save"):
                        try:
                            page.get_by_role("button", name=name).first.click(timeout=3000)
                            if log_event and not generation_completed_logged:
                                log_event("FLOW_GENERATION_COMPLETED")
                                generation_completed_logged = True
                            if log_event and not download_started_logged:
                                log_event("FLOW_DOWNLOAD_STARTED")
                                download_started_logged = True
                            break
                        except Exception:
                            continue
                download = download_info.value
                suffix = Path(download.suggested_filename or "").suffix or ".mp4"
                target = self.config.downloads_dir / f"flow-scene-{scene_id}-{int(time.time())}{suffix}"
                download.save_as(str(target))
                if log_event:
                    log_event("FLOW_DOWNLOAD_COMPLETED")
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
