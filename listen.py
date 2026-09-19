"""Mic -> wav. Stream A.

No speech-to-text: MixMind judges how you sound, not what you said, so the
words never enter the pipeline. See README "Why there is no STT".
"""
import tempfile, wave
import numpy as np

SR          = 16000
SILENCE_DB  = -40.0   # below this a frame counts as silence
SILENCE_S   = 0.9     # stop after this much trailing silence
MIN_S, MAX_S = 1.5, 25.0


def record(path=None, on_level=None):
    """Blocking record until 900 ms of silence, or 25 s. Returns the wav path."""
    import sounddevice as sd          # imported late: the Pi has it, laptops may not
    path = path or tempfile.mktemp(suffix=".wav")
    block = int(SR * 0.05)
    chunks, quiet_for, elapsed = [], 0.0, 0.0

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=block) as s:
        while elapsed < MAX_S:
            buf, _ = s.read(block)
            chunks.append(buf.copy())
            elapsed += block / SR
            rms = float(np.sqrt(np.mean((buf.astype(np.float64) / 32768.0) ** 2)) + 1e-12)
            db = 20 * np.log10(rms)
            if on_level: on_level(db)
            quiet_for = quiet_for + block / SR if db < SILENCE_DB else 0.0
            if elapsed > MIN_S and quiet_for > SILENCE_S:
                break

    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.concatenate(chunks).tobytes())
    return path
