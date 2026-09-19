"""features.py -- vocal feature extraction, numpy only.

Handbook §08. Deliberately NOT librosa: numba/llvmlite will not build on a Pi.
"""
import numpy as np, wave
import vad

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

DEFAULTS = dict(pitch_mean_hz=0, pitch_sd_hz=0, loudness_db=-90, pause_ratio=1.0,
                onset_rate_hz=0, jitter_pct=0, shimmer_pct=0, duration_s=0)
VAD_THRESH = 0.5          # Silero speech probability cut
LAST_BACKEND = None       # "silero" or "energy" -- which VAD the last call used


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


def _yin_many(F, sr, fmin=F_MIN, fmax=F_MAX):
    """Fundamental frequency of each row of F, YIN (de Cheveigne & Kawahara 2002).

    Plain autocorrelation picks the wrong peak an octave out whenever the
    signal is noisy or the amplitude moves -- on our own clips it read one
    speaker as anything from 108 to 202 Hz. YIN's cumulative-mean normalisation
    removes the zero-lag bias that causes it.

    All frames at once through one FFT: frame-by-frame np.correlate spent most
    of the feature budget computing lags YIN never looks at.
    """
    F = np.atleast_2d(F).astype(np.float64)
    m, n = F.shape
    tau_min, tau_max = int(sr / fmax), int(sr / fmin)
    out = np.zeros(m)
    if m == 0 or n < tau_max + 2:
        return out
    x = F - F.mean(axis=1, keepdims=True)
    X = np.fft.rfft(x, n=2 * n)                   # 2n: no circular wrap-around
    ac = np.fft.irfft(X * np.conj(X), n=2 * n)[:, :tau_max + 1]
    cum = np.concatenate([np.zeros((m, 1)), np.cumsum(x * x, axis=1)], axis=1)
    taus = np.arange(tau_max + 1)
    # d(tau) = sum over the overlap of (x_j - x_j+tau)^2, via the identity
    d = cum[:, n - taus] + (cum[:, n:n + 1] - cum[:, taus]) - 2 * ac
    run = np.cumsum(d[:, 1:], axis=1)
    cmnd = np.ones_like(d)
    with np.errstate(divide="ignore", invalid="ignore"):
        cmnd[:, 1:] = np.where(run > 0, d[:, 1:] * taus[1:] / run, 1.0)

    for r in range(m):
        c = cmnd[r]
        below = np.flatnonzero(c[tau_min:tau_max + 1] < YIN_THRESH)
        tau = tau_min + int(below[0] if below.size else np.argmin(c[tau_min:tau_max + 1]))
        # The threshold is crossed on the way DOWN into the dip; the period is
        # at the bottom. Skipping this walk read a 90 Hz voice as 86 Hz -- and
        # low voices, whose dips are widest, are exactly where it went wrong.
        while tau + 1 <= tau_max and c[tau + 1] < c[tau]:
            tau += 1
        if c[tau] > YIN_MAX_CMND:
            continue                              # unvoiced or unsure
        t = float(tau)
        if 1 <= tau < tau_max:                    # parabolic refinement, bounded
            a, b, cc = c[tau - 1], c[tau], c[tau + 1]
            den = a - 2 * b + cc
            if den != 0:
                t += min(0.5, max(-0.5, 0.5 * (a - cc) / den))
        out[r] = sr / t
    return out


def _yin(x, sr, fmin=F_MIN, fmax=F_MAX):
    """One frame. See _yin_many."""
    return float(_yin_many(np.asarray(x)[None, :], sr, fmin, fmax)[0])


