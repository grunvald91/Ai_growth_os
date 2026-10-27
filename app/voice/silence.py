"""Silence handling: a cheap check before recognition, and a filter for what Whisper invents on silence."""
from __future__ import annotations

import io
import re
import wave
from array import array
from dataclasses import dataclass

from app.voice.stt import Transcript

# Whisper's own rule for a segment that is probably not speech.
NO_SPEECH_PROB = 0.6
LOW_LOGPROB = -1.0
_MIN_BYTES = 1200  # anything smaller than this cannot hold audible speech in any browser or Telegram format

# Phrases Whisper tends to produce on silence or music, in Russian and English.
_PHANTOM_EXACT = frozenset({
    "продолжение следует", "спасибо за просмотр", "спасибо за просмотр и до новых встреч",
    "thanks for watching", "thank you for watching", "thank you for watching and see you next time",
})
_PHANTOM_PATTERNS = (
    re.compile(r"^субтитры\s+(сделал|делал|создавал|подогнал|добавил)\b"),
    re.compile(r"^редактор субтитров\b"),
    re.compile(r"^корректор\b"),
    re.compile(r"^amara\.org\b"),
)


@dataclass(frozen=True)
class Precheck:
    ok: bool
    reason: str | None = None


def precheck_audio(audio: bytes, *, duration_ms: int | None, min_audio_ms: int, silence_rms: float) -> Precheck:
    """Decide without any network call whether the recording is worth recognising."""
    if len(audio) < _MIN_BYTES:
        return Precheck(False, "too_short")
    if duration_ms is not None and duration_ms < min_audio_ms:
        return Precheck(False, "too_short")
    level = _wav_level(audio)
    if level is not None and level < silence_rms:
        return Precheck(False, "silence")
    return Precheck(True)


def _wav_level(audio: bytes) -> float | None:
    """RMS of 16-bit PCM WAV as a share of full scale; None for any other format (checked after recognition)."""
    if audio[:4] != b"RIFF":
        return None
    try:
        with wave.open(io.BytesIO(audio)) as handle:
            if handle.getsampwidth() != 2 or handle.getnframes() == 0:
                return None
            samples = array("h")
            samples.frombytes(handle.readframes(handle.getnframes()))
    except (wave.Error, EOFError, ValueError):
        return None
    if not samples:
        return None
    # Subsample very long recordings: the level is an average, every 4th sample is plenty.
    view = samples[::4] if len(samples) > 400_000 else samples
    mean_square = sum(sample * sample for sample in view) / len(view)
    return (mean_square ** 0.5) / 32768.0


def is_phantom(text: str) -> bool:
    normalized = re.sub(r"[^\w\s.]", " ", text.lower()).replace(".", " ")
    normalized = " ".join(normalized.split())
    if not normalized:
        return True
    return normalized in _PHANTOM_EXACT or any(pattern.match(normalized) for pattern in _PHANTOM_PATTERNS)


def speech_text(transcript: Transcript) -> str:
    """The recognised text without segments that are probably silence, or "" when nothing real was said."""
    if transcript.segments:
        kept = [segment.text for segment in transcript.segments
                if not (segment.no_speech_prob is not None and segment.avg_logprob is not None
                        and segment.no_speech_prob > NO_SPEECH_PROB and segment.avg_logprob < LOW_LOGPROB)]
        text = " ".join(part.strip() for part in kept).strip()
    else:
        text = transcript.text.strip()
    if not text or is_phantom(text):
        return ""
    return text
