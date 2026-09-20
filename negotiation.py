"""Versioned drink negotiation; no audio, network, or hardware dependencies."""
import copy
import uuid
from catalog import CATALOG, SAMPLE_RATIO, sample_recipe, validate


class Session:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.recipe = None
        self.version = self.turn = 0
        self.revised_turn = -1
        self.ledger = []
        self.status = "active"
        self.pending_final = False
        self.excluded = set()
        self.versions = []

    def snapshot(self):
        return copy.deepcopy(dict(session_id=self.id, recipe=self.recipe, version=self.version,
            turn=self.turn, ledger=self.ledger, status=self.status, pending_final=self.pending_final,
            updates_left=3-self.version, excluded=sorted(self.excluded), sample_ratio=SAMPLE_RATIO))

    def begin_turn(self, baseline):
        if self.status != "active":
            raise ValueError("Session is closed")
        validate(baseline)
        self.turn += 1
        self.pending_final = False
        if self.recipe is None:
            self.recipe = copy.deepcopy(baseline)

    def call(self, name, args):
        try:
            if name in ("pour_sample", "finish_and_pour"):
                kind = "sample" if name == "pour_sample" else "final"
                prior = next((e for e in self.ledger if e["kind"] == kind and e["version"] == args.get("recipe_version")), None)
                if prior:
                    return {"ok": True, "duplicate": True, "event": prior}
            if self.status != "active":
                raise ValueError("Session is closed")
            if name == "cancel_session":
                self.status, self.pending_final = "cancelled", False
                return {"ok": True, "status": self.status}
            if name == "set_exclusions":
                channels = args["channels"]
                if not isinstance(channels, list) or any(type(c) is not int or c not in CATALOG for c in channels):
                    raise ValueError("Invalid exclusions")
                self.excluded.update(channels)
                return {"ok": True, "state": self.snapshot()}
            if name == "revise_drink":
                if args["expected_version"] != self.version:
                    raise ValueError("Stale recipe version")
                if self.version >= 3 or self.revised_turn == self.turn:
                    raise ValueError("Revision limit reached")
                if self.version and not self.sampled():
                    raise ValueError("Sample the current proposal before revising")
                candidate = copy.deepcopy(self.recipe)
                before = {p["channel"]: p["ml"] for p in candidate["pours"]}
                amounts, seen = before.copy(), set()
                for p in args["set_amounts"]:
                    ch, ml = p["channel"], p["ml"]
                    if type(ch) is not int or ch not in CATALOG or ch in seen or type(ml) is not int:
                        raise ValueError("Invalid ingredient edit")
                    seen.add(ch)
                    if ml == 0:
                        amounts.pop(ch, None)
                    else:
                        amounts[ch] = ml
                candidate.update(name=args["name"], rationale=args["explanation"],
                    pours=[{"channel": ch, "ml": ml} for ch, ml in sorted(amounts.items())],
                    ml_total=sum(amounts.values()), stir_seconds=0)
                validate(candidate, self.excluded)
                diff = [{"channel": ch, "ingredient": CATALOG[ch]["name"],
                    "before_ml": before.get(ch, 0), "after_ml": amounts.get(ch, 0),
                    "delta_ml": amounts.get(ch, 0)-before.get(ch, 0)}
                    for ch in sorted(before.keys() | amounts.keys()) if before.get(ch, 0) != amounts.get(ch, 0)]
                previous = self.version
                self.recipe = candidate
                self.version += 1
                self.revised_turn = self.turn
                self.pending_final = False
                self.versions.append(copy.deepcopy(candidate))
                return {"ok": True, "state": self.snapshot(), "amount_diff": diff,
                    "diff_base": "internal_baseline" if not previous else "previous_sampled_recipe",
                    "previous_version": previous}
            if name not in ("pour_sample", "finish_and_pour"):
                raise ValueError("Unknown tool")
            if args["recipe_version"] != self.version or not self.version:
                raise ValueError("No matching committed recipe")
            validate(self.recipe, self.excluded)
            if name == "finish_and_pour":
                if self.revised_turn == self.turn:
                    raise ValueError("Wait for feedback after this proposal")
                self.pending_final = True
                return {"ok": True, "status": "awaiting_operator_confirmation",
                        "message": "Use /pour to confirm or /cancel to discard"}
            if self.sampled():
                return {"ok": True, "duplicate": True, "event": self.ledger[-1]}
            event = self.record_sample(simulated=True)
            return {"ok": True, "event": event, "wait_for_user": True}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def sampled(self):
        return any(e["kind"] == "sample" and e["version"] == self.version for e in self.ledger)

    def record_sample(self, simulated=False):
        sample = sample_recipe(self.recipe)
        event = dict(kind="sample", version=self.version, turn=self.turn,
                     sample_total_ml=sample["ml_total"], simulation_only=simulated, pours=sample["pours"])
        self.ledger.append(event)
        return event

    def confirm_final(self, simulated=True):
        if self.status == "served":
            return {"ok": True, "duplicate": True}
        if self.status != "active" or not self.version:
            return {"ok": False, "error": "No active committed recipe"}
        try:
            validate(self.recipe, self.excluded)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self.ledger.append(dict(kind="final", version=self.version, simulation_only=simulated,
                                recipe=copy.deepcopy(self.recipe)))
        self.status, self.pending_final = "served", False
        return {"ok": True, "state": self.snapshot()}
