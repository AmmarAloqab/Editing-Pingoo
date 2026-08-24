import json
import unittest
from pathlib import Path


WORKFLOW = Path("n8n/01-pingoo-video-telegram-bot.json")


def _workflow():
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def _node(name: str):
    for node in _workflow()["nodes"]:
        if node.get("name") == name:
            return node
    raise AssertionError(f"node not found: {name}")


class N8nOnePromptWorkflowTest(unittest.TestCase):
    def test_one_prompt_parser_is_enabled_for_plain_telegram_text(self):
        code = _node("Command Router")["parameters"]["jsCode"]

        self.assertIn("scriptMode='one_prompt'", code)
        self.assertIn("route='video'", code)
        self.assertIn("targetDurationSeconds=seconds", code)
        self.assertIn("flow_user_pexels", code)

    def test_build_fast_script_uses_requirements_not_raw_instruction_as_script(self):
        code = _node("Build Fast Script")["parameters"]["jsCode"]

        self.assertIn("parseRequirements", code)
        self.assertIn("requirements.topic", code)
        self.assertIn("bitcoinScript", code)
        self.assertIn("maxWords=Math.round(targetDuration * 2.2)", code)
        self.assertIn("preferred_source:preferred", code)
        self.assertNotIn("الجمهور مبتدئون", code)
        self.assertNotIn("60 ثانية باللغة العربية يشرح", code)

    def test_arabic_font_and_subtitle_params_are_sent_to_api(self):
        body = _node("Create MoneyPrinterTurbo Task")["parameters"]["jsonBody"]

        self.assertIn("NotoSansArabic-Regular.ttf", body)
        self.assertIn("subtitle_enabled: true", body)
        self.assertIn("font_size: 54", body)
        self.assertNotIn("DejaVuSans.ttf", body)

    def test_completed_video_delivery_resolves_final_url_and_sends_video(self):
        capture = _node("Capture Final Video")["parameters"]["jsCode"]
        send = _node("Telegram Send Final Video")["parameters"]

        self.assertIn("function toApiUrl", capture)
        self.assertIn("if (/^https?:\\/\\//i.test(path)) return path;", capture)
        self.assertIn("combined_videos", capture)
        self.assertEqual(send["operation"], "sendVideo")
        self.assertTrue(send["binaryData"])
        self.assertEqual(send["binaryPropertyName"], "video")


if __name__ == "__main__":
    unittest.main()
