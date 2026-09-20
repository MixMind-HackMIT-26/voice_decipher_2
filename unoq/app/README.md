# The UNO Q App Lab app

Two files, one App. `sketch.ino` runs on the STM32 microcontroller and owns
the relays and the HX711; `main.py` runs on the Qualcomm Linux side and turns
the Pi's HTTP into Bridge calls.

## Put it on the board

Arduino App Lab -> new App -> paste `sketch.ino` into the sketch tab and
`main.py` into the python tab -> Run. The sketch prints to **`Monitor`**, not
`Serial` -- `Serial` on this board belongs to the Linux side.

## Why HTTP and not USB

The UNO Q's single USB-C port belongs to its Linux half, so the board cannot
be a USB-serial peripheral of the Pi the way an UNO R3 can. The Pi reaches it
over Wi-Fi instead. See the system handover, Section 7, for the three
independent reasons -- it is written down so nobody spends another hour on it.

## Keeping it up

The App Lab container publishes no ports and does not restart itself, so the
host runs `mixmind.sh` under systemd: it restarts the app if the container
dies and forwards host `:8081` to the container's `:8080` with socat.

## Pins

| | |
|---|---|
| D2-D7 | pumps 1-6, relay board, active LOW |
| D9 | HX711 DT |
| D10 | HX711 SCK |

**The HX711 runs from 3.3 V, not 5 V.** Its DT line is an output that follows
its supply, and this board's pins are 3.3 V tolerant only.

Off is `pinMode(pin, INPUT)`, never `digitalWrite(pin, HIGH)`: the UNO Q
drives 3.3 V into opto-LED anodes fed from 5 V, so a 3.3 V "off" still leaves
about 1.7 V across the LED and the relay sits half on.
