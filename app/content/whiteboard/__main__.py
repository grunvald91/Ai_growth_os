"""Codespaces CLI for diagnostics and local re-rendering; never calls models."""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import sys
import hashlib

from .checkpoints import CheckpointStore
from .contracts import WhiteboardRegionPlan, WhiteboardRequest, WhiteboardSceneAssets, WhiteboardStoryboard
from .render import MarkerSweepRenderer


def _public_model(value):
    if not value or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', value) or value.startswith(('sk-', 'sk_')):
        return None
    from app.privacy import PrivacyGateway
    return value if PrivacyGateway().sanitize_text(value) == value else None


def doctor() -> dict:
    from app.core.config import get_settings
    settings = get_settings()
    return {
        'python': sys.version.split()[0],
        'ffmpeg_available': bool(shutil.which('ffmpeg')),
        'ffprobe_available': bool(shutil.which('ffprobe')),
        'text_provider': settings.llm_provider,
        'configured_text_model': _public_model(settings.llm_model),
        'claudehub_compatible_key_present': bool(settings.xai_api_key),
        'anthropic_key_present': bool(settings.anthropic_api_key),
        'openrouter_key_present': bool(os.environ.get('OPENROUTER_API_KEY')),
        'whiteboard_models': {kind: _public_model(os.environ.get('WHITEBOARD_' + kind.upper() + '_MODEL'))
            for kind in ('storyboard', 'image', 'speech', 'regions')},
        'paid_generation_enabled_by_cli': False,
        'notes': ['Key presence does not verify API access, balance or model availability.'],
    }


def render_checkpoint(directory: Path, destination: Path, *, width=720, height=1280, fps=20) -> dict:
    directory = Path(directory).absolute()
    # Fixed manifest path only, bounded and no symlink dereference.
    manifest_path = directory / 'manifest.json'
    if any(p.is_symlink() for p in (directory, *directory.parents, manifest_path)):
        raise ValueError('Symlink assets rejected')
    if manifest_path.stat().st_size > 64_000:
        raise ValueError('Manifest exceeds size limit')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    request = WhiteboardRequest.model_validate(manifest['request'])
    from .review import REVIEW_POLICY, VisualReview, VisualDecision
    policy = manifest.get('visual_review')
    if policy not in (None, REVIEW_POLICY):
        raise ValueError('Unsupported visual review policy')
    store = CheckpointStore(directory, request, models=manifest['models'], visual_review=bool(policy),
                            image_provider=manifest.get('image_provider'))
    storyboard = WhiteboardStoryboard.model_validate_json(store.load('storyboard', limit=64_000))
    if policy:
        review = VisualReview.model_validate_json(store.load('visual-review', limit=64_000))
        review.check_for(storyboard)
        decision = VisualDecision.model_validate_json(store.load('visual-decision', limit=64_000))
        decision.check_for(review)
    scenes = []
    image_hashes = []
    layout_repairs = []
    for index, scene in enumerate(storyboard.scenes):
        prefix = f'scene-{index:02}'
        revised = bool(policy) and bool(decision.accepted(review, index))
        image = store.load(prefix + ('-image-revised' if revised else '-image'))
        if policy:
            image_hashes.append({'scene_index': index,
                'original_sha256': hashlib.sha256(store.load(prefix + '-image')).hexdigest(),
                'final_sha256': hashlib.sha256(image).hexdigest()})
        regions = WhiteboardRegionPlan.model_validate_json(store.load(prefix + '-regions', limit=64_000))
        from .layout import complete_diagram_regions
        regions, layout_repair = complete_diagram_regions(scene, image, regions)
        if layout_repair is not None:
            layout_repairs.append({'scene_index':index, **layout_repair})
        audio = tuple(store.load(f'{prefix}-beat-{beat_index:02}-speech', limit=10_000_000)
            for beat_index in range(len(scene.beats)))
        scenes.append(WhiteboardSceneAssets(scene, image, audio, regions))
    renderer = MarkerSweepRenderer(width=width, height=height, fps=fps)
    duration = renderer.render_sync(tuple(scenes), destination, request)
    if policy:
        metadata = destination.with_suffix('.json')
        value = json.loads(metadata.read_text())
        value['visual_review'] = {'policy': policy, 'critic_passes': 1, 'review': review.model_dump(),
            'author_decision': decision.model_dump(), 'redrawn_scenes': [i for i in range(len(storyboard.scenes)) if decision.accepted(review, i)],
            'revised_images_reviewed_again': False, 'human_review_required': True, 'images': image_hashes}
        if layout_repairs:
            value['visual_review']['region_layout'] = layout_repairs
        metadata.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    return {'video': str(destination), 'duration_seconds': duration, 'provider_calls': 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor', help='Report local tools and key presence without printing keys')
    render = commands.add_parser('render', help='Re-render a complete checkpoint bundle without AI calls')
    render.add_argument('--assets', type=Path, required=True)
    render.add_argument('--output', type=Path, required=True)
    render.add_argument('--width', type=int, default=720)
    render.add_argument('--height', type=int, default=1280)
    render.add_argument('--fps', type=int, default=20)
    args = parser.parse_args(argv)
    try:
        result = doctor() if args.command == 'doctor' else render_checkpoint(
            args.assets, args.output, width=args.width, height=args.height, fps=args.fps,
        )
    except Exception as exc:
        # Do not print provider responses or arbitrary exception text (may contain secrets).
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__,
            'message': 'Local operation failed; inspect the private run and configuration.'}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
