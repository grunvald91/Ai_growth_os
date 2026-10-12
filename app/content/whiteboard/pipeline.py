"""A bounded, vendor-neutral whiteboard production pipeline.

This module never starts paid jobs itself. Provider adapters are passed in
explicitly by a future owner-approved worker, not loaded from global secrets.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from app.content.whiteboard.contracts import (
    WhiteboardOutput, WhiteboardRegionPlan, WhiteboardRequest,
    WhiteboardScene, WhiteboardSceneAssets, WhiteboardStoryboard,
)


class StoryboardProvider(Protocol):
    async def plan(self, request: WhiteboardRequest) -> WhiteboardStoryboard: ...


class ImageProvider(Protocol):
    async def generate(self, scene: WhiteboardScene, request: WhiteboardRequest) -> bytes: ...


class SpeechProvider(Protocol):
    async def synthesize(self, text: str, *, language: str, voice: str) -> bytes: ...


class RegionProvider(Protocol):
    async def detect(self, scene: WhiteboardScene, image_png: bytes) -> WhiteboardRegionPlan: ...


class VideoRenderer(Protocol):
    async def render(
        self, scenes: tuple[WhiteboardSceneAssets, ...], destination: Path,
        request: WhiteboardRequest,
    ) -> float: ...


class WhiteboardPipeline:
    def __init__(
        self, *, storyboard: StoryboardProvider, images: ImageProvider,
        speech: SpeechProvider, regions: RegionProvider, renderer: VideoRenderer,
        sanitizer=None,
    ) -> None:
        self.storyboard = storyboard
        self.images = images
        self.speech = speech
        self.regions = regions
        self.renderer = renderer
        self.sanitizer = sanitizer

    def _check_text(self, text: str) -> None:
        # Guard all external media calls, not just the LLM storyboard request.
        if self.sanitizer is not None and self.sanitizer.sanitize_text(text) != text:
            raise ValueError("Sensitive text is not allowed in a video generation request")

    async def produce(self, request: WhiteboardRequest, destination: Path) -> WhiteboardOutput:
        self._check_text(request.topic)
        storyboard = await self.storyboard.plan(request)
        if len(storyboard.scenes) != request.scenes:
            raise ValueError("Storyboard scene count mismatch")
        for scene in storyboard.scenes:
            for text in (scene.title, *(b.label for b in scene.beats),
                         *(b.narration for b in scene.beats), *(b.visual for b in scene.beats)):
                self._check_text(text)

        assets: list[WhiteboardSceneAssets] = []
        # Sequential scenes bound concurrent API pressure and simplify failure recovery.
        for scene in storyboard.scenes:
            image = await self.images.generate(scene, request)
            if not isinstance(image, bytes) or not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Image provider returned invalid PNG bytes")
            regions = await self.regions.detect(scene, image)
            regions.check_for(scene)
            # Separate beat clips: exact drawing windows can later be measured
            # with ffprobe instead of guessing from pauses or character count.
            speech = tuple(await asyncio.gather(*(
                self.speech.synthesize(beat.narration, language=request.language, voice=request.voice)
                for beat in scene.beats
            )))
            assets_for_scene = WhiteboardSceneAssets(scene, image, speech, regions)
            assets_for_scene.validate()
            assets.append(assets_for_scene)

        destination = Path(destination)
        if destination.suffix.lower() != ".mp4" or not destination.parent.is_dir():
            raise ValueError("An existing output directory and .mp4 file name are required")
        if destination.exists():
            raise FileExistsError("Refusing to overwrite an existing video")
        duration = await self.renderer.render(tuple(assets), destination, request)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Renderer did not produce a nonempty MP4")
        if not (0 < duration <= 180):
            raise ValueError("Renderer returned an invalid video duration")
        return WhiteboardOutput(destination, storyboard, len(assets), duration)
