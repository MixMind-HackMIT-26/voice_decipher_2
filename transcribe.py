"""What they said. Deepgram nova-3 over the network, Whisper on the Pi's CPU.

    MIXMIND_STT=auto        Deepgram if DEEPGRAM_API_KEY is set, else Whisper
    MIXMIND_STT=deepgram    Deepgram only
    MIXMIND_STT=whisper     Whisper only -- the offline machine

Deepgram first, when there is a key. nova-3 is a single HTTP POST: nothing to
download, nothing to load into the Pi's RAM, and better words than tiny.en.
A 20 s clip is a 640 KB upload, so on a bad hotspot it can be the slow part
-- DG_TIMEOUT_S bounds it and Whisper picks up the pieces.

faster-whisper with tiny.en, measured on the Pi 4 itself:

                      20 s of speech   venue noise
    base.en               9.0 s           3.6 s
    tiny.en               5.1 s           2.0 s

base.en is too slow for a guest standing at the machine. tiny.en is rougher
(79-86% of our script's words against base.en's 98%) but still catches
"I'm fine, really", which is what the demo needs. MIXMIND_STT_MODEL=base.en
on anything faster than a Pi 4.

temperature=0.0 matters even more than the model: on venue noise Whisper
decodes junk (". . . .") and by default retries at five higher temperatures --
30 s on the Pi for a clip with no words in it. One pass, no retries.

Never breaks the pipeline: no package, no model, no network, any error -> "".
The voice measurements still work without the words, and the voice is what
chooses the drink.

Download the Whisper model ONCE while there is internet (it is cached after):
    python transcribe.py
"""
import io, json, os, urllib.error, urllib.request, wave
import env  # noqa: F401  -- loads ~/.mixmind.env
import numpy as np

MODEL       = os.environ.get("MIXMIND_STT_MODEL", "tiny.en")     # whisper's
WANT        = os.environ.get("MIXMIND_STT", "auto").strip().lower()
DG_MODEL    = os.environ.get("MIXMIND_DG_STT_MODEL", "nova-3")
DG_URL      = "https://api.deepgram.com/v1/listen"
DG_TIMEOUT_S = float(os.environ.get("MIXMIND_DG_STT_TIMEOUT", "8"))
_model = None
LAST_ERROR = None
BACKEND = None          # what actually produced the last transcript


def _key():
    return os.environ.get("DEEPGRAM_API_KEY", "").strip()


def _order():
    if WANT == "deepgram":
        return ["deepgram"]
    if WANT == "whisper":
        return ["whisper"]
    return (["deepgram"] if _key() else []) + ["whisper"]


# ------------------------------------------------------------------ deepgram
def _wav_bytes(x, sr):
    """float audio -> a 16-bit mono wav in memory. No file, no scipy."""
    pcm = (np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0) * 32767
           ).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm)
    return buf.getvalue()


def _deepgram(x, sr):
    key = _key()
    if not key:
        return None
    url = "%s?model=%s&smart_format=true&punctuate=true&language=en" % (DG_URL, DG_MODEL)
    req = urllib.request.Request(
        url, data=_wav_bytes(x, sr),
        headers={"Authorization": "Token " + key, "Content-Type": "audio/wav"})
    with urllib.request.urlopen(req, timeout=DG_TIMEOUT_S) as r:
        out = json.loads(r.read())
    alts = out["results"]["channels"][0]["alternatives"]
    return (alts[0]["transcript"] or "").strip() if alts else ""


# ------------------------------------------------------------------- whisper
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


def _whisper(x, sr):
    if sr != 16000:
        return ""
    segs, _ = _load().transcribe(np.asarray(x, dtype=np.float32), language="en",
                                 beam_size=1, vad_filter=False,
                                 condition_on_previous_text=False,
                                 temperature=0.0, without_timestamps=True)
    return " ".join(s.text.strip() for s in segs).strip()


# ---------------------------------------------------------------------- both
def available():
    """True if ANY backend can produce words. Loads Whisper if it is the one."""
    global LAST_ERROR
    errs = []
    for name in _order():
        if name == "deepgram":
            if _key():
                return True
            errs.append("no DEEPGRAM_API_KEY")
            continue
        try:
            _load()
            return True
        except Exception as e:
            errs.append(str(e))
    LAST_ERROR = "; ".join(errs) or "no speech-to-text configured"
    return False


def describe():
    """One phrase for the startup banner."""
    if not available():
        return "OFF (%s)" % LAST_ERROR
    first = _order()[0]
    return "deepgram/%s" % DG_MODEL if first == "deepgram" else "whisper/%s" % MODEL


def transcribe(x, sr=16000):
    """x: float audio at 16 kHz (as features._read_wav returns). Returns text.

    Tries each backend in turn and returns "" rather than raising: a guest
    with no transcript still gets a drink, because the voice chose it.
    """
    global LAST_ERROR, BACKEND
    for name in _order():
        try:
            got = _deepgram(x, sr) if name == "deepgram" else _whisper(x, sr)
            if got is None:                  # no key for this one
                continue
            BACKEND = name
            return got
        except (urllib.error.URLError, OSError, ValueError, KeyError,
                IndexError, ImportError) as e:
            LAST_ERROR = "%s: %s" % (name, e)
        except Exception as e:               # whisper raises all sorts
            LAST_ERROR = "%s: %s" % (name, e)
    BACKEND = None
    return ""


if __name__ == "__main__":
    import sys
    print("speech-to-text: %s" % describe())
    if _key():
        print("deepgram key found (%s) -- no download needed" % DG_MODEL)
    if "deepgram" not in _order() or WANT == "auto":
        print("loading whisper %s (the offline fallback) ..." % MODEL)
        try:
            _load()
            print("whisper ready")
        except Exception as e:
            print("whisper FAILED: %r" % e)
            if not _key():
                sys.exit(1)      # a Docker build must stop here, not ship mute
