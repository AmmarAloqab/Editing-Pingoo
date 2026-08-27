import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.google_flow_worker.config import FlowWorkerConfig
from tools.google_flow_worker.errors import (
    FlowAuthRequired,
    FlowBlockedEmptyDom,
    FlowGenerateActionChanged,
    FlowProjectNavigationFailed,
    FlowProjectNavigationChanged,
    FlowPromptInputChanged,
    FlowWorkspaceLoadChanged,
    PingooUploadFailed,
)
from tools.google_flow_worker.flow_browser import GoogleFlowBrowser
from tools.google_flow_worker.planner import (
    build_flow_prompt,
    select_auto_flow_candidates,
)
from tools.google_flow_worker.worker import (
    FlowGenerateRequest,
    flow_diagnostics,
    flow_dry_run,
    flow_generate,
    flow_job_status,
    flow_ui_inventory,
    flow_workspace_probe,
)
from tools.google_flow_worker.config import get_config


def _scene(scene_id, preferred_source="auto"):
    return {
        "scene_id": scene_id,
        "narration": f"narration {scene_id}",
        "visual_prompt": f"visual prompt {scene_id}",
        "visual_query": f"visual query {scene_id}",
        "preferred_source": preferred_source,
    }


class GoogleFlowWorkerPlannerTest(unittest.TestCase):
    def test_flow_budget_reuses_existing_flow_and_preserves_order(self):
        scenes = [
            _scene(1, "flow"),
            _scene(2),
            _scene(3),
            _scene(4),
            _scene(5),
        ]

        candidates = select_auto_flow_candidates(
            scenes=scenes,
            existing_flow_scene_ids={1},
            visual_style="futuristic",
            material_source_mode="flow_user_pexels",
            max_auto_flow_scenes=2,
        )

        self.assertEqual([candidate.scene_id for candidate in candidates], [2, 3])
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all("vertical 9:16" in c.prompt for c in candidates))

    def test_no_auto_generation_for_realistic_without_preferred_flow(self):
        candidates = select_auto_flow_candidates(
            scenes=[_scene(1), _scene(2)],
            existing_flow_scene_ids=set(),
            visual_style="realistic",
            material_source_mode="auto",
            max_auto_flow_scenes=2,
        )

        self.assertEqual(candidates, [])

    def test_mode_without_flow_returns_no_candidates(self):
        candidates = select_auto_flow_candidates(
            scenes=[_scene(1, "flow"), _scene(2)],
            existing_flow_scene_ids=set(),
            visual_style="futuristic",
            material_source_mode="pexels_only",
            max_auto_flow_scenes=2,
        )

        self.assertEqual(candidates, [])

    def test_prompt_uses_visual_prompt_and_query_not_long_narration(self):
        prompt = build_flow_prompt(
            {
                "scene_id": 1,
                "narration": "long narration should not dominate",
                "visual_prompt": "glowing bitcoin network",
                "visual_query": "global computers",
            },
            "futuristic",
        )

        self.assertIn("glowing bitcoin network", prompt)
        self.assertIn("global computers", prompt)
        self.assertIn("no text", prompt)
        self.assertNotIn("long narration should not dominate", prompt)


