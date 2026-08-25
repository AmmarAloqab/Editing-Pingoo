import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.google_flow_worker.config import FlowWorkerConfig
from tools.google_flow_worker.errors import FlowAuthRequired, PingooUploadFailed
from tools.google_flow_worker.flow_browser import GoogleFlowBrowser
from tools.google_flow_worker.planner import (
    build_flow_prompt,
    select_auto_flow_candidates,
)
from tools.google_flow_worker.worker import FlowGenerateRequest, flow_diagnostics, flow_generate, flow_job_status
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
