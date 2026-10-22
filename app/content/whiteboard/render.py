"""Independent CPU marker-sweep pilot; no upstream Animator code/weights.

Reveals rectangular regions in horizontal strokes during measured audio beats.
This is not contour tracing or handwriting reconstruction. No provider calls.
"""
from __future__ import annotations

import asyncio
import bisect
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from PIL import Image, ImageDraw

from .contracts import WhiteboardRequest, WhiteboardSceneAssets

SAMPLE_RATE = 24000
MAX_IMAGE_BYTES = 12_000_000
# Speech models do not obey a time budget; longer narration lengthens the video.
MAX_VIDEO_SECONDS = 60
# The same rule for the one-minute video: it may run to twice its target.
MAX_LONG_VIDEO_SECONDS = 120


def max_video_seconds(request) -> int:
    return MAX_VIDEO_SECONDS if request.duration_seconds <= 30 else MAX_LONG_VIDEO_SECONDS


def _run(args: list[str], *, timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Local media command failed") from exc
    return result.stdout


def _timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02}:{minute:02}:{sec:02},{ms:03}"


def _validate_regions(assets: WhiteboardSceneAssets) -> None:
    regions = assets.regions.regions
    for i, first in enumerate(regions):
        for second in regions[i + 1:]:
            if first.beat_index == second.beat_index:
                continue
            a, b = first.box, second.box
            if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
                raise ValueError("Regions from different beats must not overlap")


