"""Mic -> wav. Stream A.

No speech-to-text: MixMind judges how you sound, not what you said, so the
words never enter the pipeline. See README "Why there is no STT".
"""
import os, tempfile, wave
import numpy as np

SR          = 16000
SILENCE_DB  = -40.0   # below this a frame counts as silence
SILENCE_S   = 0.9     # stop after this much trailing silence
MIN_S, MAX_S = 1.5, 25.0

# Which input to open. The Pi has no built-in mic: a USB mic, a USB speaker and
# HDMI audio all register, and the "default" is regularly not the one you want.
# Set MIXMIND_MIC to an index (2) or part of a name ("USB"). Blank = default.
#   python listen.py     lists what this machine can see
MIC = os.environ.get("MIXMIND_MIC", "").strip()


def _device():
    if not MIC:
        return None                      # sounddevice picks the system default
    if MIC.lstrip("-").isdigit():
        return int(MIC)
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and MIC.lower() in d["name"].lower():
            return i
    raise RuntimeError("MIXMIND_MIC=%r matches no input device; run "
                       "'python listen.py' to list them" % MIC)


def list_devices():
    import sounddevice as sd
    cur = _device()
    print("input devices (set MIXMIND_MIC to an index or a name fragment):")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print("  %s[%d] %-42s %d ch @ %.0f Hz"
                  % ("->" if i == cur else "  ", i, d["name"],
                     d["max_input_channels"], d["default_samplerate"]))
    print("\ncurrently selected: %s" % ("default" if cur is None else cur))


NO_SPEECH_S = 8.0     # nobody has said anything: give up rather than wait 25 s


class Endpointer:
    """Decides, chunk by chunk, when the guest has finished talking.

    Uses Silero when it is available. The energy fallback cannot hear the end
    of speech in a loud hall -- the room never goes quiet, so it records until
    the 25 s hard stop for every single guest.
    """

    def __init__(self, use_vad=True):
        import vad
        self.vad = vad.Stream() if use_vad and vad.available() else None
        self.chunk = vad.CHUNK
        self.heard = False
        self.quiet_for = self.elapsed = 0.0

    def feed(self, block_int16):
        """Feed exactly self.chunk samples. Returns (done, level_db)."""
        x = block_int16.astype(np.float32) / 32768.0
        db = 20 * np.log10(float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) + 1e-12)
        speaking = (self.vad.feed(x) > 0.5) if self.vad else (db >= SILENCE_DB)
        dt = len(x) / SR
        self.elapsed += dt
        if speaking:
            self.heard, self.quiet_for = True, 0.0
        else:
            self.quiet_for += dt
        done = (self.elapsed >= MAX_S
                or (self.heard and self.elapsed > MIN_S and self.quiet_for > SILENCE_S)
                or (not self.heard and self.elapsed > NO_SPEECH_S))
        return done, db


def record(path=None, on_level=None):
    """Blocking record until they stop talking, or 25 s. Returns the wav path."""
    import sounddevice as sd          # imported late: the Pi has it, laptops may not
    path = path or tempfile.mktemp(suffix=".wav")
    ep = Endpointer()
    chunks = []
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16",
                        blocksize=ep.chunk, device=_device()) as s:
        while True:
            buf, _ = s.read(ep.chunk)
            chunks.append(buf.copy())
            done, db = ep.feed(buf[:, 0])
            if on_level: on_level(db)
            if done:
                break

    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.concatenate(chunks).tobytes())
    return path


if __name__ == "__main__":
    list_devices()
