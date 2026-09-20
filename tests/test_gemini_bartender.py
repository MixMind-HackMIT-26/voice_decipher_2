"""Provider contract checks without network access."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemini_bartender import Bartender
from negotiation import Session

BASE = dict(name="Test", pours=[dict(channel=1, ml=50), dict(channel=4, ml=50)])


def response(action, edits=None, exclusions=None):
    args = dict(action=action, message="Would you prefer more citrus?", name="Test",
                set_amounts=edits or [], exclusions=exclusions or [])
    return io.BytesIO(json.dumps({"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call1", "type": "function", "function": {"name": "respond", "arguments": json.dumps(args)}}]}}]}).encode())


class ProviderTests(unittest.TestCase):
    def run_response(self, responses):
        s = Session(); s.begin_turn(BASE)
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder)/"audio.wav"; wav.write_bytes(b"audio")
            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}), patch("urllib.request.urlopen", side_effect=responses) as request:
                result = Bartender().respond(wav, s, {})
                payload = json.loads(request.call_args[0][0].data)
        return s, result, payload

    def test_clarification_does_not_commit(self):
        s, result, payload = self.run_response([response("clarify")])
        self.assertEqual(s.version, 0)
        self.assertEqual(s.ledger, [])
        audio = payload["messages"][-1]["content"][1]["input_audio"]
        self.assertEqual(audio["format"], "wav")

    def test_exclusion_rejects_recipe_until_corrected(self):
        s, result, payload = self.run_response([
            response("propose", exclusions=[4]),
            response("propose", [dict(channel=4, ml=0), dict(channel=6, ml=50)])])
        self.assertEqual(s.version, 1)
        self.assertEqual(s.excluded, {4})
        self.assertEqual([p["channel"] for p in s.recipe["pours"]], [1, 6])
        self.assertEqual(payload["messages"][-1]["role"], "tool")


if __name__ == "__main__": unittest.main()