class GoogleFlowWorkerEndpointTest(unittest.TestCase):
    def _wait_for_job(self, job_id, state, timeout=2.0):
        deadline = time.monotonic() + timeout
        result = flow_job_status(job_id)
        while time.monotonic() < deadline:
            result = flow_job_status(job_id)
            if result["state"] == state:
                return result
            time.sleep(0.02)
        return result

    def test_flow_failure_falls_back_as_error_without_upload(self):
        with patch("tools.google_flow_worker.worker.GoogleFlowBrowser") as browser_cls:
            browser_cls.return_value.generate_and_download.side_effect = FlowAuthRequired(
                "auth required"
            )
            result = flow_generate(
                FlowGenerateRequest(scene_id=3, prompt="prompt")
            )
            failed = self._wait_for_job(result["job_id"], "failed")

        self.assertEqual(result["state"], "queued")
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error_code"], "FLOW_AUTH_REQUIRED")

    def test_success_uploads_and_removes_temp_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.mp4"
            path.write_bytes(b"video")
            with (
                patch("tools.google_flow_worker.worker.GoogleFlowBrowser") as browser_cls,
                patch("tools.google_flow_worker.worker.upload_material", return_value="flow.mp4") as upload,
            ):
                browser_cls.return_value.generate_and_download.return_value = path
                result = flow_generate(
                    FlowGenerateRequest(scene_id=1, prompt="prompt")
                )
                completed = self._wait_for_job(result["job_id"], "completed")

            self.assertEqual(result["state"], "queued")
            self.assertTrue(completed["ok"])
            self.assertEqual(completed["source"], "flow")
            self.assertEqual(completed["material_url"], "flow.mp4")
            upload.assert_called_once()
            self.assertFalse(path.exists())

    def test_long_generation_returns_running_job_without_network_error(self):
        release = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.mp4"
            path.write_bytes(b"video")

            def slow_generate(**_kwargs):
                release.wait(1.0)
                return path

            with (
                patch("tools.google_flow_worker.worker.GoogleFlowBrowser") as browser_cls,
                patch("tools.google_flow_worker.worker.upload_material", return_value="flow.mp4"),
            ):
                browser_cls.return_value.generate_and_download.side_effect = slow_generate
                result = flow_generate(FlowGenerateRequest(scene_id=4, prompt="prompt"))
                running = self._wait_for_job(result["job_id"], "generating", timeout=0.5)
                release.set()
                completed = self._wait_for_job(result["job_id"], "completed")

        self.assertEqual(result["state"], "queued")
        self.assertEqual(running["state"], "generating")
        self.assertNotEqual(running.get("error_code"), "FLOW_NETWORK_ERROR")
        self.assertEqual(completed["material_url"], "flow.mp4")

    def test_upload_failure_is_distinct_from_generation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.mp4"
            path.write_bytes(b"video")
            with (
                patch("tools.google_flow_worker.worker.GoogleFlowBrowser") as browser_cls,
                patch("tools.google_flow_worker.worker.upload_material", side_effect=PingooUploadFailed("upload failed")),
            ):
                browser_cls.return_value.generate_and_download.return_value = path
                result = flow_generate(FlowGenerateRequest(scene_id=5, prompt="prompt"))
                failed = self._wait_for_job(result["job_id"], "failed")

        self.assertEqual(failed["error_code"], "PINGOO_UPLOAD_FAILED")
        self.assertNotEqual(failed["error_code"], "FLOW_GENERATION_FAILED")

    def test_diagnostics_endpoint_returns_safe_fields(self):
        expected = {
            "url": "https://labs.google/fx/tools/flow",
            "title": "Google Flow",
            "has_google_account_button": False,
            "has_sign_in": False,
            "detected_flow_controls": ["new project"],
        }
        with patch("tools.google_flow_worker.worker.GoogleFlowBrowser") as browser_cls:
            browser_cls.return_value.diagnostics.return_value = expected

            result = flow_diagnostics()

        self.assertEqual(result, expected)



class FakeItem:
    def __init__(self, text="", visible=True, attrs=None, children=None):
        self.text = text
        self.visible = visible
        self.attrs = attrs or {}
        self.children = children or {}
        self.clicked = False
        self.filled = ""

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def nth(self, _index):
        return self

    def is_visible(self, timeout=None):
        return self.visible

    def inner_text(self, timeout=None):
        return self.text

    def get_attribute(self, name, timeout=None):
        return self.attrs.get(name)

    def get_by_role(self, role, name=None):
        items = self.children.get(role, [])
        if name is None:
            return FakeLocator(items)
        return FakeLocator([item for item in items if hasattr(name, "search") and name.search(item.text)])

    def click(self, timeout=None):
        self.clicked = True

    def fill(self, value, timeout=None):
        self.filled = value

    def press(self, _key):
        pass


class FakeLocator:
    def __init__(self, items=None):
        self.items = items or []

    @property
    def first(self):
        return self.items[0] if self.items else EmptyLocator()

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class EmptyLocator(FakeItem):
    def __init__(self):
        super().__init__(visible=False)

    def count(self):
        return 0


