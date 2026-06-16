"""
Wraps faster-whisper for audio transcription.
Loaded once at startup via init(); all requests share the same model instance.
"""
import tempfile
from pathlib import Path

from faster_whisper import WhisperModel

from app.utils.logger import get_logger

logger = get_logger(__name__)

_model: WhisperModel | None = None
_ready: bool = False



def init(model_size: str = "base", device: str = "cpu") -> bool:
    global _model, _ready
    try:
        logger.info("Loading Whisper model", model_size=model_size, device=device)
        # compute_type: "float16" on GPU (half-precision, fast + accurate).
        # "int8" on CPU (quantised weights, halves RAM with negligible accuracy loss).
        compute_type = "float16" if device == "cuda" else "int8"
        _model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _ready = True
        logger.info("Whisper model ready", model_size=model_size, device=device)
        return True
    except Exception as exc:
        logger.warning("Whisper model load failed — transcription disabled",
                       error=str(exc))
        return False
    


def transcribe(audio_bytes: bytes) -> str:
    """Transcribe audio bytes to text. Returns '' if not ready or on error."""
    if not _ready or _model is None:
        return ""
    try:
        # WHY NamedTemporaryFile with delete=False: faster-whisper opens the
        # file by path internally; deleting before it finishes reading causes
        # a Windows file-lock error. We delete manually after transcription.
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)

        segments, _ = _model.transcribe(str(tmp_path), language="en")
        transcript = " ".join(seg.text.strip() for seg in segments)
        return transcript.strip()
    except Exception as exc:
        logger.error("Transcription failed", error=str(exc), exc_info=True)
        return ""
    finally:
        # WHY finally: guarantees cleanup even if transcribe() raises mid-way.
        if tmp_path.exists():
            tmp_path.unlink()

