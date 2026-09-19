"""Voice in, drink out. Runs on the Raspberry Pi; the UNO Q does the pouring.

    python pipeline.py                  mic -> drink -> UNO Q on /dev/serial0
    python pipeline.py --mock           no UNO Q: prints what it would send
    python pipeline.py --wav f.wav      skip the mic
    python pipeline.py --port /dev/ttyUSB0   a USB-serial adapter instead of GPIO

Press Enter, talk, stop. It stops listening by itself ~0.9 s after you do.
Ctrl-C to quit. Every drink is logged to logs/ as JSON.
"""
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor

import content, features, listen, local_bartender, transcribe, vad
import uno_q


def show(kind, i, n, p):
    if kind == "pour":
        print("   pump %d  %3d ml   (%d/%d)" % (p["channel"], p["ml"], i + 1, n))
    elif kind == "stir":
        print("   stir %d s" % p["seconds"])


def one_drink(board, wav=None):
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
    t["think_s"] = round(time.time() - t1, 3)
    print("HEARD     pitch %.0f Hz, wobble %.0f, pause %.2f, pace %.2f, %.1f s of talking"
          % (feats["pitch_mean_hz"], feats["pitch_sd_hz"], feats["pause_ratio"],
             feats["onset_rate_hz"], feats["duration_s"]))
    print("SAID      \"%s\"" % (text or "(no words caught)"))
    print("          %d words/min, mood of words %+.2f%s"
          % (words["words_per_min"], words["valence"],
             ", claims to be fine" if words["says_okay"] else ""))
    print("DRINK     %s  [%s]" % (recipe["name"], recipe["mood"]))
    print("          %s" % recipe["rationale"])

    t2 = time.time()
    board.make(recipe, on_step=show)
    t["pour_s"] = round(time.time() - t2, 2)
    print("SERVED    %s\n" % t)

    os.makedirs("logs", exist_ok=True)
    with open(os.path.join("logs", "%d.json" % time.time()), "w") as f:
        json.dump({"at": time.strftime("%F %T"), "wav": wav, "features": feats,
                   "words": words, "recipe": recipe, "timings": t,
                   "vad": features.LAST_BACKEND}, f, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="no UNO Q attached")
    ap.add_argument("--wav", help="use this recording instead of the mic")
    ap.add_argument("--port", default=uno_q.PORT)
    a = ap.parse_args()

    board = uno_q.MockUnoQ() if a.mock else uno_q.UnoQ(a.port)
    vad.available()                      # load both models now, not mid-guest
    stt = transcribe.available()
    print("MixMind  board: %s  VAD: %s  speech-to-text: %s\n"
          % (board.version, "silero" if vad.available() else "energy",
             transcribe.MODEL if stt else "OFF (%s)" % transcribe.LAST_ERROR))
    try:
        if a.wav:
            one_drink(board, a.wav)
            return 0
        while True:
            input("[Enter] to start ")
            try:
                one_drink(board)
            except uno_q.UnoQError as e:
                print("BOARD     %s -- everything switched off\n" % e)
    except (KeyboardInterrupt, EOFError):
        print()
        return 0
    finally:
        board.close()


if __name__ == "__main__":
    sys.exit(main())
