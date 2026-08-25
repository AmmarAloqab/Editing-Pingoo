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

    def test_video_source_option_3_state_reaches_create_task_once(self):
        code = _node("Command Router")["parameters"]["jsCode"]

        self.assertIn("user.awaiting='video_material_source'", code)
        self.assertIn("'3':{\n    key:'flow_user_pexels'", code)
        self.assertIn("user.pending_material_source_mode=materialSourceMode", code)
        self.assertIn("route='video'", code)
        self.assertIn("scriptMode='auto'", code)

        create_task_connections = _workflow()["connections"]["Create MoneyPrinterTurbo Task"]["main"]
        self.assertEqual(len(create_task_connections), 1)

    def test_workflow_export_does_not_overwrite_runtime_static_data(self):
        self.assertNotIn("staticData", _workflow())

    def test_build_fast_script_uses_global_regexes_with_match_all(self):
        code = _node("Build Fast Script")["parameters"]["jsCode"]

        self.assertIn(r"/ابدأ\s+ب([^\.\n]+)/ig", code)
        self.assertIn(r"/واختتم\s+ب([^\.\n]+)/ig", code)
        self.assertNotIn(r"/ابدأ\s+ب([^\.\n]+)/i,", code)
        self.assertNotIn(r"/واختتم\s+ب([^\.\n]+)/i,", code)

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

    def test_create_task_body_sends_target_duration_seconds(self):
        body = _node("Create MoneyPrinterTurbo Task")["parameters"]["jsonBody"]

        self.assertIn("target_duration_seconds: Number($json.target_duration_seconds || 0)", body)

    def test_gravity_one_prompt_uses_current_flow_scene_plan(self):
        code = _node("Build Fast Script")["parameters"]["jsCode"]

        self.assertIn("function gravityScenes", code)
        self.assertIn("modern city suddenly losing gravity", code)
        self.assertIn("People and cars floating above a city street", code)
        self.assertIn("/جاذبية|الجاذبية|gravity/i.test(requirements.topic)", code)
        self.assertIn("'flow'", code)

    def test_scheduled_poll_failed_state_wins_over_combined_video(self):
        code = _node("Update Scheduled Status")["parameters"]["jsCode"]

        self.assertIn("const failed = payload.state === -1", code)
        self.assertIn("if (failed) {\n    status='failed';", code)
        self.assertIn("const hasFinalVideos = Array.isArray(payload.videos)", code)
        self.assertNotIn("Array.isArray(payload.combined_videos) &&\n      payload.combined_videos.length > 0\n    );\n\n  if (\n    progress >= 100 ||\n    hasVideos", code)

    def test_polling_stops_and_failed_task_does_not_stay_at_40(self):
        load = _node("Load Pending Jobs")["parameters"]["jsCode"]
        update = _node("Update Scheduled Status")["parameters"]["jsCode"]

        self.assertIn("j.status === 'submitted'", load)
        self.assertIn("j.status === 'processing'", load)
        self.assertNotIn("j.status === 'failed'", load)
        self.assertIn("send_failure_notice:sendFailureNotice", update)
        self.assertIn("❌ فشلت مهمة الفيديو", update)
        self.assertIn("progress,", update)

    def test_failed_detection_sends_sanitized_telegram_notice(self):
        failed_gate = _node("Failed Detection Gate")["parameters"]["jsCode"]
        failed_send = _node("Telegram Failed Proof")["parameters"]

        self.assertIn("send_failure_notice === true", failed_gate)
        self.assertEqual(failed_send["operation"], "sendMessage")
        self.assertEqual(failed_send["text"], "={{ $json.failure_notice }}")
        self.assertIn("Failed Detection Gate", _workflow()["connections"])

    def test_final_success_report_reads_actual_render_manifest_summary(self):
        code = _node("Completed Detection Gate")["parameters"]["jsCode"]

        self.assertIn("final_material_report", code)
        self.assertIn("تقرير المواد النهائي", code)
        self.assertIn("Google Flow count", code)
        self.assertIn("Artifact Validation", code)
        self.assertIn("flow_scene_ids", code)

    def test_build_fast_script_extends_gravity_narration_for_target_duration(self):
        code = _node("Build Fast Script")["parameters"]["jsCode"]

        self.assertIn("while (words.length < minWords)", code)
        self.assertIn("خلال هذه الثواني القصيرة", code)

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
