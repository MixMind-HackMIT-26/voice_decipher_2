"""Teach the load cell what a gram is, once, and write loadcell.json.

The HX711 reports raw counts. How many counts make a gram depends on the
cell, the amplifier and how hard the bolts are done up, so it has to be
measured on the built machine -- there is no right number to look up.

    python loadcell.py              calibrate: empty plate, then a known weight
    python loadcell.py --check      put things on it and watch the grams
    python loadcell.py --tare       zero it where it stands

You need one object whose weight you know. A kitchen scale and a full cup is
fine, and so is a bag of sugar -- anything from about 100 g to 500 g. Heavier
is better: the error in your known weight divides straight into the result.

Once this is done, unoq_http pours to weight instead of to time, and a cup
cannot overfill just because a pump drifted.
"""
import json, os, sys, time
import unoq_http

LC_FILE = unoq_http.LC_FILE


def board():
    b = unoq_http.HttpUnoQ(weigh=False)         # don't need a scale to make one
    try:
        b.all_off()
    except unoq_http.UnoQError as e:
        raise SystemExit("%s\nSet MIXMIND_UNOQ to the board's address." % e)
    return b


def calibrate():
    b = board()
    print("board: %s\n" % b.version)
    print("The plate must be EMPTY and the machine must not be moving.")
    input("[Enter] when it is ")
    try:
        b.tare()
    except unoq_http.UnoQError as e:
        raise SystemExit("the HX711 is not answering: %s\n"
                         "Check DT on D9, SCK on D10, and that VCC is on "
                         "3.3 V -- not 5 V." % e)
    zero = b.raw()
    print("   zero = %d counts" % zero)

    raw = input("\nPut the known weight on. Grams, then [Enter]: ").strip()
    try:
        grams = float(raw)
    except ValueError:
        raise SystemExit("%r is not a number of grams" % raw)
    if grams < 50:
        raise SystemExit("use something heavier than 50 g -- the error in "
                         "your known weight divides straight into this")

    time.sleep(1.0)                              # let it settle
    loaded = b.raw()
    span = loaded - zero
    print("   loaded = %d counts   (span %d)" % (loaded, span))
    if abs(span) < 1000:
        raise SystemExit("only %d counts for %g g -- the cell is not being "
                         "loaded. Is the weight actually on the plate, and "
                         "are E+/E-/A+/A- in the right terminals?" % (span, grams))

    cpdg = int(round(span / (grams * 10.0)))
    if cpdg == 0:
        raise SystemExit("less than one count per tenth of a gram -- this "
                         "cell is too coarse for 10 ml doses")
    print("\n   %d counts per tenth of a gram" % cpdg)
    if cpdg < 0:
        print("   (negative: A+ and A- are swapped. Harmless -- it is "
              "handled in software, not damage.)")

    b.set_scale(cpdg)
    time.sleep(0.5)
    back = b.weigh() / 10.0
    print("   reading back: %.1f g   (you said %g g)" % (back, grams))
    off = abs(back - grams)
    if off > max(3.0, grams * 0.05):
        print("   that is %.1f g out -- re-run this, something moved" % off)

    with open(LC_FILE, "w") as f:
        json.dump({"counts_per_dg": cpdg, "known_g": grams,
                   "measured_at": time.strftime("%F %T")}, f, indent=2)
    print("\nwrote %s" % os.path.abspath(LC_FILE))
    print("Take the weight off. `python loadcell.py --check` to play with it.")


def watch():
    b = unoq_http.HttpUnoQ()
    if not b.weighing:
        raise SystemExit("no scale: run `python loadcell.py` first")
    print("%s\nCtrl-C to stop. Tared where it stands now.\n" % b.version)
    b.tare()
    try:
        while True:
            print("\r  %8.1f g " % (b.weigh() / 10.0), end="", flush=True)
            time.sleep(0.3)
    except KeyboardInterrupt:
        print()


def tare_only():
    b = unoq_http.HttpUnoQ()
    b.tare()
    print("zeroed: %.1f g" % (b.weigh() / 10.0))


if __name__ == "__main__":
    try:
        if "--check" in sys.argv:
            watch()
        elif "--tare" in sys.argv:
            tare_only()
        else:
            calibrate()
    except KeyboardInterrupt:
        print("\nstopped")
