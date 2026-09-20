"""The Pi -> UNO Q Wi-Fi link, against a fake UNO Q that follows the handover
contract: GET /pour?ch&ms blocks for the whole pour, GET /stop, JSON replies.
"""
import json, os, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import unoq_http

seen, fail_on = [], set()
scale = {"on": False, "dg": 0, "accuracy": 1.02}

class FakeUnoQ(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        seen.append(self.path)
        if u.path == "/pour":
            ch, ms = int(q["ch"][0]), int(q["ms"][0])
            if ch in fail_on or not 1 <= ch <= 7 or not 0 <= ms <= 30000:
                body = {"ok": False, "error": "Bridge said no"}
            else:
                time.sleep(ms / 1000)                  # like the real one: the whole pour
                body = {"ok": True}
        elif u.path == "/stop":
            body = {"ok": True}
        elif u.path == "/scale":
            body = {"ok": True, "cpdg": int(q["cpdg"][0])} if scale["on"] else \
                   {"ok": False, "error": "no scale"}
        elif u.path == "/tare":
            scale["dg"] = 0
            body = {"ok": True} if scale["on"] else {"ok": False, "error": "no scale"}
        elif u.path == "/weigh":
            body = {"ok": True, "dg": scale["dg"]} if scale["on"] else \
                   {"ok": False, "error": "no scale"}
        elif u.path == "/pour_to":
            if not scale["on"]:
                body = {"ok": False, "error": "no scale"}
            else:
                dg = int(q["dg"][0])
                got = int(dg * scale["accuracy"])     # a real pump overshoots a little
                scale["dg"] += got
                body = {"ok": True, "dg": got}
        else:
            body = {"ok": False, "error": "unknown path"}
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(data)
    def log_message(self, *a): pass

srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeUnoQ)
threading.Thread(target=srv.serve_forever, daemon=True).start()
b = unoq_http.HttpUnoQ("http://127.0.0.1:%d" % srv.server_port)
b.rates = [370.0] * 6              # 100x faster pumps so the test is quick

# 1. a drink: pumps in order, the right lengths, and never the stirrer
r = {"pours": [{"channel": 1, "ml": 35}, {"channel": 5, "ml": 50}, {"channel": 6, "ml": 45}]}
steps = []
b.make(r, on_step=lambda k, i, n, p: steps.append((k, p["channel"])))
assert seen == ["/pour?ch=1&ms=95", "/pour?ch=5&ms=135", "/pour?ch=6&ms=122"], seen
assert steps == [("pour", 1), ("pour", 5), ("pour", 6)], steps

# 2. a long pour is a long request -- it must not time out mid-pour
b.rates = [3.7] * 6
seen.clear(); t = time.time()
b.pour(2, 10)                      # 2.7 s: longer than a naive short timeout
assert seen == ["/pour?ch=2&ms=2703"] and time.time() - t >= 2.7

# 3. the UNO Q refuses a pump midway: the error surfaces AND everything goes off
b.rates = [370.0] * 6
seen.clear(); fail_on.add(5)
try:
    b.make(r); raise AssertionError("a refused pour was swallowed")
except unoq_http.UnoQError as e:
    assert "Bridge said no" in str(e)
assert seen[-1] == "/stop" and "/pour?ch=6" not in " ".join(seen), seen
fail_on.clear()

# 4. an unsafe recipe never reaches a pump
for bad in ({"pours": [{"channel": 1, "ml": 40}]},                                   # one ingredient
            {"pours": [{"channel": 7, "ml": 40}, {"channel": 1, "ml": 40}]},         # no pump 7
            {"pours": [{"channel": 1, "ml": 90}, {"channel": 2, "ml": 40}]},         # dose too big
            {"pours": [{"channel": 1, "ml": 40}, {"channel": 1, "ml": 40}]},         # same pump twice
            {"pours": [{"channel": c, "ml": 60} for c in (1, 2, 3)]}):               # 180 ml: over the cup
    seen.clear()
    try:
        b.make(bad); raise AssertionError("poured %r" % bad)
    except unoq_http.UnoQError:
        pass
    assert seen == [], seen

# 5. UNO Q off the network: a clear error, fast -- not a hang
# ---- the load cell: pour to weight, and survive losing it ----
# 7. no scale on the board -> HttpUnoQ never claims one, pours stay timed
assert b.weighing is False
seen.clear()
b.make({"pours": [{"channel": 1, "ml": 40}, {"channel": 2, "ml": 40}]})
assert all(p.startswith("/pour?") for p in seen), seen

# 8. with a scale: the cup is tared, every pour is weighed, and what actually
#    landed is written back into the recipe
scale["on"] = True
import json as _j
open(unoq_http.LC_FILE, "w").write(_j.dumps({"counts_per_dg": 21}))
try:
    w = unoq_http.HttpUnoQ("http://127.0.0.1:%d" % srv.server_port)
    assert w.weighing, "a calibrated, answering scale was not picked up"
    assert "+ scale" in w.version
    w.rates = [370.0] * 6
    seen.clear()
    r = {"pours": [{"channel": 1, "ml": 40}, {"channel": 5, "ml": 50}]}
    w.make(r)
    assert seen[0] == "/tare", seen                    # the ice is zeroed first
    assert [p.split("?")[0] for p in seen[1:3]] == ["/pour_to", "/pour_to"], seen
    assert "/pour?" not in " ".join(seen), seen        # nothing fell back
    # asked for 40 ml; the fake pump gave 2% more, and the recipe says so
    assert 40 < r["pours"][0]["poured_ml"] < 42, r
    assert r["poured_total_ml"] > 90, r

    # 9. the scale dies mid-drink -> the rest of the drink pours on time
    seen.clear()
    r2 = {"pours": [{"channel": 1, "ml": 40}, {"channel": 2, "ml": 40},
                    {"channel": 3, "ml": 20}]}
    class Dies:
        n = 0
    _real = w._get
    def flaky(path, timeout):
        if path.startswith("/pour_to"):
            Dies.n += 1
            if Dies.n > 1:
                raise unoq_http.UnoQError("UNO Q refused /pour_to: no scale")
        return _real(path, timeout)
    w._get = flaky
    w.make(r2)
    assert w.weighing is False, "kept asking a scale that said no"
    timed = [p for p in seen if p.startswith("/pour?")]
    assert len(timed) == 2, seen      # first weighed, other two on time
    assert "poured_ml" in r2["pours"][0] and "poured_ml" not in r2["pours"][2]
    w._get = _real
finally:
    os.remove(unoq_http.LC_FILE)

print("load cell: absent -> timed, present -> tare + weighed + poured_ml "
      "recorded, dies mid-drink -> falls back without dropping the drink -- all pass")


srv.shutdown(); srv.server_close()
t = time.time()
try:
    b.all_off(); raise AssertionError("reached a dead UNO Q")
except unoq_http.UnoQError as e:
    assert "cannot reach the UNO Q" in str(e)
assert time.time() - t < 6

print("unoq_http: pours in order, no stirrer, long pour, refusal -> all off, unsafe recipes, unreachable -- all pass")

