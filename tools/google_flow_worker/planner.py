from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


FLOW_ENABLED_MODES = {"auto", "flow_user_pexels"}
FLOW_FRIENDLY_STYLES = {"cartoon", "illustration", "3d", "futuristic"}


@dataclass(frozen=True)
class SceneCandidate:
    scene_id: int
    prompt: str
    reason: str


def _scene_get(scene: object, key: str, default=None):
    if isinstance(scene, dict):
        return scene.get(key, default)
    return getattr(scene, key, default)


def _scene_id(scene: object, fallback: int) -> int:
    try:
        value = int(_scene_get(scene, "scene_id", fallback) or fallback)
    except (TypeError, ValueError):
        value = fallback
    return value


def build_flow_prompt(scene: object, visual_style: str = "futuristic") -> str:
    visual_prompt = str(_scene_get(scene, "visual_prompt", "") or "").strip()
    visual_query = str(_scene_get(scene, "visual_query", "") or "").strip()
    base = visual_prompt or visual_query
    if visual_prompt and visual_query and visual_query.lower() not in visual_prompt.lower():
        base = f"{visual_prompt}, {visual_query}"
    if not base:
        base = str(_scene_get(scene, "narration", "") or "").strip()
    directives = [
        base,
        f"{visual_style} style",
        "vertical 9:16",
        "no text",
        "professional cinematic footage",
    ]
    return ", ".join(part for part in directives if part)


def select_auto_flow_candidates(
    scenes: Iterable[object],
    existing_flow_scene_ids: Iterable[int] | None,
    visual_style: str,
    material_source_mode: str,
    max_auto_flow_scenes: int = 2,
) -> list[SceneCandidate]:
    if material_source_mode not in FLOW_ENABLED_MODES:
        return []

    existing = {int(scene_id) for scene_id in (existing_flow_scene_ids or [])}
    ordered_scenes = list(scenes or [])
    budget = max(0, int(max_auto_flow_scenes or 0))
    if budget <= 0:
        return []

    selected: list[SceneCandidate] = []
    selected_ids: set[int] = set()

    def add(scene: object, reason: str, index: int) -> None:
        if len(selected) >= budget:
            return
        scene_id = _scene_id(scene, index)
        if scene_id in existing or scene_id in selected_ids:
            return
        selected_ids.add(scene_id)
        selected.append(
            SceneCandidate(
                scene_id=scene_id,
                prompt=build_flow_prompt(scene, visual_style),
                reason=reason,
            )
        )

    for index, scene in enumerate(ordered_scenes, start=1):
        if str(_scene_get(scene, "preferred_source", "") or "").lower() == "flow":
            add(scene, "preferred_source", index)

    if selected:
        return selected

    if str(visual_style or "").lower() in FLOW_FRIENDLY_STYLES:
        for index, scene in enumerate(ordered_scenes, start=1):
            add(scene, "visual_style", index)

    return selected
