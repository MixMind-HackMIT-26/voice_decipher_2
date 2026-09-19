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
