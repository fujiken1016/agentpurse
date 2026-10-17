"""Dashboard: the page that approves spending must not be drivable by a random web page."""
import http.client, json, threading, unittest
from http.server import ThreadingHTTPServer
from agentpurse import dashboard
from helpers import req, make

TOKEN = "t0ken"


class Dash(unittest.TestCase):
    def setUp(self):
        self.svc, self.pp, self.t = make()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.srv.server_address[1]
        self.srv.RequestHandlerClass = dashboard.make_handler(self.svc, TOKEN, self.port)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read().decode()
        return r.status, data

    J = {"Content-Type": "application/json", "X-AgentPurse-Token": TOKEN}

    def test_page_embeds_token_and_state_requires_it(self):
        code, html = self.call("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(TOKEN, html)
        self.assertNotIn("__TOKEN__", html)
        self.assertEqual(self.call("GET", "/api/state")[0], 403)
        self.assertEqual(self.call("GET", "/api/state", headers={"X-AgentPurse-Token": "wrong"})[0], 403)
        code, data = self.call("GET", "/api/state", headers=self.J)
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(data)["audit"]["ok"])

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(self.call("GET", "/", host="evil.example:80")[0], 403)
        self.assertEqual(self.call("GET", "/api/state", headers=self.J, host="evil.example")[0], 403)

    def test_post_needs_token_and_json(self):
        r = self.svc.propose(req(amount="30.00"))
        pid = r["payment_id"]
        self.assertEqual(self.call("POST", "/api/approve", {"payment_id": pid})[0], 403)
        bad_ct = dict(self.J, **{"Content-Type": "text/plain"})
        self.assertEqual(self.call("POST", "/api/approve", {"payment_id": pid}, bad_ct)[0], 415)
        self.assertEqual(self.pp.count(), 0)

    def test_approve_and_reject_with_note(self):
        a = self.svc.propose(req(amount="30.00", description="a"))["payment_id"]
        b = self.svc.propose(req(amount="30.00", description="b"))["payment_id"]
        code, out = self.call("POST", "/api/approve", {"payment_id": a, "note": "ok by me"}, self.J)
        self.assertEqual(json.loads(out)["status"], "ORDER_CREATED")
        code, out = self.call("POST", "/api/reject", {"payment_id": b}, self.J)
        self.assertEqual(json.loads(out)["status"], "REJECTED")
        row = [x for x in self.svc.ledger.audit_rows(a) if x["event"] == "human_approved"][0]
        self.assertEqual(row["actor"], "human:dashboard")
        self.assertIn("ok by me", row["detail"])

    def test_bad_json_and_oversize(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/api/approve", body=b"{nope", headers=dict(self.J, Host=f"127.0.0.1:{self.port}"))
        self.assertEqual(c.getresponse().status, 400)
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/api/approve", body=b"x" * 20000, headers=dict(self.J, Host=f"127.0.0.1:{self.port}"))
        self.assertEqual(c.getresponse().status, 413)

    def test_mock_payer_flow_and_audit(self):
        pid = self.svc.propose(req())["payment_id"]
        self.assertEqual(self.call("POST", "/api/mock_payer_approve", {"payment_id": pid}, self.J)[0], 200)
        self.assertEqual(self.svc.capture(pid)["status"], "CAPTURED")
        self.assertTrue(self.svc.ledger.verify_audit()[0])

    def test_html_has_no_unescaped_sinks(self):
        html = dashboard.HTML
        self.assertIn("const esc=", html)
        self.assertNotIn("eval(", html)

    def test_security_headers(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", "/", headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))


if __name__ == "__main__":
    unittest.main()
