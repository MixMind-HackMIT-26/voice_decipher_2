"""The Pi's kiosk server: serves the touchscreen UI and runs the pipeline
behind it. Everything runs here -- no cloud, no API keys. The only other
thing it talks to is the UNO Q.

    python server.py --ui ~/mixmind-ui                  the real machine
    python server.py --ui ~/mixmind-ui --board mock     no UNO Q: pours are printed
    python server.py --ui pi-ui --board mock --replay tests/samples/flat-1.wav
                                                        no mic: plays a recording

Then open http://localhost:8080 on the touchscreen.

The UI polls GET /api/state and sends POST /api/start (tap to speak) and
/api/reset (Try again). Recording ends when the guest goes quiet.
"""
import argparse, functools, json, os, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import content, features, listen, local_bartender, transcribe, uno_q, unoq_http, vad

# Pump number -> what the screen calls it. PLACEHOLDERS: the bottles are not
# filled yet, so each pump just got a drink. Change the names here when they
# are; the recipes only ever use pump numbers (1-6 = UNO Q pins D2-D7).
INGREDIENTS = {"1": "Orange juice", "2": "Cranberry", "3": "Grapefruit",
               "4": "Iced tea", "5": "Apple juice", "6": "Ginger ale"}
THINK_MIN_S = 4.0     # let the voice dials animate in, even when we are fast
REVEAL_S = 8.0        # time to read the drink's name and why
SERVE_S = 8.0         # "take your drink", then back to idle


class GuestError(Exception):
    """Something the guest should be told, in one plain sentence."""


class Machine:
    def __init__(self, board, replay=None, timings=(THINK_MIN_S, REVEAL_S, SERVE_S)):
        self.board, self.replay = board, replay
        self.think_min, self.reveal_s, self.serve_s = timings
        self.lock = threading.Lock()
        self.worker = None
        self._set(state="idle", level_db=-60.0, elapsed_s=0.0, features=None,
                  recipe=None, pour=None, error=None, reset=True)

    # ---------- state the UI polls ----------
    def _set(self, reset=False, **kw):
        with self.lock:
            if reset:
                self.s = {"ingredients": INGREDIENTS}
            self.s.update(kw)

    def snapshot(self):
        with self.lock:
            return json.dumps(self.s)

    # ---------- what the UI's buttons do ----------
    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return False                     # one guest at a time
            self.worker = threading.Thread(target=self._guest, daemon=True)
            self.worker.start()
            return True

    def reset(self):
        with self.lock:
            busy = self.worker and self.worker.is_alive()
            if busy and self.s["state"] not in ("error", "serving"):
                return False                     # never abandon a pour midway
        self._set(state="idle", level_db=-60.0, elapsed_s=0.0, features=None,
                  recipe=None, pour=None, error=None, reset=True)
        return True

    # ---------- one guest, start to finish ----------
    def _guest(self):
        log = {"at": time.strftime("%F %T")}
        wav = None
        try:
            self._set(state="listening", level_db=-60.0, elapsed_s=0.0, features=None,
                      recipe=None, pour=None, error=None, reset=True)
            t0 = time.time()

            def level(db):
                self._set(level_db=round(max(-60.0, db), 1),
                          elapsed_s=round(time.time() - t0, 1))

            wav = (listen.replay(self.replay, on_level=level) if self.replay
                   else listen.record(on_level=level))
            log["listen_s"] = round(time.time() - t0, 1)

            self._set(state="thinking")
            t1 = time.time()
            x, sr = features._read_wav(wav)
            with ThreadPoolExecutor(2) as ex:      # how it sounded / what was said
                fv = ex.submit(features.extract, wav)
                ft = ex.submit(transcribe.transcribe, x, sr)
                feats, text = fv.result(), ft.result()
            if feats["duration_s"] < 0.5:
                raise GuestError("I didn't catch that. Tap and tell me about your day.")
            words = content.analyze(text, feats["duration_s"])
            recipe = local_bartender.recipe(feats, words)
            log.update(features=feats, words=words, recipe=recipe,
                       think_s=round(time.time() - t1, 2))
            self._set(features=feats)
            time.sleep(max(0.0, self.think_min - (time.time() - t1)))

            self._set(state="reveal", recipe=recipe)
            time.sleep(self.reveal_s)

            self._set(state="pouring")
            speed = getattr(self.board, "speed", 1.0)
            def step(kind, i, n, p):
                if kind == "pour":
                    ms = (self.board.ms_for(p["channel"], p["ml"])
                          if hasattr(self.board, "ms_for")
                          else p["ml"] / uno_q.ML_PER_SEC * 1000 / speed)
                    # the screen counts pours from 1 ("POUR 2 OF 3"); sending 0
                    # made the first bar light up for the first two pours
                    self._set(pour={"index": i + 1, "total": n, "channel": p["channel"],
                                    "ml": p["ml"], "duration_ms": int(ms),
                                    "started_at_ms": int(time.time() * 1000)})
                else:
                    self._set(pour=None)             # stirring
            try:
                self.board.make(recipe, on_step=step)
            except Exception as e:
                log["board_error"] = str(e)
                raise GuestError("The pumps didn't answer. Please get a MixMind team member.")

            self._set(state="serving", pour=None)
            time.sleep(self.serve_s)
            self._set(state="idle", level_db=-60.0, elapsed_s=0.0, features=None,
                      recipe=None, pour=None, error=None, reset=True)
        except GuestError as e:
            log["error"] = str(e)
            self._set(state="error", error=str(e), pour=None)
        except Exception as e:
            traceback.print_exc()
            log["error"] = repr(e)
            self._set(state="error", pour=None,
                      error="Something went wrong on our side. Please try again.")
        finally:
            # One recording per guest, deleted once used. /tmp is in RAM on the
            # Pi: kept, a weekend of guests would slowly eat the memory.
            if wav and os.path.exists(wav):
                os.remove(wav)
            os.makedirs("logs", exist_ok=True)
            with open(os.path.join("logs", "%d.json" % time.time()), "w") as f:
                json.dump(log, f, indent=2)


