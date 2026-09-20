"""Check the whole machine, in one command, before a guest is standing there.

    python preflight.py             everything
    python preflight.py --quiet     no sound out of the speaker

Every check says PASS, WARN or FAIL and, when it is not PASS, the one command
that fixes it. FAIL means a guest gets a bad drink or no drink. WARN means the
machine works but worse -- a robot voice, a repeated line, guessed doses.

Exit code is the number of FAILs, so `python preflight.py && echo READY` works.
"""
import os, subprocess, sys, time
import env  # noqa: F401  -- loads ~/.mixmind.env

QUIET = "--quiet" in sys.argv
ROWS, FAILS, WARNS = [], 0, 0


def row(name, level, detail, fix=""):
    global FAILS, WARNS
    if level == "FAIL": FAILS += 1
    if level == "WARN": WARNS += 1
    ROWS.append((name, level, detail, fix))
    tag = {"PASS": "  ok  ", "WARN": " warn ", "FAIL": " FAIL "}[level]
    print("[%s] %-22s %s" % (tag, name, detail))
    if fix and level != "PASS":
        print("%s-> %s" % (" " * 9, fix))


def check(name, fn, fix="", warn_only=False):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, "%s: %s" % (type(e).__name__, e)
    row(name, "PASS" if ok else ("WARN" if warn_only else "FAIL"), detail, fix)
    return ok


print("MixMind preflight  %s" % time.strftime("%F %T"))
print("env file: %s\n%s" % (env.describe(), "-" * 64))

# ---------------------------------------------------------------- the brain
def _numpy():
    import numpy
    return True, "numpy %s" % numpy.__version__
check("python + numpy", _numpy, "pip install numpy")

def _vad():
    import vad
    return (vad.available(), "silero" if vad.available() else
            "energy fallback (onnxruntime missing)")
check("voice activity", _vad, "pip install onnxruntime", warn_only=True)

def _stt():
    import transcribe
    d = transcribe.describe()
    return not d.startswith("OFF"), d
check("speech-to-text", _stt,
      "put DEEPGRAM_API_KEY in ~/.mixmind.env, or pip install faster-whisper")

# a real round trip, on real speech, not a synthetic tone
def _stt_live():
    import glob, transcribe, features
    if "deepgram" not in transcribe._order():
        return True, "skipped (whisper only)"
    wavs = sorted(glob.glob("tests/samples/*.wav"))
    if not wavs:
        return True, "skipped (no sample wavs)"
    x, sr = features._read_wav(wavs[0])
    t0 = time.time()
    got = transcribe.transcribe(x, sr)
    if transcribe.BACKEND != "deepgram":
        return False, "fell back to %s: %s" % (transcribe.BACKEND, transcribe.LAST_ERROR)
    return bool(got), "nova-3 in %.1fs: \"%s\"" % (time.time() - t0, got[:46])
check("  ...round trip", _stt_live,
      "check the key and that this machine has internet")

def _narrator():
    import narrate
    a = narrate.available()
    return "template" not in a and a != "off (template lines)", a
check("narrator", _narrator,
      "ANTHROPIC_API_KEY or OPENAI_API_KEY in ~/.mixmind.env -- without one, "
      "every guest hears one of the same 8 phrases", warn_only=True)

def _narrator_live():
    import narrate, local_bartender
    if "template" in narrate.available():
        return True, "skipped (no key)"
    f = {"loudness_db": -19.0, "onset_rate_hz": 1.9, "pitch_sd_hz": 25.0,
         "pause_ratio": 0.03, "duration_s": 7.0, "pitch_mean_hz": 160.0}
    r = local_bartender.recipe(f, {"valence": -0.2})
    t0 = time.time()
    line = narrate.line(r, f, {}, {"1": "orange"})
    if line == r["rationale"]:
        return False, ("fell back to the template (%s) -- every guest would hear "
                       "one of the same 8 phrases" % (narrate.LAST_ERROR or "timed out"))
    return True, "%.1fs: \"%s\"" % (time.time() - t0, line[:46])
check("  ...round trip", _narrator_live,
      "raise MIXMIND_LLM_TIMEOUT if the network is slow", warn_only=True)

# ---------------------------------------------------------------- the mouth
def _tts():
    import speak
    a = speak.available()
    return not a.startswith("OFF"), a
check("text-to-speech", _tts,
      "DEEPGRAM_API_KEY in ~/.mixmind.env, or: sudo apt install espeak-ng")

def _tts_budget():
    """ElevenLabs bills per character. Say how many guests that is."""
    import narrate, speak
    if not os.environ.get(speak.EL_KEY_ENV, "").strip():
        return True, "skipped (not using ElevenLabs)"
    chars = narrate.MAX_WORDS * 5.5              # ~5.5 chars a word with spaces
    return True, ("~%d characters a guest; 10,000 free credits is roughly "
                  "%d-%d drinks" % (chars, 10000 / (chars * 1.0), 10000 / (chars * 0.5)))
