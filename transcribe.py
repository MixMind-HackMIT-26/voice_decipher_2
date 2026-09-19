"""What they said. Whisper, offline, on the Pi's CPU.

faster-whisper with base.en: on our recordings tiny.en turned "it's not a
big deal" into "they thought big do" -- the kind of phrase that flips the
sentiment -- while base.en got it right. tiny.en is ~2x faster; set
MIXMIND_STT_MODEL=tiny.en if the Pi is too slow.

Never breaks the pipeline: no package, no model, any error -> "". The voice
measurements still work without the words.

Download the model ONCE while there is internet (it is cached after):
    python transcribe.py
"""
import os
import numpy as np

MODEL = os.environ.get("MIXMIND_STT_MODEL", "base.en")
_model = None
LAST_ERROR = None


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        try:        # cache first: the hall wifi must not be able to break this
            _model = WhisperModel(MODEL, device="cpu", compute_type="int8",
                                  local_files_only=True)
        except Exception:
            _model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    return _model


def available():
    global LAST_ERROR
    try:
        _load()
        return True
    except Exception as e:
        LAST_ERROR = e
        return False


def transcribe(x, sr=16000):
    """x: float audio at 16 kHz (as features._read_wav returns). Returns text."""
    global LAST_ERROR
    try:
        if sr != 16000:
            return ""
        segs, _ = _load().transcribe(np.asarray(x, dtype=np.float32), language="en",
                                     beam_size=1, vad_filter=False,
                                     condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()
    except Exception as e:
        LAST_ERROR = e
        return ""


if __name__ == "__main__":
    import sys
    print("downloading/loading %s ..." % MODEL)
    if not available():
        print("FAILED: %r" % LAST_ERROR)
        sys.exit(1)             # a Docker build must stop here, not ship without it
    print("ready")
