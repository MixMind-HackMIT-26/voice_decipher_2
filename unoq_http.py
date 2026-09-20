"""Pour through the UNO Q over Wi-Fi.

    GET /pour?ch=<1-6>&ms=<0-30000>    run that pump for that long
    GET /pour_to?ch=&dg=&maxms=        run it until the cup gains dg tenths
                                       of a gram -> {"dg": delivered}
    GET /weigh /tare /raw /scale       the load cell under the cup
    GET /stop                          every channel off, now
    -> {"ok": true, ...}  or  {"ok": false, "error": "..."}

TWO WAYS TO POUR. Timed is the original: millilitres divided by a measured
ml/s, and then you hope. Weighed is what the load cell bought us -- the pump
stops because the drink arrived, not because a stopwatch said it should
have. Weighed is used whenever the scale is calibrated and answering, and
every pour falls back to timed on its own if the scale goes quiet, so a
loose connector costs accuracy and not the evening.

Pumps 1-6 are on the UNO Q's D2-D7. Channel 7 was the stirrer, which was
cut, so this never stirs. Standard library only.
"""
import json, os, socket, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = os.environ.get("MIXMIND_UNOQ", "auto")   # "auto" = go and find it
PORT = 8081
ML_PER_SEC = 3.7        # the handover's guess -- calibration.json overrides, per pump
CAL_FILE = os.environ.get("MIXMIND_CAL", "calibration.json")
LC_FILE  = os.environ.get("MIXMIND_LOADCELL", "loadcell.json")
JUICE_GML = 1.04        # grams per millilitre: juice, not water
TOL_DG   = 30           # a weighed pour more than 3 g off target is reported
MAX_MS = 30000          # the sketch refuses anything longer
# The safety net for the 18 oz cups: local_bartender aims at 130 ml and
# shows its working; this refuses anything that would go over the side even
# if that file is wrong. 151 ml is the measured liquid room above a cup
# brim-full of floating ice -- stop short of it.
MIN_ML, MAX_ML, MAX_TOTAL_ML = 10, 60, 145


class UnoQError(RuntimeError): pass


