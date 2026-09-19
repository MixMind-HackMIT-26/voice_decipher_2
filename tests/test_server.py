"""The kiosk server end to end: a guest taps, talks (a replayed recording),
and the touchscreen's /api/state walks every state in order. Pretend pumps,
short screen timings -- everything else is the real pipeline.
"""
import json, os, sys, tempfile, threading, time, urllib.request
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(tempfile.mkdtemp())                       # keep test logs out of the repo
import server, uno_q
from http.server import ThreadingHTTPServer

ui = tempfile.mkdtemp()
open(os.path.join(ui, "index.html"), "w").write("<title>MixMind</title>")
m = server.Machine(uno_q.MockUnoQ(speed=200), replay=os.path.join(HERE, "tests", "samples", "flat-1.wav"),
                   timings=(0.2, 0.2, 0.2))
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(m, ui))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d" % httpd.server_port

def get(path):
    return urllib.request.urlopen(url + path, timeout=5)
def post(path):
    try:
        return urllib.request.urlopen(urllib.request.Request(url + path, method="POST"), timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code
def state():
    return json.load(get("/api/state"))

# the UI is served, missing files are a clean 404 (this used to drop the connection)
assert b"MixMind" in get("/").read()
try:
    get("/nope.js"); raise AssertionError("missing file served")
except urllib.error.HTTPError as e:
    assert e.code == 404

s = state()
assert s["state"] == "idle" and s["ingredients"]["1"] and s["recipe"] is None
assert post("/api/start") == 200
assert post("/api/start") == 409                    # one guest at a time
assert post("/api/stop") == 404                     # no stop button: silence ends it

seen, t0, snaps = [], time.time(), {}
while time.time() - t0 < 90:
    s = state()
    if not seen or seen[-1] != s["state"]:
        seen.append(s["state"]); snaps[s["state"]] = s
        if s["state"] == "pouring":
            assert post("/api/reset") == 409        # never abandon a pour
    if s["state"] == "idle" and len(seen) > 1:
        break
    time.sleep(0.05)

assert seen == ["listening", "thinking", "reveal", "pouring", "serving", "idle"], seen
assert snaps["reveal"]["recipe"]["rationale"].startswith("You said you're fine, but"), snaps["reveal"]
assert snaps["thinking"]["features"] is None or "pitch_mean_hz" in snaps["thinking"]["features"]

# the guest's recording is gone once it has been used
import glob, tempfile as _t
assert not [p for p in glob.glob(os.path.join(_t.gettempdir(), "tmp*.wav"))
            if os.path.getmtime(p) > t0], "a recording was left behind"

# an error is shown in plain words, and Try again gets back to idle
m.board = type("Broken", (uno_q.MockUnoQ,), {"make": lambda *a, **k: (_ for _ in ()).throw(uno_q.UnoQError("x"))})()
post("/api/start")
deadline = time.time() + 90
while state()["state"] != "error":
    assert time.time() < deadline, "never reached the error screen"
    time.sleep(0.05)
s = state()
assert s["state"] == "error" and "pumps" in s["error"], s
assert post("/api/reset") == 200 and state()["state"] == "idle"

httpd.shutdown()
print("server: UI served, 404s, one guest at a time, listening->thinking->reveal->pouring->serving->idle, error + try again -- all pass")
