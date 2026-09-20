"""OpenRouter audio negotiation, independent of existing narration/STT providers."""
import base64
import json
import os
from pathlib import Path
import urllib.request
from catalog import CATALOG

PROMPT = """You are MixMind, one warm, concise bartender. Hear the guest's audio.
You get JSON context: session {recipe, version, turn, updates_left, excluded,
ledger, status}, catalog, features. Read version, updates_left, excluded, and the
ledger before every call. Explicit preferences and corrections outrank inferred
mood. Acoustic measurements do not prove emotions. Subsequent feedback edits the
CURRENT recipe, never a new acoustic baseline.
Call respond exactly once per turn. First decide the action, then fill only what
that action needs:
- propose: commit a new recipe revision. Requires: updates_left >= 1 AND
  (version == 0 OR ledger already has kind=sample with version == current
  version). Never propose twice in one turn, never propose when updates_left
  is 0, never propose a 2nd/3rd version before the current one was sampled.
  If the guest wants a change but the current version is unsampled, use
  clarify and ask them to taste first (or offer finish/cancel).
- clarify: ask one specific short question, change nothing. Use for ambiguity
  such as 'more interesting'. Send set_amounts=[], exclusions=[] unless the
  guest just named an ingredient to exclude (then list only that new channel).
  Clarify costs no revision.
- accept: only on explicit acceptance of the existing recipe (e.g. 'yes, pour
  it', 'perfect'). Send set_amounts=[], exclusions=[]. Never accept in the
  same turn as a proposal; acceptance always waits for a later turn.
- cancel: only on explicit cancel. Send set_amounts=[], exclusions=[].
'Surprise me' permits discretion: propose within the rules.
Recipe math (applies to the FINAL drink after your edit, unchanged channels
included): 2-6 ingredient lines total. ml are whole integers only, no decimals
or strings. Each dose 10-60 ml; channel 3 (lime cordial) at most 20 ml; total
at most 130 ml. Compute total = sum(unchanged channels + your new amounts)
before calling. Keep total within ~10 ml of the current total unless the guest
asked for bigger/smaller. set_amounts are ABSOLUTE final-serving ml, not deltas:
omitted channels stay unchanged, ml=0 removes that channel. Never leave 0 or 1
ingredients. exclusions holds newly excluded channel numbers and persists:
if you list a channel in exclusions you MUST also set it to 0 ml in the same
call, and never include an excluded channel (old or new) with ml > 0.
There are three proposals total including the first (versions 1, 2, 3). When
updates_left is 0, offer acceptance or cancellation, never another revision.
Ingredients are the supplied physical catalog only, not an imagined menu.
message: at most two short sentences, under 35 words AND under 240 characters.
Name the whole first drink; describe later changes relative to the sampled
drink. The proposal is shown BEFORE dispensing: ask whether the balance sounds
right, never ask how it tastes yet. Do not claim liquid was dispensed. Do not
explain internal algorithms. name: short drink name, 1-48 characters.
"""

TOOL = {"type": "function", "function": {"name": "respond", "description": "Respond to the guest",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["propose", "clarify", "accept", "cancel"]},
        "message": {"type": "string"}, "name": {"type": "string"},
        "exclusions": {"type": "array", "items": {"type": "integer"}},
        "set_amounts": {"type": "array", "items": {"type": "object", "properties": {
            "channel": {"type": "integer"}, "ml": {"type": "integer"}}, "required": ["channel", "ml"]}}
    }, "required": ["action", "message", "name", "exclusions", "set_amounts"]}}}


class Bartender:
    def __init__(self):
        self.history = []

    def respond(self, wav, session, features):
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("Taste & Tune needs an OpenRouter key")
        context = dict(session=session.snapshot(), catalog=CATALOG, features=features)
        user = {"role": "user", "content": [
            {"type": "text", "text": json.dumps(context)},
            {"type": "input_audio", "input_audio": {"format": "wav",
                "data": base64.b64encode(Path(wav).read_bytes()).decode("ascii")}}]}
        messages = [{"role": "system", "content": PROMPT}] + self.history + [user]
        for attempt in range(2):
            payload = dict(model=os.environ.get("OPENROUTER_MODEL", "google/gemini-3.8-flash"),
                messages=messages, tools=[TOOL], tool_choice={"type": "function", "function": {"name": "respond"}},
                temperature=0.2, max_tokens=1200, provider={"require_parameters": True})
            request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + key,
                "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=25) as response:
                raw = json.load(response)
            message = raw["choices"][0]["message"]
            calls = message.get("tool_calls", [])
            if len(calls) != 1 or calls[0]["function"]["name"] != "respond":
                raise ValueError("Expected one bartender response")
            decision = json.loads(calls[0]["function"]["arguments"])
            if not isinstance(decision.get("message"), str) or not 1 <= len(decision["message"]) <= 240:
                raise ValueError("Invalid bartender message")
            if not isinstance(decision.get("name"), str) or len(decision["name"]) > 48:
                raise ValueError("Invalid drink name")
            result = session.call("set_exclusions", {"channels": decision.get("exclusions", [])})
            action = decision.get("action")
            if result["ok"] and action == "propose":
                result = session.call("revise_drink", dict(expected_version=session.version,
                    set_amounts=decision["set_amounts"], name=decision["name"], explanation=decision["message"]))
            elif result["ok"] and action == "accept":
                result = session.call("finish_and_pour", {"recipe_version": session.version})
            elif result["ok"] and action == "cancel":
                result = session.call("cancel_session", {})
            elif action != "clarify" and action not in ("propose", "accept", "cancel"):
                raise ValueError("Invalid bartender action")
            if result["ok"]:
                self.history += [user, {"role": "assistant", "content": json.dumps(decision)}]
                return decision
            messages += [message, {"role": "tool", "tool_call_id": calls[0]["id"], "content": json.dumps(result)}]
        raise ValueError(result["error"])
