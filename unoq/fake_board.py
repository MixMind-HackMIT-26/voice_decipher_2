"""A pretend UNO Q on a virtual serial port, for testing the Pi side on a Mac.

It answers exactly as unoq/sketch/sketch.ino does -- same commands, same
limits, same replies -- through a real pty, so uno_q.UnoQ and pyserial run
the path they will run on the Pi. If you change the sketch, change this.

    python unoq/fake_board.py            prints a port, e.g. /dev/ttys012
    python pipeline.py --port /dev/ttys012 --wav tests/samples/tired-1.wav
"""
import os, sys, threading, time, tty

MAX_MS = 30000


class FakeBoard:
    def __init__(self, speed=50.0):
        self.speed, self.log, self.on = speed, [], set()
        self.master, slave = os.openpty()
        tty.setraw(slave)
        self.path = os.ttyname(slave)
        self._slave = slave
        threading.Thread(target=self._run, daemon=True).start()
        self._send("READY")                     # like a fresh boot

    def _send(self, line):
        os.write(self.master, (line + "\r\n").encode())

    def _reply(self, cmd):
        """sketch.ino loop(), line for line."""
        k, sp = cmd[0], cmd.find(" ")
        num = lambda s: int(s) if s.strip().lstrip("-").isdigit() else 0
        def fire(ch, ms):
            if not 0 <= ms <= MAX_MS:
                return "ERR"
            self.on.add(ch); time.sleep(ms / 1000 / self.speed); self.on.discard(ch)
            return "DONE"
        if k == "?": return "MIXMIND v3 UNOQ"
        if k == "X": self.on.clear(); return "DONE"
        if k == "P":
            ch = num(cmd[1:] if sp < 0 else cmd[1:sp]) - 1
            return "ERR" if sp < 0 or not 0 <= ch <= 5 else fire(ch, num(cmd[sp + 1:]))
        if k == "M":
            return "ERR" if sp < 0 else fire(6, num(cmd[sp + 1:]))
        return "ERR"

    def _run(self):
        buf = b""
        while True:
            try:
                buf += os.read(self.master, 256)
            except OSError:
                return
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                cmd = line.decode(errors="replace").strip()
                if cmd:
                    r = self._reply(cmd)
                    self.log.append((cmd, r))
                    self._send(r)


if __name__ == "__main__":
    b = FakeBoard(speed=float(sys.argv[1]) if len(sys.argv) > 1 else 50.0)
    print("fake UNO Q listening on %s  (Ctrl-C to stop)" % b.path)
    try:
        n = 0
        while True:
            time.sleep(0.2)
            for cmd, r in b.log[n:]:
                print("  %-10s -> %s" % (cmd, r))
            n = len(b.log)
    except KeyboardInterrupt:
        pass
