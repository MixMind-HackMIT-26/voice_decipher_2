"""Say it out loud. Stream C: the bartender's mouth.

Four backends, tried in that order:

  1. ElevenLabs      ELEVENLABS_API_KEY. The only one that takes DIRECTION:
                     the guest's own voice measurements shape the delivery,
                     so a wired guest is answered briskly and a flat one
                     gets something slower and steadier. See _shape().
  2. Deepgram Aura   DEEPGRAM_API_KEY. Lower latency, and it hands back
                     linear16 in a wav container -- exactly what aplay wants,
                     with nothing to decode on the Pi.
  3. OpenAI TTS      OPENAI_API_KEY, if that is the key you have.
  4. espeak-ng       robotic, offline, always there. On a Mac, `say` instead.

Every backend takes the same two arguments, and all but ElevenLabs ignore
the second one.

    MIXMIND_TTS=auto         first backend with a key            (default)
    MIXMIND_TTS=deepgram     skip ElevenLabs -- testing, on a metered plan
    MIXMIND_TTS=elevenlabs   only ElevenLabs, no silent downgrade
    MIXMIND_TTS=espeak       offline, costs nothing

ElevenLabs credits are consumed per CHARACTER, so a weekend of rehearsal can
quietly eat an allowance meant for guests. Test on Deepgram, serve on
ElevenLabs.

Nothing here raises. If every backend fails the machine simply stays quiet,
because a guest with a drink and no voice is a working machine and a guest
watching a crash is not.

    speak("your drink is ready")      blocking; returns the backend it used
    t = say("..."); ...; t.join()     the same, on a thread -- pour while it talks
    python speak.py "hello"           check the speaker before a shift

The speaker is picked the way listen.py picks the mic: card numbers move
after a reboot, so pin it by name.
    MIXMIND_SPK=UACDemo    or an index, or blank for the system default
"""
import hashlib, json, os, platform, shutil, subprocess, threading, urllib.error, urllib.request
import env  # noqa: F401  -- loads ~/.mixmind.env

SPK        = os.environ.get("MIXMIND_SPK", "").strip()
WANT       = os.environ.get("MIXMIND_TTS", "auto").strip().lower()
# ElevenLabs. The voice id comes from their voice library -- paste the id,
# not the name. flash is the low-latency model; multilingual_v2 is the
# documented default and is retried automatically if flash is refused.
EL_KEY_ENV = "ELEVENLABS_API_KEY"
EL_VOICE   = os.environ.get("MIXMIND_EL_VOICE", "JBFqnCBsd6RMkjVDRZzb")
EL_MODEL   = os.environ.get("MIXMIND_EL_MODEL", "eleven_flash_v2_5")
EL_URL     = "https://api.elevenlabs.io/v1/text-to-speech"
EL_RATE    = 24000
# Aura-2, masculine, "natural, smooth, clear, comfortable" -- a bartender
# rather than a receptionist. aura-2-cordelia-en if you want warmer.
DG_VOICE   = os.environ.get("MIXMIND_DG_VOICE", "aura-2-arcas-en")
DG_URL     = "https://api.deepgram.com/v1/speak"
VOICE      = os.environ.get("MIXMIND_VOICE", "ballad")
TTS_MODEL  = os.environ.get("MIXMIND_TTS_MODEL", "gpt-4o-mini-tts")
TIMEOUT_S  = float(os.environ.get("MIXMIND_TTS_TIMEOUT", "8"))
CACHE      = os.environ.get("MIXMIND_TTS_CACHE", "tts_cache")
MAC        = platform.system() == "Darwin"
LAST       = ""          # which backend spoke last -- the logs want this


