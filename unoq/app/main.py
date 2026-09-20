"""MixMind on the Arduino UNO Q -- the Linux (Qualcomm) half.

Runs as the App Lab app's Python side. It does one thing: turn HTTP from the
Raspberry Pi into Bridge calls to the sketch on the STM32.

    GET /pour?ch=1-6&ms=0-30000     run that pump for that long
    GET /pour_to?ch=&dg=&maxms=     run it until the cup gains dg tenths of
                                    a gram  -> {"dg": <actually delivered>}
                                    {"ok":false,"error":"no scale"} if the
                                    HX711 is absent or uncalibrated
    GET /weigh                      -> {"dg": <tenths of a gram>}
    GET /tare                       this is now zero
    GET /scale?cpdg=<int>           counts per tenth of a gram (calibration)
    GET /raw                        averaged raw counts (calibration)
    GET /stop                       every channel off, now
    -> {"ok": true, ...}  or  {"ok": false, "error": "..."}

The App Lab container publishes no ports, so a socat forwarder on the host
maps :8081 to this :8080 -- see mixmind.sh and the system handover.
"""
from arduino.app_utils import App, Bridge
import http.server, urllib.parse, threading


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        extra = {}
        try:
            if u.path == "/pour":
                Bridge.call("pour", int(q["ch"][0]), int(q["ms"][0]))
            elif u.path == "/pour_to":
                dg = Bridge.call("pourTo", int(q["ch"][0]), int(q["dg"][0]),
                                 int(q.get("maxms", [30000])[0]))
                if dg == -2:
                    raise ValueError("no scale")
                if dg < 0:
                    raise ValueError("refused")
                extra["dg"] = dg
            elif u.path == "/weigh":
                dg = Bridge.call("lcWeigh")
                if dg == -1:
                    raise ValueError("no scale")
                extra["dg"] = dg
            elif u.path == "/tare":
                if Bridge.call("lcTare") != 0:
                    raise ValueError("no scale")
            elif u.path == "/scale":
                extra["cpdg"] = Bridge.call("lcScale", int(q["cpdg"][0]))
            elif u.path == "/raw":
                raw = Bridge.call("lcRaw")
                if raw == 2147483647:
                    raise ValueError("HX711 not answering -- check VCC on 3.3V, "
                                     "DT on D9, SCK on D10")
                extra["raw"] = raw
            elif u.path == "/stop":
                Bridge.call("stop")
            else:
                raise ValueError("unknown path")
            body = '{"ok":true' + "".join(',"%s":%d' % kv for kv in extra.items()) + "}"
        except Exception as e:
            body = '{"ok":false,"error":"%s"}' % e
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def serve():
    http.server.HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


threading.Thread(target=serve, daemon=True).start()
print("MixMind pour server on 8080 (pumps + scale)")
App.run()
