"""HTTP mode selection must never silently route Taste & Tune to Quick Mix."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import uno_q


class ModeRoutingTests(unittest.TestCase):
    def test_dedicated_endpoint_forces_mixed_even_without_body(self):
        machine = Mock()
        machine.start.return_value = True
        with tempfile.TemporaryDirectory() as ui:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(machine, ui))
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                for path, body, expected in [
                    ("/api/start/mixed", None, "mixed"),
                    ("/api/start/mixed", {"mode": "quick"}, "mixed"),
                    ("/api/start", None, "quick"),
                    ("/api/start", {"mode": "mixed"}, "mixed"),
                ]:
                    req = urllib.request.Request("http://127.0.0.1:%d%s" % (httpd.server_port, path),
                        data=json.dumps(body).encode() if body is not None else None, method="POST")
                    with urllib.request.urlopen(req) as response:
                        self.assertEqual(json.load(response), {"ok": True, "mode": expected})
                    machine.start.assert_called_with(expected)
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join()

    def test_mixed_launch_calls_conversation_not_quick_worker(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test"}), patch("server.MixedMode") as mixed:
            machine = server.Machine(uno_q.MockUnoQ(), voice=False)
            machine._guest = Mock()
            self.assertTrue(machine.start("mixed"))
            machine.worker.join(2)
            mixed.return_value.turn.assert_called_once_with(initial=True)
            machine._guest.assert_not_called()


if __name__ == "__main__": unittest.main()
