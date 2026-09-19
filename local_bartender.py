"""Recipes with no network, no API key, no model. Pure arithmetic.

The six numbers from features.py land on three axes, the axes weight the six
ingredients, and the doses fall out continuously -- so two different voices get
two different drinks, not one of four buckets.

Deterministic: the same voice always gets the same drink (Gate 3 wants that).
Every output is built to pass Machine.validate(); test_local_bartender.py
fuzzes the whole feature space to keep it that way.
"""
import hashlib

# Feature ranges the axes normalise against. These are CALIBRATION, not
# constants: a quiet mic, a loud room or a different phone shifts them, and a
# range that does not match the hardware silently pins an axis at 0 or 1 and
# throws that feature away. Re-measure in Boston on the real mic:
#     python local_bartender.py tests/samples/
RANGES = {
    "loudness_db":   (-32.0, -12.0),   # handbook §08 defaults
    "onset_rate_hz": (1.5, 6.0),
    "pitch_sd_hz":   (10.0, 55.0),
    "pause_ratio":   (0.15, 0.65),
}

MIN_DOSE, MAX_DOSE = 10, 80
ACCENT_MAX = 25          # ch3 is an accent; a big pour of it is undrinkable
STEP = 5                 # doses in multiples of 5


def _n(v, lo, hi):
    """Squash a feature onto 0..1 against its typical range (handbook §08)."""
    return min(1.0, max(0.0, (float(v) - lo) / (hi - lo)))


def axes(f):
    """Six numbers -> three things you can actually pour against."""
    energy   = (0.5 * _n(f["loudness_db"],   *RANGES["loudness_db"])
              + 0.5 * _n(f["onset_rate_hz"], *RANGES["onset_rate_hz"]))
    halting  = _n(f["pause_ratio"],  *RANGES["pause_ratio"])
    animated = _n(f["pitch_sd_hz"],  *RANGES["pitch_sd_hz"])
    return energy, halting, animated


def _weights(energy, halting, animated):
    # 1 citrus base · 2 tart red · 3 sour accent · 4 sparkling · 5 dark · 6 warm
    return {
        1: 0.30 + 0.40 * halting + 0.30 * animated,
        2: 0.15 + 0.60 * energy,
        3: 0.10 + 0.45 * energy + 0.25 * animated,
        4: 0.25 + 0.55 * energy + 0.25 * halting,
        5: 0.15 + 0.55 * (1 - energy),
        6: 0.20 + 0.50 * (1 - energy) + 0.25 * (1 - halting),
    }


def _pick(w):
    """Top three, but the drink must be long -- keep ch4 or ch6 in every glass."""
    chosen = sorted(w, key=lambda c: -w[c])[:3]
    if not (4 in chosen or 6 in chosen):
        chosen[-1] = 4 if w[4] >= w[6] else 6
    return sorted(chosen)


def _doses(chosen, w, energy):
    target = 150 + 40 * energy                 # 150-190 ml
    tot = sum(w[c] for c in chosen)
    out = {}
    for c in chosen:
        ml = target * w[c] / tot
        cap = ACCENT_MAX if c == 3 else MAX_DOSE
        ml = min(cap, max(MIN_DOSE, ml))
        out[c] = int(round(ml / STEP) * STEP)
    # rounding and clamping move the total; push the drift into the biggest
    # non-accent pour, which is the one that can absorb it.
    drift = int(round(target)) - sum(out.values())
    if drift:
        big = max((c for c in chosen if c != 3), key=lambda c: out[c])
        out[big] = min(MAX_DOSE, max(MIN_DOSE,
                       int(round((out[big] + drift) / STEP) * STEP)))
    return [{"channel": c, "ml": out[c]} for c in sorted(out)]


NAMES = {
    "wired":     ["The Overclock", "Red Eye Express", "Second Wind", "Live Wire", "Full Tilt"],
    "bright":    ["Bright Monday", "The Upswing", "Good News", "Open Window", "Clear Signal"],
    "steady":    ["House Special", "Even Keel", "The Usual", "Middle Distance", "Level Set"],
    "careful":   ["The Long Way", "Measured Pour", "Second Thought", "Slow Draw", "Careful Now"],
    "depleted":  ["The Long Thursday", "Closing Time", "Last Train", "Running On Empty", "Deep Breath"],
    "calm":      ["Slow Sunday", "Low Tide", "Quiet Hours", "The Unhurried", "Still Water"],
}


