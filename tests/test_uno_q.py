"""The Pi side of the UNO Q link, over a real (virtual) serial port.

unoq/fake_board.py answers like the sketch, so this runs pyserial and
uno_q.UnoQ exactly as the Pi will -- just without the board.
"""
import os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [HERE, os.path.join(HERE, "unoq")]
import uno_q
from fake_board import FakeBoard

RECIPE = {"pours": [{"channel": 1, "ml": 40}, {"channel": 5, "ml": 40},
                    {"channel": 6, "ml": 60}], "stir_seconds": 6}

# 1. a whole drink, in order, and the board is left off
b = FakeBoard(); q = uno_q.UnoQ(b.path)
q.make(RECIPE); q.close()
assert [c for c, _ in b.log] == ["?", "P1 10667", "P5 10667", "P6 16000", "M 6000", "X"], b.log

# 2. a pump fails halfway: exception raised AND everything switched off
class Flaky(FakeBoard):
    def _reply(self, cmd):
        return "ERR" if cmd.startswith("P5") else super()._reply(cmd)
b = Flaky(); q = uno_q.UnoQ(b.path)
try:
    q.make(RECIPE); raise AssertionError("failure was swallowed")
except uno_q.UnoQError:
    pass
assert b.log[-1][0] == "X" and "P6 16000" not in [c for c, _ in b.log], b.log

# 3. the sketch refuses what it should, and Python never sends nonsense
b = FakeBoard(); q = uno_q.UnoQ(b.path)
for bad in ("P1 45000", "P7 100", "Z"):
    try:
        q._cmd(bad); raise AssertionError("board accepted %r" % bad)
    except uno_q.UnoQError:
        pass
n = len(b.log)
for ch, ml in ((7, 50), (1, 500)):
    try:
        q.pour(ch, ml); raise AssertionError("sent pump %d, %d ml" % (ch, ml))
    except uno_q.UnoQError:
        pass
assert len(b.log) == n, "Python sent a command it should have refused"

# 4. the board reboots mid-session: its READY is skipped, not taken as a reply
b = FakeBoard(); q = uno_q.UnoQ(b.path)
b._send("READY")
time.sleep(0.05)
assert q._cmd("?").startswith("MIXMIND")

# 5. nothing answering (wrong wire, no sketch): a clear error, not a hang
class Dead(FakeBoard):
    def _reply(self, cmd):
        return ""
t = time.time()
try:
    uno_q.UnoQ(Dead().path); raise AssertionError("connected to a dead board")
except uno_q.UnoQError as e:
    assert "no MixMind sketch" in str(e)
assert time.time() - t < 10

print("uno_q: full drink, failure -> all off, refusals, reboot, dead board -- all pass")
