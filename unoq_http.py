"""Pour through the UNO Q over Wi-Fi -- the contract in the system handover:

    GET /pour?ch=<1-6>&ms=<0-30000>   runs that pump, then stops it. The
                                      request lasts as long as the pour.
    GET /stop                         every channel off, now
    -> {"ok": true}  or  {"ok": false, "error": "..."}

Pumps 1-6 are on the UNO Q's D2-D7. Channel 7 was the stirrer, which was cut,
so this never stirs. Standard library only.
"""
import json, os, urllib.error, urllib.request

URL = os.environ.get("MIXMIND_UNOQ", "http://10.189.87.190:8081")   # changes on DHCP renewal
ML_PER_SEC = 3.7        # the handover's guess -- calibration.json overrides, per pump
CAL_FILE = os.environ.get("MIXMIND_CAL", "calibration.json")
MAX_MS = 30000          # the sketch refuses anything longer
# The UNO Q's Bridge gives up on any call after 10 s ("Request 'pour' timed out
# after 10s"), though the sketch itself accepts 30 s, so long pours go out as
# back-to-back pieces that each fit inside the Bridge.
CHUNK_MS = 9000
# The safety net for the 18 oz cups: local_bartender aims at 130 ml and
# shows its working; this refuses anything that would go over the side even
# if that file is wrong. 151 ml is the measured liquid room above a cup
# brim-full of floating ice -- stop short of it.
MIN_ML, MAX_ML, MAX_TOTAL_ML = 10, 60, 145


class UnoQError(RuntimeError): pass


def validate(recipe):
    """Nothing reaches a pump unless the drink is physically sane."""
    pours = recipe.get("pours") or []
    chans = [p.get("channel") for p in pours]
    if not 2 <= len(pours) <= 6 or len(set(chans)) != len(chans):
        raise UnoQError("a drink needs 2-6 different pumps: %r" % chans)
    for p in pours:
        if not isinstance(p["channel"], int) or not 1 <= p["channel"] <= 6:
            raise UnoQError("no pump %r" % p["channel"])
        if not MIN_ML <= p["ml"] <= MAX_ML:
            raise UnoQError("pump %d: %s ml is outside %d-%d" % (p["channel"], p["ml"], MIN_ML, MAX_ML))
    if sum(p["ml"] for p in pours) > MAX_TOTAL_ML:
        raise UnoQError("more than %d ml in one cup" % MAX_TOTAL_ML)


class HttpUnoQ:
    speed = 1.0

    def __init__(self, url=URL):
        self.url = url.rstrip("/")
        self.version = "UNO Q at %s" % self.url
        self.rates = [ML_PER_SEC] * 6
        self.calibrated = False
        try:                                     # {"1": 3.4, ..., "6": 3.9}
            cal = json.load(open(CAL_FILE))
            self.rates = [float(cal[str(i)]) for i in range(1, 7)]
            self.calibrated = True
            self.version += " (calibrated %s)" % cal.get("_measured_at", "?")
        except (OSError, KeyError, ValueError):
            # Silence here is how every dose ends up quietly wrong. Ten minutes
            # with a scale and calibrate.py fixes it for the whole weekend.
            print("WARNING: no usable %s -- all six pumps assumed %.1f ml/s. "
                  "Run `python calibrate.py`." % (CAL_FILE, ML_PER_SEC))

    def _get(self, path, timeout):
        try:
            with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
                out = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise UnoQError("cannot reach the UNO Q at %s: %s" % (self.url, e))
        if not out.get("ok"):
            raise UnoQError("UNO Q refused %s: %s" % (path, out.get("error", "unknown")))
        return out

    def ms_for(self, channel, ml):
        return int(round(ml / self.rates[channel - 1] * 1000))

    def all_off(self):
        return self._get("/stop", timeout=5)

    def pour(self, channel, ml):
        ms = self.ms_for(channel, ml)
        if not 1 <= channel <= 6 or not 0 <= ms <= MAX_MS:
            raise UnoQError("won't send pump %r for %d ms" % (channel, ms))
        # Each request lasts its whole piece: a shorter timeout would give up
        # while the pump is still running and report a failure that wasn't.
        left = ms
        while left > 0:
            part = min(left, CHUNK_MS)
            self._get("/pour?ch=%d&ms=%d" % (channel, part), timeout=part / 1000 + 5)
            left -= part
        return {"ok": True}

    def make(self, recipe, on_step=None):
        """Every ingredient in order. Anything goes wrong: all pumps off."""
        validate(recipe)
        pours = recipe["pours"]
        try:
            for i, p in enumerate(pours):
                if on_step: on_step("pour", i, len(pours), p)
                self.pour(p["channel"], p["ml"])
        except Exception:
            try: self.all_off()
            except Exception: pass
            raise

    def close(self):
        try: self.all_off()
        except UnoQError: pass
