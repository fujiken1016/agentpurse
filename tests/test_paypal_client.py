"""PayPalClient against a fake transport: proves the sandbox wiring works the moment keys are set."""
import io, json, os, unittest, urllib.error
from unittest import mock
from agentpurse import paypal
from agentpurse.paypal import PayPalClient, PayPalError
from agentpurse.service import AgentPurse
from agentpurse.ledger import Ledger
from helpers import req, policy


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass


class Fake:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, request, timeout=None):
        body = request.data.decode() if request.data else ""
        self.calls.append((request.get_method(), request.full_url, dict(request.header_items()), body))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeResp(json.dumps(r).encode())


TOKEN = {"access_token": "T1", "expires_in": 3200}
ORDER = {"id": "5O190127TN364715T", "status": "PAYER_ACTION_REQUIRED",
         "links": [{"rel": "self", "href": "x"}, {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=5O19"}]}
CAPTURE = {"id": "5O190127TN364715T", "status": "COMPLETED",
           "purchase_units": [{"payments": {"captures": [{"id": "CAP1", "status": "COMPLETED"}]}}]}


class Client(unittest.TestCase):
    def test_refuses_anything_but_the_sandbox_host(self):
        for base in ("https://api-m.paypal.com", "https://evil.example/sandbox", "https://api-m.sandbox.paypal.com.evil.example",
                     "http://api-m.sandbox.paypal.com", "https://sandbox.evil.example", ""):
            with self.assertRaises(PayPalError, msg=base):
                PayPalClient("a", "b", base=base)
        PayPalClient("a", "b")

    def test_create_order_request_shape(self):
        f = Fake([TOKEN, ORDER])
        with mock.patch("urllib.request.urlopen", f):
            o = PayPalClient("id", "secret").create_order("pay_abc", "12.50", "USD", "API credits")
        self.assertEqual(o["approve_url"], "https://www.sandbox.paypal.com/checkoutnow?token=5O19")
        m, url, h, body = f.calls[0]
        self.assertTrue(h["Authorization"].startswith("Basic "))
        self.assertIn("grant_type=client_credentials", body)
        m, url, h, body = f.calls[1]
        self.assertEqual((m, url), ("POST", "https://api-m.sandbox.paypal.com/v2/checkout/orders"))
        self.assertEqual(h["Authorization"], "Bearer T1")
        self.assertEqual(h["Paypal-request-id"], "pay_abc")        # idempotent create
        b = json.loads(body)
        self.assertEqual(b["intent"], "CAPTURE")
        self.assertEqual(b["purchase_units"][0]["amount"], {"currency_code": "USD", "value": "12.50"})
        self.assertEqual(b["payment_source"]["paypal"]["experience_context"]["user_action"], "PAY_NOW")

    def test_capture_and_pending_capture_is_not_success(self):
        f = Fake([TOKEN, CAPTURE])
        with mock.patch("urllib.request.urlopen", f):
            c = PayPalClient("i", "s").capture("5O1")
        self.assertEqual(c["capture_id"], "CAP1")
        self.assertEqual(f.calls[1][2]["Paypal-request-id"], "cap-5O1")
        pending = json.loads(json.dumps(CAPTURE))
        pending["purchase_units"][0]["payments"]["captures"][0]["status"] = "PENDING"
        with mock.patch("urllib.request.urlopen", Fake([TOKEN, pending])):
            with self.assertRaises(PayPalError):
                PayPalClient("i", "s").capture("5O1")
        with mock.patch("urllib.request.urlopen", Fake([TOKEN, {"id": "x", "status": "APPROVED"}])):
            with self.assertRaises(PayPalError):
                PayPalClient("i", "s").capture("5O1")

    def test_expired_token_is_refreshed_once(self):
        e401 = urllib.error.HTTPError("u", 401, "unauth", {}, io.BytesIO(b"{}"))
        f = Fake([TOKEN, e401, {"access_token": "T2"}, ORDER])
        with mock.patch("urllib.request.urlopen", f):
            PayPalClient("i", "s").create_order("p", "1.00", "USD", "d")
        self.assertEqual(f.calls[3][2]["Authorization"], "Bearer T2")

    def test_http_and_network_errors_become_paypalerror(self):
        e422 = urllib.error.HTTPError("u", 422, "x", {}, io.BytesIO(b'{"name":"UNPROCESSABLE_ENTITY"}'))
        with mock.patch("urllib.request.urlopen", Fake([TOKEN, e422])):
            with self.assertRaises(PayPalError) as cm:
                PayPalClient("i", "s").capture("o")
        self.assertIn("422", str(cm.exception))
        with mock.patch("urllib.request.urlopen", Fake([urllib.error.URLError("dns")])):
            with self.assertRaises(PayPalError):
                PayPalClient("i", "s").get_order("o")

    def test_error_through_service_marks_failed(self):
        e = urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(b"bad"))
        with mock.patch("urllib.request.urlopen", Fake([TOKEN, e])):
            svc = AgentPurse(PayPalClient("i", "s"), Ledger(), policy())
            self.assertEqual(svc.propose(req())["status"], "FAILED")


class FromEnv(unittest.TestCase):
    def test_mock_without_keys_real_with_keys(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paypal.from_env().mode, "mock")
        with mock.patch.dict(os.environ, {"PAYPAL_CLIENT_ID": "a", "PAYPAL_CLIENT_SECRET": "b"}, clear=True):
            self.assertEqual(paypal.from_env().mode, "sandbox")
        with mock.patch.dict(os.environ, {"PAYPAL_CLIENT_ID": "a"}, clear=True):
            self.assertEqual(paypal.from_env().mode, "mock")   # half-configured never goes live

    def test_live_base_url_env_is_refused(self):
        env = {"PAYPAL_CLIENT_ID": "a", "PAYPAL_CLIENT_SECRET": "b", "PAYPAL_BASE_URL": "https://api-m.paypal.com"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(PayPalError):
                paypal.from_env()

    def test_return_urls_from_env(self):
        env = {"PAYPAL_RETURN_URL": "https://r.test/ok", "PAYPAL_CANCEL_URL": "https://r.test/no"}
        with mock.patch.dict(os.environ, env):
            c = PayPalClient("a", "b")
        self.assertEqual((c.return_url, c.cancel_url), ("https://r.test/ok", "https://r.test/no"))


class Mock(unittest.TestCase):
    def test_mock_persists_across_instances(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "m.db")
        a, b = paypal.MockPayPal(path), paypal.MockPayPal(path)
        oid = a.create_order("p", "1.00", "USD", "d")["order_id"]
        b.payer_approve(oid)
        self.assertEqual(a.capture(oid)["status"], "COMPLETED")
        with self.assertRaises(PayPalError):
            a.get_order("NOPE")


if __name__ == "__main__":
    unittest.main()
