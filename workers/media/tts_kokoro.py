"""Render one scene to 24 kHz mono PCM, with a pause between sentences."""

from __future__ import annotations

import math
import re
import struct
import wave
from functools import lru_cache
from pathlib import Path

SAMPLE_RATE = 24_000
PAUSE_MS = 250
PAUSE = bytes(SAMPLE_RATE * PAUSE_MS // 1000 * 2)


def sentences(narration: str) -> list[str]:
    # Keep closing quotes with their sentence. A newline also separates utterances.
    return [part.strip() for part in re.split(r"""(?<=[.!?])\s+|(?<=[.!?]["'”’])\s+|\n+""", narration) if part.strip()]


@lru_cache(maxsize=2)
def _pipeline(language: str):
    from kokoro import KPipeline

    return KPipeline(lang_code=language, repo_id="hexgrad/Kokoro-82M", device="cpu")


def synthesize(
    narration: str,
    destination: Path,
    *,
    voice_id: str = "am_michael",
    speed: float = 1.0,
    mock: bool = False,
) -> int:
    if not re.fullmatch(r"[ab][fm]_[a-z0-9]+", voice_id):
        raise ValueError("Expected an American or British English Kokoro voice ID")
    if not 0.7 <= speed <= 1.4:
        raise ValueError("Kokoro voice speed must be between 0.7 and 1.4")

    lines = sentences(narration)
    with wave.open(str(destination), "wb") as wav:
        wav.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        if not lines:
            wav.writeframesraw(PAUSE)
        for index, sentence in enumerate(lines):
            if index:
                wav.writeframesraw(PAUSE)
            if mock:
                # A quiet 240 Hz marker makes speech and the exact sentence gaps distinguishable.
                period = struct.pack("<100h", *(round(1000 * math.cos(2 * math.pi * i / 100)) for i in range(100)))
                frames = round(0.2 * SAMPLE_RATE * len(sentence.split()) / speed)
                wav.writeframesraw((period * ((frames + 99) // 100))[: frames * 2])
                continue

            import numpy as np

            chunks = [
                audio.detach().cpu().numpy().reshape(-1)
                for _, _, audio in _pipeline(voice_id[0])(sentence, voice=voice_id, speed=speed)
                if audio is not None and audio.numel()
            ]
            if not chunks:
                raise ValueError("Kokoro returned no audio for a sentence")
            signal = np.concatenate(chunks)
            # Kokoro pads each utterance with near-silence. Remove that padding
            # at joins so that the inserted 250 ms gap is the actual pause.
            voiced = np.flatnonzero(np.abs(signal) > 0.001)  # -60 dBFS
            if not voiced.size:
                raise ValueError("Kokoro returned only silence for a sentence")
            first = voiced[0] if index else 0
            last = voiced[-1] + 1 if index + 1 < len(lines) else len(signal)
            pcm = np.clip(signal[first:last], -1, 1)
            wav.writeframesraw((pcm * 32767).round().astype("<i2").tobytes())
        return round(wav.tell() * 1000 / SAMPLE_RATE)
