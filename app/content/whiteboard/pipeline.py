"""A bounded, vendor-neutral whiteboard production pipeline.

This module never starts paid jobs itself. Provider adapters are passed in
explicitly by a future owner-approved worker, not loaded from global secrets.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol
import hashlib
import json

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
        sanitizer=None, checkpoints=None, progress=None,
        critic=None, visual_author=None, visual_review=False, max_api_calls=25,
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
        self.progress = progress
        self.critic, self.visual_author = critic, visual_author
        self.visual_review, self.max_api_calls = visual_review, max_api_calls
        if visual_review and (critic is None or visual_author is None):
            raise ValueError('An advisory critic and primary visual author are required')

    def _report(self, stage, completed, total=None, *, scene=None, beat=None):
        if self.progress is not None:
            self.progress({'stage': stage, 'progress_done': completed, 'progress_total': total,
                           'scene_index': scene, 'beat_index': beat})

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
        if self.checkpoints is not None and bool(self.checkpoints.identity.get('visual_review')) != self.visual_review:
            raise ValueError('Checkpoint visual review policy does not match this job')
        self._report('storyboard', 0)
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
        total = 2 + 2 * len(storyboard.scenes) + sum(len(scene.beats) for scene in storyboard.scenes)
        completed = 1
        for scene in storyboard.scenes:
            for text in (scene.title, *(b.label for b in scene.beats),
                         *(b.narration for b in scene.beats), *(b.visual for b in scene.beats)):
                self._check_text(text)

        reviewed_images = {}
        visual_audit = None
        layout_repairs = []
        if self.visual_review:
            from .review import VisualReview, VisualDecision, IssueDecision, REVIEW_POLICY
            from .runtime import ReadinessError
            if total - 1 + 2 + len(storyboard.scenes) > self.max_api_calls:
                raise ReadinessError('review_budget')
            total += 2
            originals = []
            for index, scene in enumerate(storyboard.scenes):
                self._report('image', completed, total, scene=index)
                async def generate():
                    return await self.images.generate(scene, request)
                image = (await self.checkpoints.bytes_stage(f'scene-{index:02}-image', generate)
                         if self.checkpoints else await generate())
                if not isinstance(image, bytes) or not image.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('Image provider returned invalid PNG bytes')
                originals.append(image); completed += 1
            self._report('visual_review', completed, total)
            async def review_images():
                return await self.critic.review(storyboard, tuple(originals))
            review = (await self.checkpoints.json_stage('visual-review', review_images, VisualReview)
                      if self.checkpoints else await review_images())
            review.check_for(storyboard)
            for issue in review.issues:
                self._check_text(issue.evidence); self._check_text(issue.correction)
            completed += 1
            self._report('visual_decision', completed, total)
            async def decide():
                if any(i.eligible for i in review.issues):
                    return await self.visual_author.decide(storyboard, review)
                return VisualDecision(decisions=[IssueDecision(issue_id=i.id, accept=False,
                    reason='Нет подтверждённого существенного дефекта; платная перерисовка не требуется.')
                    for i in review.issues])
            decision = (await self.checkpoints.json_stage('visual-decision', decide, VisualDecision)
                        if self.checkpoints else await decide())
            decision.check_for(review)
            for item in decision.decisions:
                self._check_text(item.reason)
            completed += 1
            chosen = [index for index in range(len(originals)) if decision.accepted(review, index)]
            total += len(chosen)
            visual_audit = {'policy': REVIEW_POLICY, 'critic_passes': 1,
                'review': review.model_dump(), 'author_decision': decision.model_dump(),
                'redrawn_scenes': chosen, 'revised_images_reviewed_again': False,
                'human_review_required': True, 'images': []}
            if self.progress:
                self.progress({'visual_review_result': visual_audit})
            for index, scene in enumerate(storyboard.scenes):
                image = originals[index]
                corrections = decision.accepted(review, index)
                if corrections:
                    self._report('image_revision', completed, total, scene=index)
                    async def revise():
                        return await getattr(self, 'revision_images', self.images).revise(scene, request, corrections)
                    image = (await self.checkpoints.bytes_stage(f'scene-{index:02}-image-revised', revise)
                             if self.checkpoints else await revise())
                    if not isinstance(image, bytes) or not image.startswith(b'\x89PNG\r\n\x1a\n'):
                        raise ValueError('Revised image provider returned invalid PNG bytes')
                    completed += 1
                reviewed_images[index] = image
                visual_audit['images'].append({'scene_index': index,
                    'original_sha256': hashlib.sha256(originals[index]).hexdigest(),
                    'final_sha256': hashlib.sha256(image).hexdigest()})
            if self.progress:
                self.progress({'visual_review_result': visual_audit})

        assets: list[WhiteboardSceneAssets] = []
        # Sequential scenes bound concurrent API pressure and simplify failure recovery.
        for scene_index, scene in enumerate(storyboard.scenes):
            prefix = f'scene-{scene_index:02}'
            if self.visual_review:
                image = reviewed_images[scene_index]
            elif self.checkpoints is not None:
                self._report('image', completed, total, scene=scene_index)
                image = await self.checkpoints.bytes_stage(
                    prefix + '-image', lambda: self.images.generate(scene, request),
                )
            else:
                self._report('image', completed, total, scene=scene_index)
                image = await self.images.generate(scene, request)
            if not isinstance(image, bytes) or not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Image provider returned invalid PNG bytes")
            if not self.visual_review:
                completed += 1
            self._report('regions', completed, total, scene=scene_index)
            if self.checkpoints is not None:
                regions = await self.checkpoints.json_stage(
                    prefix + '-regions', lambda: self.regions.detect(scene, image), WhiteboardRegionPlan,
                )
            else:
                regions = await self.regions.detect(scene, image)
            # Unusable plans are repaired or replaced offline; see layout.fallback_regions.
            from .layout import complete_diagram_regions
            regions, layout_repair = complete_diagram_regions(scene, image, regions)
            if layout_repair is not None and visual_audit is not None:
                visual_audit.setdefault('region_layout', []).append({'scene_index':scene_index, **layout_repair})
                if self.progress:
                    self.progress({'visual_review_result':visual_audit})
            elif layout_repair is not None and self.progress:
                layout_repairs.append({'scene_index': scene_index, **layout_repair})
                self.progress({'region_layout': layout_repairs})
            completed += 1
            # Separate beat clips: exact drawing windows can later be measured
            # with ffprobe instead of guessing from pauses or character count.
            speech_parts = []
            for beat_index, beat in enumerate(scene.beats):
                self._report('audio_reuse' if getattr(self, 'cached_audio_only', False) else 'speech',
                             completed, total, scene=scene_index, beat=beat_index)
                async def synthesize():
                    if getattr(self, 'cached_audio_only', False):
                        raise PermissionError('Visual repair must never generate new speech')
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
                completed += 1
            speech = tuple(speech_parts)
            assets_for_scene = WhiteboardSceneAssets(scene, image, speech, regions)
            assets_for_scene.validate()
            assets.append(assets_for_scene)

        self._report('rendering', completed, total)
        duration = await self.renderer.render(tuple(assets), destination, request)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Renderer did not produce a nonempty MP4")
        if not (0 < duration <= 180):
            raise ValueError("Renderer returned an invalid video duration")
        if visual_audit is not None:
            meta_path = destination.with_suffix('.json')
            if meta_path.is_file() and not meta_path.is_symlink():
                meta = json.loads(meta_path.read_text())
                meta['visual_review'] = visual_audit
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        return WhiteboardOutput(destination, storyboard, len(assets), duration, visual_audit)
