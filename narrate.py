"""One sentence, written fresh for this guest.

local_bartender decides the drink -- the channels, the millilitres, the name.
That stays arithmetic: deterministic, offline, ~0 ms, and it is what pours.
This file only rewrites the SENTENCE said about it, because _rationale()
draws from eight phrases by six moods, and sixty guests in one night will
hear the same line come round more than once.

    line(recipe, feats, words, ingredients) -> str

Never raises, never blocks for long: no key, no network, a slow reply or a
strange one, and you get recipe["rationale"] back unchanged. Standard
library only. Set MIXMIND_LLM=off to force the template.
"""
import json, os, re, urllib.error, urllib.request
import env  # noqa: F401  -- loads ~/.mixmind.env

# Measured at 2.3 s on a phone hotspot, so 2.5 was no margin at all. The
# guest is reading the drink's name while this runs, so it is not dead time.
TIMEOUT_S = float(os.environ.get("MIXMIND_LLM_TIMEOUT", "5"))
MODEL_A   = os.environ.get("MIXMIND_LLM_MODEL", "claude-sonnet-4-5")
MODEL_O   = os.environ.get("MIXMIND_LLM_MODEL_OPENAI", "gpt-4o-mini")
MAX_WORDS = int(os.environ.get("MIXMIND_LINE_WORDS", "26"))
# ElevenLabs bills per character, so this number is a budget as well as a
# style choice: ~26 words is about 140 characters a guest.
LAST_ERROR = ""

BRIEF = (
    "You are the voice of MixMind: a machine that listens to how someone sounds "
    "-- loudness, pace, pauses, pitch movement -- and pours them a soft drink to "
    "match. You speak one short line aloud as the drink pours.\n"
    "Rules:\n"
    "- The recipe is already decided. Never change, question or list the amounts.\n"
    "- Mention at most one ingredient, by the name given, or none.\n"
    "- Say what you heard in their voice, then the drink that answers it.\n"
    "- Under %d words. One or two sentences. Spoken English, no markdown, no "
    "emoji, no lists, no stage directions.\n"
    "- Warm and dry, like a bartender who has been on shift a while. Never perky.\n"
    "- No questions -- they cannot reply to you.\n"
    "- This is a party trick, not a diagnosis. Never suggest how they feel is a "
    "problem, never give advice about rest, health, mood or wellbeing, and never "
    "use clinical words. If the voice read low, be light about it.\n"
    "Return the line only." % MAX_WORDS
)


def _facts(recipe, feats, words, ingredients):
    picked = ", ".join("%s (%d ml)" % (ingredients.get(str(p["channel"]),
                                       "pump %d" % p["channel"]), p["ml"])
                       for p in recipe["pours"])
    said = (words or {}).get("transcript") or ""
    return (
        "Voice: %s. Loudness %.0f dB, %.2f syllables per second, pauses %.0f%% of "
        "the time, pitch moves %.0f Hz, they talked for %.0f seconds.\n"
        "They said: \"%s\"\n"
        "Drink: \"%s\" -- %s.\n"
        "The template line, for tone only -- write a different one: %s"
        % (recipe["mood"], feats.get("loudness_db", -25), feats.get("onset_rate_hz", 1.2),
           100 * feats.get("pause_ratio", 0.05), feats.get("pitch_sd_hz", 22),
           feats.get("duration_s", 5), said[:300].replace('"', "'"),
           recipe["name"], picked, recipe.get("rationale", ""))
    )


def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=dict(headers, **{"Content-Type": "application/json"}))
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def _anthropic(key, facts):
    out = _post("https://api.anthropic.com/v1/messages",
                {"x-api-key": key, "anthropic-version": "2023-06-01"},
                {"model": MODEL_A, "max_tokens": 120, "temperature": 0.9,
                 "system": BRIEF, "messages": [{"role": "user", "content": facts}]})
    return "".join(b.get("text", "") for b in out.get("content", []))


def _openai(key, facts):
    out = _post("https://api.openai.com/v1/chat/completions",
                {"Authorization": "Bearer " + key},
                {"model": MODEL_O, "temperature": 0.9, "max_tokens": 120,
                 "messages": [{"role": "system", "content": BRIEF},
                              {"role": "user", "content": facts}]})
    return out["choices"][0]["message"]["content"]


def _clean(text):
    """A spoken line or nothing. Anything odd falls back to the template."""
    t = " ".join((text or "").split())
    t = t.strip().strip('"').strip("*").strip()
    t = re.sub(r"^(Line|Response|Answer)\s*:\s*", "", t, flags=re.I)
    if not t or len(t.split()) > MAX_WORDS + 8 or len(t) > 260:
        return ""
    if "\n" in t or t.startswith(("-", "#", "{")) or "http" in t:
        return ""
    return t


def line(recipe, feats, words=None, ingredients=None):
    """The spoken rationale. Falls back to recipe['rationale'], always."""
    global LAST_ERROR
    fallback = recipe.get("rationale", "")
    if os.environ.get("MIXMIND_LLM", "").lower() in ("off", "0", "no"):
        return fallback
    facts = _facts(recipe, feats, words or {}, ingredients or {})
    for env, fn in (("ANTHROPIC_API_KEY", _anthropic), ("OPENAI_API_KEY", _openai)):
        key = os.environ.get(env, "").strip()
        if not key:
            continue
        try:
            got = _clean(fn(key, facts))
            if got:
                LAST_ERROR = ""
                return got
            LAST_ERROR = "reply did not look like a spoken line"
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
            LAST_ERROR = "%s: %s" % (env.split("_")[0].lower(), e)
    return fallback


def available():
    if os.environ.get("MIXMIND_LLM", "").lower() in ("off", "0", "no"):
        return "off (template lines)"
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return MODEL_A
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return MODEL_O
    return "no key -- template lines"


if __name__ == "__main__":
    import local_bartender
    f = {"loudness_db": -19.0, "onset_rate_hz": 1.9, "pitch_sd_hz": 25.0,
         "pause_ratio": 0.03, "duration_s": 7.0, "pitch_mean_hz": 160.0}
    w = {"valence": -0.2, "says_okay": True, "transcript": "yeah no I'm fine, just a long week"}
    r = local_bartender.recipe(f, w)
    ing = {"1": "orange", "2": "cranberry", "3": "lime", "4": "ginger ale",
           "5": "grape", "6": "apple"}
    print("narrator: %s\n" % available())
    print("template: %s" % r["rationale"])
    for i in range(3):
        print("    llm %d: %s" % (i + 1, line(r, f, w, ing)))
    if LAST_ERROR:
        print("\nlast error: %s" % LAST_ERROR)
