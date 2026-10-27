"""Try dictation on recordings from the command line, without touching the production database.

    GROQ_API_KEY=... XAI_API_KEY=... python -m app.voice.cli record1.ogg record2.ogg --project sto

Prints, per file, what was recognised and what the correction made of it. Meant for the first 10-15 real recordings.
"""
from __future__ import annotations

import argparse
import asyncio
import mimetypes
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.voice import get_stt, get_voice_llm
from app.core.config import get_settings
from app.db.models import Base
from app.voice.service import VoiceService
from app.voice.stt import STTError


async def run(paths: list[Path], project: str | None) -> int:
    settings = get_settings()
    stt, llm = get_stt(settings), get_voice_llm(settings)
    if stt is None:
        print("GROQ_API_KEY is not set")
        return 2
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    failures = 0
    with Session(engine, expire_on_commit=False) as db:
        service = VoiceService(db=db, settings=settings, stt=stt, llm=llm)
        for path in paths:
            print(f"\n=== {path.name}")
            try:
                result = await service.dictate(
                    path.read_bytes(), filename=path.name,
                    content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    account="cli", project=project, context=[])
            except STTError as exc:
                failures += 1
                print(f"recognition failed: {exc}")
                continue
            if result.empty:
                print(f"(empty: {result.empty_reason})")
                continue
            print(f"raw:      {result.raw_text}")
            print(f"corrected: {result.text}")
            print(f"cleaned={result.cleaned} fallback={result.fallback_reason} replacements={result.replacements}")
    engine.dispose()
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--project", default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.files, args.project)))


if __name__ == "__main__":
    main()
