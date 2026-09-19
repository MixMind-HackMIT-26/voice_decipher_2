"""features.py -- vocal feature extraction, numpy only.

Handbook §08. Deliberately NOT librosa: numba/llvmlite will not build on a Pi.
"""
import numpy as np, wave

FRAME_MS, HOP_MS = 32, 10
VOICED_DB    = -35.0      # frames quieter than this are 'silence'
F_MIN, F_MAX = 70, 400    # human pitch search range, Hz
CONF_MIN     = 0.30       # autocorrelation peak threshold

DEFAULTS = dict(pitch_mean_hz=0, pitch_sd_hz=0, loudness_db=-90,
                pause_ratio=1.0, onset_rate_hz=0, duration_s=0)


def _read_wav(path):
    with wave.open(path, "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        raw = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64)
        if w.getnchannels() == 2:
            raw = raw.reshape(-1, 2).mean(axis=1)
    return raw / 32768.0, sr


def extract(path):
    x, sr = _read_wav(path)
    if x.size == 0:
        return dict(DEFAULTS)
    fl, hl = int(sr * FRAME_MS / 1000), int(sr * HOP_MS / 1000)
    win = np.hanning(fl)
    frames = [x[i:i + fl] * win for i in range(0, max(1, len(x) - fl), hl)]

    rms = np.array([np.sqrt(np.mean(f ** 2)) + 1e-12 for f in frames])
    db = 20 * np.log10(rms)
    voiced = db > VOICED_DB

    pitches = []
    lo, hi = int(sr / F_MAX), int(sr / F_MIN)
    for f, v in zip(frames, voiced):
        if not v: continue
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        if ac[0] <= 0: continue
        ac = ac / ac[0]
        seg = ac[lo:hi]
        if seg.size == 0: continue
        k = int(np.argmax(seg))
        if seg[k] >= CONF_MIN:
            pitches.append(sr / (lo + k))

    p = np.array(pitches) if pitches else np.array([0.0])
    d = np.diff(db, prepend=db[0])          # onsets: rising energy edges
    onsets = int(np.sum((d > 6) & voiced))
    dur = len(x) / sr

    return dict(
        pitch_mean_hz = round(float(p.mean()), 1),
        pitch_sd_hz   = round(float(p.std()), 1),
        loudness_db   = round(float(db[voiced].mean()) if voiced.any() else -90, 1),
        pause_ratio   = round(float(1 - voiced.mean()), 3),
        onset_rate_hz = round(onsets / dur, 2) if dur > 0 else 0.0,
        duration_s    = round(dur, 2),
    )
