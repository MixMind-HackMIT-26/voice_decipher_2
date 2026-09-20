"""Measure what each pump actually does, once, and write calibration.json.

Peristaltic pumps are not identical and tubing length changes the rate, so
"3.7 ml per second" is a guess that makes every dose wrong by however wrong
it is. Ten minutes with a kitchen scale fixes the whole machine.

    python calibrate.py               all six pumps
    python calibrate.py 3 5           just those two
    python calibrate.py --check       pour 100 ml and see what you get

For each pump: put the tube in a cup on a tared scale, it runs for 10
seconds, you type the grams. Water is 1 g = 1 ml. Juice is about 1.04 g/ml,
so if you calibrate with juice type the grams and this corrects for it --
but calibrate with water and flush after, it is faster and cleaner.
"""
import json, os, sys, time
import env  # noqa: F401  -- loads ~/.mixmind.env
import unoq_http

SECONDS   = float(os.environ.get("MIXMIND_CAL_SECONDS", "10"))
CAL_FILE  = unoq_http.CAL_FILE
JUICE_GML = 1.04          # grams per ml, if there is juice in the line


def load():
    try:
        return json.load(open(CAL_FILE))
    except (OSError, ValueError):
        return {}


def save(cal):
    cal["_measured_at"] = time.strftime("%F %T")
    with open(CAL_FILE, "w") as f:
        json.dump(cal, f, indent=2, sort_keys=True)
    print("\nwrote %s" % os.path.abspath(CAL_FILE))
    print(json.dumps({k: v for k, v in cal.items() if not k.startswith("_")},
                     indent=2, sort_keys=True))


def measure(board, cal, channels):
    print("Each pump runs %g s. Tube into a cup on a tared scale, then type the"
          " grams.\nBlank skips a pump, 'q' stops and saves what you have.\n"
          % SECONDS)
    for ch in channels:
        input("pump %d -- tube in the cup, scale tared, [Enter] to run " % ch)
        t0 = time.time()
        board._get("/pour?ch=%d&ms=%d" % (ch, int(SECONDS * 1000)),
                   timeout=SECONDS + 10)
        ran = time.time() - t0
        raw = input("   grams on the scale (w = it was water): ").strip().lower()
        if raw in ("q", "quit"):
            break
        if not raw:
            continue
        water = raw.endswith("w")
        try:
            grams = float(raw.rstrip("w").strip())
        except ValueError:
            print("   not a number -- skipping pump %d" % ch)
            continue
        ml = grams if water else grams / JUICE_GML
        rate = ml / SECONDS
        if not 0.3 <= rate <= 20:
            print("   %.2f ml/s is not believable -- is the tube primed? skipping" % rate)
            continue
        cal[str(ch)] = round(rate, 2)
        print("   pump %d: %.1f ml in %.1f s = %.2f ml/s   (%.0f ml would take %.1f s)"
              % (ch, ml, ran, rate, 100, 100 / rate))
    return cal


def check(board):
    """Pour a known 100 ml from each calibrated pump and see what lands."""
    print("Pouring 100 ml from each pump. Weigh each one: you want 100 g of water"
          " (or 104 g of juice), within about 5.\n")
    for ch in range(1, 7):
        input("pump %d -- empty cup on a tared scale, [Enter] " % ch)
        board.pour(ch, 100)
        print("   asked for 100 ml at %.2f ml/s = %.1f s\n"
              % (board.rates[ch - 1], 100 / board.rates[ch - 1]))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    board = unoq_http.HttpUnoQ()
    try:
        board.all_off()
    except unoq_http.UnoQError as e:
        raise SystemExit("%s\nSet MIXMIND_UNOQ to the board's address and retry." % e)
    print("board: %s\n" % board.version)

    if "--check" in sys.argv:
        return check(board)
    channels = [int(a) for a in args] or list(range(1, 7))
    cal = load()
    try:
        cal = measure(board, cal, channels)
    finally:
        board.all_off()
    if any(k.isdigit() for k in cal):
        save(cal)
        missing = [c for c in range(1, 7) if str(c) not in cal]
        if missing:
            print("\nstill guessing %.1f ml/s for pump(s) %s -- run those before a shift"
                  % (unoq_http.ML_PER_SEC, ", ".join(map(str, missing))))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
