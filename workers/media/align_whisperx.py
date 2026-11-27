"""Align the known English script against the actual scene WAV."""

from __future__ import annotations

import wave
from functools import lru_cache
from pathlib import Path

from .tts_kokoro import PAUSE_MS, sentences


@lru_cache(maxsize=1)
def _alignment_model():
    import whisperx

    return whisperx.load_align_model(language_code="en", device="cpu")


def _mock_words(narration: str, duration_ms: int) -> list[dict]:
    lines = sentences(narration)
    speech_ms = duration_ms - PAUSE_MS * (len(lines) - 1)
    count = len(narration.split())
    if speech_ms < count:
        raise ValueError("Audio is too short to align the script with sentence pauses")
    words = []
    for index, line in enumerate(lines):
        for word in line.split():
            start = round(len(words) * speech_ms / count) + index * PAUSE_MS
            end = round((len(words) + 1) * speech_ms / count) + index * PAUSE_MS
            words.append({"word": word, "start_ms": start, "end_ms": end})
    return words


def align_words(narration: str, wav: Path, *, mock: bool = False) -> list[dict]:
    with wave.open(str(wav), "rb") as audio:
        duration_ms = round(1000 * audio.getnframes() / audio.getframerate())
    if not narration.strip():
        return []
    if mock:
        return _mock_words(narration, duration_ms)

    import whisperx

    model, metadata = _alignment_model()
    transcript = [{"text": " ".join(narration.split()), "start": 0.0, "end": duration_ms / 1000}]
    aligned = whisperx.align(transcript, model, metadata, str(wav), device="cpu")
    segments = aligned["word_segments"]
    if not any("start" in word and "end" in word for word in segments):
        raise ValueError("WhisperX could not align this scene")

    words = []
    untimed = []
    previous_end = 0
    # Digits and symbols can lack acoustic timestamps. Preserve them in the gap
    # between neighboring aligned words, including at the beginning and end.
    for segment in [*segments, {"word": "", "start": duration_ms / 1000, "end": duration_ms / 1000}]:
        if "start" not in segment or "end" not in segment:
            untimed.append(segment["word"])
            continue
        start = min(duration_ms, max(previous_end, round(1000 * segment["start"])))
        end = min(duration_ms, max(start, round(1000 * segment["end"])))
        for i, word in enumerate(untimed):
            gap = start - previous_end
            words.append(
                {
                    "word": word,
                    "start_ms": previous_end + round(i * gap / len(untimed)),
                    "end_ms": previous_end + round((i + 1) * gap / len(untimed)),
                }
            )
        untimed.clear()
        if segment["word"]:
            words.append({"word": segment["word"], "start_ms": start, "end_ms": end})
        previous_end = end
    return words
