"""Talk to the MixMind sketch on an Arduino UNO Q, from a Raspberry Pi.

Three wires, 3.3 V both sides (see unoq/sketch/sketch.ino for the pinout).
On the Pi that UART is /dev/serial0. One command out, one line back; the
sketch blocks while it pours, so there is never more than one in flight.

    UnoQ()            the real board
    MockUnoQ()        prints instead of pouring -- a laptop with no hardware
"""
import os, time
import serial        # pip install pyserial

PORT = os.environ.get("MIXMIND_PORT", "/dev/serial0")
BAUD = 115200
ML_PER_SEC = 3.75    # per pump; accuracy is not the point yet
MAX_MS = 30000       # the sketch refuses anything longer


class UnoQError(RuntimeError): pass


class UnoQ:
    def __init__(self, port=PORT, baud=BAUD):
        self.ser = serial.Serial(port, baud, timeout=2)
        # Unlike the old USB UNO, opening this UART does not reset the board,
        # so there is no boot window to sleep through -- but a READY line may
        # be sitting in the buffer. Ask who is there instead of assuming.
        self.ser.reset_input_buffer()
        for _ in range(3):
            try:
                v = self._cmd("?", timeout=2)
                if v.startswith("MIXMIND"):
                    self.version = v
                    return
            except UnoQError:
                pass
        raise UnoQError("no MixMind sketch answering on %s -- wiring, baud, "
                        "or the sketch is not uploaded" % port)

    def _cmd(self, text, timeout=5):
        self.ser.reset_input_buffer()
        self.ser.write((text + "\n").encode())
        self.ser.flush()
        self.ser.timeout = timeout
        while True:
            line = self.ser.readline().decode(errors="replace").strip()
            if line == "READY":            # the board rebooted mid-session
                continue
            if line == "":
                raise UnoQError("no reply to %r" % text)
            if line == "ERR":
                raise UnoQError("UNO Q refused %r" % text)
            return line

    def all_off(self):
        return self._cmd("X")

    def pour(self, channel, ml):
        ms = int(round(ml / ML_PER_SEC * 1000))
        if not 1 <= channel <= 6 or not 0 <= ms <= MAX_MS:
            raise UnoQError("won't send pump %r for %d ms" % (channel, ms))
        return self._cmd("P%d %d" % (channel, ms), timeout=ms / 1000 + 5)

    def stir(self, seconds):
        return self._cmd("M %d" % int(seconds * 1000), timeout=seconds + 5)

    def make(self, recipe, on_step=None):
        """Pour every ingredient, then stir. Anything goes wrong: all off."""
        pours = recipe["pours"]
        try:
            for i, p in enumerate(pours):
                if on_step: on_step("pour", i, len(pours), p)
                self.pour(p["channel"], p["ml"])
            st = recipe.get("stir_seconds", 6)
            if st:
                if on_step: on_step("stir", len(pours), len(pours), {"seconds": st})
                self.stir(st)
        except Exception:
            try: self.all_off()
            except Exception: pass
            raise

    def close(self):
        try: self.all_off()
        finally: self.ser.close()


class MockUnoQ(UnoQ):
    """Same API, no board. speed=20 runs pours 20x faster than real time."""
    def __init__(self, speed=20.0):
        self.speed, self.version, self.sent = speed, "MIXMIND v3 MOCK", []

    def _cmd(self, text, timeout=5):
        self.sent.append(text)
        print("  [uno q] %s" % text)
        if text[0] in "PM":
            time.sleep(int(text.split()[-1]) / 1000 / self.speed)
        return self.version if text == "?" else "DONE"

    def close(self): pass
