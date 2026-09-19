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
.venv/bin/python tests/test_uno_q.py      # the Pi <-> UNO Q link, over a fake board
.venv/bin/python tests/test_content.py    # speech-to-text and what the words mean
.venv/bin/python record_samples.py        # record your own ten clips
```

## Docker

One image for the Pi and a Mac: both are linux/arm64. The speech-to-text
model is baked in, so the container never needs the internet -- every test
and the full pipeline pass with `--network none`.

```
docker build -t voice-decipher .                     # ~2.5 min on a Mac, 1 GB
```

**On the Pi** -- the mic and the UNO Q's UART are passed through:

```
docker run -it --rm --device /dev/snd --device /dev/serial0 \
  -e MIXMIND_MIC=USB -v "$PWD/logs:/app/logs" voice-decipher
```

Faster than building on the Pi: build on the Mac (same architecture) and ship it.

```
docker save voice-decipher | gzip | ssh <user>@mixmind.local 'gunzip | docker load'
```

**On a Mac** there is no microphone inside Docker (Docker Desktop has no
audio passthrough), so use recordings:

```
docker run --rm voice-decipher python pipeline.py --mock --wav tests/samples/flat-1.wav
docker run --rm voice-decipher sh -c 'for t in tests/test_*.py; do python $t; done'
```

Python in the image is 3.11 -- what Raspberry Pi OS ships -- which is how the
image caught that Python's `wave` module cannot read WAVE_FORMAT_EXTENSIBLE
headers before 3.12. `features.py` now reads them itself.

## How they sound AND what they say

The voice gives **energy** (pace, pauses, wobble, loudness); the words give
**what they claim** and a rough **mood**. The voice decides the drink's mood;
the words change what the machine says about it. Same words, said two ways,
from our own recordings:

> **flat-1**: "You said you're fine, but you left a lot of space between your
> words and you spoke softly -- this is mostly citrus and soda..."
>
> **happy-1**: "You said you're fine and you sounded it: you talked quickly.
> This is bright and sharp..."

| piece | what | runs |
|---|---|---|
| `transcribe.py` | Whisper `base.en` via faster-whisper | offline, ~0.7 s per 15 s clip on a Mac |
| `content.py` | did they claim to be fine? + VADER sentiment | offline, instant |

`base.en` over `tiny.en`: tiny turned "it's not a big deal" into "they thought
big do". Set `MIXMIND_STT_MODEL=tiny.en` if the Pi is too slow.

The "claims to be fine" check is a narrow phrase match and reliable. VADER's
sentiment is only a nudge on the pour: on our own scripts it scored "it's been
a long day... nonstop" as positive. Speech-to-text and the voice measurements
run in parallel. If the model is missing, the words are skipped and the voice
path carries on.

**Download the model once while online** -- it is cached and loads offline after:

```
.venv/bin/python transcribe.py
```

## Voice in, drink out: Raspberry Pi + Arduino UNO Q

```
mic -> Pi: listen.py -> features.py   \
                        transcribe.py -> content.py -> local_bartender.py -> uno_q.py
                                                                 | 3 wires, UART
                                        UNO Q STM32: unoq/sketch -> relays -> pumps
```

The Pi does the listening and thinking; the UNO Q's microcontroller switches
the pumps. `pipeline.py` is the whole loop: press Enter, talk, it pours.

### Wire it -- three wires, no level shifter

| Raspberry Pi | UNO Q |
|---|---|
| GPIO14 TXD, **pin 8** | **D0** (RX) |
| GPIO15 RXD, **pin 10** | **D1** (TX) |
| GND, **pin 6** | **GND** |

Both are 3.3 V logic. Why not USB: on the UNO Q the USB-C port belongs to
its Linux side, not the microcontroller, so the old "plug the UNO into the
Pi's USB and send `P3 2400`" setup does not exist on this board. D0/D1 are the
microcontroller's own UART (`Serial1`).

Relays: pumps 1-6 on **D2-D7**, stirrer on **D8**. **The UNO Q drives 3.3 V,
and the relay board was chosen for a 5 V UNO** -- with an active-low board on
5 V, a 3.3 V "off" can leave a relay half on. Power the relay board's VCC
(logic side) from **3.3 V**, keep **JD-VCC on 5 V** for the coils.

### UNO Q: upload the sketch

Arduino App Lab -> new App -> paste `unoq/sketch/sketch.ino` into the sketch
tab -> Run. The console then shows every command it receives, and the onboard
LED lights while a pump runs -- so it can be tested with no relays at all.

### Pi: turn on the UART, then run

```
sudo raspi-config    # Interface Options -> Serial Port:
                     #   login shell over serial? NO   hardware enabled? YES
sudo reboot
sudo usermod -a -G dialout $USER     # then log out and in
.venv/bin/python -m serial.tools.miniterm /dev/serial0 115200
                     # type ?  and Enter -> MIXMIND v3 UNOQ   (Ctrl-] quits)
.venv/bin/python pipeline.py
```

If replies come back garbled, the Pi 4's mini-UART is drifting with the CPU
clock: add `dtoverlay=disable-bt` to `/boot/firmware/config.txt` to put the
proper UART on those pins. No free GPIO? A **3.3 V** USB-serial adapter works
too: `pipeline.py --port /dev/ttyUSB0`.

### Test all of it on a Mac first

```
.venv/bin/python pipeline.py --mock --wav tests/samples/tired-1.wav   # no board
.venv/bin/python unoq/fake_board.py                     # a pretend UNO Q on a serial port
.venv/bin/python pipeline.py --port /dev/ttys00N --wav tests/samples/wired-1.wav
.venv/bin/python tests/test_uno_q.py                    # failures: all off, refusals, dead board
```

`fake_board.py` answers exactly like the sketch through a real virtual serial
port, so the Pi's serial code runs for real. Drop `--wav` to use the mic.

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
