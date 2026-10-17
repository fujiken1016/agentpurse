"""Human-in-the-loop dashboard (stdlib http.server). Run: python -m agentpurse.dashboard --db agentpurse.db"""
from __future__ import annotations
import argparse, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import paypal, policy as P
from .ledger import Ledger
from .service import AgentPurse

HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


def make_handler(svc):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, HTML, "text/html")
            if self.path == "/api/state":
                rows = svc.ledger.list(50)
                for r in rows:
                    r["decision"] = json.loads(r["decision"] or "{}")
                return self._send(200, json.dumps({"budget": svc.budget(), "payments": rows}))
            self._send(404, '{"error":"not found"}')

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, '{"error":"bad json"}')
            pid = str(body.get("payment_id", ""))
            if self.path == "/api/approve":
                return self._send(200, json.dumps(svc.human_decide(pid, True)))
            if self.path == "/api/reject":
                return self._send(200, json.dumps(svc.human_decide(pid, False)))
            if self.path == "/api/mock_payer_approve" and svc.pp.mode == "mock":
                p = svc.ledger.get(pid)
                if p and p["order_id"]:
                    svc.pp.payer_approve(p["order_id"])
                    return self._send(200, '{"ok":true}')
            self._send(400, '{"error":"bad request"}')

        def log_message(self, *a):
            pass
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy")
    ap.add_argument("--db", default="agentpurse.db")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    svc = AgentPurse(paypal.from_env(a.db), Ledger(a.db), P.load_policy(a.policy))
    print(f"AgentPurse dashboard: http://127.0.0.1:{a.port}  (PayPal mode: {svc.pp.mode})")
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(svc)).serve_forever()


if __name__ == "__main__":
    main()
