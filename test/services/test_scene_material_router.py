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

    def test_flow_materials_bind_to_explicit_scenes_first(self):
        calls = []
        assignments = {}

        def fake_preprocess_video(materials, clip_duration):
            return materials

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
            ],
            flow_materials=[
                MaterialInfo(
                    provider="local",
                    source="flow",
                    scene_id=3,
                    url="flow-3.mp4",
                ),
                MaterialInfo(
                    provider="local",
                    source="flow",
                    scene_id=5,
                    url="flow-5.mp4",
                ),
            ],
            scene_manifest=[
                _scene(1, "Bitcoin digital currency"),
                _scene(2, "Bitcoin transaction"),
                _scene(3, "blockchain network"),
                _scene(4, "Bitcoin mining"),
                _scene(5, "hardware wallet"),
            ],
        )

        with (
            patch.object(task.video, "preprocess_video", fake_preprocess_video),
            patch.object(task.material, "download_videos", fake_download_videos),
            patch.object(task.task_artifacts, "patch_script_data", fake_patch_script_data),
        ):
            result = task.get_video_materials(
                "flow-router-test",
                params,
                params.video_terms,
                20,
            )

        self.assertEqual(
            result,
            [
                "user-1.mp4",
                "/tmp/Bitcoin transaction.mp4",
                "flow-3.mp4",
                "/tmp/Bitcoin mining.mp4",
                "flow-5.mp4",
            ],
        )
        self.assertEqual(
            [call["search_terms"] for call in calls],
            [
                ["Bitcoin transaction"],
                ["Bitcoin mining"],
            ],
        )
        self.assertEqual(
            [
                item["source"]
                for item in assignments["scene_material_assignments"]
            ],
            ["user", "pexels", "flow", "pexels", "flow"],
        )
        self.assertEqual(
            [
                item["scene_id"]
                for item in assignments["scene_material_assignments"]
            ],
            [1, 2, 3, 4, 5],
        )

    def test_material_source_modes_route_without_random_insertion(self):
        scenes = [
            _scene(1, "scene one"),
            _scene(2, "scene two"),
            _scene(3, "scene three"),
            _scene(4, "scene four"),
            _scene(5, "scene five"),
        ]

        expected = {
            "auto": [
                "user-1.mp4",
                "flow-2.mp4",
                "user-2.mp4",
                "flow-4.mp4",
                "/tmp/scene five.mp4",
            ],
            "user_pexels": [
                "user-1.mp4",
                "user-2.mp4",
                "/tmp/scene three.mp4",
                "/tmp/scene four.mp4",
                "/tmp/scene five.mp4",
            ],
            "flow_user_pexels": [
                "user-1.mp4",
                "flow-2.mp4",
                "user-2.mp4",
                "flow-4.mp4",
                "/tmp/scene five.mp4",
            ],
            "pexels_only": [
                "/tmp/scene one.mp4",
                "/tmp/scene two.mp4",
                "/tmp/scene three.mp4",
                "/tmp/scene four.mp4",
                "/tmp/scene five.mp4",
            ],
        }

        expected_sources = {
            "auto": ["user", "flow", "user", "flow", "pexels"],
            "user_pexels": ["user", "user", "pexels", "pexels", "pexels"],
            "flow_user_pexels": ["user", "flow", "user", "flow", "pexels"],
            "pexels_only": ["pexels", "pexels", "pexels", "pexels", "pexels"],
        }

        for mode in expected:
            with self.subTest(mode=mode):
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
                    material_source_mode=mode,
                    supplemental_materials=[
                        MaterialInfo(provider="local", url="user-1.mp4"),
                        MaterialInfo(provider="local", url="user-2.mp4"),
                    ],
                    flow_materials=[
                        MaterialInfo(
                            provider="local",
                            source="flow",
                            scene_id=2,
                            url="flow-2.mp4",
                        ),
                        MaterialInfo(
                            provider="local",
                            source="flow",
                            scene_id=4,
                            url="flow-4.mp4",
                        ),
                    ],
                    scene_manifest=scenes,
                )

                with (
                    patch.object(task.video, "preprocess_video", lambda materials, clip_duration: materials),
                    patch.object(task.material, "download_videos", fake_download_videos),
                    patch.object(task.task_artifacts, "patch_script_data", fake_patch_script_data),
                ):
                    result = task.get_video_materials(
                        f"source-mode-{mode}",
                        params,
                        params.video_terms,
                        20,
                    )

                self.assertEqual(result, expected[mode])
                self.assertEqual(
                    [
                        item["source"]
                        for item in assignments["scene_material_assignments"]
                    ],
                    expected_sources[mode],
                )
                self.assertEqual(
                    [
                        item["scene_id"]
                        for item in assignments["scene_material_assignments"]
                    ],
                    [1, 2, 3, 4, 5],
                )
                self.assertTrue(
                    all(call["video_concat_mode"] == task.VideoConcatMode.sequential for call in calls)
                )

    def test_unknown_material_source_mode_falls_back_to_auto(self):
        assignments = {}

        params = VideoParams(
            video_subject="bitcoin",
            video_terms=[],
            video_source="pexels",
            video_clip_duration=4,
            match_materials_to_script=True,
            material_source_mode="surprise",
            supplemental_materials=[
                MaterialInfo(provider="local", url="user-1.mp4"),
            ],
            flow_materials=[
                MaterialInfo(
                    provider="local",
                    source="flow",
                    scene_id=2,
                    url="flow-2.mp4",
                ),
            ],
            scene_manifest=[
                _scene(1, "scene one"),
                _scene(2, "scene two"),
            ],
        )

        with (
            patch.object(task.video, "preprocess_video", lambda materials, clip_duration: materials),
            patch.object(task.material, "download_videos", lambda **kwargs: ["/tmp/pexels.mp4"]),
            patch.object(
                task.task_artifacts,
                "patch_script_data",
                lambda task_id, **updates: assignments.update(updates) or True,
            ),
        ):
            result = task.get_video_materials(
                "unknown-source-mode",
                params,
                params.video_terms,
                20,
            )

        self.assertEqual(result, ["user-1.mp4", "flow-2.mp4"])
        self.assertEqual(
            [item["source"] for item in assignments["scene_material_assignments"]],
            ["user", "flow"],
        )


if __name__ == "__main__":
    unittest.main()
