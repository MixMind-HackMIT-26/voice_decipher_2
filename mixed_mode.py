"""Taste & Tune orchestration. Quick Mix remains in server.Machine._guest."""
import json
import os
import time
from pathlib import Path
import features
import listen
import local_bartender
import speak
from catalog import sample_recipe, validate
from dispensing import Dispenser
from gemini_bartender import Bartender
from negotiation import Session


class MixedMode:
    def __init__(self, machine):
        self.machine = machine
        self.session = Session()
        self.bartender = Bartender()
        self.dispenser = Dispenser(machine.board)
        self.features = None
        self.baseline = None
        self.samples_enabled = ("MOCK" in getattr(machine.board, "version", "") or
                                os.environ.get("MIXMIND_SAMPLES", "off").lower() == "on")

    def publish(self, state, message, actions):
        s = self.session.snapshot()
        self.machine._set(state=state, mode="mixed", speech=message, recipe=s["recipe"],
            session_id=s["session_id"], version=s["version"], updates_left=s["updates_left"],
            excluded=s["excluded"], allowed_actions=actions, pour=None, error=None,
            samples_enabled=self.samples_enabled,
            sample_ratio=s["sample_ratio"], sample_recipe=sample_recipe(s["recipe"]) if s["version"] else None)
        Path("logs/sessions").mkdir(parents=True, exist_ok=True)
        path = Path("logs/sessions") / (self.session.id + ".json")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=2))
        os.replace(tmp, path)

    def say(self, message):
        if self.machine.voice:
            try:
                speak.speak(message, (self.session.recipe or {}).get("axes"))
            except Exception:
                pass

    def can_finish(self):
        try:
            if not self.session.version:
                return False
            validate(self.session.recipe, self.session.excluded)
            return True
        except ValueError:
            return False

    def waiting(self, message):
        actions = ["feedback", "cancel"]
        if self.can_finish():
            actions.insert(1, "finish")
            if not self.session.sampled() and self.samples_enabled:
                actions.insert(0, "sample")
        self.publish("feedback", message, actions)
        self.say(message)

    def turn(self, initial=False):
        wav = None
        try:
            if initial:
                message = "Tell me about your day, and what you like in a drink."
                self.publish("greeting", message, [])
                self.say(message)
            time.sleep(0.35 if self.machine.voice else 0)
            self.machine._set(state="listening", speech=None, allowed_actions=[], elapsed_s=0, level_db=-60)
            start = time.time()
            def level(db):
                self.machine._set(level_db=round(max(-60, db), 1), elapsed_s=round(time.time()-start, 1))
            wav = (listen.replay(self.machine.replay, on_level=level) if self.machine.replay
                   else listen.record(on_level=level))
            self.machine._set(state="thinking")
            if initial:
                self.features = features.extract(wav)
                if self.features["duration_s"] < 0.5:
                    raise ValueError("No usable speech")
                self.baseline = local_bartender.recipe(self.features)
                self.machine._set(features=self.features)
            self.session.begin_turn(self.baseline)
            decision = self.bartender.respond(wav, self.session, self.features)
            action, message = decision["action"], decision["message"]
            if action == "cancel":
                self.cancel()
            elif action == "propose":
                self.publish("sample_ready", message, (["sample"] if self.samples_enabled else []) + ["finish", "cancel"])
                self.say(message)
            elif action == "accept":
                self.publish("final_ready", "Place a fresh cup with ice under the spouts.", ["pour", "back", "cancel"])
                self.say("Place a fresh cup with ice under the spouts.")
            else:
                self.waiting(message)
        except Exception as exc:
            print("Mixed conversation failed: %s" % type(exc).__name__)
            if initial and self.baseline is None:
                self.publish("error", "I didn't catch that. Please try again.", [])
                self.machine._set(error="I didn't catch that. Please try again.")
            else:
                self.waiting("I couldn't complete that request. Try speaking again, or keep your current drink.")
        finally:
            if wav and os.path.exists(wav):
                os.remove(wav)

    def cancel(self):
        self.session.call("cancel_session", {})
        self.publish("cancelled", "Your drink is cancelled.", [])
        self.say("Your drink is cancelled.")
        time.sleep(self.machine.serve_s)
        self.machine._idle()

    def action(self, action):
        if action == "feedback":
            return self.turn(initial=self.baseline is None)
        if action == "cancel":
            return self.cancel()
        if action == "back":
            return self.waiting("Would you like to keep this drink or change it?")
        if action == "finish":
            if not self.can_finish():
                return self.waiting("This recipe needs to change before I can pour it.")
            self.publish("final_ready", "Place a fresh cup with ice under the spouts.", ["pour", "back", "cancel"])
            self.say("Place a fresh cup with ice under the spouts.")
            return
        kind = "sample" if action == "sample" else "final"
        try:
            if kind == "sample" and not self.samples_enabled:
                raise ValueError("Samples are not enabled")
            validate(self.session.recipe, self.session.excluded)
            self.publish("sampling" if kind == "sample" else "pouring", None, [])
            def step(_, i, n, p):
                ms = p.get("duration_ms")
                if ms is None:
                    ms = (self.machine.board.ms_for(p["channel"], p["ml"]) if hasattr(self.machine.board, "ms_for")
                          else p["ml"] / 3.75 * 1000 / getattr(self.machine.board, "speed", 1))
                self.machine._set(pour=dict(index=i+1, total=n, channel=p["channel"], ml=p["ml"],
                    duration_ms=int(ms), started_at_ms=int(time.time()*1000)))
            self.dispenser.execute(self.session.id, self.session.version, kind, self.session.recipe, step)
            if kind == "sample":
                if not self.session.sampled():
                    self.session.record_sample()
                message = ("Give your sample a stir and a taste. What would you change?" if self.session.version < 3
                           else "Give your sample a stir and a taste. Shall I make the full drink?")
                self.waiting(message)
            else:
                self.session.confirm_final(simulated=False)
                self.publish("serving", "That is yours. Give it a stir and mind the ice.", [])
                self.say("That is yours. Give it a stir and mind the ice.")
                time.sleep(self.machine.serve_s)
                self.machine._idle()
        except Exception:
            self.publish("error", "The pour could not be confirmed. Please get a MixMind team member.", [])
            self.machine._set(error="The pour could not be confirmed. Please get a MixMind team member.")
