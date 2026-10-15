"""Real local media acceptance tests. Synthetic tones, no paid AI calls."""
import io
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from PIL import Image, ImageDraw
import pytest

from app.content.whiteboard.contracts import (
    WhiteboardBeat, WhiteboardRegion, WhiteboardRegionPlan, WhiteboardRequest,
    WhiteboardScene, WhiteboardSceneAssets, WhiteboardStoryboard,
)
from app.content.whiteboard.pipeline import WhiteboardPipeline
from app.content.whiteboard.render import MarkerSweepRenderer
from app.privacy import PrivacyGateway


@pytest.fixture
def tone():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Local FFmpeg/FFprobe required")
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000", "-t", "0.4", "-c:a", "libmp3lame", "-f", "mp3", "pipe:1"], capture_output=True, check=True).stdout


def assets(audio=b"ID3fixture"):
    scene = WhiteboardScene(title="Техническая проверка", beats=[
        WhiteboardBeat(label="Первый", narration="Это тестовый первый рисунок.", visual="Красный квадрат в верхней части"),
        WhiteboardBeat(label="Второй", narration="Это тестовый второй рисунок.", visual="Синий квадрат в нижней части"),
    ])
    image = Image.new("RGB", (180, 320), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 25, 65, 80), fill="red")
    draw.rectangle((110, 210, 150, 270), fill="blue")
    out = io.BytesIO()
    image.save(out, format="PNG")
    regions = WhiteboardRegionPlan(regions=[
        WhiteboardRegion(beat_index=0, box=(100, 50, 450, 300)),
        WhiteboardRegion(beat_index=1, box=(550, 600, 900, 950)),
    ])
    return WhiteboardSceneAssets(scene, out.getvalue(), (audio, audio), regions)


def req():
    return WhiteboardRequest(product_id=uuid4(), topic="Проверка синхронизации рисунков", request_generation=True)


def test_future_beat_is_hidden_and_previous_beat_remains_visible():
    item = assets()
    renderer = MarkerSweepRenderer(width=180, height=320, fps=5)
    with Image.open(io.BytesIO(item.image_png)) as image:
        first = renderer.frame(image, item, (1.0, 1.0), 0.9)
        second = renderer.frame(image, item, (1.0, 1.0), 1.9)
        assert first.getpixel((40, 40)) == (255, 0, 0)
        assert first.getpixel((130, 230)) == (255, 255, 255)
        assert second.getpixel((40, 40)) == (255, 0, 0)
        assert second.getpixel((130, 230)) == (0, 0, 255)


@pytest.mark.asyncio
async def test_pipeline_produces_real_vertical_mp4_audio_subtitles_cover_and_timeline(tmp_path, tone):
    item = assets(tone)
    class Planner:
        async def plan(self, request):
            return WhiteboardStoryboard(scenes=[item.scene] * 3)
    class Images:
        async def generate(self, scene, request):
            return item.image_png
    class Speech:
        async def synthesize(self, text, **kwargs):
            return tone
    class Regions:
        async def detect(self, scene, image_png):
            return item.regions
    pipeline = WhiteboardPipeline(storyboard=Planner(), images=Images(), speech=Speech(),
        regions=Regions(), renderer=MarkerSweepRenderer(width=180, height=320, fps=5), sanitizer=PrivacyGateway())
    destination = tmp_path / "pilot.mp4"
    result = await pipeline.produce(req(), destination, approval_verified=True)
    assert result.scene_count == 3 and abs(result.duration_seconds - 30) < 0.1
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(destination)], capture_output=True, check=True).stdout)
    assert {s["codec_name"] for s in probe["streams"]} == {"h264", "aac"}
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (180, 320)
    manifest = json.loads(destination.with_suffix(".json").read_text())
    assert len(manifest["beats"]) == 6
    assert 0 < manifest["narration_seconds"] < 30
    assert all(b["end"] == a["start"] for b, a in zip(manifest["beats"], manifest["beats"][1:]))
    assert "Это тестовый первый рисунок." in destination.with_suffix(".srt").read_text()
    with Image.open(destination.with_suffix(".png")) as cover:
        assert cover.size == (180, 320)
    assert not list(tmp_path.glob(".whiteboard-*"))
    original = destination.read_bytes()
    with pytest.raises(FileExistsError):
        await pipeline.produce(req(), destination, approval_verified=True)
    assert destination.read_bytes() == original


def test_overlap_and_corrupt_audio_fail_without_publishing(tmp_path, tone):
    item = assets(tone)
    overlap = WhiteboardRegionPlan(regions=[
        WhiteboardRegion(beat_index=0, box=(0, 0, 700, 700)),
        WhiteboardRegion(beat_index=1, box=(500, 500, 1000, 1000)),
    ])
    renderer = MarkerSweepRenderer(width=180, height=320, fps=5)
    invalid = WhiteboardSceneAssets(item.scene, item.image_png, item.narration_chunks, overlap)
    with pytest.raises(ValueError, match="overlap"):
        renderer.render_sync((invalid,) * 3, tmp_path / "overlap.mp4", req())
    with pytest.raises(RuntimeError, match="media command"):
        renderer.render_sync((assets(b"broken audio"),) * 3, tmp_path / "broken.mp4", req())
    assert list(tmp_path.iterdir()) == []


def test_long_narration_is_not_truncated_or_sped_up(tmp_path, tone):
    long_audio = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000", "-t", "6", "-c:a", "libmp3lame", "-f", "mp3", "pipe:1"], capture_output=True, check=True).stdout
    renderer = MarkerSweepRenderer(width=180, height=320, fps=5)
    with pytest.raises(ValueError, match="exceeds 30 seconds"):
        renderer.render_sync((assets(long_audio),) * 3, tmp_path / "long.mp4", req())
    assert list(tmp_path.iterdir()) == []
