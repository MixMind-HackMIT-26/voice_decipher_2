"""Voice in, drink out, from the command line. The bench version of server.py:
same brain, no touchscreen. The Pi runs this; the UNO Q does the pouring.

    python pipeline.py                     mic -> drink -> the UNO Q over Wi-Fi
    python pipeline.py --board mock        no board: prints what it would pour
    python pipeline.py --wav f.wav         skip the mic
    python pipeline.py --unoq http://10.0.0.5:8081    when the board moves
    python pipeline.py --no-voice          don't speak out loud

Press Enter, talk, stop. It stops listening by itself ~0.9 s after you do.
Ctrl-C to quit. Every drink is logged to logs/ as JSON.

Cups are 18 oz, packed with ice: see local_bartender for why a pour is
capped at 130 ml and what happens to that number if you change cups.
"""
import argparse, json, os, sys, time
import env  # noqa: F401  -- loads ~/.mixmind.env
from concurrent.futures import ThreadPoolExecutor

import content, features, listen, local_bartender, narrate, speak, transcribe, vad
import uno_q, unoq_http

INGREDIENTS = {"1": "Orange juice", "2": "Cranberry", "3": "Lime cordial",
               "4": "Ginger ale", "5": "Grape juice", "6": "Apple juice"}


def show(kind, i, n, p):
    if kind == "pour":
        print("   pump %d  %-14s %3d ml   (%d/%d)"
              % (p["channel"], INGREDIENTS.get(str(p["channel"]), ""), p["ml"], i + 1, n))


def one_drink(board, wav=None, voice=True):
    t, t0 = {}, time.time()
    if wav is None:
        print("LISTENING -- talk, then stop")
        wav = listen.record()
        t["listen_s"] = round(time.time() - t0, 2)

    t1 = time.time()
    # How they sounded and what they said both read the same recording and
    # need nothing from each other: run them side by side.
    x, sr = features._read_wav(wav)
    with ThreadPoolExecutor(2) as ex:
        f_voice = ex.submit(features.extract, wav)
        f_text = ex.submit(transcribe.transcribe, x, sr)
        feats, text = f_voice.result(), f_text.result()
    words = content.analyze(text, feats["duration_s"])
    recipe = local_bartender.recipe(feats, words)
    spoken = narrate.line(recipe, feats, words, INGREDIENTS)
    t["think_s"] = round(time.time() - t1, 3)
    print("HEARD     pitch %.0f Hz, wobble %.0f, pause %.2f, pace %.2f, %.1f s of talking"
          % (feats["pitch_mean_hz"], feats["pitch_sd_hz"], feats["pause_ratio"],
             feats["onset_rate_hz"], feats["duration_s"]))
    print("SAID      \"%s\"" % (text or "(no words caught)"))
    print("          %d words/min, mood of words %+.2f%s"
          % (words["words_per_min"], words["valence"],
             ", claims to be fine" if words["says_okay"] else ""))
    print("DRINK     %s  [%s]  %d ml total" % (recipe["name"], recipe["mood"],
                                               recipe["ml_total"]))
    print("SAYS      %s" % spoken)

    # Talk and pour at the same time: the guest hears why while it happens.
    t2 = time.time()
    v = speak.say(spoken, recipe["axes"]) if voice else None
    board.make(recipe, on_step=show)
    if v:
        v.join(timeout=6.0)
    t["pour_s"] = round(time.time() - t2, 2)
    print("SERVED    %s\n" % t)

    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "%d.json" % time.time()), "w") as f:
        json.dump({"at": time.strftime("%F %T"), "wav": wav, "features": feats,
                   "words": words, "recipe": recipe, "spoken": spoken,
                   "timings": t, "voice": speak.LAST, "stt": transcribe.BACKEND,
                   "narrator": narrate.available(),
                   "vad": features.LAST_BACKEND}, f, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", choices=["http", "mock", "serial"], default="http",
                    help="http: the UNO Q over Wi-Fi (the build); mock: no pumps")
    ap.add_argument("--unoq", default=unoq_http.URL, help="the UNO Q's address")
    ap.add_argument("--serial-port", default=uno_q.PORT)
    ap.add_argument("--wav", help="use this recording instead of the mic")
    ap.add_argument("--no-voice", action="store_true", help="don't speak out loud")
    a = ap.parse_args()

    if a.board == "http":
        board = unoq_http.HttpUnoQ(a.unoq)
        try:
            board.all_off()                  # reachable? (/stop is always safe)
        except unoq_http.UnoQError as e:
            print("WARNING: %s" % e)
    elif a.board == "mock":
        board = uno_q.MockUnoQ()
    else:
        board = uno_q.UnoQ(a.serial_port)

    vad.available()                      # load both models now, not mid-guest
    transcribe.available()               # load Whisper now if it is the one
    voice = not a.no_voice
    print("MixMind  board %s  VAD %s  speech-to-text %s  voice %s  narrator %s"
          % (board.version, "silero" if vad.available() else "energy",
             transcribe.describe(),
             speak.available() if voice else "MUTED", narrate.available()))
    print("         mic %s" % listen.backend())
    print("         18 oz cups with ice -- pours capped at %d ml\n"
          % local_bartender.TARGET_MAX_ML)
    try:
        if a.wav:
            one_drink(board, a.wav, voice)
            return 0
        while True:
            input("[Enter] to start ")
            try:
                one_drink(board, voice=voice)
            except unoq_http.UnoQError as e:
                print("BOARD     %s -- everything switched off\n" % e)
            except uno_q.UnoQError as e:
                print("BOARD     %s -- everything switched off\n" % e)
    except (KeyboardInterrupt, EOFError):
        print()
        return 0
    finally:
        board.close()


if __name__ == "__main__":
    sys.exit(main())
