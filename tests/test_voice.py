"""The mouth and the narrator must never take a guest down with them.

Both are optional extras on a machine whose job is to pour a drink. With no
API key, no network, no speaker and no espeak-ng, every call here still has
to return quietly and let the pour happen.
"""
import os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(k, None)
import local_bartender, narrate, speak

F = {"loudness_db": -19.0, "onset_rate_hz": 1.9, "pitch_sd_hz": 25.0,
     "pause_ratio": 0.03, "duration_s": 7.0, "pitch_mean_hz": 160.0}
W = {"valence": -0.2, "says_okay": True, "transcript": "yeah I'm fine, long week"}
ING = {"1": "orange", "2": "cranberry", "3": "lime", "4": "ginger ale",
       "5": "grape", "6": "apple"}
R = local_bartender.recipe(F, W)

# 1. no key -> the template line, unchanged, no exception
assert narrate.line(R, F, W, ING) == R["rationale"]
assert narrate.available().endswith("template lines")

# 2. a key that cannot work -> still the template, still no exception
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-not-a-real-key"
narrate.TIMEOUT_S = 1.0
assert narrate.line(R, F, W, ING) == R["rationale"]
del os.environ["ANTHROPIC_API_KEY"]

# 3. switched off on purpose
os.environ["MIXMIND_LLM"] = "off"
assert narrate.line(R, F, W, ING) == R["rationale"]
del os.environ["MIXMIND_LLM"]

# 4. anything that is not a spoken line is refused before a guest hears it
for junk in ("", "   ", None, "- one\n- two", "{\"line\": \"hi\"}",
             "see http://example.com", "word " * 60):
    assert narrate._clean(junk) == "", repr(junk)
for good in ('"You came in loud, so this one is long and cold."',
             "Line: You sounded tired. This is mostly citrus."):
    assert narrate._clean(good) and "\n" not in narrate._clean(good)
assert not narrate._clean('"Quiet one today."').startswith('"')

# 5. the mouth: no speaker, no espeak -- returns, does not raise
assert speak.speak("") == ""
out = speak.speak("MixMind test line, one two three.")
assert out in ("", "espeak", "openai", "openai(cached)"), out
t = speak.say("threaded line")
t.join(timeout=20)
assert not t.is_alive(), "speak.say() hung -- it would hold a guest at the machine"

print("voice: narrator falls back on no key / bad key / off, junk replies refused, "
      "speak never raises and never hangs -- all pass")


# ---- Deepgram: sponsor tech, but never a single point of failure ----
import transcribe
import numpy as np

x = (np.sin(np.arange(16000 * 2) * 0.05) * 0.2).astype(np.float32)   # 2 s of tone

# 6. no key -> speak falls past Deepgram, transcribe falls past it too
for k in ("DEEPGRAM_API_KEY",):
    os.environ.pop(k, None)
assert speak._deepgram_wav("hello") is None
assert transcribe._deepgram(x, 16000) is None
assert "deepgram" not in transcribe._order()

# 7. a key that cannot work -> still no exception out of either
os.environ["DEEPGRAM_API_KEY"] = "not-a-real-deepgram-key"
transcribe.DG_TIMEOUT_S = 5.0
speak.TIMEOUT_S = 5.0
assert transcribe._order()[0] == "deepgram"
assert transcribe.transcribe(x, 16000) == ""        # falls through to whisper, then ""
assert speak.speak("MixMind deepgram fallback test") in ("", "espeak"), speak.LAST
assert "deepgram" in speak.available()

# 8. the wav we upload is a real 16-bit mono wav, not raw floats
import io, wave
w = wave.open(io.BytesIO(transcribe._wav_bytes(x, 16000)))
assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
assert w.getnframes() == len(x)

# 9. switching voices must not replay the old one out of the cache
assert speak._cached("hi", "aura-2-arcas-en") != speak._cached("hi", "gpt-4o-mini-tts/ballad")

# 9b. ElevenLabs: the guest's voice really does change the delivery, and two
#     different deliveries of one sentence are two different cache entries
wired = {"energy": .9, "animated": .85, "halting": .05}
flat  = {"energy": .15, "animated": .1, "halting": .7}
sw, sf = speak._shape(wired), speak._shape(flat)
assert sw["stability"] < sf["stability"] - 0.2, (sw, sf)   # wired = more dynamic
assert sw["style"] > sf["style"] + 0.2, (sw, sf)
for v in list(sw.values()) + list(sf.values()):
    if isinstance(v, float):
        assert 0.0 <= v <= 1.0, v                          # the API rejects anything else
assert speak._shape(None) and speak._shape({"energy": 99})  # junk in, valid out
el = "el/x/eleven_flash_v2_5"
assert speak._cached("hi", el, wired) != speak._cached("hi", el, flat)
assert speak._cached("hi", "aura-2-arcas-en", wired) == speak._cached("hi", "aura-2-arcas-en", flat)

# 9c. no key -> skipped, bad key -> falls through, same as the others
os.environ.pop(speak.EL_KEY_ENV, None)
assert speak._elevenlabs_audio("hello") is None
os.environ[speak.EL_KEY_ENV] = "not-a-real-elevenlabs-key"
assert "elevenlabs" in speak.available()
assert speak.speak("MixMind elevenlabs fallback test", wired) in ("", "espeak"), speak.LAST
del os.environ[speak.EL_KEY_ENV]

# 9d. the PCM wrapper produces a wav aplay will actually accept
import wave as _w, io as _io
h = _w.open(_io.BytesIO(speak._wav_header(b"\x00\x01" * 480, 24000)))
assert (h.getnchannels(), h.getsampwidth(), h.getframerate()) == (1, 2, 24000)
assert h.getnframes() == 480

# 10. MIXMIND_STT pins the backend
transcribe.WANT = "whisper"
assert transcribe._order() == ["whisper"]
transcribe.WANT = "deepgram"
assert transcribe._order() == ["deepgram"]
transcribe.WANT = "auto"
del os.environ["DEEPGRAM_API_KEY"]

print("deepgram: absent key skipped, bad key falls through in both directions, "
      "upload is a valid wav, cache keys are per-voice, MIXMIND_STT pins -- all pass")
