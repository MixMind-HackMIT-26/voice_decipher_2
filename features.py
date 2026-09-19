"""features.py -- vocal feature extraction, numpy only.

Handbook §08. Deliberately NOT librosa: numba/llvmlite will not build on a Pi.
"""
import numpy as np, wave

FRAME_MS, HOP_MS = 32, 10
# A frame counts as speech if it is within VOICED_REL_DB of the clip's own
# loudest frame. It must be RELATIVE: recording level varies by ~10 dB between
# phones, rooms and speakers, and a fixed dBFS line mislabels every quiet clip
# as silence and every noisy one as wall-to-wall speech.
VOICED_REL_DB = 30.0      # speech covers roughly this dynamic range
VOICED_FLOOR  = -60.0     # ...but an (almost) silent clip stays silent
F_MIN, F_MAX  = 70, 400   # human pitch search range, Hz
YIN_THRESH    = 0.12      # YIN absolute threshold; lower = pickier
YIN_MAX_CMND  = 0.60      # above this the frame is too uncertain to call

DEFAULTS = dict(pitch_mean_hz=0, pitch_sd_hz=0, loudness_db=-90,
                pause_ratio=1.0, onset_rate_hz=0, duration_s=0)


def _read_wav(path):
    with wave.open(path, "rb") as w:
        # int16 is assumed three lines down. A 24-bit or float wav parses here
        # without complaint and yields silent nonsense, so refuse it loudly.
        if w.getsampwidth() != 2:
            raise ValueError(
                "%s is %d-bit; need 16-bit PCM wav. Convert it:\n"
                "  afconvert -f WAVE -d LEI16@16000 -c 1 '%s' out.wav"
                % (path, w.getsampwidth() * 8, path))
        sr, n = w.getframerate(), w.getnframes()
        raw = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64)
        if w.getnchannels() == 2:
            raw = raw.reshape(-1, 2).mean(axis=1)
    return raw / 32768.0, sr


def _yin(x, sr, fmin=F_MIN, fmax=F_MAX):
    """Fundamental frequency of one frame, YIN (de Cheveigne & Kawahara 2002).

    Plain autocorrelation picks the wrong peak an octave out whenever the
    signal is noisy or the amplitude moves -- on our own clips that inflated
    pitch_sd to 60-100 Hz and pinned the 'animated' axis at maximum. YIN's
    cumulative-mean normalisation removes the zero-lag bias that causes it.
    """
    x = x - x.mean()
    n = x.size
    tau_min, tau_max = int(sr / fmax), int(sr / fmin)
    if n < tau_max + 2:
        return 0.0
    # difference function d(tau) from the autocorrelation identity, vectorised
    ac = np.correlate(x, x, mode="full")[n - 1:]
    cum = np.concatenate(([0.0], np.cumsum(x * x)))
    total = cum[n]
    taus = np.arange(tau_max + 1)
    head = total - (cum[n] - cum[n - taus])      # power of x[:n-tau]
    tail = total - cum[taus]                     # power of x[tau:]
    d = head + tail - 2 * ac[:tau_max + 1]
    # cumulative mean normalised difference
    run = np.cumsum(d[1:])
    cmnd = np.ones(tau_max + 1)
    nz = run > 0
    cmnd[1:][nz] = d[1:][nz] * taus[1:][nz] / run[nz]

    seg = cmnd[tau_min:tau_max + 1]
    if seg.size == 0:
        return 0.0
    below = np.flatnonzero(seg < YIN_THRESH)
    tau = tau_min + int(below[0] if below.size else np.argmin(seg))
    if cmnd[tau] > YIN_MAX_CMND:
        return 0.0                                # unvoiced or unsure
    if 1 <= tau < tau_max:                        # parabolic refinement
        a, b, c = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
        den = a - 2 * b + c
        if den != 0:
            tau = tau + 0.5 * (a - c) / den
    return sr / tau if tau > 0 else 0.0


def extract(path):
    x, sr = _read_wav(path)
    if x.size == 0:
        return dict(DEFAULTS)
    fl, hl = int(sr * FRAME_MS / 1000), int(sr * HOP_MS / 1000)
    win = np.hanning(fl)
    # Raw frames, not windowed. Energy uses the window; YIN must NOT -- a Hann
    # taper distorts the difference function and puts the octave errors back.
    frames = [x[i:i + fl] for i in range(0, max(1, len(x) - fl), hl)]

    rms = np.array([np.sqrt(np.mean((f * win) ** 2)) + 1e-12 for f in frames])
    db = 20 * np.log10(rms)
    voiced = db > max(db.max() - VOICED_REL_DB, VOICED_FLOOR)

    pitches = []
    for f, v in zip(frames, voiced):
        if not v: continue
        f0 = _yin(f, sr)
        if f0 > 0:
            pitches.append(f0)

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
