"""OpenRouter audio negotiation, independent of existing narration/STT providers."""
import base64
import json
import os
from pathlib import Path
import urllib.request
from catalog import CATALOG

PROMPT = """You are MixMind, one warm, concise bartender. Hear the guest's audio.
Use the initial acoustic recipe as a starting point; subsequent feedback edits the
CURRENT recipe, never a new acoustic baseline. Explicit preferences and corrections
outrank inferred mood. Acoustic measurements do not prove emotions.
Call respond once. action is propose, clarify, accept, or cancel. For ambiguity such
as 'more interesting', ask a specific short question without changing the recipe.
'Surprise me' permits discretion. exclusions contains newly stated excluded channel
numbers; these persist. Never reintroduce exclusions. Ingredients are the supplied
physical catalog, not an imagined menu. Use 2-6 ingredients, integer 10-60 ml doses,
lime cordial at most 20 ml, total at most 130 ml. Keep volume stable unless requested.
set_amounts are absolute final-serving ml; zero removes an ingredient; omitted
channels stay unchanged. There are three proposals including the first. When none
remain, offer acceptance or cancellation, not another revision. Never accept in the
same turn as a new proposal. message is at most two short sentences, under 35 words
and 240 characters. Drink names must fit within 48 characters.
Introduce the whole first drink; describe later changes relative to the sampled
drink. Do not claim liquid was dispensed. The proposal is shown BEFORE dispensing:
ask whether the proposed balance sounds right, never ask how it tastes yet. Do not
explain internal algorithms. Accept only explicit acceptance of an existing recipe.
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
