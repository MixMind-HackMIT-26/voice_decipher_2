# voice_decipher

The part of MixMind that listens. A guest talks for ten or twenty seconds; this
turns the *sound* of their voice -- never the words -- into numbers, and the
numbers into a drink. No speech-to-text, no API, no network. ~60 ms per clip
on a laptop.

Extracted from the MixMind software repo with its history intact, so `git log`
is the development record.

## Run it on a Mac

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python eval_voice.py            # every clip in tests/samples + pair check
.venv/bin/python eval_voice.py --live     # talk into the mic, watch the numbers move
.venv/bin/python tests/test_features.py   # measurements checked against known answers
.venv/bin/python record_samples.py        # record your own ten clips
```

## What it measures

| feature | what it hears | how |
|---|---|---|
| `pitch_mean_hz` | how high the voice sits | YIN, batched FFT |
| `pitch_sd_hz` | how much it moves -- flat vs animated | YIN over speech only |
| `loudness_db` | how loud | RMS over speech only |
| `pause_ratio` | hesitation | gaps between first and last word |
| `onset_rate_hz` | pace | rising-energy syllable onsets |
| `jitter_pct` | cycle-to-cycle pitch wobble -- strain | cross-correlated periods |
| `shimmer_pct` | cycle-to-cycle loudness wobble -- breathiness | peak-to-peak per cycle |
| `duration_s` | how long they actually talked | first word to last |

Speech is found with **Silero VAD**, run straight from its 2 MB ONNX file
(`models/`, MIT). The same model, streamed, decides when the guest has stopped
talking.

## Why each choice -- all measured, not assumed

| decision | evidence |
|---|---|
| YIN, not autocorrelation | autocorrelation read one speaker as 108-202 Hz; YIN 89-112 Hz |
| Silero, not an energy threshold | energy called a noisy clip 99% speech (Silero: 50%) |
| Silero for endpointing | in room noise, energy recorded to the 25 s cut-off every time; Silero stops ~0.9 s after the last word |
| pauses only between words | lead-in silence was being counted -- tired-2 was 8.8 s of file, 4.9 s of talking |
| Silero's own post-processing | without it, 2 s of lead-in moved pause_ratio by 0.19; now 0.04 |
| ONNX, not the silero-vad package | the package pulls in ~700 MB of torch |
| own jitter/shimmer, not Praat | parselmouth has never shipped a Raspberry Pi wheel |

## How good is it

On synthetic voices with a known answer (`tests/test_features.py`):
pitch within 2% from 90 to 300 Hz; shimmer within 1%; jitter always ranks
correctly but reads 60-130% of truth depending on pulse shape -- use it to
compare voices, not to quote a number.

On the ten recordings in `tests/samples`, same words said two ways:

| pair | pauses more | slower | quieter |
|---|---|---|---|
| tired-1 / wired-1 (the demo) | yes | yes | yes |
| tired-2 / wired-2 | yes | yes | no |
| flat-1 / happy-1 ("I'm fine") | yes | yes | no |
| careful-1 / rushed-1 | yes | yes | no |
| noisy-2 / noisy-1 | yes | yes | no |

Every pause and pace check passes. Every loudness failure is the iPhone's
automatic gain control, which squeezed all eight re-recorded clips into
1.3 dB -- it will re-test on the real USB mic.

## Calibration

`RANGES` in `local_bartender.py` maps each feature onto 0..1 and is specific to
the microphone. Re-derive it on the real mic:

```
.venv/bin/python local_bartender.py tests/samples/
```

## Known limits

- Loudness is untested on a mic without gain control.
- `noisy-1/-2` are from an older, shorter recording session.
- Timing is from a laptop; check it against the 400 ms budget on the Pi.
- `0` jitter/shimmer means too few clean voice cycles, not a perfect voice.