def handler(machine, ui_dir):
    class H(SimpleHTTPRequestHandler):
        def _json(self, code, body):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] == "/api/state":
                return self._json(200, machine.snapshot())
            return super().do_GET()                  # the UI's files

        def do_POST(self):
            act = {"/api/start": machine.start,
                   "/api/reset": machine.reset}.get(self.path.split("?")[0])
            if act is None:
                return self._json(404, '{"error": "no such action"}')
            ok = act()
            self._json(200 if ok else 409, json.dumps({"ok": ok}))

        def log_message(self, fmt, *a):              # 5 polls a second: stay quiet
            # a[0] is the request line -- or, for errors, a status code (an int),
            # which crashed this and dropped the connection instead of a 404.
            if "/api/state" not in str(a[0] if a else ""):
                super().log_message(fmt, *a)
    return functools.partial(H, directory=ui_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", default=os.path.expanduser("~/mixmind-ui"),
                    help="the built touchscreen UI (the pi-ui folder)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--board", choices=["http", "mock", "serial"], default="http",
                    help="http: the UNO Q over Wi-Fi (the build); mock: no pumps")
    ap.add_argument("--unoq", default=unoq_http.URL, help="the UNO Q's address")
    ap.add_argument("--serial-port", default=uno_q.PORT)
    ap.add_argument("--replay", help="play this recording instead of using the mic")
    a = ap.parse_args()

    if not os.path.isfile(os.path.join(a.ui, "index.html")):
        raise SystemExit("no UI at %s -- copy the pi-ui folder there first" % a.ui)
    if a.board == "http":
        board = unoq_http.HttpUnoQ(a.unoq)
        try:
            board.all_off()                  # reachable? (/stop is always safe)
        except unoq_http.UnoQError as e:
            # Start anyway: the touchscreen should come up, and a pour will
            # show the guest the error screen instead of a dead machine.
            print("WARNING: %s" % e)
    elif a.board == "mock":
        board = uno_q.MockUnoQ(speed=4.0)
    else:
        board = uno_q.UnoQ(a.serial_port)
    vad.available()                            # load both models now, not mid-guest
    stt = transcribe.available()
    print("MixMind kiosk on http://0.0.0.0:%d  board: %s  speech-to-text: %s  %s"
          % (a.port, board.version, transcribe.MODEL if stt else "OFF",
             "REPLAYING " + a.replay if a.replay else "mic"))
    m = Machine(board, a.replay)
    ThreadingHTTPServer(("0.0.0.0", a.port), handler(m, a.ui)).serve_forever()


if __name__ == "__main__":
    main()