class FakeFlowPage:
    def __init__(
        self,
        *,
        buttons=None,
        links=None,
        inputs=None,
        placeholders=None,
        dialogs=None,
        articles=None,
        listitems=None,
        tabs=None,
        videos=None,
        images=None,
        title_value="Google Flow",
    ):
        self.url = "https://labs.google/fx/ar/tools/flow"
        self.frames = []
        self.buttons = [FakeItem(text=value) for value in (buttons or [])]
        self.links = [FakeItem(text=value) for value in (links or [])]
        self.inputs = inputs or []
        self.placeholders = [FakeItem(attrs={"placeholder": value}) for value in (placeholders or [])]
        self.dialogs = dialogs or []
        self.articles = articles or []
        self.listitems = listitems or []
        self.tabs = [FakeItem(text=value) for value in (tabs or [])]
        self.videos = videos or []
        self.images = images or []
        self.title_value = title_value
        self.waits = 0

    def title(self):
        return self.title_value

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    def wait_for_timeout(self, _ms):
        self.waits += 1

    def get_by_role(self, role, name=None):
        items = {
            "button": self.buttons,
            "link": self.links,
            "textbox": self.inputs,
            "dialog": self.dialogs,
            "article": self.articles,
            "listitem": self.listitems,
            "tab": self.tabs,
            "group": [],
        }.get(role, [])
        if name is None:
            return FakeLocator(items)
        filtered = []
        for item in items:
            if hasattr(name, "search") and name.search(item.text):
                filtered.append(item)
            elif str(name).lower() in item.text.lower():
                filtered.append(item)
        return FakeLocator(filtered)

    def locator(self, selector):
        if selector == "body":
            return FakeItem(text="")
        if selector == "video":
            return FakeLocator(self.videos)
        if selector == "img":
            return FakeLocator(self.images)
        if "placeholder" in selector:
            return FakeLocator(self.placeholders)
        if "aria-label" in selector:
            return FakeLocator([])
        if "textarea" in selector or "contenteditable" in selector or "role='textbox'" in selector:
            return FakeLocator(self.inputs)
        return FakeLocator([])

    def get_by_text(self, _text, exact=False):
        return FakeLocator([])

    def screenshot(self, path, full_page=True):
        Path(path).write_bytes(b"safe screenshot")


