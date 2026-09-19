"""What their words say -- read offline, next to how their voice sounded.

Two signals, deliberately different in how much they are trusted:

  says_okay  did they claim to be fine? "I'm fine", "all good", "not a big
             deal"... A narrow phrase match, and reliable. Paired with a
             drained voice this is the demo: the words say fine, the voice
             does not.
  valence    -1..1, VADER sentiment over the whole transcript. Only a gentle
             nudge: on our own scripts it scored "it's been a long day...
             nonstop" as +0.66 and "I'm fine, really" as -0.24.

Online, an LLM reads the transcript far better; this is what still works
when the hotspot drops.
"""
import re

_OKAY = re.compile(
    r"\b(?:i'?m|i am|it'?s|everything'?s|all|doing|feeling)\s+"
    r"(?:really\s+|totally\s+|pretty\s+|just\s+)?"
    r"(?:fine|okay|ok|alright|all right|good|great)\b"
    r"|\bnot a big deal\b|\bno big deal\b|\bit'?s nothing\b|\bdon'?t worry\b",
    re.I)
_NEGATED = re.compile(r"\b(?:not|never|n't)\s+(?:really\s+)?(?:fine|okay|ok|alright|good|great)\b", re.I)

_vader = None


def _valence(text):
    global _vader
    try:
        if _vader is None:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _vader = SentimentIntensityAnalyzer()
        return round(_vader.polarity_scores(text)["compound"], 2)
    except Exception:
        return 0.0                              # no package: neutral, never broken


def analyze(text, talk_s=0.0):
    text = (text or "").strip()
    words = len(re.findall(r"[A-Za-z']+", text))
    return dict(
        transcript=text,
        words=words,
        words_per_min=round(words / talk_s * 60, 1) if talk_s > 0 else 0.0,
        says_okay=bool(_OKAY.search(text)) and not _NEGATED.search(text),
        valence=_valence(text) if words else 0.0,
    )
