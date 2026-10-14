"""Local speech-to-text for WhatsApp audio messages, using faster-whisper.

The model is loaded on first use and kept in memory. The first time a given model is used,
faster-whisper downloads it from Hugging Face (about 460 MB for "small").

Configuration (environment variables):
    WHISPER_MODEL         Model size or path (default "small"; "base" is faster, "medium" more accurate)
    WHISPER_DEVICE        "cpu" (default), "cuda" or "auto"
    WHISPER_COMPUTE_TYPE  Quantization (default "int8", which is fast on CPU)
"""
import os
import threading
from typing import Optional, Tuple

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(
                os.environ.get("WHISPER_MODEL", "small"),
                device=os.environ.get("WHISPER_DEVICE", "cpu"),
                compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
            )
        return _model


def transcribe_file(path: str, language: Optional[str] = None) -> Tuple[str, str]:
    """Transcribe an audio file.

    Args:
        path: Path to the audio file (any format ffmpeg/PyAV can decode, e.g. WhatsApp .ogg)
        language: ISO 639-1 code such as "es" or "en", or None to detect it

    Returns:
        A tuple of (transcribed text, detected or given language code)
    """
    segments, info = _get_model().transcribe(path, language=language, vad_filter=True)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, info.language
