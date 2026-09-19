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
        # A click, a breath or the tap on the touchscreen is not the guest
        # starting to talk. Count them as speaking only after 250 ms of speech
        # in a row -- the same rule features.py uses (Silero's own default).
        # Without it, one click then 0.9 s of quiet ended the recording at 1.7 s.
        self.run = 0
        self.min_talk = int(np.ceil(0.250 * SR / self.chunk))

    def feed(self, block_int16):
        """Feed exactly self.chunk samples. Returns (done, level_db)."""
        x = block_int16.astype(np.float32) / 32768.0
        db = 20 * np.log10(float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) + 1e-12)
        speaking = (self.vad.feed(x) > 0.5) if self.vad else (db >= SILENCE_DB)
        dt = len(x) / SR
        self.elapsed += dt
        if speaking:
            self.run += 1
            self.quiet_for = 0.0
            if self.run >= self.min_talk:
                self.heard = True
        else:
            self.run = 0
            self.quiet_for += dt
        done = (self.elapsed >= MAX_S
                or (self.heard and self.elapsed > MIN_S and self.quiet_for > SILENCE_S)
                or (not self.heard and self.elapsed > NO_SPEECH_S))
        return done, db


def _capture(read, path=None, on_level=None):
    """The recording loop, whatever the audio source. Stops when they stop
    talking, at 25 s, or when the source runs dry."""
    path = path or tempfile.mktemp(suffix=".wav")
    ep = Endpointer()
    chunks = []
    while True:
        buf = read(ep.chunk)
        if buf is None or len(buf) < ep.chunk:
            break
        chunks.append(buf.copy())
        done, db = ep.feed(buf)
        if on_level: on_level(db)
        if done:
            break
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.concatenate(chunks).tobytes() if chunks else b"")
    return path


def record(path=None, on_level=None):
    """Record from the mic until they stop talking, or 25 s."""
    try:
        import sounddevice as sd      # imported late: the Pi has it, laptops may not
    except OSError:                   # PortAudio missing, and installing it needs sudo
        return _record_arecord(path, on_level)
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16",
                        blocksize=Endpointer().chunk, device=_device()) as s:
        return _capture(lambda n: s.read(n)[0][:, 0], path, on_level)


def _alsa_device():
    """MIXMIND_MIC as an ALSA device for arecord: a name fragment ("USB"),
    a card number from `arecord -l` ("3"), or a device string ("plughw:3,0").
    plughw converts whatever the mic delivers to 16 kHz mono for us."""
    import re, subprocess
    if not MIC:
        return None
    if ":" in MIC:
        return MIC
    if MIC.isdigit():
        return "plughw:%s,0" % MIC
    cards = subprocess.run(["arecord", "-l"], capture_output=True, text=True).stdout
    for num, name in re.findall(r"^card (\d+): (.*)$", cards, re.M):
        if MIC.lower() in name.lower():
            return "plughw:%s,0" % num
    raise RuntimeError("MIXMIND_MIC=%r matches no card in `arecord -l`" % MIC)


def _record_arecord(path=None, on_level=None):
    """The same recording loop, fed by ALSA's own recorder. arecord ships on
    every Raspberry Pi, so this needs no install and no sudo."""
    import subprocess
    dev = _alsa_device()
    cmd = ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(SR), "-c", "1"]
    p = subprocess.Popen(cmd + (["-D", dev] if dev else []),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def read(n):
        b = p.stdout.read(n * 2)
        if not b and p.poll() not in (None, 0):
            raise RuntimeError("arecord failed: %s" % p.stderr.read().decode().strip())
        return np.frombuffer(b, dtype=np.int16) if b else None
    try:
        return _capture(read, path, on_level)
    finally:
        p.terminate()
        p.wait()


def replay(wav, path=None, on_level=None):
    """Play a recording through the same loop, in real time, as if spoken
    into the mic -- for testing the kiosk with no microphone (Mac, Docker)."""
    import time, features
    x, sr = features._read_wav(wav)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    pos = [0]
    def read(n):
        time.sleep(n / SR)
        buf = pcm[pos[0]:pos[0] + n]
        pos[0] += n
        return buf
    return _capture(read, path, on_level)


if __name__ == "__main__":
    list_devices()
