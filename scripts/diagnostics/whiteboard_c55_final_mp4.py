"""Strict read-only verification of one completed ServGo Whiteboard MP4."""
from __future__ import annotations
import json
import shutil
import subprocess
from uuid import UUID
from sqlalchemy import func, select
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import AgentRun, Artifact, Task, TaskStatus
from app.content.whiteboard.jobs import AGENT, job_directory

TASK_ID = UUID("c55fd232-73f7-4080-b15e-026c4c3e6df3")

def verify():
    settings = get_settings()
    with SessionLocal() as db:
        task = db.get(Task, TASK_ID)
        if task is None or task.assigned_agent != AGENT or task.status != TaskStatus.COMPLETED:
            raise RuntimeError("Whiteboard job not completed")
        states = list(db.scalars(select(Artifact).where(
            Artifact.task_id == TASK_ID, Artifact.kind.in_(("whiteboard_job_state", "whiteboard_video")))))
        by_kind = {a.kind:a for a in states}
        if set(by_kind) != {"whiteboard_job_state", "whiteboard_video"}:
            raise RuntimeError("Completed video has no final result artifact")
        state = json.loads(by_kind["whiteboard_job_state"].content)
        result = json.loads(by_kind["whiteboard_video"].content)
        if state.get("stage") != "completed" or state.get("error") is not None:
            raise RuntimeError("Job is not cleanly completed")
        cap = state.get("owner_chat_paid_attempt_cap") or {}
        if cap.get("max_additional") != 19 or cap.get("baseline_runs") != 40:
            raise RuntimeError("Missing 19-call owner-approved cap")
        begun = db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.task_id == TASK_ID))
        additional = begun - cap["baseline_runs"]
        if not 0 <= additional <= 19:
            raise RuntimeError("Raw provider attempts exceeded authorization")
        folder = job_directory(settings.products_root, task.product_id, TASK_ID) / "output"
        video = folder / "video.mp4"
        if not video.is_file() or video.is_symlink() or video.stat().st_size <= 1024:
            raise RuntimeError("Output MP4 missing or too small")
        with video.open("rb") as inp:
            header = inp.read(32)
        if header[4:8] != b"ftyp":
            raise RuntimeError("Output file is not an MP4 container")
        if not shutil.which("ffprobe"):
            raise RuntimeError("ffprobe unavailable; cannot verify tracks")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,size:stream=codec_type,codec_name,width,height",
             "-of", "json", str(video)],
            capture_output=True, text=True, timeout=30, check=True)
        detail = json.loads(probe.stdout)
        tracks = detail.get("streams") or []
        video_tracks = [t for t in tracks if t.get("codec_type") == "video"]
        audio_tracks = [t for t in tracks if t.get("codec_type") == "audio"]
        seconds = float(detail["format"]["duration"])
        if len(video_tracks) < 1 or len(audio_tracks) < 1 or not 0 < seconds <= 180:
            raise RuntimeError("MP4 lacks audio/video streams or valid duration")
        expected = ("video.mp4", "video.json", "video.png", "video.srt", "video.vtt")
        missing = [name for name in expected if not (folder / name).is_file()]
        if missing:
            raise RuntimeError("Missing output files: " + ", ".join(missing))
        if result.get("duration_seconds") is None or not result.get("human_review_required"):
            raise RuntimeError("Missing video metadata or human-review requirement")
        visual = result.get("visual_review") or {}
        return {
            "result": "verified",
            "task_id": str(TASK_ID),
            "release_sha": settings.release_sha,
            "status": task.status.value,
            "mp4_bytes": video.stat().st_size,
            "duration_seconds": round(seconds, 2),
            "video_codec": video_tracks[0].get("codec_name"),
            "video_dimensions": [video_tracks[0].get("width"),video_tracks[0].get("height")],
            "audio_codec": audio_tracks[0].get("codec_name"),
            "files": list(expected),
            "scenes": result.get("scene_count"),
            "additional_paid_attempts_begun": additional,
            "approved_additional_attempts": 19,
            "unused_attempts": 19-additional,
            "original_images_kept": visual.get("redrawn_scenes") == [],
            "author_decision_fallback": visual.get("author_decision_fallback"),
            "offline_region_fallback": (state.get("c55_offline_region_fallback") or {}).get("stage_names"),
            "human_review_required": True,
            "page_path": f"/studio/products/{task.product_id}/whiteboard/{TASK_ID}",
        }

if __name__ == "__main__":
    print(json.dumps(verify(),ensure_ascii=False,sort_keys=True))
