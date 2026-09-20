"""Shared session constraints and physical operation recovery, no hardware."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import sample_recipe, validate
from negotiation import Session
from dispensing import Dispenser

BASE = {"name": "Test", "pours": [{"channel": 1, "ml": 50}, {"channel": 3, "ml": 10}, {"channel": 4, "ml": 50}], "ml_total": 110}


class SessionTests(unittest.TestCase):
    def test_exclusion_survives_failed_edit_and_blocks_final(self):
        s = Session(); s.begin_turn(BASE)
        args = dict(expected_version=0, name="Test", explanation="Test", set_amounts=[])
        self.assertTrue(s.call("revise_drink", args)["ok"])
        s.call("set_exclusions", {"channels": [3]})
        self.assertFalse(s.confirm_final()["ok"])
        s.record_sample(simulated=True); s.begin_turn(BASE)
        args.update(expected_version=1, set_amounts=[{"channel": 3, "ml": 0}])
        self.assertTrue(s.call("revise_drink", args)["ok"])
        self.assertEqual(s.recipe["ml_total"], 100)
        self.assertNotIn(3, [p["channel"] for p in s.recipe["pours"]])

    def test_sample_is_eight_percent_per_ingredient(self):
        sample = sample_recipe(BASE)
        self.assertEqual([p["ml"] for p in sample["pours"]], [4, .8, 4])
        self.assertEqual(sample["ml_total"], 8.8)

    def test_deployed_limits(self):
        for pours in ([{"channel": 1, "ml": 60}, {"channel": 3, "ml": 21}],
                      [{"channel": 1, "ml": 60}, {"channel": 4, "ml": 60}, {"channel": 6, "ml": 20}],
                      [{"channel": 1, "ml": float("nan")}, {"channel": 4, "ml": 30}]):
            with self.assertRaises(ValueError): validate(dict(name="Test", pours=pours))


class DispensingTests(unittest.TestCase):
    def board(self):
        class Board:
            calibrated = True
            rates = [5] * 6
            speed = 1
            def __init__(self):
                self._get = Mock(return_value={"ok": True})
                self.all_off = Mock()
                self.make = Mock()
        return Board()

    def test_duplicate_sample_does_not_run_again(self):
        with tempfile.TemporaryDirectory() as folder:
            board = self.board(); d = Dispenser(board, folder)
            first = d.execute("abc", 1, "sample", BASE)
            self.assertEqual(first["pulses_ms"], [800, 160, 800])
            d.execute("abc", 1, "sample", BASE)
            self.assertEqual(board._get.call_count, 3)
            self.assertEqual(BASE["ml_total"], 110)

    def test_uncertain_pour_blocks_replay_and_new_session(self):
        with tempfile.TemporaryDirectory() as folder:
            board = self.board(); board._get.side_effect = TimeoutError()
            d = Dispenser(board, folder)
            with self.assertRaises(TimeoutError): d.execute("abc", 1, "sample", BASE)
            board.all_off.assert_called_once()
            d = Dispenser(board, folder)
            with self.assertRaises(RuntimeError): d.execute("def", 1, "final", BASE)
            board.make.assert_not_called()

    def test_restart_marks_started_operation_uncertain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"old.json"
            path.write_text(json.dumps({"status": "started"}))
            Dispenser(self.board(), folder)
            self.assertEqual(json.loads(path.read_text())["status"], "uncertain")

    def test_final_uses_existing_make_and_keeps_actual_amounts(self):
        with tempfile.TemporaryDirectory() as folder:
            board = self.board()
            board.make.side_effect = lambda r, **kw: r.update(poured_total_ml=109.2)
            event = Dispenser(board, folder).execute("abc", 1, "final", BASE)
            board.make.assert_called_once()
            self.assertEqual(event["recipe"]["poured_total_ml"], 109.2)


if __name__ == "__main__": unittest.main()