def _speech_mask(x, sr, n_frames, fl, hl, fallback):
    """Per-frame speech/not-speech. Silero if available, energy otherwise."""
    probs = vad.speech_prob(x, sr)
    if probs is None or probs.size == 0:
        return fallback, "energy"
    # Silero's own post-processing defaults (get_speech_timestamps): speech
    # starts above 0.5 but only ends below 0.35, and a gap counts as silence
    # only if it lasts 100 ms. Without them, soft speech hovering near 0.5
    # flickered chunk by chunk, and two seconds of lead-in silence moved
    # tired-2's pause_ratio from 0.129 to 0.17 -- a third of its calibrated range.
    on = np.zeros(probs.size, dtype=bool)
    state = False
    for k, p in enumerate(probs):
        state = p > VAD_THRESH if not state else p >= VAD_THRESH - 0.15
        on[k] = state
    min_gap = int(np.ceil(0.100 * sr / vad.CHUNK))
    k = 0
    while k < on.size:
        if on[k]:
            k += 1; continue
        e = k
        while e < on.size and not on[e]:
            e += 1
        if 0 < k and e < on.size and e - k < min_gap:
            on[k:e] = True                      # too short to be a pause
        k = e
    # ...and a burst under 250 ms is not speech (their min_speech_duration).
    # A cough or a click before the first word otherwise starts the clock
    # early, and the wait until they really begin reads as one long pause.
    min_talk = int(np.ceil(0.250 * sr / vad.CHUNK))
    k = 0
    while k < on.size:
        if not on[k]:
            k += 1; continue
        e = k
        while e < on.size and on[e]:
            e += 1
        if e - k < min_talk:
            on[k:e] = False
        k = e
    centres = np.arange(n_frames) * hl + fl // 2
    idx = np.minimum(centres // vad.CHUNK, probs.size - 1)
    return on[idx], "silero"


def _perturbation(x, sr, f0, fl, hl):
    """Local jitter and shimmer, in percent, measured cycle to cycle.

    jitter  = mean |period_i - period_i+1|       / mean period
    shimmer = mean |amplitude_i - amplitude_i+1| / mean amplitude

    Both rise with vocal strain and fatigue, and neither is visible in pitch,
    loudness or pace. Same definitions as Praat's "local" measures, and like
    Praat each period is found by cross-correlating a cycle with the next one
    -- peak-picking instead read 0.5% true jitter as 1.6%, because where the
    peak sits inside a cycle moves when the cycle length does. Not Praat
    itself: it has no Raspberry Pi wheel. tests/test_features.py checks both
    numbers against synthetic voices with known perturbation.
    """
    from numpy.lib.stride_tricks import sliding_window_view
    per, amp = [], []                                  # per voiced run
    voiced = f0 > 0
    i = 0
    while i < voiced.size:
        if not voiced[i]:
            i += 1; continue
        j = i
        while j + 1 < voiced.size and voiced[j + 1]:
            j += 1
        seg = x[i * hl:min(len(x), j * hl + fl)]
        P, A = [], []
        p = 0
        while True:
            fi = min(j, i + p // hl)
            T = sr / f0[fi] if f0[fi] > 0 else (P[-1] * sr if P else sr / f0[i])
            w = int(T)
            lo, hi = int(0.8 * T), int(1.25 * T)
            if p + hi + w >= len(seg) or w < 8:
                break
            ref = seg[p:p + w]
            cand = sliding_window_view(seg[p + lo:p + hi + w], w)
            nr = np.linalg.norm(ref) * np.linalg.norm(cand, axis=1) + 1e-12
            c = cand @ ref / nr
            k = int(np.argmax(c))
            if c[k] < 0.5:                             # cycles do not resemble each
                break                                  # other: not periodic here
            off = 0.0
            if 0 < k < len(c) - 1:
                den = c[k - 1] - 2 * c[k] + c[k + 1]
                if den:
                    off = min(0.5, max(-0.5, 0.5 * (c[k - 1] - c[k + 1]) / den))
            L = lo + k + off
            P.append(L / sr)
            cyc = seg[p:p + int(round(L))]
            A.append(float(cyc.max() - cyc.min()))     # peak-to-peak, like Praat
            p += int(round(L))
        per.append(P); amp.append(A)
        i = j + 1

    # Praat's defaults for which neighbouring cycles are comparable at all:
    # period factor 1.3, amplitude factor 1.6. Without the amplitude factor, a
    # syllable fading in -- amplitude doubling cycle to cycle, which is real --
    # counted as shimmer and one clip read 73%.
    dP, sP, dA, sA = [], [], [], []
    for P, A in zip(per, amp):
        for n in range(len(P) - 1):
            if not 1 / 1.3 <= P[n + 1] / P[n] <= 1.3:
                continue
            dP.append(abs(P[n + 1] - P[n])); sP.append(P[n])
            if A[n] > 0 and 1 / 1.6 <= A[n + 1] / A[n] <= 1.6:
                dA.append(abs(A[n + 1] - A[n])); sA.append(A[n])
    if len(dP) < 10:
        return 0.0, 0.0                                 # too few cycles to say
    return (100 * float(np.sum(dP) / np.sum(sP)),
            100 * float(np.sum(dA) / np.sum(sA)) if sA else 0.0)


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
    loud = db > max(db.max() - VOICED_REL_DB, VOICED_FLOOR)
    speech, backend = _speech_mask(x, sr, len(frames), fl, hl, loud)
    global LAST_BACKEND
    LAST_BACKEND = backend

    # Only measure between the first and last word. Dead air before they start
    # and the ~0.9 s listen.py waits before stopping are not pauses -- counting
    # them made every short clip look hesitant.
    idx = np.flatnonzero(speech)
    if idx.size == 0:
        return dict(DEFAULTS, duration_s=round(len(x) / sr, 2))
    a, b = idx[0], idx[-1] + 1
    span = slice(a, b)
    sp = speech[span]
    talk_s = float((b - a) * hl / sr)

    f0 = np.zeros(len(frames))
    track = np.flatnonzero(speech & loud)   # never pitch-track the room
    if track.size:
        f0[track] = _yin_many(np.stack([frames[k] for k in track]), sr)
    p = f0[f0 > 0] if (f0 > 0).any() else np.array([0.0])
    jitter, shimmer = _perturbation(x, sr, f0, fl, hl)

    d = np.diff(db, prepend=db[0])          # onsets: rising energy edges
    onsets = int(np.sum(((d > 6) & speech)[span]))

    return dict(
        pitch_mean_hz = round(float(p.mean()), 1),
        pitch_sd_hz   = round(float(p.std()), 1),
        loudness_db   = round(float(db[span][sp].mean()), 1),
        pause_ratio   = round(float(1 - sp.mean()), 3),
        onset_rate_hz = round(onsets / talk_s, 2) if talk_s > 0 else 0.0,
        jitter_pct    = round(jitter, 2),
        shimmer_pct   = round(shimmer, 2),
        duration_s    = round(talk_s, 2),
    )
