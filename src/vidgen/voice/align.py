"""Fallback word alignment with faster-whisper, used only when TTS returns no word timings."""

import logging
from functools import lru_cache
from pathlib import Path

from vidgen.config import WhisperConfig
from vidgen.models import WordTiming

log = logging.getLogger(__name__)


@lru_cache
def _model(name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(name, device=device, compute_type=compute_type)


def _transcribe(model, audio: Path, lang: str) -> list[WordTiming]:
    segments, _ = model.transcribe(str(audio), language=lang, word_timestamps=True, vad_filter=False)
    # segments is lazy: CUDA errors surface here, not at model construction
    return [WordTiming(word=w.word.strip(), start=w.start, end=w.end)
            for seg in segments for w in (seg.words or [])]


def align(audio: Path, lang: str, cfg: WhisperConfig) -> list[WordTiming]:
    if cfg.device != "cpu":
        try:
            return _transcribe(_model(cfg.model, cfg.device, cfg.compute_type), audio, lang)
        except Exception as e:  # missing cuBLAS/cuDNN DLLs on Windows is common
            log.warning("whisper on %s failed (%s); falling back to CPU int8", cfg.device, e)
    return _transcribe(_model(cfg.model, "cpu", "int8"), audio, lang)
