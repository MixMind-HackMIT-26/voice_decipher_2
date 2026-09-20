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

import content, features, listen, local_bartender, narrate, speak, transcribe, uno_q, unoq_http, vad

# Pump number -> what the screen calls it. PLACEHOLDERS: the bottles are not
# filled yet, so each pump just got a drink. Change the names here when they
# are; the recipes only ever use pump numbers (1-6 = UNO Q pins D2-D7).
# Pump number -> what the screen and the voice call it. The BOTTLE ORDER MUST
# MATCH local_bartender._weights, which is written around these roles:
#   1 citrus base   2 tart red   3 sour accent   4 sparkling   5 dark   6 warm
# _pick() forces 4 or 6 into every glass to keep the drink long, and ch3 is
# capped at 20 ml because an accent poured big is undrinkable. Put a sharp
# cordial on 3 and something fizzy on 4, or the recipes stop making sense.
INGREDIENTS = {"1": "Orange juice", "2": "Cranberry", "3": "Lime cordial",
               "4": "Ginger ale", "5": "Grape juice", "6": "Apple juice"}

# 18 oz party cups, packed with ice. local_bartender does the arithmetic that
# keeps a pour under the rim; this is what the screen tells the guest to do.
CUP = {"size_oz": 18, "hint": "Fill your cup with ice, then place it under the spouts",
       "max_ml": local_bartender.TARGET_MAX_ML}

THINK_MIN_S = 4.0     # let the voice dials animate in, even when we are fast
REVEAL_LEAD_S = 2.5   # the name lands and the voice starts, THEN the pumps run
SERVE_S = 7.0         # "take your drink", then back to idle
SERVE_LINE = "That is yours. Give it a stir and mind the ice."


class GuestError(Exception):
    """Something the guest should be told, in one plain sentence."""


class Machine:
    def __init__(self, board, replay=None, voice=True,
                 timings=(THINK_MIN_S, REVEAL_LEAD_S, SERVE_S)):
        self.board, self.replay, self.voice = board, replay, voice
        self.think_min, self.reveal_lead, self.serve_s = timings
        self.lock = threading.Lock()
        self.worker = None
        self._set(state="idle", level_db=-60.0, elapsed_s=0.0, features=None,
                  recipe=None, pour=None, error=None, reset=True)

    # ---------- state the UI polls ----------
    def _set(self, reset=False, **kw):
        with self.lock:
            if reset:
                self.s = {"ingredients": INGREDIENTS, "cup": CUP, "speech": None}
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

    def _say(self, text, shape=None):
        """Start talking. `shape` is the guest's axes -- ElevenLabs uses it to
        match the delivery to how they sounded. Returns a thread, or None."""
        if not (self.voice and text):
            return None
        try:
            return speak.say(text, shape)
        except Exception:                      # a mute machine still serves
            return None

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
                       stt=transcribe.BACKEND,
                       think_s=round(time.time() - t1, 2))
            self._set(features=feats)

            # The narrator writes this guest's line while the dials animate, so
            # it costs nothing: it has until think_min is up, and if it is slow
            # or offline the template line is already sitting in the recipe.
            spoken = [recipe["rationale"]]
            def write():
                try: spoken[0] = narrate.line(recipe, feats, words, INGREDIENTS)
                except Exception: pass
            w = threading.Thread(target=write, daemon=True); w.start()
            w.join(timeout=max(0.5, self.think_min - (time.time() - t1)))
            time.sleep(max(0.0, self.think_min - (time.time() - t1)))
            log["spoken"] = spoken[0]
            log["narrator"] = narrate.available()

            # Reveal: the name goes up, the voice starts, and the pumps run
            # UNDER it. The old code read the line for 8 s in silence and only
            # then poured -- 8 s per guest, times sixty guests, for nothing.
            self._set(state="reveal", recipe=recipe, speech=spoken[0])
            voice = self._say(spoken[0], recipe["axes"])
            time.sleep(self.reveal_lead)

            self._set(state="pouring")
            speed = getattr(self.board, "speed", 1.0)
            def step(kind, i, n, p):
                if kind == "pour":
                    ms = (self.board.ms_for(p["channel"], p["ml"])
                          if hasattr(self.board, "ms_for")
                          else p["ml"] / uno_q.ML_PER_SEC * 1000 / speed)
                    self._set(pour={"index": i, "total": n, "channel": p["channel"],
                                    "ml": p["ml"], "duration_ms": int(ms),
                                    "started_at_ms": int(time.time() * 1000)})
                else:
                    self._set(pour=None)             # stirring
            try:
                self.board.make(recipe, on_step=step)
            except Exception as e:
                log["board_error"] = str(e)
                raise GuestError("The pumps didn't answer. Please get a MixMind team member.")

            # A long line over a short pour: let it finish, but never hold a
            # guest at the machine waiting for a sentence.
            if voice:
                voice.join(timeout=6.0)
            log["voice"] = speak.LAST

            self._set(state="serving", pour=None)
            self._say(SERVE_LINE, recipe["axes"])
            time.sleep(self.serve_s)
            self._set(state="idle", level_db=-60.0, elapsed_s=0.0, features=None,
                      recipe=None, pour=None, error=None, reset=True)
        except GuestError as e:
            # These are already written as one plain sentence to a guest, so
            # they are the one error worth saying out loud.
            log["error"] = str(e)
            self._set(state="error", error=str(e), pour=None, speech=str(e))
            self._say(str(e))
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
    ap.add_argument("--no-voice", action="store_true", help="do not speak out loud")
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
    transcribe.available()                     # load Whisper now if it is the one
    voice = not a.no_voice
    if voice:
        # The fixed lines get fetched and cached now. Paying a round trip for
        # "that is yours" while a guest stands there is the kind of thing you
        # only notice at 2 am with a queue.
        speak.warm([SERVE_LINE,
                    "I didn't catch that. Tap and tell me about your day.",
                    "The pumps didn't answer. Please get a MixMind team member.",
                    "Something went wrong on our side. Please try again."])
    print("MixMind kiosk on http://0.0.0.0:%d\n"
          "  board %s\n  speech-to-text %s\n  voice %s\n  narrator %s\n"
          "  cup %d oz, pours capped at %d ml\n  %s"
          % (a.port, board.version, transcribe.describe(),
             speak.available() if voice else "MUTED (--no-voice)", narrate.available(),
             CUP["size_oz"], local_bartender.TARGET_MAX_ML,
             "REPLAYING " + a.replay if a.replay else "mic"))
    m = Machine(board, a.replay, voice=voice)
    ThreadingHTTPServer(("0.0.0.0", a.port), handler(m, a.ui)).serve_forever()


if __name__ == "__main__":
    main()