# ---------------------------------------------------------------- the speaker
def _device():
    """'plughw:2,0' for the card MIXMIND_SPK names, or None for the default."""
    if not SPK or MAC:
        return None
    if SPK.lstrip("-").isdigit():
        return "plughw:%d,0" % int(SPK)
    try:
        out = subprocess.run(["aplay", "-l"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith("card ") and SPK.lower() in line.lower():
            return "plughw:%d,0" % int(line.split()[1].rstrip(":"))
    return None                          # named something that is not plugged in


def list_devices():
    print("output devices (set MIXMIND_SPK to an index or a name fragment):")
    cmd = ["system_profiler", "SPAudioDataType"] if MAC else ["aplay", "-l"]
    try:
        print(subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout)
    except (OSError, subprocess.SubprocessError) as e:
        print("  could not list them: %s" % e)
    print("currently: MIXMIND_SPK=%r -> %s" % (SPK, _device() or "system default"))


def _play(path):
    if MAC:
        cmd = ["afplay", path]
    elif path.endswith(".mp3"):
        player = shutil.which("mpg123") or shutil.which("ffplay")
        if not player:
            raise OSError("got mp3 audio but neither mpg123 nor ffplay is "
                          "installed: sudo apt install mpg123")
        dev = _device()
        cmd = ([player, "-q"] + (["-a", dev] if dev else []) + [path]
               if player.endswith("mpg123") else
               [player, "-nodisp", "-autoexit", "-loglevel", "quiet", path])
    else:
        dev = _device()
        cmd = ["aplay", "-q"] + (["-D", dev] if dev else []) + [path]
    subprocess.run(cmd, timeout=90, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- the voices
def _wav_header(pcm, rate):
    """Raw PCM -> a wav aplay will take. ElevenLabs sends headerless PCM."""
    import struct
    n = len(pcm)
    return (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
            b"data" + struct.pack("<I", n) + pcm)


def _shape(shape):
    """The guest's voice, turned into ElevenLabs voice_settings.

    This is the point of using them at all. stability is inverted expression:
    low is dynamic and varied, high is even and calm. So an energetic,
    animated guest gets a livelier read, and someone flat and halting gets
    something steadier -- the machine answers in kind instead of reading
    every guest in the same cheerful monotone.
    """
    s = shape or {}
    e = min(1.0, max(0.0, float(s.get("energy", 0.5))))
    a = min(1.0, max(0.0, float(s.get("animated", 0.5))))
    h = min(1.0, max(0.0, float(s.get("halting", 0.5))))
    return {
        "stability":        round(min(0.85, max(0.25, 0.72 - 0.32 * e - 0.12 * a + 0.10 * h)), 3),
        "style":            round(min(0.70, max(0.05, 0.08 + 0.38 * e + 0.22 * a)), 3),
        "similarity_boost": 0.75,
        "use_speaker_boost": True,
    }


def _elevenlabs_audio(text, shape=None):
    """(bytes, extension) or None. PCM is wrapped into a wav here; if the
    account's tier refuses PCM we fall back to mp3, which needs mpg123."""
    key = os.environ.get(EL_KEY_ENV, "").strip()
    if not key:
        return None

    def ask(model, fmt):
        body = json.dumps({"text": text, "model_id": model,
                           "voice_settings": _shape(shape)}).encode()
        req = urllib.request.Request(
            "%s/%s?output_format=%s" % (EL_URL, EL_VOICE, fmt), data=body,
            headers={"xi-api-key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.read()

    for model in (EL_MODEL, "eleven_multilingual_v2"):
        try:
            return _wav_header(ask(model, "pcm_%d" % EL_RATE), EL_RATE), ".wav"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise                              # bad key: stop, do not loop
            # 422 here is usually the tier: PCM output is a paid format.
            try:
                return ask(model, "mp3_44100_128"), ".mp3"
            except urllib.error.HTTPError as e2:
                if e2.code in (401, 403) or model != EL_MODEL:
                    raise
    return None


def _deepgram_wav(text, shape=None):
    """Aura, as 16-bit wav bytes. None if there is no key or no net.

    encoding=linear16 + container=wav is the whole reason this is first: the
    reply is a playable wav with a header, so aplay takes it straight and the
    Pi never decodes an mp3.
    """
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not key:
        return None
    url = "%s?model=%s&encoding=linear16&container=wav" % (DG_URL, DG_VOICE)
    req = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode(),
        headers={"Authorization": "Token " + key,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.read()


def _openai_wav(text, shape=None):
    """A real voice, as 16-bit wav bytes. None if there is no key or no net."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    body = json.dumps({"model": TTS_MODEL, "voice": VOICE, "input": text,
                       "response_format": "wav",
                       "instructions": "Warm, unhurried, a bartender talking to "
                                       "one person across the bar. Not perky."}).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech", data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.read()


# (name, function, identity-for-the-cache-key). Order is the fallback order.
def _backends():
    all_of = [("elevenlabs", _elevenlabs_audio, "el/%s/%s" % (EL_VOICE, EL_MODEL)),
              ("deepgram",   _deepgram_wav,     DG_VOICE),
              ("openai",     _openai_wav,       "%s/%s" % (TTS_MODEL, VOICE))]
    if WANT in ("auto", ""):
        return all_of
    if WANT == "espeak":
        return []                        # straight to the offline voice
    return [b for b in all_of if b[0] == WANT]


def _cached(text, ident, shape=None):
    """Cloud audio costs a round trip; the fixed lines should pay it once.

    The voice is in the key, so switching backends never plays back the old
    one out of the cache.
    """
    mark = ""
    if shape and ident.startswith("el/"):         # only ElevenLabs is directed
        v = _shape(shape)
        mark = "|%s/%s" % (v["stability"], v["style"])
    key = hashlib.sha1(("%s|%s%s" % (ident, text, mark)).encode()).hexdigest()
    return os.path.join(CACHE, key)               # the extension is added later


def _robot(text, path):
    """Offline. Never pretty, always available."""
    if MAC:
        subprocess.run(["say", "-o", path, "--data-format=LEI16@22050", text],
                       timeout=30, check=True)
    else:
        subprocess.run(["espeak-ng", "-v", "en-gb", "-s", "150", "-p", "42",
                        "-w", path, text], timeout=30, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- the mouth
def speak(text, shape=None):
    """Say it. `shape` is the guest's axes (energy/halting/animated), which
    only ElevenLabs can act on. Returns the backend used, or '' -- never
    raises."""
    global LAST
    text = (text or "").strip()
    if not text:
        return ""
    for name, fn, ident in _backends():
        try:
            stem = _cached(text, ident, shape)
            for ext in (".wav", ".mp3"):
                if os.path.exists(stem + ext) and os.path.getsize(stem + ext) > 44:
                    _play(stem + ext); LAST = name + "(cached)"; return LAST
            got = fn(text, shape)
            if not got:
                continue                     # no key for this one: try the next
            audio, ext = got if isinstance(got, tuple) else (got, ".wav")
            os.makedirs(CACHE, exist_ok=True)
            tmp = stem + ext + ".part"
            with open(tmp, "wb") as f:
                f.write(audio)
            os.replace(tmp, stem + ext)
            _play(stem + ext); LAST = name; return LAST
        except (urllib.error.URLError, OSError, ValueError,
                subprocess.SubprocessError) as e:
            print("  [speak] %s unavailable (%s) -- falling back" % (name, e))
    try:
        tmp = os.path.join(CACHE if os.path.isdir(CACHE) else ".", "_say.wav")
        _robot(text, tmp)
        _play(tmp)
        LAST = "espeak"
        return LAST
    except (OSError, subprocess.SubprocessError) as e:
        print("  [speak] no voice at all (%s) -- staying quiet: %r" % (e, text[:60]))
        LAST = ""
        return ""


def say(text, shape=None):
    """speak() on a thread, so the pumps can run while it talks."""
    t = threading.Thread(target=speak, args=(text, shape), daemon=True)
    t.start()
    return t


def warm(lines):
    """Fetch and cache the fixed lines before the first guest, not during."""
    got = 0
    for line in lines:
        for name, fn, ident in _backends():
            try:
                stem = _cached(line, ident)
                if any(os.path.exists(stem + e) for e in (".wav", ".mp3")):
                    got += 1; break
                res = fn(line)
                if res:
                    audio, ext = res if isinstance(res, tuple) else (res, ".wav")
                    os.makedirs(CACHE, exist_ok=True)
                    with open(stem + ext, "wb") as f:
                        f.write(audio)
                    got += 1
                    break
            except Exception:
                pass
    return got


def available():
    out = _device() or "default out"
    if WANT not in ("auto", ""):
        for name, _, ident in _backends():
            has = os.environ.get({"elevenlabs": EL_KEY_ENV,
                                  "deepgram": "DEEPGRAM_API_KEY",
                                  "openai": "OPENAI_API_KEY"}[name], "").strip()
            return "%s%s (%s)" % (name, "" if has else " -- NO KEY, will use espeak", out)
        return "espeak, pinned (%s)" % out
    if os.environ.get(EL_KEY_ENV, "").strip():
        return "elevenlabs/%s directed (%s)" % (EL_MODEL, out)
    if os.environ.get("DEEPGRAM_API_KEY", "").strip():
        return "deepgram/%s (%s)" % (DG_VOICE, out)
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai/%s (%s)" % (VOICE, out)
    if MAC or shutil.which("espeak-ng"):
        return "espeak (%s)" % out
    return "OFF -- no ELEVENLABS_API_KEY, no DEEPGRAM_API_KEY, no OPENAI_API_KEY and no espeak-ng"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("-l", "--list"):
        list_devices()
    else:
        print("backend: %s" % available())
        line = " ".join(sys.argv[1:]) or "Right. This one is mostly citrus and soda. Go easy."
        # the same sentence, delivered to two different guests
        for who, shape in (("wired  ", {"energy": .9, "animated": .85, "halting": .05}),
                           ("flat   ", {"energy": .15, "animated": .1, "halting": .7})):
            print("%s %s -> %r" % (who, _shape(shape), speak(line, shape)))