class MarkerSweepRenderer:
    """Stream RGB frames to FFmpeg, retaining only current images and PCM.

    Speech is never sped up or cut to fit. Shorter narration holds the final
    frame with silence until the 30-second target; longer narration extends
    the video to the narration plus a short tail, up to MAX_VIDEO_SECONDS.
    The async entry point uses a thread; it is not a cancelable job worker.
    """

    def __init__(self, *, width: int = 720, height: int = 1280, fps: int = 20, timeout: int = 180):
        if not (90 <= width <= 1080 and height * 9 == width * 16 and width % 2 == height % 2 == 0):
            raise ValueError("Even 9:16 dimensions required, width 90..1080")
        if not 5 <= fps <= 30 or not 10 <= timeout <= 600:
            raise ValueError("Invalid renderer resource limits")
        self.size, self.fps, self.timeout = (width, height), fps, timeout

    async def render(self, scenes: tuple[WhiteboardSceneAssets, ...], destination: Path, request: WhiteboardRequest) -> float:
        return await asyncio.to_thread(self.render_sync, scenes, destination, request)

    def frame(self, image: Image.Image, assets: WhiteboardSceneAssets, durations: tuple[float, ...], elapsed: float) -> Image.Image:
        boundaries = [0.0]
        for duration in durations:
            boundaries.append(boundaries[-1] + duration)
        if elapsed >= boundaries[-1]:
            return image.copy()
        beat = max(0, bisect.bisect_right(boundaries, max(0.0, elapsed)) - 1)
        progress = min(1.0, max(0.0, (elapsed - boundaries[beat]) / durations[beat]) / 0.85)
        mask = Image.new("L", image.size, 0)
        draw = ImageDraw.Draw(mask)
        w, h = image.size
        boxes = [(region.beat_index, region.box) for region in assets.regions.regions]
        # A detector can label only captions and omit the actual illustration.
        # Reveal the remaining canvas during the final beat, before the scene
        # switch; the post-narration full frame is not sampled for earlier scenes.
        if beat == len(durations) - 1:
            boxes.append((beat, (0, 0, 1000, 1000)))
        for region_beat, box in boxes:
            if region_beat > beat:
                continue
            x1, y1, x2, y2 = box
            left, top = x1 * w // 1000, y1 * h // 1000
            right, bottom = math.ceil(x2 * w / 1000), math.ceil(y2 * h / 1000)
            amount = 1.0 if region_beat < beat else progress
            stripe = max(2, (bottom - top) // 24)
            rows = math.ceil((bottom - top) / stripe)
            stroke = amount * rows
            for row in range(rows):
                fraction = min(1.0, max(0.0, stroke - row))
                span = round((right - left) * fraction)
                if not span:
                    continue
                y = top + row * stripe
                xl = left if row % 2 == 0 else right - span
                draw.rectangle((xl, y, xl + span - 1, min(bottom, y + stripe) - 1), fill=255)
        return Image.composite(image, Image.new("RGB", image.size, "white"), mask)

    def render_sync(self, scenes: tuple[WhiteboardSceneAssets, ...], destination: Path, request: WhiteboardRequest) -> float:
        destination = Path(destination)
        if destination.suffix.lower() != ".mp4" or not destination.parent.is_dir():
            raise ValueError("Existing directory and MP4 destination required")
        targets = [destination, destination.with_suffix(".srt"), destination.with_suffix(".png"), destination.with_suffix(".json")]
        if any(path.exists() or path.is_symlink() for path in targets):
            raise FileExistsError("Output bundle already exists")
        if len(scenes) != request.scenes:
            raise ValueError("Scene count mismatch")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("FFmpeg and FFprobe are required")
        limit_seconds = max_video_seconds(request)
        images = []
        for assets in scenes:
            assets.validate()
            _validate_regions(assets)
            if len(assets.image_png) > MAX_IMAGE_BYTES:
                raise ValueError("Image exceeds size limit")
            with Image.open(io.BytesIO(assets.image_png)) as source:
                w, h = source.size
                if source.format != "PNG" or w * h > 10_000_000 or abs(w / h - 9 / 16) > 0.01:
                    raise ValueError("Valid bounded 9:16 PNG required")
                rgba = source.convert("RGBA")
                background = Image.new("RGBA", source.size, "white")
                background.alpha_composite(rgba)
                images.append(background.convert("RGB").resize(self.size, Image.Resampling.LANCZOS))
        published = []
        with tempfile.TemporaryDirectory(prefix=".whiteboard-", dir=destination.parent) as root:
            root = Path(root)
            durations, chunks, subtitles, ends, timeline = [], [], [], [], []
            total_samples = 0
            for scene_index, assets in enumerate(scenes):
                scene_durations = []
                for beat_index, (beat, encoded) in enumerate(zip(assets.scene.beats, assets.narration_chunks, strict=True)):
                    if len(encoded) > 10_000_000:
                        raise ValueError("Audio exceeds size limit")
                    source = root / f"{scene_index}-{beat_index}.mp3"
                    source.write_bytes(encoded)
                    pcm = _run(["ffmpeg", "-v", "error", "-i", str(source), "-t", str(limit_seconds + 1), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"])
                    samples = len(pcm) // 2
                    if not samples or len(pcm) % 2:
                        raise ValueError("Decoded audio is empty or malformed")
                    start = total_samples / SAMPLE_RATE
                    total_samples += samples
                    end = total_samples / SAMPLE_RATE
                    if end > limit_seconds:
                        raise ValueError(f"Narration exceeds {limit_seconds} seconds; shorten the script")
                    subtitles.append(f"{len(subtitles) + 1}\n{_timestamp(start)} --> {_timestamp(end)}\n{beat.narration}\n")
                    timeline.append({"scene": scene_index, "beat": beat_index, "start": start, "end": end})
                    scene_durations.append(samples / SAMPLE_RATE)
                    chunks.append(pcm)
                durations.append(tuple(scene_durations))
                ends.append(total_samples / SAMPLE_RATE)
            narration = total_samples / SAMPLE_RATE
            seconds = max(request.duration_seconds, min(limit_seconds, math.ceil(narration + 0.5)))
            pcm_path = root / "narration.pcm"
            pcm_path.write_bytes(b"".join(chunks) + b"\0\0" * (seconds * SAMPLE_RATE - total_samples))
            rendered = root / "video.mp4"
            args = ["ffmpeg", "-v", "error", "-nostdin", "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{self.size[0]}x{self.size[1]}", "-framerate", str(self.fps), "-i", "pipe:0", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", str(pcm_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-threads", "2", "-c:a", "aac", "-b:a", "96k", "-t", str(seconds), "-movflags", "+faststart", str(rendered)]
            with (root / "ffmpeg.log").open("wb") as log:
                process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
                watchdog = threading.Timer(self.timeout, process.kill)
                watchdog.start()
                try:
                    for index in range(seconds * self.fps):
                        elapsed = index / self.fps
                        scene_index = min(bisect.bisect_right(ends, elapsed), len(scenes) - 1)
                        offset = ends[scene_index - 1] if scene_index else 0.0
                        # Frame sampling can skip the final 15% of a short beat.
                        # Keep one complete frame immediately before each switch.
                        if elapsed + 1 / self.fps >= ends[scene_index]:
                            frame = images[scene_index]
                        else:
                            frame = self.frame(images[scene_index], scenes[scene_index], durations[scene_index], elapsed - offset)
                        process.stdin.write(frame.tobytes())
                    process.stdin.close()
                    if process.wait(timeout=10) != 0:
                        raise RuntimeError("Video encoding failed or timed out")
                except (BrokenPipeError, subprocess.SubprocessError) as exc:
                    raise RuntimeError("Video encoding failed or timed out") from exc
                finally:
                    watchdog.cancel()
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    if not process.stdin.closed:
                        process.stdin.close()
            probe = json.loads(_run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(rendered)]))
            video = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
            audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
            duration = float(probe["format"]["duration"])
            if not video or not audio or (video["width"], video["height"]) != self.size or abs(duration - seconds) > 0.1:
                raise RuntimeError("Encoded video failed media verification")
            srt, cover, manifest = root / "captions.srt", root / "cover.png", root / "manifest.json"
            srt.write_text("\n".join(subtitles), encoding="utf-8")
            images[0].save(cover, format="PNG")
            manifest.write_text(json.dumps({"renderer": "independent_marker_sweep_v2", "completion_policy": "full_canvas_during_final_beat", "product_id": str(request.product_id), "duration_seconds": duration, "narration_seconds": total_samples / SAMPLE_RATE, "width": self.size[0], "height": self.size[1], "fps": self.fps, "beats": timeline, "video_sha256": hashlib.sha256(rendered.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                # Same-filesystem hard links publish complete files without overwrite.
                for source, target in zip((rendered, srt, cover, manifest), targets, strict=True):
                    os.link(source, target)
                    published.append(target)
            except OSError:
                for path in published:
                    path.unlink()
                raise
            return duration
