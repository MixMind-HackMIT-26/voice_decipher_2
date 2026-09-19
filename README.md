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
.venv/bin/python tests/test_server.py     # the kiosk, tap to idle, end to end
.venv/bin/python tests/test_unoq_http.py  # the Wi-Fi pump link, against a fake UNO Q
.venv/bin/python record_samples.py        # record your own ten clips
```

## The kiosk: touchscreen UI + pipeline on the Pi

`server.py` serves the touchscreen UI (the `pi-ui` folder from
[voice-pour-pro-interface](https://github.com/MixMind-HackMIT-26/voice-pour-pro-interface))
and runs the pipeline behind it, one guest at a time:
idle -> listening -> thinking -> reveal -> pouring -> serving -> idle.
Everything runs on the Pi -- no cloud, no keys. The "API" is only the local
address the page polls.

```
.venv/bin/python listen.py                               # which mic is which
MIXMIND_MIC=USB .venv/bin/python server.py --ui ~/mixmind-ui    # the machine
.venv/bin/python server.py --ui ~/mixmind-ui --board mock       # no UNO Q yet
```

Then open `http://localhost:8080` on the touchscreen. Recording ends when the
guest goes quiet for 0.9 s (or at 25 s). A sound under 250 ms -- a click, a
breath, the tap on the screen -- does not count as starting to talk: without
that rule one tap ended the recording at 1.7 s.

Test the whole kiosk on a laptop, no mic and no UNO Q, by replaying a
recording as if it were spoken:

```
.venv/bin/python server.py --ui pi-ui --board mock --port 8090 --replay tests/samples/flat-1.wav
```

## The pumps: Pi -> UNO Q over Wi-Fi

`unoq_http.py` speaks the contract from the system handover. The UNO Q runs
the pumps on D2-D7 (pumps 1-6); the Pi only sends HTTP:

```
GET http://10.189.87.190:8081/pour?ch=<1-6>&ms=<0-30000>   -> {"ok":true}
GET http://10.189.87.190:8081/stop                          -> all off
```

- A pour request lasts the whole pour, so its timeout is `ms/1000 + 5`.
- Every recipe is checked before any pump runs: 2-6 different pumps, 10-60 ml
  each, **145 ml at most**. If the UNO Q refuses a pump midway, the Pi sends
  `/stop` straight away.
- No stirring: channel 7 was the stirrer, which was cut.
- ml -> milliseconds uses 3.7 ml/s until `calibration.json` exists, and says so
  loudly on startup. `python calibrate.py` measures it: ten seconds per pump
  into a cup on a kitchen scale, type the grams, it writes the file.
- Pump names on the screen are `server.py`'s `INGREDIENTS`, and the **bottle
  order matters**: `local_bartender._weights` is written around
  1 citrus · 2 tart red · 3 sour accent · 4 sparkling · 5 dark · 6 warm.
- The UNO Q's address changes on DHCP renewal:
  `MIXMIND_UNOQ=http://<new-ip>:8081 ./kiosk.sh`.

`./kiosk.sh` uses the real pumps; `BOARD=mock ./kiosk.sh` prints instead.
`uno_q.py` (the UART link) is the older plan, kept as a fallback -- it is the
only thing that needs pyserial, and it imports it lazily so a Pi without it
still runs the kiosk.

## The cup: why a drink is 130 ml and not 220

18 oz party cups, packed with ice. The number that matters is not the cup's
volume, because **ice floats**: it does not leave its gaps free for the
juice, it displaces liquid upward.

```
cup to the brim                                        532 ml  (18 US fl oz)
- dry rim to carry it across a room                   -100 ml
- ice, brim-full: 532 x 0.60 packing x 0.88 submerged -281 ml
                                                     = 151 ml of liquid room
/ 1.15 for pump error                                = 130 ml  <- the ceiling
```

`local_bartender` aims at 100-130 ml and shows that arithmetic in constants
you can edit if the cups change. `unoq_http.MAX_TOTAL_ML = 145` is a second,
independent net: if the recipe engine is ever edited badly, the board layer
still refuses to pour a cup over the side. `tests/test_cup.py` walks 26,244
recipes across the whole feature space and checks both, including the case
where every pump has drifted 15% long.

## The machine talks: `speak.py` + `narrate.py`

The drink was always unique -- doses are continuous, not buckets, so two
voices get two different glasses. The *sentence* was not: `_rationale()` draws
on eight phrases across six moods, and sixty guests in one night will hear it
come round.

