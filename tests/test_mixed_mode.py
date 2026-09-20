"""Exercise kiosk actions and recipe revisions with mock audio/model/board."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import uno_q

FEATS = dict(pitch_mean_hz=120, pitch_sd_hz=23, loudness_db=-25,
             pause_ratio=.07, onset_rate_hz=1.2, jitter_pct=1, shimmer_pct=2, duration_s=8)


class KioskTests(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        self.env = patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"})
        self.env.start()
        self.machine = server.Machine(uno_q.MockUnoQ(speed=1000), voice=False, timings=(0, 0, 0))

    def tearDown(self):
        self.env.stop()
        os.chdir(self.old)
        self.tmp.cleanup()

    def wait(self):
        self.machine.worker.join(5)
        self.assertFalse(self.machine.worker.is_alive())
        return json.loads(self.machine.snapshot())

    def audio(self, **kw):
        path = Path(self.tmp.name)/"speech.wav"
        path.write_bytes(b"mock audio")
        return str(path)

    def proposal(self, client, wav, session, feats):
        result = session.call("revise_drink", dict(expected_version=session.version,
            name="Test mix", explanation="A bright balanced mix.", set_amounts=[]))
        self.assertTrue(result["ok"], result)
        return dict(action="propose", message="A bright balanced mix.")

    def test_complete_mixed_flow_and_stale_actions(self):
        with patch("mixed_mode.listen.record", side_effect=self.audio), patch("mixed_mode.features.extract", return_value=FEATS), patch("mixed_mode.Bartender.respond", autospec=True, side_effect=self.proposal):
            self.assertTrue(self.machine.start("mixed"))
            state = self.wait()
            self.assertEqual(state["state"], "sample_ready")
            sid = state["session_id"]
            self.assertFalse(self.machine.start("quick"))
            self.assertFalse(self.machine.action("sample", "stale", 1))
            self.assertTrue(self.machine.action("sample", sid, 1)); self.wait()
            count = len(self.machine.board.sent)
            self.assertFalse(self.machine.action("sample", sid, 1))
            self.assertTrue(self.machine.action("feedback", sid, 1)); state = self.wait()
            self.assertEqual(state["version"], 2)
            self.assertFalse(self.machine.action("sample", sid, 1))
            self.assertTrue(self.machine.action("sample", sid, 2)); self.wait()
            self.assertGreater(len(self.machine.board.sent), count)
            self.assertTrue(self.machine.action("finish", sid, 2)); self.wait()
            self.assertTrue(self.machine.action("pour", sid, 2)); state = self.wait()
            self.assertEqual(state["state"], "idle")
            events = [json.loads(p.read_text()) for p in Path("logs/dispensing").glob("*.json")]
            self.assertEqual(sorted(e["kind"] for e in events), ["final", "sample", "sample"])

    def test_api_failure_preserves_recipe_and_cancel_pours_nothing(self):
        with patch("mixed_mode.listen.record", side_effect=self.audio), patch("mixed_mode.features.extract", return_value=FEATS), patch("mixed_mode.Bartender.respond", autospec=True, side_effect=self.proposal):
            self.machine.start("mixed"); state = self.wait()
            sid = state["session_id"]
            self.machine.action("sample", sid, 1); self.wait()
        before = list(self.machine.board.sent)
        with patch("mixed_mode.listen.record", side_effect=self.audio), patch("mixed_mode.Bartender.respond", side_effect=TimeoutError):
            self.machine.action("feedback", sid, 1); state = self.wait()
        self.assertEqual(state["version"], 1)
        self.assertIn("finish", state["allowed_actions"])
        self.machine.action("cancel", sid, 1); self.wait()
        self.assertEqual(self.machine.board.sent, before)
        self.assertEqual(self.machine.mixed.session.status, "cancelled")


if __name__ == "__main__": unittest.main()
