"""Record the real voice clips the bakeoff needs. Then run eval_voice.py.

    python record_samples.py

Ten prompts, two takes each of the same sentence -- one energised, one flat.
That contrast IS the demo (handbook §01), and it is the only thing that tells
you whether math or Gemini reads people better. Synthetic tones cannot:
a model hearing a sine wave says "this is a synthesized tone", correctly.

Recording stops on its own after ~900 ms of silence. Ctrl-C to bail.
"""
import os, sys
import listen

OUT = "tests/samples"

TAKES = [
    ("tired-1",    "Say 'yeah, it's been a long day' -- like you mean it, slowly."),
    ("wired-1",    "Say the SAME sentence, but fast and bright, like you just got good news."),
    ("tired-2",    "Tell me about your week. Sound drained."),
    ("wired-2",    "Tell me about your week. Sound caffeinated."),
    ("flat-1",     "Say 'I'm fine' in a way that is plainly not fine."),
    ("happy-1",    "Say 'I'm fine' and actually mean it."),
    ("rushed-1",   "Describe your morning as fast as you can."),
    ("careful-1",  "Describe your morning, pausing to pick your words."),
    ("noisy-1",    "Anything you like -- but play music or run a tap first. We need a loud room."),
    ("noisy-2",    "Same again, still noisy, but sound tired this time."),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    print(__doc__)
    for name, instruction in TAKES:
        path = os.path.join(OUT, name + ".wav")
        if os.path.exists(path):
            print("\n[%s] already recorded, skipping (delete it to redo)" % name)
            continue
        print("\n" + "=" * 70)
        print("  %s" % name)
        print("  %s" % instruction)
        print("=" * 70)
        try:
            input("  press enter, then speak... ")
        except (KeyboardInterrupt, EOFError):
            print("\nstopped.")
            return 0
        listen.record(path)
        import features
        print("  saved %s  %s" % (path, features.extract(path)))

    print("\nall takes recorded. now run:")
    print("  python eval_voice.py %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
