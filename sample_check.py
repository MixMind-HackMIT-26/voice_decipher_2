"""Operator-run small-dose check using the same timed sample executor."""
import argparse
import env
from dispensing import Dispenser
from unoq_http import HttpUnoQ


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", type=int, choices=range(1, 7), required=True)
    parser.add_argument("--ml", type=float, choices=[0.8, 1.6, 3.2, 4.8], required=True)
    args = parser.parse_args()
    board = HttpUnoQ()
    dispenser = Dispenser(board)
    ms = dispenser.sample_ms(args.channel, args.ml)
    print("Channel %d: %.2f ml target, %d ms. Place an empty measurement cup." % (args.channel, args.ml, ms))
    if input("Type POUR to run one pulse: ").strip() != "POUR":
        return
    try:
        board._get("/pour?ch=%d&ms=%d" % (args.channel, ms), timeout=ms/1000+6)
        print("Measure the delivered quantity independently; repeat to assess spread.")
    finally:
        board.all_off()


if __name__ == "__main__": main()
