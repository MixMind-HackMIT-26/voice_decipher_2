"""The cup cannot overflow.

18 oz party cups, packed with ice. The trap: ice FLOATS, so it does not
politely leave its gaps free for the juice -- every cube displaces liquid
upward. A cup brim-full of ice has ~151 ml of room, not the ~213 ml the
gaps alone suggest, and the old ceiling of 220 ml would have gone over the
side onto a table of hackathon laptops.

This walks the whole feature space and checks the worst drink the machine
can possibly produce, twice: once as designed, and once with every pump
running 15% long because the calibration drifted.
"""
import os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import local_bartender as lb, unoq_http

CUP, ICE = lb.CUP_ML, lb.CUP_ML * lb.ICE_FILL * lb.ICE_PACK * lb.ICE_SUBMERGED

# 0. the two layers agree, and the safety net is itself under the physical room
assert lb.TARGET_MAX_ML <= unoq_http.MAX_TOTAL_ML <= lb.LIQUID_ROOM_ML, (
    "board ceiling %d is outside %d..%.0f"
    % (unoq_http.MAX_TOTAL_ML, lb.TARGET_MAX_ML, lb.LIQUID_ROOM_ML))
assert unoq_http.MAX_ML <= lb.MAX_DOSE

# 1. every drink the machine can make, across the whole feature space
def grid(k, n=9):
    lo, hi = lb.RANGES[k]
    pad = (hi - lo) * 0.25                      # beyond the calibrated range too
    return [lo - pad + (hi - lo + 2 * pad) * i / (n - 1) for i in range(n)]

worst = smallest = None
n = 0
for l in grid("loudness_db"):
    for o in grid("onset_rate_hz"):
        for p in grid("pitch_sd_hz"):
            for pr in grid("pause_ratio"):
                f = {"loudness_db": l, "onset_rate_hz": o, "pitch_sd_hz": p,
                     "pause_ratio": pr, "duration_s": 5.0, "pitch_mean_hz": 150.0}
                for w in ({}, {"valence": 1.0}, {"valence": -1.0},
                          {"says_okay": True, "valence": -0.5}):
                    r = lb.recipe(f, w)
                    t = r["ml_total"]
                    n += 1
                    assert t == sum(x["ml"] for x in r["pours"])
                    assert t <= lb.TARGET_MAX_ML, "%d ml drink: %r" % (t, r)
                    unoq_http.validate(r)       # the board would accept it
                    worst = t if worst is None else max(worst, t)
                    smallest = t if smallest is None else min(smallest, t)

# 2. the worst drink, in the worst cup a guest can build
level = ICE + worst
assert level < CUP, "%.0f ml of liquid+ice in a %d ml cup" % (level, CUP)
assert CUP - level >= 80, "only %.0f ml of rim left" % (CUP - level)

# 3. the same, with the pumps running 15% long
drifted = ICE + worst * 1.15
assert drifted < CUP, "overflows once calibration drifts: %.0f ml" % drifted

print("cup: %d recipes, %d-%d ml. Brim-full of ice + the biggest drink sits at "
      "%.0f ml of a %d ml cup (%.0f ml of rim; %.0f ml even if every pump runs "
      "15%% long) -- all pass" % (n, smallest, worst, level, CUP, CUP - level, drifted))
