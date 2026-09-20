"""Load ~/.mixmind.env so every entry point sees the keys, not just kiosk.sh.

kiosk.sh sources that file before starting the server, which is right for the
kiosk and wrong for everything else: run `python preflight.py` by hand and it
would report no keys at all while the file sits there in your home directory,
which is a confusing ten minutes nobody needs at 2 am.

    KEY=value           one per line, no `export`, quotes optional
    # comments and blank lines are ignored

A variable already in the environment always wins, so
`MIXMIND_STT=whisper python pipeline.py` still overrides the file.

Import it first, before anything that reads os.environ at import time:

    import env  # noqa: F401  -- must come before speak/narrate/transcribe
"""
import os

PATHS = ("~/.mixmind.env", "~/mixmind.env", ".env", "~/.config/mixmind/env")
LOADED = []


def load(*paths):
    """Read each file that exists. Returns the ones it used."""
    for p in (paths or PATHS):
        full = os.path.expanduser(p)
        try:
            with open(full) as f:
                lines = f.readlines()
        except OSError:
            continue
        n = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k:
                os.environ.setdefault(k, v)      # a real env var beats the file
                n += 1
        if n:
            LOADED.append("%s (%d)" % (full, n))
    return LOADED


def describe():
    return ", ".join(LOADED) if LOADED else "none found"


load()
