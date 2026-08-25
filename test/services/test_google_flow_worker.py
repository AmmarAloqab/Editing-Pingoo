import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.google_flow_worker.config import FlowWorkerConfig
from tools.google_flow_worker.errors import (
    FlowAuthRequired,
    FlowGenerateActionChanged,
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
    def __init__(self, text="", visible=True, attrs=None):
        self.text = text
        self.visible = visible
        self.attrs = attrs or {}
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
    def __init__(self, *, buttons=None, links=None, inputs=None, placeholders=None):
        self.url = "https://labs.google/fx/ar/tools/flow"
        self.frames = []
        self.buttons = [FakeItem(text=value) for value in (buttons or [])]
        self.links = [FakeItem(text=value) for value in (links or [])]
        self.inputs = inputs or []
        self.placeholders = [FakeItem(attrs={"placeholder": value}) for value in (placeholders or [])]
        self.waits = 0

    def title(self):
        return "Google Flow"

    def wait_for_timeout(self, _ms):
        self.waits += 1

    def get_by_role(self, role, name=None):
        items = {"button": self.buttons, "link": self.links, "textbox": self.inputs}.get(role, [])
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
        if "placeholder" in selector:
            return FakeLocator(self.placeholders)
        if "aria-label" in selector:
            return FakeLocator([])
        if "textarea" in selector or "contenteditable" in selector or "role='textbox'" in selector:
            return FakeLocator(self.inputs)
        return FakeLocator([])

    def get_by_text(self, _text, exact=False):
        return FakeLocator([])


class GoogleFlowWorkerUiAutomationTest(unittest.TestCase):
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
        self.assertEqual(navigation, "existing")
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

        with self.assertRaises(FlowWorkspaceLoadChanged) as raised:
            browser._ensure_prompt_workspace(page)

        self.assertEqual(raised.exception.code, "FLOW_UI_CHANGED_WORKSPACE_LOAD")

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

    def test_workspace_probe_reuses_production_navigation_without_generation(self):
        import inspect

        source = inspect.getsource(GoogleFlowBrowser.workspace_probe)

        self.assertIn("_ensure_prompt_workspace", source)
        self.assertIn("_first_prompt_input_with_frame", source)
        self.assertIn("_find_generate_action", source)
        self.assertNotIn("_submit_prompt", source)
        self.assertNotIn("fill(", source)
        self.assertNotIn("FLOW_PROMPT_SUBMITTED", source)

class GoogleFlowWorkerWindowsTest(unittest.TestCase):
    @patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Users\me\AppData\Local"}, clear=True)
    @patch("platform.system", return_value="Windows")
    def test_windows_defaults_use_dedicated_profile(self, _system):
        config = get_config()

        self.assertIn("PingooGoogleFlow", str(config.base_dir))
        self.assertEqual(config.profile_dir.name, "profile")
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
