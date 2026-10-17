"""Human-in-the-loop dashboard (stdlib http.server). Run: python -m agentpurse.dashboard --db agentpurse.db

Hardening (this page can approve spending, so it is not an open localhost endpoint):
  * Host header must be 127.0.0.1/localhost:<port>     -> blocks DNS-rebinding
  * every /api call needs the per-run token (header X-AgentPurse-Token), embedded only in the served page
    -> a web page in another tab cannot read it (no CORS headers) and cannot send the custom header cross-site
  * POST bodies must be application/json
"""
from __future__ import annotations
import argparse, hmac, json, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import paypal, policy as P
from .ledger import Ledger
from .service import AgentPurse

HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


def make_handler(svc, token, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(b)

        def _guard(self, api=False):
            if (self.headers.get("Host") or "") not in allowed_hosts:
                self._send(403, '{"error":"bad host"}')
                return False
            if api and not hmac.compare_digest(self.headers.get("X-AgentPurse-Token") or "", token):
                self._send(403, '{"error":"missing or wrong token"}')
                return False
            return True

        def do_GET(self):
            if self.path == "/":
                if self._guard():
                    self._send(200, HTML.replace("__TOKEN__", token), "text/html")
                return
            if self.path == "/api/state":
                if not self._guard(api=True):
                    return
                rows = svc.ledger.list(50)
                for r in rows:
                    r["decision"] = json.loads(r["decision"] or "{}")
                ok, bad, n = svc.ledger.verify_audit()
                audit = [{"seq": a["seq"], "ts": a["ts"], "event": a["event"], "payment_id": a["payment_id"], "actor": a["actor"]}
                         for a in svc.ledger.audit_rows(limit=30)]
                return self._send(200, json.dumps({"budget": svc.budget(), "payments": rows, "now": svc.clock(),
                                                   "audit": {"ok": ok and not svc.ledger.verify_ledger(), "first_bad_seq": bad, "rows": n, "tail": audit}}))
            self._send(404, '{"error":"not found"}')

        def do_POST(self):
            if not self._guard(api=True):
                return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                return self._send(415, '{"error":"application/json required"}')
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400, '{"error":"bad content-length"}')
            if n < 0:
                return self._send(400, '{"error":"bad content-length"}')
            if n > 10_000:
                return self._send(413, '{"error":"too large"}')
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                pid = str(body.get("payment_id", ""))
                note = str(body.get("note", ""))
            except (ValueError, AttributeError):
                return self._send(400, '{"error":"bad json"}')
            if self.path == "/api/approve":
                return self._send(200, json.dumps(svc.human_decide(pid, True, "human:dashboard", note)))
            if self.path == "/api/reject":
                return self._send(200, json.dumps(svc.human_decide(pid, False, "human:dashboard", note)))
            if self.path == "/api/mock_payer_approve" and svc.pp.mode == "mock":
                p = svc.ledger.get(pid)
                if p and p["order_id"]:
                    svc.pp.payer_approve(p["order_id"])
                    svc.ledger.log("mock_payer_approved", pid, "mock-payer", {})
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
    token = secrets.token_urlsafe(24)
    print(f"AgentPurse dashboard: http://127.0.0.1:{a.port}  (PayPal mode: {svc.pp.mode})")
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(svc, token, a.port)).serve_forever()


if __name__ == "__main__":
    main()
