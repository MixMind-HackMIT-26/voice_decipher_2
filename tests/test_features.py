"""features.extract against voices whose answers we know.

Synthetic voices are built cycle by cycle with a chosen pitch and a chosen
amount of jitter/shimmer, so every number here is checked against the truth
rather than against itself. Run: python tests/test_features.py
"""
import os, sys, tempfile, wave
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import features, vad

SR = 16000
FL, HL = int(SR * .032), int(SR * .010)
HERE = os.path.dirname(os.path.abspath(__file__))


def synth(f0=110, jit=0.0, shim=0.0, secs=2.0, seed=0):
    """Harmonic pulse train with KNOWN per-cycle period and amplitude noise."""
    rng = np.random.default_rng(seed)
    out, P, A, t = [], [], [], 0.0
    while t < secs:
        p = (1 / f0) * (1 + rng.normal(0, jit)); a = 1 + rng.normal(0, shim)
        n = int(round(p * SR)); tt = np.arange(n) / n
        out.append(a * sum(np.sin(2 * np.pi * k * tt) / k ** 2 for k in range(1, 12)))
        P.append(n / SR); A.append(a); t += p
    P, A = np.array(P), np.array(A)
    return (np.concatenate(out) * 0.2,
            100 * np.mean(np.abs(np.diff(P))) / P.mean(),
            100 * np.mean(np.abs(np.diff(A))) / A.mean())


def perturb(x):
    fr = [x[i:i + FL] for i in range(0, len(x) - FL, HL)]
    f0 = np.array([features._yin(f, SR) for f in fr])
    return features._perturbation(x, SR, f0, FL, HL)


def write(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


# 1. YIN finds the pitch -- the old autocorrelation was off by an octave here
for f0 in (90, 110, 150, 220, 300):
    x, _, _ = synth(f0)
    got = np.median([features._yin(x[i:i + FL], SR) for i in range(0, len(x) - FL, HL)])
    assert abs(got - f0) / f0 < 0.02, "YIN read %d Hz as %.1f" % (f0, got)

# 2. shimmer is accurate; jitter ranks correctly (absolute value swings with
#    pulse shape, so it is for comparing voices, not quoting numbers)
cj, cs = perturb(synth()[0])
assert cj < 0.2 and cs < 0.2, "a perfect voice read as jitter %.2f shimmer %.2f" % (cj, cs)
for shim in (0.03, 0.08):
    x, _, true_s = synth(shim=shim)
    _, s = perturb(x)
    assert abs(s - true_s) / true_s < 0.15, "shimmer %.2f read as %.2f" % (true_s, s)
js = [perturb(synth(jit=j, seed=1)[0])[0] for j in (0.005, 0.01, 0.02, 0.04)]
assert js == sorted(js), "jitter does not rise with true jitter: %s" % js
_, s_leak = perturb(synth(jit=0.02)[0])
assert s_leak < 1.0, "jitter leaked into shimmer (%.2f)" % s_leak

# 3. waiting before you speak is not a pause. Lead in with the clip's own room
#    tone (what a real mic hears), not digital zeros, and the answer holds.
#    0.05 is the measured floor: Silero carries state chunk to chunk, so the
#    same speech after a different lead-in can shift pause_ratio by ~0.04.
#    Every contrast pair in tests/samples differs by more than that.
for name in ("tired-2", "wired-1"):
    x, sr = features._read_wav(os.path.join(HERE, "samples", name + ".wav"))
    room = x[:SR // 2] if vad.available() else np.zeros(SR // 2)
    if vad.available():
        pr = vad.speech_prob(x, sr)
        room = np.concatenate([x[i * 512:(i + 1) * 512] for i in np.flatnonzero(pr < 0.1)][:60])
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.wav"), os.path.join(d, "b.wav")
        write(a, x)
        write(b, np.concatenate([np.tile(room, 20)[:2 * SR], x, np.tile(room, 20)[:2 * SR]]))
        fa, fb = features.extract(a), features.extract(b)
    assert abs(fa["pause_ratio"] - fb["pause_ratio"]) < 0.05, (name, fa, fb)
    assert abs(fa["duration_s"] - fb["duration_s"]) < 0.06 * fa["duration_s"], (name, fa, fb)

# 4. a noisy room is not wall-to-wall speech (the energy VAD said 99% on noisy-1)
if vad.available():
    f = features.extract(os.path.join(HERE, "samples", "noisy-1.wav"))
    assert f["pause_ratio"] > 0.1, "noise still reads as speech: %r" % f
    noise = np.random.default_rng(0).normal(0, 0.1, 3 * SR).astype(np.float32)
    assert (vad.speech_prob(noise, SR) > 0.5).mean() < 0.2, "white noise called speech"

# 5. without onnxruntime it falls back to energy and still returns numbers
real = vad.speech_prob
vad.speech_prob = lambda x, sr: None
try:
    f = features.extract(os.path.join(HERE, "samples", "tired-2.wav"))
    assert features.LAST_BACKEND == "energy" and f["duration_s"] > 0
finally:
    vad.speech_prob = real

# 6. degenerate files never throw, and always carry every key
with tempfile.TemporaryDirectory() as d:
    for name, x in (("empty", np.zeros(0)), ("silent", np.zeros(SR)),
                    ("click", np.r_[np.zeros(800), 0.9, np.zeros(800)])):
        p = os.path.join(d, name + ".wav"); write(p, x)
        f = features.extract(p)
        assert set(f) == set(features.DEFAULTS), (name, f)

# 7. the mic stops when they stop talking -- even in a loud room. Replay a real
#    noisy clip, then keep feeding that room's own noise, as a hall would.
if vad.available():
    import listen
    x, sr = features._read_wav(os.path.join(HERE, "samples", "noisy-1.wav"))
    p = vad.speech_prob(x, sr)
    room = np.concatenate([x[i * 512:(i + 1) * 512] for i in np.flatnonzero(p < 0.2)])
    feed = (np.concatenate([x, np.tile(room, 40)[:30 * SR]]) * 32767).astype(np.int16)
    talk_end = (np.flatnonzero(p > 0.5)[-1] + 1) * 512 / SR
    stops = []
    for use in (True, False):
        ep, n = listen.Endpointer(use_vad=use), 0
        while not ep.feed(feed[n:n + 512])[0]:
            n += 512
        stops.append(ep.elapsed)
    assert stops[0] - talk_end < 2.0, "silero kept recording %.1fs past the end" % (stops[0] - talk_end)
    assert stops[1] >= 24.9, "energy endpointing now copes with noise? update this test"

# 8. the tap on the touchscreen is not the guest talking: a click, 1.5 s of
#    quiet, then speech must record the speech (it used to stop at 1.7 s)
if vad.available():
    import listen
    y, _ = features._read_wav(os.path.join(HERE, "samples", "tired-1.wav"))
    click = np.zeros(SR // 2); click[4000:4080] = 0.8
    with tempfile.TemporaryDirectory() as d:
        q = os.path.join(d, "tap.wav")
        write(q, np.concatenate([click, np.zeros(int(1.5 * SR)), y]))
        rec, _ = features._read_wav(listen.replay(q))
    assert len(rec) / SR > 10, "a tap ended the recording after %.1f s" % (len(rec) / SR)

print("features: YIN, jitter, shimmer, VAD, endpointing, tap-noise, silence trimming, fallback, edge cases -- all pass")
