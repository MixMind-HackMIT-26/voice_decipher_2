"""Run the relays in patterns worth filming. No drink, no guest, no kiosk.

    python relaydemo.py                      chase 1-6, forever
    python relaydemo.py bounce               1-6 and back
    python relaydemo.py drink                the rhythm of a real drink
    python relaydemo.py slow                 1.5 s holds, one pump at a time
    python relaydemo.py chase --ms 180       faster
    python relaydemo.py --once               one pass instead of looping

FILMING THE ROLLERS
A chase is the wrong shape for a macro shot: you are focused on one pump and
it turns for 1.5 s out of every ten. Pin it to a single pump instead, and
give it a long hold so it runs near-continuously:

    python relaydemo.py --only 1 --ms 8000 --gap 0.4

Eight seconds is the longest single hold worth using -- the UNO Q's Bridge
gives up at ten. With a 0.4 s gap that reads as continuous rotation on
camera, and the brief pause each cycle is a natural cut point.

FILMING WITHOUT POURING ANYTHING
The relay coils are powered from the 5 V logic supply; the 12 V only passes
through the contacts. So unplug the 12 V and every relay still clicks and
every LED still lights -- with no pump turning and nothing to mop up. That is
the setup you want for the close-up: you can run it for ten minutes and film
as many takes as you like.

Leave the 12 V in and this pumps for real, so prime the lines and put a jug
under the spouts first.

The sketch runs one channel at a time, so patterns are sequential by design.
That is also what looks best: a travelling light reads on camera, six coming
on at once just looks like a lamp.

Ctrl-C stops and switches everything off.
"""
import argparse, os, sys, time
import env  # noqa: F401  -- loads ~/.mixmind.env
import unoq_http

PATTERNS = {
    "chase":  [1, 2, 3, 4, 5, 6],
    "bounce": [1, 2, 3, 4, 5, 6, 5, 4, 3, 2],
    # Three pumps, held roughly in proportion to a real drink's doses, so the
    # rhythm on camera matches the rhythm of an actual pour.
    "drink":  [(1, 3.0), (4, 2.3), (6, 2.0)],
    # slow honours --ms like the others; 1500 is the default hold for it
    "slow":   [1, 2, 3, 4, 5, 6],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", nargs="?", default="chase", choices=sorted(PATTERNS))
    ap.add_argument("--ms", type=int, help="how long each relay holds, ms "
                                          "(default 320, or 1500 for slow)")
    ap.add_argument("--only", type=int, choices=range(1, 7), metavar="1-6",
                    help="one pump, over and over -- what you want for roller macro")
    ap.add_argument("--gap", type=float, default=0.12, help="silence between clicks")
    ap.add_argument("--once", action="store_true", help="one pass, then stop")
    ap.add_argument("--countdown", type=int, default=3, help="seconds before it starts")
    a = ap.parse_args()
    if a.ms is None:
        a.ms = 1500 if a.pattern == "slow" else 320
    if a.ms > 9000:
        print("capping the hold at 9000 ms: the UNO Q's Bridge gives up at 10 s")
        a.ms = 9000

    b = unoq_http.HttpUnoQ(weigh=False)
    try:
        b.all_off()
    except unoq_http.UnoQError as e:
        raise SystemExit("%s\nIs the UNO Q on this network?" % e)
    print("board: %s" % b.version)
    print("pattern: %s   hold: %d ms   gap: %.0f ms%s\n"
          % (a.pattern, a.ms, a.gap * 1000,
             "   PUMP %d ONLY" % a.only if a.only else ""))
    print("If the 12 V is unplugged this only clicks -- nothing pours.")
    print("If it is plugged in, the pumps ARE running. Jug under the spouts.\n")

    for i in range(a.countdown, 0, -1):
        print("\r  starting in %d ... " % i, end="", flush=True)
        time.sleep(1)
    print("\r  rolling.            \n")

    steps = [a.only] if a.only else PATTERNS[a.pattern]
    passes = 0
    t0 = time.time()
    try:
        while True:
            for step in steps:
                ch, hold = step if isinstance(step, tuple) else (step, a.ms / 1000.0)
                print("\r  %5.1fs   pump %d  %4.0f ms      "
                      % (time.time() - t0, ch, hold * 1000), end="", flush=True)
                b._get("/pour?ch=%d&ms=%d" % (ch, int(hold * 1000)),
                       timeout=hold + 6)
                time.sleep(a.gap)
            passes += 1
            if a.once:
                break
    except KeyboardInterrupt:
        pass
    finally:
        print("\r  %d passes, %.0f s. Everything off.      " % (passes, time.time() - t0))
        try:
            b.all_off()
        except unoq_http.UnoQError:
            pass


if __name__ == "__main__":
    sys.exit(main())
