import unittest
from unittest.mock import patch

from app.models.schema import MaterialInfo, VideoParams
from app.services import task


def _scene(scene_id: int, query: str) -> dict:
    return {
        "scene_id": scene_id,
        "narration": query,
        "visual_prompt": query,
        "visual_query": query,
        "duration_seconds": 4.0,
        "preferred_source": "auto",
        "material_status": "pending",
    }


class SceneMaterialRouterTest(unittest.TestCase):
    def test_prioritizes_ordered_user_materials(self):
        calls = []
        assignments = {}

        def fake_download_videos(**kwargs):
            calls.append(kwargs)
            query = kwargs["search_terms"][0]
            return [f"/tmp/{query}.mp4"]

        def fake_patch_script_data(task_id, **updates):
            assignments.update(updates)
            return True

        params = VideoParams(
            video_subject="bitcoin",
            video_terms=[],
            video_source="pexels",
            video_clip_duration=4,
            match_materials_to_script=True,
            supplemental_materials=[
                MaterialInfo(provider="local", url="user-1.mp4"),
                MaterialInfo(provider="local", url="user-2.mp4"),
            ],
            scene_manifest=[
                _scene(1, "Bitcoin digital currency"),
                _scene(2, "person sending Bitcoin transaction"),
                _scene(3, "global blockchain computer network"),
                _scene(4, "Bitcoin mining ASIC farm"),
                _scene(5, "hardware Bitcoin wallet private key"),
            ],
        )

        with (
            patch.object(task.video, "preprocess_video", lambda materials, clip_duration: materials),
            patch.object(task.material, "download_videos", fake_download_videos),
            patch.object(task.task_artifacts, "patch_script_data", fake_patch_script_data),
        ):
            result = task.get_video_materials(
                "scene-router-test",
                params,
                params.video_terms,
                20,
            )

        self.assertEqual(
            result,
            [
                "user-1.mp4",
                "user-2.mp4",
                "/tmp/global blockchain computer network.mp4",
                "/tmp/Bitcoin mining ASIC farm.mp4",
                "/tmp/hardware Bitcoin wallet private key.mp4",
            ],
        )
        self.assertEqual(
            [call["search_terms"] for call in calls],
            [
                ["global blockchain computer network"],
                ["Bitcoin mining ASIC farm"],
                ["hardware Bitcoin wallet private key"],
            ],
        )
        self.assertTrue(all(call["match_script_order"] is True for call in calls))
        self.assertEqual(assignments["scene_material_assignments"][0]["source"], "user")
        self.assertEqual(assignments["scene_material_assignments"][1]["source"], "user")
        self.assertEqual(
            [item["scene_id"] for item in assignments["scene_material_assignments"]],
            [1, 2, 3, 4, 5],
        )

    def test_uses_legacy_path_without_manifest(self):
        calls = []

        def fake_download_videos(**kwargs):
            calls.append(kwargs)
            return ["legacy.mp4"]

        params = VideoParams(
            video_subject="bitcoin",
            video_terms=["legacy query"],
            video_source="pexels",
            video_clip_duration=4,
            match_materials_to_script=True,
            scene_manifest=None,
        )

        with (
            patch.object(task.video, "preprocess_video", lambda materials, clip_duration: []),
            patch.object(task.material, "download_videos", fake_download_videos),
        ):
            result = task.get_video_materials(
                "legacy-test",
                params,
                ["legacy query"],
                20,
            )

        self.assertEqual(result, ["legacy.mp4"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["search_terms"], ["legacy query"])
        self.assertEqual(calls[0]["audio_duration"], 20)


if __name__ == "__main__":
    unittest.main()
