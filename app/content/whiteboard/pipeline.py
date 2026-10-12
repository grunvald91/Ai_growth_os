"""A bounded, vendor-neutral whiteboard production pipeline.

This module never starts paid jobs itself. Provider adapters are passed in
explicitly by a future owner-approved worker, not loaded from global secrets.
"""
from __future__ import annotations

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
        sanitizer=None, checkpoints=None,
    ) -> None:
        if sanitizer is None or not callable(getattr(sanitizer, "sanitize_text", None)):
            raise ValueError("PrivacyGateway is required for outbound AI stages")
        self.storyboard = storyboard
        self.images = images
        self.speech = speech
        self.regions = regions
        self.renderer = renderer
        self.sanitizer = sanitizer
        self.checkpoints = checkpoints

    def _check_text(self, text: str) -> None:
        # Guard all external media calls, not just the LLM storyboard request.
        if self.sanitizer is not None and self.sanitizer.sanitize_text(text) != text:
            raise ValueError("Sensitive text is not allowed in a video generation request")

    async def produce(
        self, request: WhiteboardRequest, destination: Path, *, approval_verified: bool = False,
    ) -> WhiteboardOutput:
        destination = Path(destination)
        if destination.suffix.lower() != ".mp4" or not destination.parent.is_dir():
            raise ValueError("An existing output directory and .mp4 file name are required")
        if destination.exists():
            raise FileExistsError("Refusing to overwrite an existing video")
        if not request.request_generation or not approval_verified:
            raise PermissionError("Verified owner approval is required for paid generation")
        self._check_text(request.topic)
        if self.checkpoints is not None:
            if self.checkpoints.identity['request'] != request.model_dump(mode='json'):
                raise ValueError('Checkpoint request does not match this job')
            storyboard = await self.checkpoints.json_stage(
                'storyboard', lambda: self.storyboard.plan(request), WhiteboardStoryboard,
            )
        else:
            storyboard = await self.storyboard.plan(request)
        if len(storyboard.scenes) != request.scenes:
            raise ValueError("Storyboard scene count mismatch")
        for scene in storyboard.scenes:
            for text in (scene.title, *(b.label for b in scene.beats),
                         *(b.narration for b in scene.beats), *(b.visual for b in scene.beats)):
                self._check_text(text)

        assets: list[WhiteboardSceneAssets] = []
        # Sequential scenes bound concurrent API pressure and simplify failure recovery.
        for scene_index, scene in enumerate(storyboard.scenes):
            prefix = f'scene-{scene_index:02}'
            if self.checkpoints is not None:
                image = await self.checkpoints.bytes_stage(
                    prefix + '-image', lambda: self.images.generate(scene, request),
                )
            else:
                image = await self.images.generate(scene, request)
            if not isinstance(image, bytes) or not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Image provider returned invalid PNG bytes")
            if self.checkpoints is not None:
                regions = await self.checkpoints.json_stage(
                    prefix + '-regions', lambda: self.regions.detect(scene, image), WhiteboardRegionPlan,
                )
            else:
                regions = await self.regions.detect(scene, image)
            regions.check_for(scene)
            # Separate beat clips: exact drawing windows can later be measured
            # with ffprobe instead of guessing from pauses or character count.
            speech_parts = []
            for beat_index, beat in enumerate(scene.beats):
                async def synthesize():
                    return await self.speech.synthesize(
                        beat.narration, language=request.language, voice=request.voice,
                    )
                if self.checkpoints is not None:
                    audio = await self.checkpoints.bytes_stage(
                        f'{prefix}-beat-{beat_index:02}-speech', synthesize, limit=10_000_000,
                    )
                else:
                    audio = await synthesize()
                speech_parts.append(audio)
            speech = tuple(speech_parts)
            assets_for_scene = WhiteboardSceneAssets(scene, image, speech, regions)
            assets_for_scene.validate()
            assets.append(assets_for_scene)

        duration = await self.renderer.render(tuple(assets), destination, request)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Renderer did not produce a nonempty MP4")
        if not (0 < duration <= 180):
            raise ValueError("Renderer returned an invalid video duration")
        return WhiteboardOutput(destination, storyboard, len(assets), duration)
