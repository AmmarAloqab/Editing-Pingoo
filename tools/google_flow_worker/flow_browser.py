from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .config import FlowWorkerConfig, ensure_runtime_dirs
from .errors import (
    FlowAuthRequired,
    FlowBlockedEmptyDom,
    FlowDownloadFailed,
    FlowGenerateActionChanged,
    FlowGenerateButtonChanged,
    FlowGenerationFailed,
    FlowGenerationTimeout,
    FlowProjectCreateChanged,
    FlowProjectNavigationFailed,
    FlowProjectNavigationChanged,
    FlowPromptInputChanged,
    FlowUiChanged,
    FlowWorkspaceLoadChanged,
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

FLOW_STATES = {
    "AUTH_REQUIRED",
    "FLOW_BLOCKED_EMPTY_DOM",
    "LANDING",
    "PROJECT_LIST",
    "PROJECT_LIST_READY",
    "PROJECT_CARD_FOUND",
    "CREATE_PROJECT_REQUIRED",
    "PROJECT_WORKSPACE",
    "CREATE_DIALOG",
    "VIDEO_COMPOSER",
    "WORKSPACE_READY",
    "GENERATING",
    "RESULT_READY",
    "UNKNOWN",
}

PROJECT_EXISTING_PATTERN = re.compile(
    r"edit project|open project|continue project|resume project|"
    r"تعديل المشروع|فتح المشروع|افتح المشروع|متابعة المشروع",
    re.I,
)
PROJECT_NEW_PATTERN = re.compile(
    r"new project|create project|مشروع جديد|إنشاء مشروع|انشاء مشروع",
    re.I,
)
PROJECT_LIST_PATTERN = re.compile(
    r"projects|my projects|edit project|delete project|"
    r"المشاريع|مشروعاتي|تعديل المشروع|حذف المشروع",
    re.I,
)
PROJECT_CREATE_CONFIRM_PATTERN = re.compile(
    r"^create$|create project|^إنشاء$|^انشاء$|إنشاء مشروع|انشاء مشروع",
    re.I,
)
LANDING_START_PATTERN = re.compile(
    r"start creating|try flow|open flow|launch flow|ابدأ|جرّب|جرب|افتح flow",
    re.I,
)
VIDEO_MODE_PATTERN = re.compile(
    r"^video$|video mode|^فيديو$|وضع الفيديو",
    re.I,
)
GENERATE_PATTERN = re.compile(
    r"^generate$|generate video|^create$|^submit$|^send$|"
    r"^توليد$|^إنشاء$|^انشاء$|إرسال|ارسل|أنشئ|انشئ",
    re.I,
)
DOWNLOAD_PATTERN = re.compile(
    r"download|export|save video|تنزيل|تحميل|تصدير|حفظ الفيديو",
    re.I,
)
GENERATING_PATTERN = re.compile(
    r"generating|creating|rendering|processing|cancel generation|stop generation|"
    r"جار(?:ٍ|ي) التوليد|جار(?:ٍ|ي) الإنشاء|قيد المعالجة|إيقاف التوليد|إلغاء التوليد",
    re.I,
)
WORKSPACE_PATTERN = re.compile(
    r"project settings|project assets|ingredients|frames|scenes|timeline|"
    r"إعدادات المشروع|مواد المشروع|المكوّنات|المكونات|الإطارات|المشاهد|الخط الزمني",
    re.I,
)
CREATE_DIALOG_PATTERN = re.compile(
    r"create (?:a )?project|new project|project name|"
    r"إنشاء مشروع|انشاء مشروع|مشروع جديد|اسم المشروع",
    re.I,
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

        if diagnostics.get("state") == "FLOW_BLOCKED_EMPTY_DOM":
            return {
                "authenticated": False,
                "error_code": "FLOW_BLOCKED_EMPTY_DOM",
                "page_title": title,
                "current_url": url,
            }

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
        except Exception as exc:
            return {
                "url": self._safe_url(page),
                "title": self._safe_title(page),
                "has_google_account_button": False,
                "has_sign_in": False,
                "detected_flow_controls": [],
                "error_code": self._status_error_code(exc),
            }
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def ui_inventory(self) -> dict:
        context = None
        page = None
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(**self._context_kwargs())
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.config.flow_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                return self.collect_safe_ui_snapshot(page)
        except Exception as exc:
            inventory = self.collect_safe_ui_snapshot(page) if page else {}
            inventory["error_code"] = self._status_error_code(exc)
            return inventory
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def workspace_probe(self) -> dict:
        result = self.dry_run()
        return {
            "url": result.get("url", result.get("workspace_url", "")),
            "title": result.get("title", result.get("workspace_title", "")),
            "iframe_count": result.get("iframe_count", 0),
            "recaptcha_detected": result.get("recaptcha_detected", False),
            "state": result.get("state", result.get("workspace_state", "UNKNOWN")),
            "workspace_ready": result.get("ready_for_generation", False),
            "workspace_url": result.get("workspace_url", ""),
            "workspace_title": result.get("workspace_title", ""),
            "project_navigation": result.get("project_navigation", "not_needed"),
            "prompt_input_found": result.get("prompt_input_found", False),
            "prompt_frame": result.get("prompt_frame", ""),
            "generate_action_found": result.get("generate_action_found", False),
            "error_code": result.get("error_code"),
        }

    def dry_run(self) -> dict:
        context = None
        page = None
        trace = []
        result = self._dry_run_result()
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(**self._context_kwargs())
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.config.flow_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
                result["initial_state"] = self.detect_flow_state(page)
                prepared = self._prepare_generation_surface(page, trace=trace)
                diagnostics = self._dom_readiness_diagnostics(page)
                result.update({key: value for key, value in prepared.items() if key not in {"prompt_input", "generate_action"}})
                result.update(
                    {
                        "authenticated": True,
                        "credit_consumed": False,
                        "ready_for_generation": True,
                        "workspace_url": self._safe_url(page),
                        "workspace_title": self._safe_title(page),
                        "url": self._safe_url(page),
                        "title": self._safe_title(page),
                        "iframe_count": diagnostics["iframe_count"],
                        "recaptcha_detected": diagnostics["recaptcha_detected"],
                        "state": diagnostics["state"],
                        "error_code": None,
                    }
                )
                self._write_safe_trace(trace)
                return result
        except (FlowAuthRequired, FlowUiChanged) as exc:
            result.update(self._probe_failure_fields(page))
            result["project_navigation"] = self._navigation_from_trace(trace)
            result["error_code"] = exc.code
            result["authenticated"] = not isinstance(exc, FlowAuthRequired)
            trace.append({"step": "dry_run_failed", "state": self.detect_flow_state(page), "error_code": exc.code})
            self._record_ui_failure(page, trace, "dry-run")
            return result
        except Exception as exc:
            result.update(self._probe_failure_fields(page))
            result["project_navigation"] = self._navigation_from_trace(trace)
            result["error_code"] = self._status_error_code(exc)
            trace.append({"step": "dry_run_failed", "state": self.detect_flow_state(page), "error_code": result["error_code"]})
            self._record_ui_failure(page, trace, "dry-run")
            return result
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def _dry_run_result(self) -> dict:
        return {
            "authenticated": False,
            "initial_state": "UNKNOWN",
            "workspace_state": "UNKNOWN",
            "workspace_url": "",
            "workspace_title": "",
            "url": "",
            "title": "",
            "iframe_count": 0,
            "recaptcha_detected": False,
            "state": "UNKNOWN",
            "project_navigation": "not_needed",
            "prompt_input_found": False,
            "prompt_frame": "",
            "generate_action_found": False,
            "credit_consumed": False,
            "ready_for_generation": False,
            "error_code": None,
        }

    def _probe_failure_fields(self, page) -> dict:
        if not page:
            return {}
        diagnostics = self._dom_readiness_diagnostics(page)
        prompt, prompt_frame = self.find_prompt_input(page)
        generate_action, _frame = self.find_generate_action(page)
        return {
            "url": diagnostics["url"],
            "title": diagnostics["title"],
            "iframe_count": diagnostics["iframe_count"],
            "recaptcha_detected": diagnostics["recaptcha_detected"],
            "state": diagnostics["state"],
            "workspace_state": self.detect_flow_state(page),
            "workspace_url": self._safe_url(page),
            "workspace_title": self._safe_title(page),
            "prompt_input_found": prompt is not None,
            "prompt_frame": prompt_frame,
            "generate_action_found": generate_action is not None,
            "credit_consumed": False,
            "ready_for_generation": False,
        }

    def _navigation_from_trace(self, trace: list[dict]) -> str:
        steps = {entry.get("step") for entry in trace}
        if "open_existing_project" in steps:
            return "existing"
        if "open_new_project" in steps or "open_landing_action" in steps:
            return "new"
        return "not_needed"

    def _page_diagnostics(self, page) -> dict:
        inventory = self._safe_ui_inventory(page)
        buttons = inventory.get("buttons", [])
        links = inventory.get("links", [])
        aria_labels = inventory.get("aria_labels", [])
        control_names = buttons + links + aria_labels
        detected_controls = []
        for value in control_names:
            lowered = value.lower()
            for marker in FLOW_AUTHENTICATED_CONTROLS:
                if marker in lowered and marker not in detected_controls:
                    detected_controls.append(marker)

        blocked_empty_dom = self._is_empty_dom_snapshot(inventory)
        return {
            "url": self._safe_url(page),
            "title": self._safe_title(page),
            "iframe_count": inventory.get("frame_count", 0),
            "recaptcha_detected": bool(inventory.get("recaptcha_detected")),
            "state": "FLOW_BLOCKED_EMPTY_DOM" if blocked_empty_dom else self.detect_flow_state(page),
            "error_code": "FLOW_BLOCKED_EMPTY_DOM" if blocked_empty_dom else None,
            "has_google_account_button": self._has_google_account_button(page, aria_labels),
            "has_sign_in": self._has_sign_in(page, buttons, links),
            "detected_flow_controls": detected_controls,
            "visible_input_count": inventory.get("visible_input_count", 0),
            "button_labels_safe": buttons[:30],
            "link_labels_safe": links[:30],
            "placeholders_safe": inventory.get("placeholders", [])[:30],
            "aria_labels_safe": aria_labels[:40],
            "contenteditable_count": inventory.get("contenteditable_count", 0),
            "frame_count": inventory.get("frame_count", 0),
        }

    def collect_safe_ui_snapshot(self, page) -> dict:
        if not page:
            return {
                "url": "",
                "title": "",
                "frames": [],
                "buttons": [],
                "links": [],
                "textboxes": [],
                "placeholders": [],
                "aria_labels": [],
                "role_names": {},
                "visible_input_count": 0,
                "textarea_count": 0,
                "contenteditable_count": 0,
                "frame_count": 0,
                "video_elements_count": 0,
                "image_elements_count": 0,
                "recaptcha_detected": False,
            }
        frames = self._candidate_frames(page)
        buttons = self._visible_role_texts_all(frames, "button")
        links = self._visible_role_texts_all(frames, "link")
        textboxes = self._visible_role_texts_all(frames, "textbox")
        role_names = {
            role: self._visible_role_texts_all(frames, role, limit=40)
            for role in ("button", "link", "textbox", "dialog", "article", "listitem", "tab")
        }
        frame_urls = [self._safe_frame_url(frame) for frame in frames]
        return {
            "url": self._safe_url(page),
            "title": self._safe_title(page),
            "frames": frame_urls,
            "buttons": buttons[:40],
            "links": links[:40],
            "textboxes": textboxes[:30],
            "placeholders": self._visible_attributes_all(frames, "[placeholder]", "placeholder")[:40],
            "aria_labels": self._visible_attributes_all(frames, "[aria-label]", "aria-label")[:80],
            "role_names": role_names,
            "visible_input_count": self._visible_locator_count_all(frames, "textarea, input, [contenteditable='true'], [role='textbox']"),
            "textarea_count": self._visible_locator_count_all(frames, "textarea"),
            "contenteditable_count": self._visible_locator_count_all(frames, "[contenteditable='true']"),
            "frame_count": max(0, len(frames) - 1),
            "video_elements_count": self._visible_locator_count_all(frames, "video"),
            "image_elements_count": self._visible_locator_count_all(frames, "img"),
            "recaptcha_detected": self._recaptcha_detected(frame_urls),
        }

    def _safe_ui_inventory(self, page) -> dict:
        return self.collect_safe_ui_snapshot(page)

    def _safe_frame_url(self, frame) -> str:
        try:
            url = str(frame.url or "")
        except Exception:
            return ""
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"}:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return url[:200]

    def detect_flow_state(self, page) -> str:
        if not page:
            return "UNKNOWN"
        snapshot = self.collect_safe_ui_snapshot(page)
        if self._is_empty_dom_snapshot(snapshot):
            return "FLOW_BLOCKED_EMPTY_DOM"
        url = snapshot.get("url", "")
        host = urlparse(url).netloc.lower()
        controls = self._snapshot_controls(snapshot)
        has_sign_in = self._has_sign_in(
            page,
            snapshot.get("buttons", []),
            snapshot.get("links", []),
        )
        if "accounts.google.com" in host or has_sign_in:
            return "AUTH_REQUIRED"

        named_prompt, _frame = self._find_named_prompt_input(page)
        prompt, _frame = self.find_prompt_input(page)
        generate, _frame = self.find_generate_action(page)
        download, _frame = self._find_download_action(page)
        has_media = (
            snapshot.get("video_elements_count", 0) > 0
            or snapshot.get("image_elements_count", 0) > 0
        )
        if download is not None and has_media:
            return "RESULT_READY"
        if GENERATING_PATTERN.search(controls):
            return "GENERATING"
        if prompt is not None and generate is not None:
            return "WORKSPACE_READY"
        if snapshot.get("role_names", {}).get("dialog") and CREATE_DIALOG_PATTERN.search(controls):
            return "CREATE_DIALOG"
        if named_prompt is not None:
            return "WORKSPACE_READY"
        if self._has_project_cards(page):
            return "PROJECT_CARD_FOUND"
        if prompt is not None or WORKSPACE_PATTERN.search(controls) or self._find_named_action(page, VIDEO_MODE_PATTERN)[0] is not None:
            return "WORKSPACE_READY"
        if PROJECT_NEW_PATTERN.search(controls):
            return "CREATE_PROJECT_REQUIRED"
        if PROJECT_LIST_PATTERN.search(controls):
            return "PROJECT_LIST_READY"
        if LANDING_START_PATTERN.search(controls):
            return "LANDING"
        return "UNKNOWN"

    def _is_empty_dom_snapshot(self, snapshot: dict) -> bool:
        return (
            not str(snapshot.get("title") or "").strip()
            and not snapshot.get("buttons")
            and not snapshot.get("links")
            and not snapshot.get("textboxes")
        )

    def _recaptcha_detected(self, frame_urls: list[str]) -> bool:
        return any("recaptcha" in str(url).lower() for url in frame_urls)

    def _dom_readiness_diagnostics(self, page) -> dict:
        snapshot = self.collect_safe_ui_snapshot(page)
        state = (
            "FLOW_BLOCKED_EMPTY_DOM"
            if self._is_empty_dom_snapshot(snapshot)
            else self.detect_flow_state(page)
        )
        return {
            "url": snapshot.get("url", ""),
            "title": snapshot.get("title", ""),
            "iframe_count": snapshot.get("frame_count", 0),
            "recaptcha_detected": bool(snapshot.get("recaptcha_detected")),
            "state": state,
            "error_code": "FLOW_BLOCKED_EMPTY_DOM" if state == "FLOW_BLOCKED_EMPTY_DOM" else None,
        }

    def _validate_dom_readiness(self, page, trace: list[dict]) -> None:
        diagnostics = self._dom_readiness_diagnostics(page)
        trace.append({"step": "validate_dom_readiness", **diagnostics})
        if diagnostics["state"] == "FLOW_BLOCKED_EMPTY_DOM":
            self._record_ui_failure(page, trace, "empty-dom")
            raise FlowBlockedEmptyDom("Flow DOM is empty; navigation blocked")

    def _snapshot_controls(self, snapshot: dict) -> str:
        values = []
        for key in ("buttons", "links", "textboxes", "placeholders", "aria_labels"):
            values.extend(snapshot.get(key, []))
        for role_values in snapshot.get("role_names", {}).values():
            values.extend(role_values)
        return "\n".join(str(value) for value in values if value)

    def _write_safe_trace(self, trace: list[dict]) -> None:
        path = self.config.base_dir / "flow-last-trace.json"
        try:
            path.write_text(
                json.dumps(trace, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _record_ui_failure(self, page, trace: list[dict], prefix: str) -> None:
        state = self.detect_flow_state(page)
        trace.append({"step": prefix, "state": state})
        self._write_safe_trace(trace)
        if state in {"UNKNOWN", "FLOW_BLOCKED_EMPTY_DOM"} or prefix in {
            "project-navigation",
            "workspace-load",
        }:
            try:
                snapshot = self.collect_safe_ui_snapshot(page)
                (self.config.diagnostics_dir / "flow-last-snapshot.json").write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
            self._save_failure_screenshot(page)

    def _save_failure_screenshot(self, page) -> None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        try:
            page.screenshot(
                path=str(self.config.diagnostics_dir / f"flow-failure-{timestamp}.png"),
                full_page=True,
            )
        except Exception:
            return
        try:
            screenshots = sorted(
                self.config.diagnostics_dir.glob("flow-failure-*.png"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for stale in screenshots[5:]:
                stale.unlink()
        except Exception:
            pass

    def _candidate_frames(self, page) -> list:
        frames = [page]
        try:
            frames.extend([frame for frame in page.frames if frame not in frames])
        except Exception:
            pass
        return frames

    def _visible_role_texts_all(self, frames: list, role: str, limit: int = 80) -> list[str]:
        values = []
        for frame in frames:
            for value in self._visible_role_texts(frame, role, limit=limit):
                safe_value = self._safe_control_label(value)
                if safe_value and safe_value not in values:
                    values.append(safe_value)
                if len(values) >= limit:
                    return values
        return values

    def _visible_attributes_all(self, frames: list, selector: str, attribute: str, limit: int = 80) -> list[str]:
        values = []
        for frame in frames:
            try:
                locator = frame.locator(selector)
                count = min(locator.count(), limit)
                for index in range(count):
                    try:
                        item = locator.nth(index)
                        if hasattr(item, "is_visible") and not item.is_visible(timeout=500):
                            continue
                        value = item.get_attribute(attribute, timeout=500)
                        safe_value = self._safe_control_label(value)
                        if safe_value and safe_value not in values:
                            values.append(safe_value)
                    except Exception:
                        continue
            except Exception:
                continue
            if len(values) >= limit:
                break
        return values[:limit]

    def _safe_control_label(self, value) -> str:
        label = str(value or "").strip()
        if not label or "@" in label:
            return ""
        return label[:200]

    def _visible_locator_count_all(self, frames: list, selector: str) -> int:
        total = 0
        for frame in frames:
            try:
                locator = frame.locator(selector)
                count = locator.count()
                for index in range(count):
                    try:
                        item = locator.nth(index)
                        if not hasattr(item, "is_visible") or item.is_visible(timeout=500):
                            total += 1
                    except Exception:
                        continue
            except Exception:
                continue
        return total

    def _first_visible_locator(self, frames: list, selector: str):
        for frame in frames:
            try:
                locator = frame.locator(selector)
                count = locator.count()
                for index in range(count):
                    item = locator.nth(index)
                    try:
                        if not hasattr(item, "is_visible") or item.is_visible(timeout=1000):
                            return item
                    except Exception:
                        return item
            except Exception:
                continue
        return None

    def _find_named_prompt_input(self, page):
        frames = self._candidate_frames(page)
        role_names = re.compile(
            r"prompt|describe|create|generate|اكتب|أدخل|ادخل|وصف|الفكرة|النص|المطالبة|موجّه|موجه|أنشئ|انشئ|توليد",
            re.I,
        )
        for index, frame in enumerate(frames):
            try:
                candidate = frame.get_by_role("textbox", name=role_names).first
                if candidate.count() and candidate.is_visible(timeout=1000):
                    return candidate, self._frame_label(index)
            except Exception:
                continue
        return None, ""

    def find_prompt_input(self, page):
        named, frame_label = self._find_named_prompt_input(page)
        if named is not None:
            return named, frame_label
        frames = self._candidate_frames(page)
        for index, frame in enumerate(frames):
            item = self._first_visible_locator(
                [frame],
                "textarea, [contenteditable='true'], [role='textbox']",
            )
            if item is not None:
                return item, self._frame_label(index)
        return None, ""

    def _first_prompt_input_with_frame(self, page):
        return self.find_prompt_input(page)

    def _frame_label(self, index: int) -> str:
        return "main" if index == 0 else f"frame_{index}"

    def _prompt_frame_label(self, page) -> str:
        _input, label = self.find_prompt_input(page)
        return label

    def _first_prompt_input(self, page):
        item, _label = self.find_prompt_input(page)
        return item

    def _find_named_action(self, page, pattern: re.Pattern):
        for index, frame in enumerate(self._candidate_frames(page)):
            for role in ("button", "link", "tab"):
                try:
                    item = frame.get_by_role(role, name=pattern).first
                    if item.count() and item.is_visible(timeout=1000):
                        return item, self._frame_label(index)
                except Exception:
                    continue
        return None, ""

    def _click_named_action(self, page, pattern: re.Pattern, timeout: int = 3000) -> bool:
        item, _label = self._find_named_action(page, pattern)
        if item is None:
            return False
        try:
            item.click(timeout=timeout)
            page.wait_for_timeout(1500)
            return True
        except Exception:
            return False

    def find_generate_action(self, page):
        return self._find_named_action(page, GENERATE_PATTERN)

    def _find_generate_action(self, page):
        return self.find_generate_action(page)

    def _find_download_action(self, page):
        return self._find_named_action(page, DOWNLOAD_PATTERN)

    def _has_project_cards(self, page) -> bool:
        return self._find_project_card_action(page) is not None

    def _find_project_card_action(self, page):
        for frame in self._candidate_frames(page):
            for role in ("article", "listitem", "group"):
                try:
                    cards = frame.get_by_role(role)
                    for index in range(min(cards.count(), 30)):
                        card = cards.nth(index)
                        if hasattr(card, "is_visible") and not card.is_visible(timeout=500):
                            continue
                        text = card.inner_text(timeout=1000).strip()
                        if not text or PROJECT_NEW_PATTERN.search(text):
                            continue
                        for nested_role in ("button", "link"):
                            nested = card.get_by_role(nested_role, name=PROJECT_EXISTING_PATTERN).first
                            if nested.count() and nested.is_visible(timeout=500):
                                return nested
                        card_role = card.get_attribute("role", timeout=500) or ""
                        tabindex = card.get_attribute("tabindex", timeout=500)
                        if card_role in {"button", "link"} or tabindex is not None:
                            return card
                except Exception:
                    continue
        return None

    def _wait_for_workspace_state(self, page, timeout_ms: int = 30000) -> str:
        state = self.detect_flow_state(page)
        for _ in range(max(1, timeout_ms // 500)):
            state = self.detect_flow_state(page)
            if state in {"WORKSPACE_READY", "PROJECT_WORKSPACE", "VIDEO_COMPOSER"}:
                return "WORKSPACE_READY"
            if state == "AUTH_REQUIRED":
                raise FlowAuthRequired("Google Flow authenticated session required")
            if state == "FLOW_BLOCKED_EMPTY_DOM":
                raise FlowBlockedEmptyDom("Flow DOM became empty during navigation")
            page.wait_for_timeout(500)
        raise FlowProjectNavigationFailed(
            f"Flow workspace navigation timed out in state {state}"
        )

    def _open_existing_project(self, page) -> bool:
        action = self._find_project_card_action(page)
        if action is None:
            return False
        try:
            action.click(timeout=5000)
            return True
        except Exception:
            return False

    def _open_new_project(self, page) -> bool:
        action, _frame = self._find_named_action(page, PROJECT_NEW_PATTERN)
        if action is None:
            return False
        try:
            action.click(timeout=5000)
            return True
        except Exception:
            return False

    def _complete_create_dialog(self, page) -> bool:
        if self.detect_flow_state(page) != "CREATE_DIALOG":
            return True
        action, _frame = self._find_named_action(page, PROJECT_CREATE_CONFIRM_PATTERN)
        if action is None:
            return False
        try:
            action.click(timeout=5000)
            return True
        except Exception:
            return False

    def _select_video_mode_if_needed(self, page) -> None:
        action, _frame = self._find_named_action(page, VIDEO_MODE_PATTERN)
        if action is not None:
            try:
                selected = (
                    action.get_attribute("aria-selected", timeout=500)
                    or action.get_attribute("aria-pressed", timeout=500)
                )
                if str(selected).lower() == "true":
                    return
                action.click(timeout=5000)
                page.wait_for_timeout(1000)
            except Exception:
                pass

    def ensure_flow_workspace(self, page, trace: list | None = None) -> tuple[str, str]:
        trace = trace if trace is not None else []
        self._validate_dom_readiness(page, trace)
        state = self.detect_flow_state(page)
        trace.append({"step": "detect_initial_state", "state": state})
        if state == "AUTH_REQUIRED":
            raise FlowAuthRequired("Google Flow authenticated session required")
        if state in {"WORKSPACE_READY", "VIDEO_COMPOSER", "PROJECT_WORKSPACE"}:
            self._select_video_mode_if_needed(page)
            final_state = self.detect_flow_state(page)
            trace.append({"step": "workspace_ready", "state": "WORKSPACE_READY"})
            return "not_needed", "WORKSPACE_READY"

        if state not in {
            "LANDING",
            "PROJECT_LIST",
            "PROJECT_LIST_READY",
            "PROJECT_CARD_FOUND",
            "CREATE_PROJECT_REQUIRED",
            "CREATE_DIALOG",
            "UNKNOWN",
        }:
            raise FlowProjectNavigationChanged("Unsupported Flow project state")

        navigation = "not_needed"
        project_action = self._find_project_card_action(page)
        new_project_action, _frame = self._find_named_action(page, PROJECT_NEW_PATTERN)
        trace.append({"step": "project_list_ready", "state": "PROJECT_LIST_READY"})

        if project_action is not None:
            trace.append({"step": "project_card_found", "state": "PROJECT_CARD_FOUND"})
            try:
                project_action.click(timeout=5000)
            except Exception as exc:
                self._record_ui_failure(page, trace, "project-navigation")
                raise FlowProjectNavigationFailed(
                    "Could not open existing Flow project card"
                ) from exc
            navigation = "existing"
            trace.append({"step": "open_existing_project", "state": state})
        elif new_project_action is not None:
            trace.append(
                {"step": "create_project_required", "state": "CREATE_PROJECT_REQUIRED"}
            )
            try:
                new_project_action.click(timeout=5000)
            except Exception as exc:
                self._record_ui_failure(page, trace, "project-navigation")
                raise FlowProjectNavigationFailed(
                    "Could not open Flow new project action"
                ) from exc
            navigation = "new"
            trace.append({"step": "open_new_project", "state": state})
            page.wait_for_timeout(500)
            if not self._complete_create_dialog(page):
                self._record_ui_failure(page, trace, "project-navigation")
                raise FlowProjectNavigationFailed(
                    "Could not complete Flow create project dialog"
                )
        else:
            if state in {
                "PROJECT_LIST",
                "PROJECT_LIST_READY",
                "PROJECT_CARD_FOUND",
                "CREATE_PROJECT_REQUIRED",
            }:
                self._record_ui_failure(page, trace, "project-navigation")
                raise FlowProjectNavigationChanged(
                    "Flow project list has no real project card or new project action"
                )
            landing_action, _frame = self._find_named_action(page, LANDING_START_PATTERN)
            if landing_action is None:
                self._record_ui_failure(page, trace, "project-navigation")
                raise FlowProjectNavigationChanged("Could not find Flow project navigation action")
            try:
                landing_action.click(timeout=5000)
                navigation = "new"
                trace.append({"step": "open_landing_action", "state": state})
            except Exception as exc:
                self._record_ui_failure(page, trace, "project-navigation")
                raise FlowProjectNavigationChanged("Could not activate Flow project navigation") from exc

        final_state = self._wait_for_workspace_state(page, timeout_ms=30000)
        trace.append({"step": "workspace_transition", "state": final_state})
        self._select_video_mode_if_needed(page)
        detected_state = self.detect_flow_state(page)
        trace.append({"step": "detect_composer", "state": detected_state})
        trace.append({"step": "workspace_ready", "state": "WORKSPACE_READY"})
        return navigation, "WORKSPACE_READY"

    def _ensure_prompt_workspace(self, page) -> str:
        navigation, _state = self.ensure_flow_workspace(page)
        return navigation

    def _prepare_generation_surface(self, page, trace: list | None = None) -> dict:
        trace = trace if trace is not None else []
        initial_state = self.detect_flow_state(page)
        if initial_state == "AUTH_REQUIRED":
            raise FlowAuthRequired("Google Flow authenticated session required")
        navigation, workspace_state = self.ensure_flow_workspace(page, trace=trace)
        prompt_input, prompt_frame = self.find_prompt_input(page)
        trace.append({"step": "prompt_input", "found": prompt_input is not None, "frame": prompt_frame})
        if prompt_input is None:
            self._record_ui_failure(page, trace, "prompt-input")
            raise FlowPromptInputChanged("Could not find Flow prompt input")
        generate_action, generate_frame = self.find_generate_action(page)
        trace.append({"step": "generate_action", "found": generate_action is not None, "frame": generate_frame})
        if generate_action is None:
            self._record_ui_failure(page, trace, "generate-action")
            raise FlowGenerateActionChanged("Could not find Flow generate action")
        return {
            "initial_state": initial_state,
            "workspace_state": workspace_state,
            "project_navigation": navigation,
            "prompt_input_found": True,
            "prompt_frame": prompt_frame,
            "generate_action_found": True,
            "prompt_input": prompt_input,
            "generate_action": generate_action,
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
        trace = []
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(**self._context_kwargs())
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(self.config.flow_url, wait_until="domcontentloaded", timeout=60000)
                trace.append({"step": "open_flow", "state": self.detect_flow_state(page)})
                self._submit_prompt(
                    page,
                    prompt,
                    aspect_ratio,
                    media_type,
                    log_event=log_event,
                    trace=trace,
                )
                if log_event:
                    log_event("FLOW_GENERATION_WAITING")
                download_path = self._wait_for_download(
                    page,
                    scene_id,
                    timeout_ms,
                    log_event=log_event,
                    trace=trace,
                )
                elapsed = time.monotonic() - started
                if elapsed > self.config.generation_timeout_seconds:
                    raise FlowGenerationTimeout("Google Flow generation timed out")
                if not download_path.exists() or download_path.stat().st_size <= 0:
                    raise FlowDownloadFailed("Google Flow download empty")
                trace.append({"step": "download_complete", "state": "RESULT_READY"})
                self._write_safe_trace(trace)
                return download_path
            except (FlowAuthRequired, FlowUiChanged, FlowGenerationFailed, FlowGenerationTimeout, FlowDownloadFailed):
                self._record_ui_failure(page, trace, f"scene-{scene_id}")
                raise
            finally:
                context.close()

    def _submit_prompt(
        self,
        page,
        prompt: str,
        aspect_ratio: str,
        media_type: str,
        log_event=None,
        trace: list | None = None,
    ) -> None:
        trace = trace if trace is not None else []
        prepared = self._prepare_generation_surface(page, trace=trace)
        prompt_box = prepared["prompt_input"]
        try:
            prompt_box.fill(prompt, timeout=30000)
        except Exception as exc:
            raise FlowPromptInputChanged("Could not fill Flow prompt input") from exc

        if aspect_ratio:
            try:
                page.get_by_text(aspect_ratio, exact=False).first.click(timeout=3000)
            except Exception:
                pass

        generate_action = prepared["generate_action"]
        try:
            generate_action.click(timeout=5000)
        except Exception as exc:
            raise FlowGenerateActionChanged("Could not click Flow generate action") from exc
        if log_event:
            log_event("FLOW_PROMPT_SUBMITTED")
        trace.append({"step": "submit_prompt", "state": self.detect_flow_state(page)})
        self._write_safe_trace(trace)

    def _wait_for_download(
        self,
        page,
        scene_id: int,
        timeout_ms: int,
        log_event=None,
        trace: list | None = None,
    ) -> Path:
        trace = trace if trace is not None else []
        deadline = time.monotonic() + (timeout_ms / 1000)
        download_started_logged = False
        generation_completed_logged = False
        while time.monotonic() < deadline:
            state = self.detect_flow_state(page)
            if state == "AUTH_REQUIRED":
                raise FlowAuthRequired("Google Flow authenticated session required")
            if state != "RESULT_READY":
                page.wait_for_timeout(5000)
                continue
            trace.append({"step": "result_ready", "state": state})
            download_action, _frame = self._find_download_action(page)
            if download_action is None:
                page.wait_for_timeout(2000)
                continue
            try:
                with page.expect_download(timeout=5000) as download_info:
                    download_action.click(timeout=3000)
                    if log_event and not generation_completed_logged:
                        log_event("FLOW_GENERATION_COMPLETED")
                        generation_completed_logged = True
                    if log_event and not download_started_logged:
                        log_event("FLOW_DOWNLOAD_STARTED")
                        download_started_logged = True
                download = download_info.value
                suffix = Path(download.suggested_filename or "").suffix or ".mp4"
                target = self.config.downloads_dir / f"flow-scene-{scene_id}-{int(time.time())}{suffix}"
                download.save_as(str(target))
                if log_event:
                    log_event("FLOW_DOWNLOAD_COMPLETED")
                return target
            except PlaywrightTimeoutError:
                page.wait_for_timeout(5000)
        self._record_ui_failure(page, trace, "generation-timeout")
        raise FlowGenerationTimeout("Timed out waiting for Google Flow download")