class GoogleFlowWorkerUiAutomationTest(unittest.TestCase):
    def _browser(self, base_dir=None):
        return GoogleFlowBrowser(
            FlowWorkerConfig(
                base_dir=base_dir or Path(tempfile.gettempdir()) / "pingoo-test"
            )
        )

    def test_arabic_and_english_landing_states(self):
        browser = self._browser()
        for label in ("مشروع جديد", "New project"):
            with self.subTest(label=label):
                self.assertEqual(
                    browser.detect_flow_state(FakeFlowPage(buttons=[label])),
                    "LANDING",
                )

    def test_empty_dom_with_recaptcha_blocks_navigation_and_returns_diagnostics(self):
        browser = self._browser()
        page = FakeFlowPage(title_value="")
        recaptcha = FakeFlowPage(title_value="")
        recaptcha.url = "https://www.google.com/recaptcha/api2/anchor"
        page.frames = [recaptcha]
        trace = []

        with (
            patch.object(browser, "_open_existing_project") as open_existing,
            patch.object(browser, "_open_new_project") as open_new,
            self.assertRaises(FlowBlockedEmptyDom) as raised,
        ):
            browser.ensure_flow_workspace(page, trace=trace)

        diagnostics = browser._dom_readiness_diagnostics(page)
        self.assertEqual(raised.exception.code, "FLOW_BLOCKED_EMPTY_DOM")
        self.assertEqual(diagnostics["url"], "https://labs.google/fx/ar/tools/flow")
        self.assertEqual(diagnostics["title"], "")
        self.assertEqual(diagnostics["iframe_count"], 1)
        self.assertTrue(diagnostics["recaptcha_detected"])
        self.assertEqual(diagnostics["state"], "FLOW_BLOCKED_EMPTY_DOM")
        self.assertEqual(diagnostics["error_code"], "FLOW_BLOCKED_EMPTY_DOM")
        self.assertEqual(trace[0]["step"], "validate_dom_readiness")
        open_existing.assert_not_called()
        open_new.assert_not_called()

    def test_empty_dom_state_requires_empty_title_and_zero_core_controls(self):
        browser = self._browser()

        self.assertEqual(
            browser.detect_flow_state(FakeFlowPage(title_value="")),
            "FLOW_BLOCKED_EMPTY_DOM",
        )
        self.assertNotEqual(
            browser.detect_flow_state(FakeFlowPage(title_value="", buttons=["New project"])),
            "FLOW_BLOCKED_EMPTY_DOM",
        )

    def test_diagnostics_endpoint_payload_includes_empty_dom_evidence(self):
        browser = self._browser()
        page = FakeFlowPage(title_value="")
        recaptcha = FakeFlowPage(title_value="")
        recaptcha.url = "https://www.google.com/recaptcha/api2/anchor"
        page.frames = [recaptcha]

        diagnostics = browser._page_diagnostics(page)

        self.assertEqual(
            {key: diagnostics[key] for key in (
                "url",
                "title",
                "iframe_count",
                "recaptcha_detected",
                "state",
                "error_code",
            )},
            {
                "url": "https://labs.google/fx/ar/tools/flow",
                "title": "",
                "iframe_count": 1,
                "recaptcha_detected": True,
                "state": "FLOW_BLOCKED_EMPTY_DOM",
                "error_code": "FLOW_BLOCKED_EMPTY_DOM",
            },
        )

    def test_existing_project_card_is_discovered(self):
        browser = self._browser()
        edit = FakeItem(text="تعديل المشروع")
        card = FakeItem(
            text="مشروع فيديو",
            children={"button": [edit]},
        )
        page = FakeFlowPage(articles=[card])

        self.assertEqual(browser.detect_flow_state(page), "PROJECT_LIST")
        self.assertIs(browser._find_project_card_action(page), edit)

    def test_project_workspace_state_without_url_change(self):
        browser = self._browser()
        page = FakeFlowPage(buttons=["إعدادات المشروع"])
        original_url = page.url

        self.assertEqual(browser.detect_flow_state(page), "PROJECT_WORKSPACE")
        self.assertEqual(page.url, original_url)

    def test_create_dialog_state(self):
        browser = self._browser()
        page = FakeFlowPage(dialogs=[FakeItem(text="إنشاء مشروع")])

        self.assertEqual(browser.detect_flow_state(page), "CREATE_DIALOG")

    def test_main_frame_composer_state(self):
        browser = self._browser()
        page = FakeFlowPage(inputs=[FakeItem(text="وصف")], buttons=["إنشاء"])

        self.assertEqual(browser.detect_flow_state(page), "VIDEO_COMPOSER")

    def test_iframe_composer_state(self):
        browser = self._browser()
        main = FakeFlowPage()
        frame = FakeFlowPage(inputs=[FakeItem(text="Describe")], buttons=["Generate"])
        main.frames = [frame]

        self.assertEqual(browser.detect_flow_state(main), "VIDEO_COMPOSER")

    def test_generating_and_result_ready_states(self):
        browser = self._browser()
        generating = FakeFlowPage(buttons=["إلغاء التوليد"])
        ready = FakeFlowPage(buttons=["تنزيل"], videos=[FakeItem()])

        self.assertEqual(browser.detect_flow_state(generating), "GENERATING")
        self.assertEqual(browser.detect_flow_state(ready), "RESULT_READY")

    def test_image_result_with_download_is_result_ready(self):
        browser = self._browser()
        ready = FakeFlowPage(buttons=["Download"], images=[FakeItem()])

        self.assertEqual(browser.detect_flow_state(ready), "RESULT_READY")

    def test_video_mode_is_selected_when_composer_is_generic(self):
        browser = self._browser()
        video = FakeItem(text="Video", attrs={"aria-selected": "false"})
        page = FakeFlowPage(inputs=[FakeItem(text="Prompt")], buttons=["Generate"])
        page.tabs = [video]

        browser._select_video_mode_if_needed(page)

        self.assertTrue(video.clicked)

    def test_unknown_failure_writes_snapshot_trace_and_keeps_five_screenshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            browser = self._browser(Path(tmp))
            page = FakeFlowPage()
            for index in range(7):
                browser._record_ui_failure(page, [], f"unknown-{index}")
                time.sleep(0.01)

            screenshots = list(browser.config.diagnostics_dir.glob("flow-failure-*.png"))
            snapshot = browser.config.diagnostics_dir / "flow-last-snapshot.json"
            trace = browser.config.base_dir / "flow-last-trace.json"

            self.assertEqual(len(screenshots), 5)
            self.assertTrue(snapshot.exists())
            self.assertTrue(trace.exists())
            self.assertNotIn("cookies", snapshot.read_text(encoding="utf-8").lower())

    def test_safe_snapshot_contains_required_counts(self):
        browser = self._browser()
        page = FakeFlowPage(
            buttons=["Generate"],
            links=["Help"],
            inputs=[FakeItem(text="Prompt")],
            placeholders=["Describe video"],
            videos=[FakeItem()],
            images=[FakeItem()],
        )

        snapshot = browser.collect_safe_ui_snapshot(page)

        self.assertEqual(snapshot["video_elements_count"], 1)
        self.assertEqual(snapshot["image_elements_count"], 1)
        self.assertIn("role_names", snapshot)
        self.assertNotIn("html", snapshot)

    def test_arabic_and_english_generate_action_fallback(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        for label in ("Generate", "إنشاء"):
            with self.subTest(label=label):
                button = FakeItem(text=label)
                page = FakeFlowPage(buttons=[label], inputs=[FakeItem(text="prompt")])
                page.buttons = [button]
                browser._submit_prompt(page, "hello", "9:16", "video")
                self.assertTrue(button.clicked)
                self.assertEqual(page.inputs[0].filled, "hello")

    def test_prompt_input_detection_uses_textbox_or_semantic_input(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        prompt = FakeItem(text="اكتب وصف الفيديو")
        page = FakeFlowPage(inputs=[prompt])

        self.assertIs(browser._first_prompt_input(page), prompt)

    def test_existing_project_navigation_is_preferred(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        edit = FakeItem(text="تعديل المشروع")
        new = FakeItem(text="مشروع جديد")
        prompt = FakeItem(text="اكتب وصف الفيديو", visible=False)
        page = FakeFlowPage(inputs=[prompt])
        page.buttons = [edit, new]

        def edit_and_reveal(timeout=None):
            edit.clicked = True
            prompt.visible = True

        edit.click = edit_and_reveal

        navigation = browser._ensure_prompt_workspace(page)

        self.assertEqual(navigation, "existing")
        self.assertTrue(edit.clicked)
        self.assertFalse(new.clicked)

    def test_new_project_navigation_fallback(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        new = FakeItem(text="New project")
        prompt = FakeItem(text="Describe your video", visible=False)
        page = FakeFlowPage(inputs=[prompt])
        page.buttons = [new]

        def new_and_reveal(timeout=None):
            new.clicked = True
            prompt.visible = True

        new.click = new_and_reveal

        navigation = browser._ensure_prompt_workspace(page)

        self.assertEqual(navigation, "new")
        self.assertTrue(new.clicked)

    def test_iframe_prompt_input_detection(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        main = FakeFlowPage()
        frame = FakeFlowPage(inputs=[FakeItem(text="Prompt")])
        main.frames = [frame]

        prompt, frame_label = browser._first_prompt_input_with_frame(main)

        self.assertIs(prompt, frame.inputs[0])
        self.assertEqual(frame_label, "frame_1")

    def test_generate_action_probe_does_not_click(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        generate = FakeItem(text="Generate")
        page = FakeFlowPage(inputs=[FakeItem(text="Prompt")])
        page.buttons = [generate]

        action, frame_label = browser._find_generate_action(page)

        self.assertIs(action, generate)
        self.assertEqual(frame_label, "main")
        self.assertFalse(generate.clicked)

    def test_project_navigation_clicks_landing_cta_before_prompt(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        cta = FakeItem(text="ابدأ الآن")
        prompt = FakeItem(text="prompt", visible=False)
        page = FakeFlowPage(buttons=["ابدأ الآن"], inputs=[prompt])
        page.buttons = [cta]

        def reveal(_timeout=None):
            return prompt.visible

        original_click = cta.click
        def click_and_reveal(timeout=None):
            original_click(timeout=timeout)
            prompt.visible = True
        cta.click = click_and_reveal

        navigation = browser._ensure_prompt_workspace(page)

        self.assertTrue(cta.clicked)
        self.assertEqual(navigation, "new")
        self.assertIs(browser._first_prompt_input(page), prompt)

    def test_precise_prompt_input_error_code(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        page = FakeFlowPage(buttons=[])

        with self.assertRaises(FlowProjectNavigationChanged) as raised:
            browser._ensure_prompt_workspace(page)

        self.assertEqual(raised.exception.code, "FLOW_UI_CHANGED_PROJECT_NAVIGATION")

    def test_workspace_load_error_code_after_navigation_without_prompt(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        page = FakeFlowPage(buttons=["مشروع جديد"])

        with self.assertRaises(FlowProjectNavigationFailed) as raised:
            browser._ensure_prompt_workspace(page)

        self.assertEqual(raised.exception.code, "FLOW_PROJECT_NAVIGATION_FAILED")

    def test_precise_generate_button_error_code(self):
        browser = GoogleFlowBrowser(FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test"))
        page = FakeFlowPage(inputs=[FakeItem(text="prompt")])

        with self.assertRaises(FlowGenerateActionChanged) as raised:
            browser._submit_prompt(page, "hello", "9:16", "video")

        self.assertEqual(raised.exception.code, "FLOW_UI_CHANGED_GENERATE_ACTION")

    def test_ui_inventory_endpoint_returns_safe_structure(self):
        expected = {
            "url": "https://labs.google/fx/ar/tools/flow",
            "title": "Google Flow",
            "buttons": ["إنشاء"],
            "links": [],
            "textboxes": [],
            "placeholders": [],
            "aria_labels": [],
            "visible_input_count": 0,
            "contenteditable_count": 0,
            "frame_count": 0,
        }
        with patch("tools.google_flow_worker.worker.GoogleFlowBrowser") as browser_cls:
            browser_cls.return_value.ui_inventory.return_value = expected
            result = flow_ui_inventory()

        self.assertEqual(result, expected)

    def test_workspace_probe_endpoint_returns_safe_structure(self):
        expected = {
            "url": "https://labs.google/fx/ar/tools/flow/project/123",
            "title": "Google Flow",
            "iframe_count": 0,
            "recaptcha_detected": False,
            "state": "VIDEO_COMPOSER",
            "workspace_ready": True,
            "workspace_url": "https://labs.google/fx/ar/tools/flow/project/123",
            "workspace_title": "Google Flow",
            "project_navigation": "existing",
            "prompt_input_found": True,
            "prompt_frame": "frame_1",
            "generate_action_found": True,
            "error_code": None,
        }
        with patch("tools.google_flow_worker.worker.GoogleFlowBrowser") as browser_cls:
            browser_cls.return_value.workspace_probe.return_value = expected

            result = flow_workspace_probe()

        self.assertEqual(result, expected)

    def test_workspace_probe_preserves_empty_dom_diagnostics(self):
        browser = self._browser()
        blocked = {
            **browser._dry_run_result(),
            "url": "https://labs.google/fx/ar/tools/flow",
            "title": "",
            "iframe_count": 1,
            "recaptcha_detected": True,
            "state": "FLOW_BLOCKED_EMPTY_DOM",
            "error_code": "FLOW_BLOCKED_EMPTY_DOM",
        }

        with patch.object(browser, "dry_run", return_value=blocked):
            result = browser.workspace_probe()

        self.assertFalse(result["workspace_ready"])
        self.assertEqual(result["state"], "FLOW_BLOCKED_EMPTY_DOM")
        self.assertEqual(result["error_code"], "FLOW_BLOCKED_EMPTY_DOM")
        self.assertTrue(result["recaptcha_detected"])

    def test_workspace_probe_reuses_production_navigation_without_generation(self):
        import inspect

        source = inspect.getsource(GoogleFlowBrowser.workspace_probe)
        dry_run_source = inspect.getsource(GoogleFlowBrowser.dry_run)
        submit_source = inspect.getsource(GoogleFlowBrowser._submit_prompt)

        self.assertIn("dry_run", source)
        self.assertIn("_prepare_generation_surface", dry_run_source)
        self.assertIn("_prepare_generation_surface", submit_source)
        self.assertNotIn("_submit_prompt", source)
        self.assertNotIn("fill(", dry_run_source)
        self.assertNotIn("click(", dry_run_source)
        self.assertNotIn("FLOW_PROMPT_SUBMITTED", dry_run_source)

    def test_dry_run_endpoint_returns_no_credit_result(self):
        expected = {
            "authenticated": True,
            "initial_state": "PROJECT_LIST",
            "workspace_state": "VIDEO_COMPOSER",
            "project_navigation": "existing",
            "prompt_input_found": True,
            "prompt_frame": "frame_1",
            "generate_action_found": True,
            "credit_consumed": False,
            "ready_for_generation": True,
            "error_code": None,
        }
        with patch("tools.google_flow_worker.worker.GoogleFlowBrowser") as browser_cls:
            browser_cls.return_value.dry_run.return_value = expected

            result = flow_dry_run()

        self.assertEqual(result, expected)
        self.assertFalse(result["credit_consumed"])

    def test_real_dry_run_path_stops_before_fill_or_generate_click(self):
        browser = self._browser()
        prompt = FakeItem(text="Describe your video")
        generate = FakeItem(text="Generate")
        page = FakeFlowPage(inputs=[prompt])
        page.buttons = [generate]

        class Context:
            pages = [page]

            def close(self):
                pass

        class Chromium:
            def launch_persistent_context(self, **_kwargs):
                return Context()

        class Playwright:
            chromium = Chromium()

        class Manager:
            def __enter__(self):
                return Playwright()

            def __exit__(self, *_args):
                pass

        with patch(
            "tools.google_flow_worker.flow_browser.sync_playwright",
            return_value=Manager(),
        ):
            result = browser.dry_run()

        self.assertTrue(result["ready_for_generation"])
        self.assertFalse(result["credit_consumed"])
        self.assertEqual(prompt.filled, "")
        self.assertFalse(generate.clicked)

    def test_dry_run_reports_empty_dom_without_workspace_ready(self):
        browser = self._browser()
        page = FakeFlowPage(title_value="")
        recaptcha = FakeFlowPage(title_value="")
        recaptcha.url = "https://www.google.com/recaptcha/api2/anchor"
        page.frames = [recaptcha]

        class Context:
            pages = [page]

            def close(self):
                pass

        class Chromium:
            def launch_persistent_context(self, **_kwargs):
                return Context()

        class Playwright:
            chromium = Chromium()

        class Manager:
            def __enter__(self):
                return Playwright()

            def __exit__(self, *_args):
                pass

        with patch(
            "tools.google_flow_worker.flow_browser.sync_playwright",
            return_value=Manager(),
        ):
            result = browser.dry_run()

        self.assertFalse(result["ready_for_generation"])
        self.assertEqual(result["url"], "https://labs.google/fx/tools/flow")
        self.assertEqual(result["title"], "")
        self.assertEqual(result["iframe_count"], 1)
        self.assertTrue(result["recaptcha_detected"])
        self.assertEqual(result["state"], "FLOW_BLOCKED_EMPTY_DOM")
        self.assertEqual(result["error_code"], "FLOW_BLOCKED_EMPTY_DOM")

class GoogleFlowWorkerWindowsTest(unittest.TestCase):
    def test_startup_script_registers_logon_restart_and_private_network_values(self):
        script = Path("tools/google_flow_worker/windows_register_startup.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", script)
        self.assertIn("-RestartCount", script)
        self.assertIn("100.123.55.125", script)
        self.assertIn("100.104.63.125:18080/api/v1/video_materials", script)
        self.assertIn('-Headless `"false`"', script)
        self.assertNotIn("password", script.lower())
        self.assertNotIn("token", script.lower())

    @patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Users\me\AppData\Local"}, clear=True)
    @patch("platform.system", return_value="Windows")
    def test_windows_defaults_use_dedicated_profile(self, _system):
        config = get_config()

        self.assertIn("PingooGoogleFlow", str(config.base_dir))
        self.assertEqual(config.profile_dir.name, "profile")
        self.assertEqual(config.diagnostics_dir.name, "diagnostics")
        self.assertEqual(config.host, "127.0.0.1")
        self.assertFalse(config.headless)

    @patch.dict("os.environ", {"PINGOO_FLOW_HEADLESS": "true"}, clear=True)
    @patch("platform.system", return_value="Windows")
    def test_headless_can_be_overridden(self, _system):
        self.assertTrue(get_config().headless)

    def test_status_diagnostics_do_not_expose_page_text(self):
        class Page:
            url = "https://accounts.google.com/signin"

            def title(self):
                return "Sign in - Google Accounts"

            def locator(self, _selector):
                body = Mock()
                body.inner_text.return_value = "Email or phone"
                return body

        config = FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test")
        result = GoogleFlowBrowser(config)._classify_page(Page())

        self.assertFalse(result["authenticated"])
        self.assertEqual(result["error_code"], "AUTH_REQUIRED")
        self.assertEqual(result["page_title"], "Sign in - Google Accounts")
        self.assertEqual(result["current_url"], "https://accounts.google.com/signin")
        self.assertNotIn("Email or phone", result.values())

    def test_current_flow_controls_authenticate_without_sign_in(self):
        class Page:
            url = "https://labs.google/fx/tools/flow"

            def title(self):
                return "Google Flow - AI Creative Studio for Video, Images & Custom Tools"

            def locator(self, _selector):
                body = Mock()
                body.inner_text.return_value = ""
                return body

        config = FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test")
        browser = GoogleFlowBrowser(config)
        with patch.object(
            browser,
            "_page_diagnostics",
            return_value={
                "url": "https://labs.google/fx/tools/flow",
                "title": "Google Flow - AI Creative Studio for Video, Images & Custom Tools",
                "has_google_account_button": False,
                "has_sign_in": False,
                "detected_flow_controls": ["new project", "edit project"],
            },
        ):
            result = browser._classify_page(Page())

        self.assertTrue(result["authenticated"])
        self.assertEqual(result["error_code"], "FLOW_UI_READY")

    def test_sign_in_button_requires_auth_without_avatar(self):
        class Page:
            url = "https://labs.google/fx/tools/flow"

            def title(self):
                return "Google Flow"

            def locator(self, _selector):
                body = Mock()
                body.inner_text.return_value = ""
                return body

        config = FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test")
        browser = GoogleFlowBrowser(config)
        with patch.object(
            browser,
            "_page_diagnostics",
            return_value={
                "url": "https://labs.google/fx/tools/flow",
                "title": "Google Flow",
                "has_google_account_button": False,
                "has_sign_in": True,
                "detected_flow_controls": [],
            },
        ):
            result = browser._classify_page(Page())

        self.assertFalse(result["authenticated"])
        self.assertEqual(result["error_code"], "AUTH_REQUIRED")

    @patch(
        "tools.google_flow_worker.flow_browser.find_chrome_executable",
        return_value=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    def test_windows_chrome_executable_is_used_for_persistent_context(self, _chrome):
        config = FlowWorkerConfig(base_dir=Path(tempfile.gettempdir()) / "pingoo-test")
        browser = GoogleFlowBrowser(config)

        kwargs = browser._context_kwargs()

        self.assertEqual(kwargs["user_data_dir"], str(config.profile_dir))
        self.assertEqual(
            kwargs["executable_path"],
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        )


if __name__ == "__main__":
    unittest.main()
