"""Journal physical actions before issuing commands; never replay uncertain pours."""
import copy
import json
import math
import os
from pathlib import Path
import threading
import time
from catalog import sample_recipe, validate


class Dispenser:
    def __init__(self, board, folder="logs/dispensing"):
        self.board = board
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        for path in self.folder.glob("*.json"):
            event = json.loads(path.read_text())
            if event["status"] in ("requested", "started"):
                event["status"] = "uncertain"
                self._save(path, event)

    def _save(self, path, event):
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(event, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def sample_ms(self, channel, ml):
        rate = (self.board.rates[channel-1] if getattr(self.board, "calibrated", False)
                else float(os.environ.get("MIXMIND_SAMPLE_ML_S", str(25/5.2))))
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("Invalid sample calibration")
        ms = round(ml / rate * 1000)
        if not 1 <= ms <= 9000:
            raise ValueError("Sample pulse outside supported range")
        return ms

    def execute(self, session_id, version, kind, recipe, on_step=None):
        validate(recipe)
        if kind not in ("sample", "final") or not session_id.isalnum() or type(version) is not int:
            raise ValueError("Invalid dispensing identity")
        with self.lock:
            # An interrupted physical operation requires operator inspection, not a retry.
            for existing in self.folder.glob("*.json"):
                if json.loads(existing.read_text())["status"] == "uncertain":
                    raise RuntimeError("An earlier pour is uncertain; ask a team member to inspect the machine")
            path = self.folder / ("%s-%d-%s.json" % (session_id, version, kind))
            if path.exists():
                event = json.loads(path.read_text())
                if event["status"] == "completed":
                    return event
                raise RuntimeError("This pour cannot be repeated")
            delivered = sample_recipe(recipe) if kind == "sample" else copy.deepcopy(recipe)
            pulses = [self.sample_ms(p["channel"], p["ml"]) for p in delivered["pours"]] if kind == "sample" else []
            event = dict(session_id=session_id, version=version, kind=kind, recipe=delivered,
                         status="requested", at=time.time(), pulses_ms=pulses)
            self._save(path, event)
            event["status"] = "started"
            self._save(path, event)
            try:
                if kind == "final":
                    self.board.make(delivered, on_step=on_step)
                else:
                    for i, (p, ms) in enumerate(zip(delivered["pours"], pulses)):
                        step = dict(p, duration_ms=ms / getattr(self.board, "speed", 1))
                        if on_step:
                            on_step("pour", i, len(pulses), step)
                        if hasattr(self.board, "_get"):
                            self.board._get("/pour?ch=%d&ms=%d" % (p["channel"], ms), timeout=ms/1000+6)
                        else:
                            self.board._cmd("P%d %d" % (p["channel"], ms), timeout=ms/1000+5)
                event["status"] = "completed"
                self._save(path, event)
                return event
            except Exception:
                event["status"] = "uncertain"
                self._save(path, event)
                try:
                    self.board.all_off()
                except Exception:
                    pass
                raise