check("  ...credit budget", _tts_budget,
      "MIXMIND_TTS=deepgram while testing, MIXMIND_LINE_WORDS to shorten",
      warn_only=True)


def _tts_live():
    import speak
    if QUIET:
        return True, "skipped (--quiet)"
    t0 = time.time()
    got = speak.speak("MixMind preflight. If you can hear this, the speaker works.")
    if not got:
        return False, "nothing came out"
    return True, "%s in %.1fs -- DID YOU HEAR IT?" % (got, time.time() - t0)
check("  ...out the speaker", _tts_live,
      "MIXMIND_SPK=<name or index>; run `python speak.py --list` to see them")

def _el_shape():
    import speak
    if not os.environ.get(speak.EL_KEY_ENV, "").strip():
        return True, "skipped (no ELEVENLABS_API_KEY)"
    w = speak._shape({"energy": .9, "animated": .85, "halting": .05})
    f = speak._shape({"energy": .15, "animated": .1, "halting": .7})
    if abs(w["stability"] - f["stability"]) < 0.2:
        return False, "wired and flat guests get the same delivery"
    return True, "wired stability %.2f / style %.2f  vs  flat %.2f / %.2f" % (
        w["stability"], w["style"], f["stability"], f["style"])
check("  ...inflection", _el_shape, "the axes are not reaching speak.py")


def _espeak():
    import shutil, speak
    if speak.MAC or shutil.which("espeak-ng"):
        return True, "installed -- the machine still talks if the wifi dies"
    return False, "missing: no offline voice at all"
check("offline voice", _espeak, "sudo apt install espeak-ng", warn_only=True)

# ---------------------------------------------------------------- the ears
def _mic():
    """Open the mic the way record() will. A device that merely RESOLVES is
    not a device that records: most USB mics refuse 16 kHz."""
    import listen
    b = listen.backend()
    return not b.startswith("BROKEN"), "MIXMIND_MIC=%r -> %s" % (listen.MIC, b)
check("microphone", _mic, "python listen.py  # lists what this machine sees")

# ---------------------------------------------------------------- the pumps
def _board():
    import unoq_http
    b = unoq_http.HttpUnoQ()
    b.all_off()
    return True, b.version
board_ok = check("UNO Q", _board,
                 "MIXMIND_UNOQ=http://<ip>:8081 -- the address moves on DHCP renewal")

def _cal():
    import unoq_http
    b = unoq_http.HttpUnoQ()
    if not b.calibrated:
        return False, "all six pumps assumed %.1f ml/s -- every dose is a guess" % unoq_http.ML_PER_SEC
    return True, "ml/s per pump: %s" % ", ".join("%.1f" % r for r in b.rates)
check("pump calibration", _cal, "python calibrate.py   # 10 min with a kitchen scale")

# ---------------------------------------------------------------- the drink
def _scale():
    import unoq_http
    b = unoq_http.HttpUnoQ()
    if not b.weighing:
        return False, "no load cell -- pours are timed and unverified"
    dg = b.weigh()
    return True, "reading %.1f g right now" % (dg / 10.0)
check("load cell", _scale,
      "python loadcell.py   # or ignore: timed pours still work", warn_only=True)


def _cup():
    import local_bartender as lb, unoq_http
    ok = lb.TARGET_MAX_ML <= unoq_http.MAX_TOTAL_ML <= lb.LIQUID_ROOM_ML
    return ok, ("%d oz cup, %d ml of room over brim-full ice, pours capped at %d ml"
                % (round(lb.CUP_ML / 29.574), lb.LIQUID_ROOM_ML, lb.TARGET_MAX_ML))
check("cup ceiling", _cup, "the two layers disagree -- see tests/test_cup.py")

def _ranges():
    import local_bartender as lb
    src = open("local_bartender.py").read()
    if "iPhone" in src:
        return False, "RANGES still calibrated on iPhone takes, not the USB mic"
    return True, "recalibrated"
check("mic calibration", _ranges,
      "record_samples.py, then: python local_bartender.py tests/samples/", warn_only=True)

def _logs():
    os.makedirs("logs", exist_ok=True)
    p = os.path.join("logs", ".preflight")
    open(p, "w").write("ok"); os.remove(p)
    return True, "logs/ is writable"
check("logging", _logs, "every drink is logged as JSON -- the demo evidence")

print("-" * 64)
if FAILS:
    print("%d FAIL, %d warn -- fix the FAILs before serving anyone." % (FAILS, WARNS))
elif WARNS:
    print("0 FAIL, %d warn -- it will serve, just not at its best." % WARNS)
else:
    print("Everything green. Go.")
sys.exit(min(FAILS, 125))
