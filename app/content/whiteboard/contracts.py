"""Provider-independent contracts for a vertical, Russian whiteboard explainer."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# Video length presets: about 30 seconds = 3 scenes, about a minute = twice as many scenes.
SCENES_FOR_SECONDS = {30: 3, 60: 6}

# Spoken when the owner leaves the call-to-action field empty.
DEFAULT_CALL_TO_ACTION = 'Подпишитесь на канал и поставьте лайк, если было интересно!'
DEFAULT_CALL_TO_ACTION_VISUAL = ('Крупная кнопка «Подписаться» с колокольчиком и рядом большой палец вверх (лайк)')


class WhiteboardRequest(Contract):
    product_id: UUID
    topic: str = Field(min_length=8, max_length=400)
    language: Literal["ru"] = "ru"
    aspect_ratio: Literal["9:16"] = "9:16"
    duration_seconds: Literal[30, 60] = 30
    scenes: Literal[3, 6] = 3
    visual_style: Literal["whiteboard_marker"] = "whiteboard_marker"
    voice: str = Field(default="default", min_length=1, max_length=80)
    # What the closing beat asks the viewer to do. Empty: subscribe to the channel and like.
    call_to_action: str = Field(default="", max_length=200)
    # API execution must be manually approved. This is NOT a spending cap.
    request_generation: bool = False

    @model_validator(mode="after")
    def length_matches_scenes(self):
        if SCENES_FOR_SECONDS[self.duration_seconds] != self.scenes:
            raise ValueError("30 seconds means 3 scenes, a minute means 6 scenes")
        # It becomes a spoken beat, which needs at least 6 characters (WhiteboardBeat.narration).
        if self.call_to_action and len(self.call_to_action) < 6:
            raise ValueError("The call to action is too short")
        return self

    @model_serializer(mode="wrap")
    def _keep_legacy_form(self, handler):
        # An empty call to action is omitted, so requests, digests and checkpoints
        # created before this field existed keep exactly the same serialization.
        data = handler(self)
        if not data.get("call_to_action"):
            data.pop("call_to_action", None)
        return data


class WhiteboardBeat(Contract):
    label: str = Field(min_length=1, max_length=60)
    narration: str = Field(min_length=6, max_length=320)
    visual: str = Field(min_length=6, max_length=500)


class WhiteboardScene(Contract):
    title: str = Field(min_length=3, max_length=120)
    beats: list[WhiteboardBeat] = Field(min_length=2, max_length=6)


class WhiteboardStoryboard(Contract):
    scenes: list[WhiteboardScene] = Field(min_length=3, max_length=6)


class WhiteboardRegion(Contract):
    beat_index: int = Field(ge=0, le=5)
    # x_min, y_min, x_max, y_max in normalized 0..1000 coordinates
    box: tuple[int, int, int, int]

    @model_validator(mode="after")
    def valid_box(self):
        x1, y1, x2, y2 = self.box
        if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
            raise ValueError("Region box must be inside the image and nonempty")
        return self


class WhiteboardRegionPlan(Contract):
    regions: list[WhiteboardRegion] = Field(min_length=1, max_length=50)

    def check_for(self, scene: WhiteboardScene) -> None:
        indices = {r.beat_index for r in self.regions}
        expected = set(range(len(scene.beats)))
        if indices != expected:
            raise ValueError("Every narration beat must have a matching drawing region")


@dataclass(frozen=True)
class WhiteboardSceneAssets:
    scene: WhiteboardScene
    image_png: bytes
    narration_chunks: tuple[bytes, ...]  # one MP3 chunk per beat, preserves exact ordering
    regions: WhiteboardRegionPlan

    def validate(self) -> None:
        if not self.image_png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Image provider must supply PNG bytes")
        if len(self.narration_chunks) != len(self.scene.beats) or not all(self.narration_chunks):
            raise ValueError("One nonempty speech chunk per beat is required")
        self.regions.check_for(self.scene)


@dataclass(frozen=True)
class WhiteboardOutput:
    video_path: Path
    storyboard: WhiteboardStoryboard
    scene_count: int
    # Only the renderer can determine actual audio/video duration.
    duration_seconds: float
    visual_review: dict | None = None
