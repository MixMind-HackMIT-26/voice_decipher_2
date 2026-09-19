"""Test the voice pipeline on a laptop. No Pi, no pumps, no API key.

    python eval_voice.py              every clip in tests/samples: numbers,
                                      drinks, and whether each pair separates
    python eval_voice.py --live       talk into the mic, see the numbers and
                                      the drink, take after take
    python eval_voice.py some/folder  a different folder of wavs

The pair check is the one that matters: same words said two ways must read
differently, in the right direction, or the demo does not work.
"""
import argparse, glob, os, sys, time

import features, local_bartender

# (calm take, energetic take) -- names from record_samples.py
PAIRS = [("tired-1", "wired-1"), ("tired-2", "wired-2"), ("flat-1", "happy-1"),
         ("careful-1", "rushed-1"), ("noisy-2", "noisy-1")]
COLS = [("pitch_mean_hz", "pitch", "%6.1f"), ("pitch_sd_hz", "wobble", "%6.1f"),
        ("loudness_db", "loud", "%6.1f"), ("pause_ratio", "pause", "%6.3f"),
        ("onset_rate_hz", "pace", "%5.2f"), ("jitter_pct", "jitter", "%6.2f"),
        ("shimmer_pct", "shimmr", "%6.2f"), ("duration_s", "talk s", "%6.1f")]


def row(name, f, ms, r):
    cells = " ".join(fmt % f[k] for k, _, fmt in COLS)
    warn = []
    if f["duration_s"] < 4.0: warn.append("SHORT")
    if f["pause_ratio"] < 0.03 and features.LAST_BACKEND == "energy":
        warn.append("NOISE?")          # only the energy VAD mistakes noise for talk
    return "%-11s %s %4.0fms  %-18s %-8s %s" % (
        name, cells, ms, r["name"], r["mood"], " ".join(warn))


def header():
    return "%-11s %s %6s  %-18s %-8s" % (
        "clip", " ".join("%6s" % h for _, h, _ in COLS), "time", "drink", "mood")


def measure(path):
    t = time.time()
    f = features.extract(path)
    return f, (time.time() - t) * 1000, local_bartender.recipe(f)


def folder(target):
    wavs = sorted(glob.glob(os.path.join(target, "*.wav")))
    if not wavs:
        print("no wavs in %s -- run record_samples.py first" % target); return 1
    res = {}
    print(header())
    for w in wavs:
        n = os.path.basename(w)[:-4]
        res[n] = measure(w)
        print(row(n, *res[n]))
    print("\nVAD: %s   (silero = neural; energy = fallback, weak in noise)"
          % features.LAST_BACKEND)

    print("\nDO THE PAIRS SEPARATE?  calm take should pause more, talk slower, be quieter")
    score = total = 0
    for calm, act in PAIRS:
        if calm not in res or act not in res:
            continue
        a, b = res[calm][0], res[act][0]
        checks = [("pauses more", a["pause_ratio"] > b["pause_ratio"]),
                  ("slower",      a["onset_rate_hz"] < b["onset_rate_hz"]),
                  ("quieter",     a["loudness_db"] < b["loudness_db"])]
        ok = sum(c for _, c in checks); score += ok; total += 3
        same = res[calm][2]["name"] == res[act][2]["name"]
        print("  %-10s vs %-10s %d/3  %s%s" % (calm, act, ok,
              "  ".join(("+" if c else "-") + n for n, c in checks),
              "   !! SAME DRINK" if same else ""))
    if total:
        print("\n  %d/%d direction checks pass" % (score, total))

    import content, transcribe
    if not transcribe.available():
        print("\nWORDS: speech-to-text off (%s)" % transcribe.LAST_ERROR)
        return 0
    print("\nWHAT THEY SAID  (voice decides the mood; words change what it says)")
    for w in wavs:
        n = os.path.basename(w)[:-4]
        x, sr = features._read_wav(w)
        f = res[n][0]
        words = content.analyze(transcribe.transcribe(x, sr), f["duration_s"])
        r = local_bartender.recipe(f, words)
        print("  %-10s %3.0f wpm  words %+.2f%s" % (n, words["words_per_min"], words["valence"],
              "  says fine" if words["says_okay"] else ""))
        print("             \"%s\"" % r["rationale"])
    return 0


def live():
    import listen, tempfile
    print("Say something, pause, and it stops by itself. Ctrl-C to quit.")
    print("Try the same sentence tired, then wired -- the numbers should move.\n")
    prev = None
    tmp = os.path.join(tempfile.gettempdir(), "mixmind_live.wav")
    while True:
        try:
            input("[enter] then speak... ")
        except (KeyboardInterrupt, EOFError):
            print(); return 0
        listen.record(tmp)
        f, ms, r = measure(tmp)
        print(header()); print(row("you", f, ms, r))
        if prev:
            moved = ["%s %+.2f" % (h, f[k] - prev[k]) for k, h, _ in COLS
                     if abs(f[k] - prev[k]) > 0.15 * max(abs(prev[k]), 1e-6)]
            print("  vs last take: %s" % (", ".join(moved) or "nothing moved much"))
        print("  says: %s\n" % r["rationale"])
        prev = f


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", default="tests/samples")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    sys.exit(live() if a.live else folder(a.target))