def _mood(energy, halting, animated):
    if energy > 0.62:                       return "wired"
    if halting > 0.55 and energy < 0.45:    return "depleted"
    if energy < 0.32:                       return "calm"
    if animated > 0.60:                     return "bright"
    if halting > 0.45:                      return "careful"
    return "steady"


# (phrase about how they sounded, phrase about the drink that answers it)
_SAYS = {
    "loud":     "you came in loud",
    "quiet":    "you spoke softly",
    "fast":     "you talked quickly",
    "slow":     "you took your time",
    "halting":  "you left a lot of space between your words",
    "fluent":   "the words came out in one piece",
    "lively":   "your voice moved around a lot",
    "flat":     "your voice stayed level the whole way through",
}
_POUR = {
    "wired":    "so this is tart and long -- something to slow you down",
    "bright":   "so this is bright and sharp, to keep that going",
    "steady":   "so this is balanced -- a little bright, a little warm",
    "careful":  "so this is easy to sip while you think",
    "depleted": "so this is mostly citrus and soda -- easy to sip, not to swallow",
    "calm":     "so here is something dark and warm to stay that way",
}


def _rationale(f, energy, halting, animated, mood):
    """Name the two loudest signals, then the drink that answers them."""
    signals = [
        (abs(_n(f["loudness_db"], *RANGES["loudness_db"]) - 0.5),
         "loud" if _n(f["loudness_db"], *RANGES["loudness_db"]) > 0.5 else "quiet"),
        (abs(_n(f["onset_rate_hz"], *RANGES["onset_rate_hz"]) - 0.5),
         "fast" if _n(f["onset_rate_hz"], *RANGES["onset_rate_hz"]) > 0.5 else "slow"),
        (abs(halting - 0.5),                          "halting" if halting > 0.5 else "fluent"),
        (abs(animated - 0.5),                         "lively" if animated > 0.5 else "flat"),
    ]
    signals.sort(key=lambda s: -s[0])
    a, b = _SAYS[signals[0][1]], _SAYS[signals[1][1]]
    return "%s and %s, %s." % (a[0].upper() + a[1:], b, _POUR[mood])


def recipe(f):
    """Six numbers in, a valid recipe out. No network, no model, ~0 ms."""
    energy, halting, animated = axes(f)
    w = _weights(energy, halting, animated)
    chosen = _pick(w)
    pours = _doses(chosen, w, energy)
    mood = _mood(energy, halting, animated)

    # deterministic name: the same voice gets the same drink twice running
    seed = "%.2f|%.2f|%.2f" % (energy, halting, animated)
    idx = int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(NAMES[mood])

    return {
        "name": NAMES[mood][idx],
        "rationale": _rationale(f, energy, halting, animated, mood),
        "pours": pours,
        "stir_seconds": 6,
        "mood": mood,
        "confidence": round(0.45 + 0.25 * max(abs(energy - .5), abs(halting - .5)) * 2, 2),
    }


def suggest_ranges(folder):
    """Print RANGES calibrated to real recordings. Twenty seconds, once per mic.

    Uses the 10th/90th percentile so one shouted clip cannot stretch the scale.
    """
    import glob, os, features, numpy as np
    wavs = sorted(glob.glob(os.path.join(folder, "*.wav")))
    if len(wavs) < 6:
        print("need at least 6 clips with a real spread; found %d" % len(wavs))
        return
    F = [features.extract(w) for w in wavs]
    print("# calibrated on %d clips in %s" % (len(F), folder))
    print("RANGES = {")
    for k in ("loudness_db", "onset_rate_hz", "pitch_sd_hz", "pause_ratio"):
        v = np.array([f[k] for f in F], dtype=float)
        lo, hi = np.percentile(v, 10), np.percentile(v, 90)
        if hi - lo < 1e-6:
            hi = lo + 1.0
        cur = RANGES[k]
        flag = "" if abs(lo - cur[0]) < abs(cur[1] - cur[0]) * .35 else "   # shifted a lot"
        print('    "%s": (%.1f, %.1f),%s' % (k, lo, hi, flag))
    print("}")
    print("\n# paste that over RANGES in local_bartender.py, then re-run the pairs")


if __name__ == "__main__":
    import sys
    suggest_ranges(sys.argv[1] if len(sys.argv) > 1 else "tests/samples")
