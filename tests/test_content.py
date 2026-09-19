"""What they said: transcription, the 'I'm fine' claim, and how words shape
the drink. Run: python tests/test_content.py
"""
import os, random, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import content, features, local_bartender as lb, transcribe

# 1. the okay-claim: the phrase that makes the demo, and the ones that must not
for text, want in [
        ("I'm fine, really. It's nothing, honestly, it's not a big deal.", True),
        ("Everything's okay, don't worry about me.", True),
        ("Honestly I'm doing great today!", True),
        ("I'm not fine.", False),
        ("It was not a good day at all.", False),
        ("Yeah, it's been a long day. I got in around seven.", False),
        ("", False)]:
    assert content.analyze(text)["says_okay"] == want, text

# 2. with any words at all, every recipe is still pourable
def pourable(r):
    ch = [p["channel"] for p in r["pours"]]
    assert 2 <= len(ch) <= 4 and len(set(ch)) == len(ch) and all(1 <= c <= 6 for c in ch), r
    assert all(10 <= p["ml"] <= 80 and p["ml"] % 5 == 0 for p in r["pours"]), r
    assert 120 <= sum(p["ml"] for p in r["pours"]) <= 220, r
    assert not any(c.isdigit() for c in r["rationale"]), "said a number to a guest: " + r["rationale"]
random.seed(2)
for _ in range(2000):
    f = dict(loudness_db=random.uniform(-60, 0), onset_rate_hz=random.uniform(0, 8),
             pause_ratio=random.uniform(0, 1), pitch_sd_hz=random.uniform(0, 80),
             pitch_mean_hz=150, jitter_pct=2, shimmer_pct=12, duration_s=10)
    w = dict(says_okay=random.random() < .5, valence=random.uniform(-1, 1))
    pourable(lb.recipe(f, w))
    assert lb.recipe(f, w) == lb.recipe(f, w), "not deterministic"

# 3. the voice decides the mood; words only change what is said about it
f = features.extract(os.path.join(HERE, "tests", "samples", "flat-1.wav"))
quiet_words, fine_words = lb.recipe(f), lb.recipe(f, {"says_okay": True, "valence": 0})
assert quiet_words["mood"] == fine_words["mood"], "words overrode the voice"
assert fine_words["rationale"].startswith("You said you're fine, but"), fine_words["rationale"]

# 4. the real thing, if the model is installed: transcribe and read our own
#    recordings, whose scripts we know
if transcribe.available():
    got = {}
    for n in ("flat-1", "happy-1"):
        x, sr = features._read_wav(os.path.join(HERE, "tests", "samples", n + ".wav"))
        text = transcribe.transcribe(x, sr)
        assert "fine" in text.lower() and "big deal" in text.lower(), (n, text)
        f = features.extract(os.path.join(HERE, "tests", "samples", n + ".wav"))
        got[n] = lb.recipe(f, content.analyze(text, f["duration_s"]))
    # same words, said two ways: the demo
    assert got["flat-1"]["rationale"].startswith("You said you're fine, but"), got["flat-1"]
    assert got["happy-1"]["rationale"].startswith("You said you're fine and you sounded it"), got["happy-1"]
    stt = "speech-to-text on real clips"
else:
    stt = "speech-to-text SKIPPED (%s)" % transcribe.LAST_ERROR

print("content: okay-claim, pourable with words, voice decides mood, %s -- all pass" % stt)