# ---------------------------------------------------------------- finding it
# The board's address changes every time the network does -- a new hotspot, a
# DHCP renewal, a reboot -- and hunting for it by hand at a bench is how an
# evening disappears. /stop is safe to call on anything (it only turns pumps
# off, and nothing else on the network answers it), so we can just knock.
def _my_ips():
    ips = []
    for probe in ("8.8.8.8", "192.168.1.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
        except OSError:
            pass
        finally:
            s.close()
    if not ips:
        # No route to the outside (a hotspot with no data, a closed venue
        # network): ask the interfaces directly instead.
        try:
            import subprocess
            out = subprocess.run(["hostname", "-I"], capture_output=True,
                                 text=True, timeout=5).stdout
            ips = [w for w in out.split()
                   if w.count(".") == 3 and not w.startswith(("127.", "172.17.",
                                                              "172.18."))]
        except Exception:
            pass
    if not ips:
        try:
            ips = [a for a in socket.gethostbyname_ex(socket.gethostname())[2]
                   if not a.startswith("127.")]
        except OSError:
            pass
    return ips


def _candidates():
    """Every address worth knocking on, nearest first."""
    out = ["http://uno-q.local:%d" % PORT]
    for ip in _my_ips():
        a, b, c, d = ip.split(".")
        # An iPhone hotspot hands out 172.20.10.2-.14 and nothing else, so
        # that whole network is 13 addresses -- it is found in a second.
        last = 15 if ip.startswith("172.20.10.") else 255
        for host in range(1, last):
            if str(host) != d:
                out.append("http://%s.%s.%s.%d:%d" % (a, b, c, host, PORT))
    return out


def _knock(url, timeout):
    try:
        with urllib.request.urlopen(url + "/stop", timeout=timeout) as r:
            return url if json.loads(r.read()).get("ok") else None
    except Exception:
        return None


def discover(timeout=1.5, verbose=False):
    """The board's URL, or None. Knocks on the whole local network at once."""
    cands = _candidates()
    if verbose:
        print("looking for the UNO Q on %d addresses ..." % len(cands))
    with ThreadPoolExecutor(64) as ex:
        for found in ex.map(lambda u: _knock(u, timeout), cands):
            if found:
                return found
    return None


def resolve(url=None, verbose=False):
    """Turn MIXMIND_UNOQ (or 'auto') into a real address."""
    url = url or URL
    if url and url != "auto":
        return url.rstrip("/")
    found = discover(verbose=verbose)
    if not found:
        raise UnoQError(
            "could not find the UNO Q on this network. Is it on the same "
            "wifi, and is mixmind.service running on it? Set MIXMIND_UNOQ="
            "http://<ip>:%d to skip the search." % PORT)
    return found


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

    def __init__(self, url=None, weigh=True):
        self.url = resolve(url)
        self.version = "UNO Q at %s" % self.url
        self.weighing = False
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
        self._wake_scale(weigh)

    # ------------------------------------------------------------ the scale
    def _wake_scale(self, want=True):
        """Push the saved counts-per-decigram to the board and see if it
        answers. Quiet failure is deliberate: no scale just means timed."""
        if not want:
            return
        try:
            cpdg = int(json.load(open(LC_FILE))["counts_per_dg"])
        except (OSError, KeyError, ValueError, TypeError):
            return
        if not cpdg:
            return
        try:
            self._get("/scale?cpdg=%d" % cpdg, timeout=5)
            self._get("/weigh", timeout=5)
        except UnoQError:
            return
        self.weighing = True
        self.version += " + scale"

    def tare(self):
        """The cup and its ice are now zero."""
        return self._get("/tare", timeout=8)

    def weigh(self):
        """Tenths of a gram since the last tare."""
        return self._get("/weigh", timeout=8).get("dg", 0)

    def raw(self):
        return self._get("/raw", timeout=8).get("raw", 0)

    def set_scale(self, counts_per_dg):
        return self._get("/scale?cpdg=%d" % int(counts_per_dg), timeout=5)

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
        # The request lasts the whole pour: a shorter timeout would give up on
        # a 10 s pour while the pump is still running and report a failure.
        return self._get("/pour?ch=%d&ms=%d" % (channel, ms), timeout=ms / 1000 + 5)

    def pour_weighed(self, channel, ml):
        """Run the pump until the cup gains this much. Returns the ml that
        actually landed, or None if the scale could not do it."""
        dg = int(round(ml * JUICE_GML * 10))
        # The stopwatch estimate is only the backstop now: a blocked tube or
        # an empty bottle has to end the pour even though the weight never
        # arrives. Generous, because being slow is normal and stopping a good
        # pour early is not.
        maxms = min(MAX_MS, int(self.ms_for(channel, ml) * 1.8) + 2500)
        try:
            out = self._get("/pour_to?ch=%d&dg=%d&maxms=%d" % (channel, dg, maxms),
                            timeout=maxms / 1000 + 6)
        except UnoQError as e:
            if "no scale" in str(e):
                self.weighing = False          # it went quiet: stop asking
                return None
            raise
        return out.get("dg", 0) / 10.0 / JUICE_GML

    def make(self, recipe, on_step=None):
        """Every ingredient in order. Anything goes wrong: all pumps off.

        With a working scale the cup is tared first and each pour stops on
        weight; what actually landed goes back into the recipe as
        `poured_ml`, so the log records the drink that was made rather than
        the one that was asked for.
        """
        validate(recipe)
        pours = recipe["pours"]
        weighed = self.weighing
        if weighed:
            try:
                self.tare()                    # the cup and its ice are zero
            except UnoQError:
                weighed = False
        try:
            for i, p in enumerate(pours):
                if on_step: on_step("pour", i, len(pours), p)
                got = self.pour_weighed(p["channel"], p["ml"]) if weighed else None
                if got is None:                # no scale, or it just dropped out
                    weighed = False
                    self.pour(p["channel"], p["ml"])
                else:
                    p["poured_ml"] = round(got, 1)
            if weighed:
                try:
                    recipe["poured_total_ml"] = round(
                        self.weigh() / 10.0 / JUICE_GML, 1)
                except UnoQError:
                    pass
        except Exception:
            try: self.all_off()
            except Exception: pass
            raise

    def close(self):
        try: self.all_off()
        except UnoQError: pass


if __name__ == "__main__":
    import sys
    found = discover(verbose=True)
    if not found:
        print("not found. Check both boxes are on the same wifi, then on the "
              "UNO Q: hostname -I")
        sys.exit(1)
    print("UNO Q at %s" % found)
    print("\n  export MIXMIND_UNOQ=%s\n" % found)
    b = HttpUnoQ(found)
    print("  %s" % b.version)
    print("  pumps: %s ml/s" % ", ".join("%.1f" % r for r in b.rates))
    if b.weighing:
        print("  scale: %.1f g on it right now" % (b.weigh() / 10.0))