- **`narrate.py`** rewrites only the spoken line, from the axes, the mood and
  the transcript, at temperature 0.9. The recipe is untouched -- it stays
  deterministic, offline and ~0 ms, and it is what pours. Uses
  `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`; with neither, a timeout, or a reply
  that does not look like a spoken line, you get the template line back.
  `MIXMIND_LLM=off` forces the template.
- **`speak.py`** says it out loud: OpenAI TTS when there is a key and network,
  `espeak-ng` when there is not, `say` on a Mac. Pin the speaker by name the
  way the mic is pinned -- `MIXMIND_SPK=UACDemo` -- because card numbers move
  after a reboot. Cloud audio is cached in `tts_cache/`, and the fixed lines
  are fetched at startup, not while a guest is standing there.

The pour now runs **under** the voice. The old kiosk read the line for 8 s in
silence and only then poured; it now starts talking, waits 2.5 s so the name
lands, and pours while it talks -- about 8 s off every guest.

Neither is required. `--no-voice` mutes the machine, and with no keys at all
it still pours exactly the same drinks.

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
| `transcribe.py` | Whisper `tiny.en` via faster-whisper | offline; 5.1 s per 20 s of speech on the Pi 4 |
| `content.py` | did they claim to be fine? + VADER sentiment | offline, instant |

`tiny.en`, not `base.en`: on the Pi 4, base took 9.0 s per 20 s of speech,
tiny 5.1 s. Tiny is rougher (79-86% of the script's words vs 98%) but still
hears "I'm fine". Retries are off (`temperature=0`): on real venue noise
Whisper otherwise re-decoded junk for **30 s**. `MIXMIND_STT_MODEL=base.en` on
faster hardware.

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
                        transcribe.py -> content.py -> local_bartender.py
                                              |                    |
                                          narrate.py           unoq_http.py
                                              |                    | Wi-Fi, HTTP
                                          speak.py            UNO Q Linux :8081
                                              |                    | Bridge
                                          speaker             STM32 -> relays -> pumps
```

The Pi does the listening, the thinking and the talking; the UNO Q's
microcontroller switches the pumps. `pipeline.py` is the whole loop from a
terminal, `server.py` is the same loop behind the touchscreen.

## The older UART plan -- NOT what was built

Everything in this section describes three wires between the Pi's GPIO UART
and the UNO Q's D0/D1. **It is not how the machine works.** The build went
over Wi-Fi (`unoq_http.py`, above) because the UNO Q's single USB-C port
belongs to its Linux side and will not act as a serial peripheral of the Pi.
`uno_q.py` and this section are kept so `--board serial` still has a home and
so nobody re-tries the USB route; see the system handover, Section 7.

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

Relays: pumps 1-6 on **D2-D7**. Channel 7 (D8) was the stirrer, which was cut.

**The relay board in this build is a ZY-OCR-08S opto-isolated board with a
single `V+`/`V-` pair -- it has no `JD-VCC` jumper**, so the usual "3.3 V on
VCC, 5 V on JD-VCC" advice does not apply and there is nothing to split.

The real problem is that the UNO Q drives **3.3 V** into opto-LED anodes fed
from 5 V: driving a pin HIGH for "off" still leaves ~1.7 V across the LED, and
the relay sits half on. The fix is in the sketch, not the wiring -- **off is
high-impedance**, not high:

```c
void chOn (int p) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }
void chOff(int p) { pinMode(p, INPUT); }          // floating, not 3.3 V
```

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

## Calibration -- two different things, both needed

**1. The pumps** (`calibration.json`). Peristaltic rate depends on the pump and
the tubing, so a guessed 3.7 ml/s makes every dose wrong by however wrong it
is. Ten minutes, once:

```
.venv/bin/python calibrate.py            # all six, 10 s each, type the grams
.venv/bin/python calibrate.py --check    # pour 100 ml and weigh what lands
```

**2. The microphone** (`RANGES` in `local_bartender.py`). Maps each feature onto
0..1 and is specific to the mic and the room. A range that does not match the
hardware silently pins an axis at 0 or 1 and throws that feature away:

```
.venv/bin/python record_samples.py       # 8-10 clips with a real spread
.venv/bin/python local_bartender.py tests/samples/
```

## Known limits

- `RANGES` is still calibrated on iPhone takes. Loudness is untested on a mic
  without gain control -- re-run the two steps above on the USB mic.
- The narrator needs an API key; without one every guest hears a line drawn
  from the same eight phrases.
- `noisy-1/-2` are from an older, shorter recording session.
- Timing is from a laptop; check it against the 400 ms budget on the Pi.
- `0` jitter/shimmer means too few clean voice cycles, not a perfect voice.
