"""Offline 9:16 typography renderer. No paid media, downloads or fabricated footage."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID
from PIL import Image, ImageDraw, ImageFont
from app.workflows.article_contracts import ShortVideoProductionBrief

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

def asset_path(root: Path, product_id: UUID, asset_id: UUID, name: str) -> Path:
    if name not in {'video.mp4', 'cover.png', 'subtitles.srt', 'script.txt'}:
        raise ValueError('Unsupported asset filename')
    base = (root / '_content' / str(UUID(str(product_id))) / str(UUID(str(asset_id)))).resolve()
    if not base.is_relative_to(root.resolve()):
        raise ValueError('Asset path escapes storage')
    return base / name

def lines_for(text, font, width):
    lines, line = [], ''
    for word in text.split():
        candidate = (line + ' ' + word).strip()
        if font.getlength(word) > width:
            raise ValueError('Video word too long for safe layout')
        if line and font.getlength(candidate) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + ([line] if line else [])

def card(text: str, brand: str, index: int, total: int, output: Path):
    image = Image.new('RGB', (720, 1280), '#101d32')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((52, 70, 668, 80), radius=5, fill='#2e4768')
    draw.rounded_rectangle((52, 70, 52 + int(616 * index / total), 80), radius=5, fill='#6ad4d0')
    draw.text((54, 126), brand[:35], font=ImageFont.truetype(BOLD, 30), fill='#6ad4d0')
    for size in range(46, 25, -2):
        font = ImageFont.truetype(BOLD, size)
        lines = lines_for(text, font, 600)
        if len(lines) * (size + 18) <= 570:
            break
    else:
        raise ValueError('Too much visible text for a phone screen')
    y = 370 - (len(lines) - 1) * (size + 18) / 2
    for line in lines:
        draw.text((60, int(y)), line, font=font, fill='#f4f7fb')
        y += size + 18
    draw.text((60, 1000), f'{index:02} / {total:02}', font=ImageFont.truetype(FONT, 24), fill='#9eacc2')
    image.save(output)

def stamp(second: float):
    millis = round(second * 1000)
    h, rest = divmod(millis, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def render_video(brief: ShortVideoProductionBrief, target: Path, brand: str):
    if brief.shots[0].start_second != 0 or brief.shots[-1].end_second != brief.duration_seconds:
        raise ValueError('Incomplete video timeline')
    if any(a.end_second != b.start_second for a,b in zip(brief.shots, brief.shots[1:])):
        raise ValueError('Video timeline contains gaps/overlaps')
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target) as tmp:
        scratch = Path(tmp)
        segments = []
        subtitles = []
        for i, shot in enumerate(brief.shots, 1):
            text = shot.on_screen_text or shot.spoken_text
            if not text:
                raise ValueError('No visible scene text')
            frame, clip = scratch / f'{i}.png', scratch / f'{i}.mp4'
            card(text, brand, i, len(brief.shots), frame)
            if i == 1:
                # Cover is the actual first frame, not an invented image.
                Image.open(frame).save(target / 'cover.png')
            frames = round((shot.end_second - shot.start_second) * 25)
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-loop', '1',
                '-i', str(frame), '-vf', "zoompan=z='1+0.0004*on':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=720x1280:fps=25,fade=t=in:st=0:d=0.25",
                '-r', '25', '-frames:v', str(frames), '-c:v', 'libx264', '-preset', 'ultrafast',
                '-crf', '23', '-pix_fmt', 'yuv420p', str(clip)], check=True, timeout=180,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            segments.append(clip)
            subtitles.append(f'{i}\n{stamp(shot.start_second)} --> {stamp(shot.end_second)}\n{text}\n')
        manifest = scratch / 'segments.txt'
        manifest.write_text('\n'.join(f"file '{p.name}'" for p in segments))
        tmp_video = scratch / 'complete.mp4'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat',
            '-safe', '1', '-i', str(manifest), '-c', 'copy', '-movflags', '+faststart', str(tmp_video)],
            check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'json', str(tmp_video)], check=True, capture_output=True, text=True, timeout=10)
        if abs(float(json.loads(probe.stdout)['format']['duration']) - brief.duration_seconds) > .1:
            raise ValueError('Rendered duration mismatch')
        os.replace(tmp_video, target / 'video.mp4')
    (target / 'subtitles.srt').write_text('\n'.join(subtitles), encoding='utf-8')
    (target / 'script.txt').write_text(brief.voiceover_or_dialogue_script, encoding='utf-8')
    return {'duration_seconds': brief.duration_seconds, 'width': 720, 'height': 1280,
            'fps': 25, 'audio': 'none', 'format': 'mp4/h264', 'mode': 'typography_cards'}
