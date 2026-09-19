"""Say it out loud. Stream C: the bartender's mouth.

Two backends, tried in that order:

  1. OpenAI TTS      a real voice. Needs OPENAI_API_KEY and the network.
  2. espeak-ng       robotic, offline, always there. On a Mac, `say` instead.

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

SPK        = os.environ.get("MIXMIND_SPK", "").strip()
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
    else:
        dev = _device()
        cmd = ["aplay", "-q"] + (["-D", dev] if dev else []) + [path]
    subprocess.run(cmd, timeout=90, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- the voices
def _openai_wav(text):
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


def _cached(text):
    """Cloud audio costs a round trip; the fixed lines should pay it once."""
    key = hashlib.sha1(("%s|%s|%s" % (TTS_MODEL, VOICE, text)).encode()).hexdigest()
    return os.path.join(CACHE, key + ".wav")


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
def speak(text):
    """Say it. Returns 'openai' / 'espeak' / '' -- never raises."""
    global LAST
    text = (text or "").strip()
    if not text:
        return ""
    try:
        path = _cached(text)
        if os.path.exists(path) and os.path.getsize(path) > 44:
            _play(path); LAST = "openai(cached)"; return LAST
        wav = _openai_wav(text)
        if wav:
            os.makedirs(CACHE, exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(wav)
            os.replace(tmp, path)
            _play(path); LAST = "openai"; return LAST
    except (urllib.error.URLError, OSError, ValueError,
            subprocess.SubprocessError) as e:
        print("  [speak] real voice unavailable (%s) -- falling back" % e)
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


def say(text):
    """speak() on a thread, so the pumps can run while it talks."""
    t = threading.Thread(target=speak, args=(text,), daemon=True)
    t.start()
    return t


def warm(lines):
    """Fetch and cache the fixed lines before the first guest, not during."""
    got = 0
    for line in lines:
        try:
            if os.path.exists(_cached(line)):
                got += 1; continue
            wav = _openai_wav(line)
            if wav:
                os.makedirs(CACHE, exist_ok=True)
                with open(_cached(line), "wb") as f:
                    f.write(wav)
                got += 1
        except Exception:
            pass
    return got


def available():
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai/%s (%s)" % (VOICE, _device() or "default out")
    if MAC or shutil.which("espeak-ng"):
        return "espeak (%s)" % (_device() or "default out")
    return "OFF -- no OPENAI_API_KEY and no espeak-ng"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("-l", "--list"):
        list_devices()
    else:
        print("backend: %s" % available())
        line = " ".join(sys.argv[1:]) or "Right. This one is mostly citrus and soda. Go easy."
        print("spoke via %r" % speak(line))
