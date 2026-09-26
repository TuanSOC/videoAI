"""Text-to-speech. edge-tts streams audio plus WordBoundary events (offsets in 100ns ticks)."""

import asyncio
import logging
from pathlib import Path

import edge_tts

from vidgen.models import WordTiming

log = logging.getLogger(__name__)
TICKS_PER_SECOND = 10_000_000
# edge-tts MP3s carry no Xing/LAME header, so decoding keeps the encoder delay + decoder padding:
# the audio starts ~46 ms later than the WordBoundary offsets say (measured: onset 0.123 s for a
# boundary at 0.080 s). Shift the words, or captions and sound effects fire early.
MP3_DELAY = 0.046


class TTSError(RuntimeError):
    pass


async def synth_edge(text: str, voice: str, out: Path, attempts: int = 4,
                     rate: str = "+0%") -> list[WordTiming]:
    """Write MP3 to `out`; return word timings relative to the start of that file."""
    for attempt in range(1, attempts + 1):
        try:
            audio = bytearray()
            words: list[WordTiming] = []
            async for chunk in edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary").stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / TICKS_PER_SECOND + MP3_DELAY
                    words.append(WordTiming(word=chunk["text"], start=start,
                                            end=start + chunk["duration"] / TICKS_PER_SECOND))
            if not audio:
                raise TTSError("edge-tts returned no audio")
            out.write_bytes(audio)
            return words
        except Exception as e:  # network hiccups, service throttling
            log.warning("edge-tts attempt %d/%d failed: %s", attempt, attempts, e)
            if attempt == attempts:
                raise TTSError(
                    f"edge-tts failed for voice {voice}: {e}. "
                    "Check internet access; edge-tts is an unofficial Microsoft endpoint."
                ) from e
            await asyncio.sleep(2 ** attempt)  # 2, 4, 8s: throttling clears within seconds
    raise AssertionError("unreachable")
