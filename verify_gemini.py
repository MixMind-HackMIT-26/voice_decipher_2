"""Explicit, hardware-free live audio capability check. Uses environment credentials."""
import argparse
import json
from pathlib import Path
import env
import features
import local_bartender
from gemini_bartender import Bartender
from negotiation import Session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    args = parser.parse_args()
    feats = features.extract(str(args.wav))
    session = Session()
    session.begin_turn(local_bartender.recipe(feats))
    result = Bartender().respond(args.wav, session, feats)
    print(json.dumps({"response": result, "state": session.snapshot()}, indent=2))


if __name__ == "__main__": main()
