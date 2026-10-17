"""PayPal Orders v2 client (sandbox) with an offline mock that mimics the same response shapes."""
from __future__ import annotations
import base64, json, os, uuid, urllib.parse, urllib.request, urllib.error

SANDBOX = "https://api-m.sandbox.paypal.com"


class PayPalError(Exception):
    pass


class PayPalClient:
    """Real client. Needs PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET of a *sandbox* REST app."""
    mode = "sandbox"

    def __init__(self, client_id, secret, base=SANDBOX):
        host = urllib.parse.urlparse(base)
        # exact host match: "https://evil.example/sandbox" or "api-m.sandbox.paypal.com.evil.example" must not pass
        if host.scheme != "https" or host.hostname not in ("api-m.sandbox.paypal.com", "api.sandbox.paypal.com"):
            raise PayPalError("AgentPurse refuses to run against a non-sandbox PayPal host")
        self.cid, self.secret, self.base, self._tok = client_id, secret, base.rstrip("/"), None
        self.return_url = os.environ.get("PAYPAL_RETURN_URL", "https://example.com/return")
        self.cancel_url = os.environ.get("PAYPAL_CANCEL_URL", "https://example.com/cancel")

    def _call(self, method, path, body=None, headers=None, form=None, auth=None, _retry=True):
        h = {"Accept": "application/json"}
        h.update(headers or {})
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h["Authorization"] = auth or f"Bearer {self._token()}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 401 and _retry and auth is None:  # token expired: fetch a new one once
                self._tok = None
                return self._call(method, path, body, headers, form, auth, _retry=False)
            raise PayPalError(f"{e.code} {e.read().decode()[:300]}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PayPalError(f"network error talking to PayPal: {e}")

    def _token(self):
        if not self._tok:
            basic = base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()
            r = self._call("POST", "/v1/oauth2/token", form={"grant_type": "client_credentials"},
                           auth=f"Basic {basic}")
            self._tok = r["access_token"]
        return self._tok

    def create_order(self, ref, amount, currency, description):
        # PayPal-Request-Id = our payment id: a retried HTTP call can never create a second order
        body = {"intent": "CAPTURE",
                "purchase_units": [{"reference_id": ref, "custom_id": ref, "description": description[:127],
                                    "amount": {"currency_code": currency, "value": str(amount)}}],
                "payment_source": {"paypal": {"experience_context": {
                    "return_url": self.return_url, "cancel_url": self.cancel_url,
                    "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING"}}}}
        r = self._call("POST", "/v2/checkout/orders", body, {"PayPal-Request-Id": ref})
        link = next((l["href"] for l in r.get("links", []) if l["rel"] in ("payer-action", "approve")), None)
        return {"order_id": r["id"], "status": r["status"], "approve_url": link}

    def get_order(self, order_id):
        r = self._call("GET", f"/v2/checkout/orders/{order_id}")
        return {"order_id": r["id"], "status": r["status"]}

    def capture(self, order_id):
        r = self._call("POST", f"/v2/checkout/orders/{order_id}/capture", {},
                       {"PayPal-Request-Id": "cap-" + order_id})
        try:
            cap = r["purchase_units"][0]["payments"]["captures"][0]
        except (KeyError, IndexError, TypeError):
            raise PayPalError(f"capture response had no capture (order status {r.get('status')})")
        if cap.get("status", "COMPLETED") != "COMPLETED":  # e.g. PENDING review: do not report money as moved
            raise PayPalError(f"capture is {cap.get('status')}, not COMPLETED")
        return {"order_id": r["id"], "status": r["status"], "capture_id": cap["id"]}


class MockPayPal:
    """Offline stand-in with the same method shapes, persisted in SQLite so the MCP server and the dashboard
    (separate processes) see the same orders. `payer_approve` plays the human payer."""
    mode = "mock"

    def __init__(self, path=":memory:"):
        import sqlite3
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("create table if not exists mock_orders(id text primary key, status text, amount text)")
        self.db.commit()

    def count(self):
        return self.db.execute("select count(*) from mock_orders").fetchone()[0]

    def _status(self, oid):
        r = self.db.execute("select status from mock_orders where id=?", (oid,)).fetchone()
        if not r:
            raise PayPalError("RESOURCE_NOT_FOUND")
        return r[0]

    def _set(self, oid, st):
        self.db.execute("update mock_orders set status=? where id=?", (st, oid))
        self.db.commit()

    def create_order(self, ref, amount, currency, description):
        oid = "MOCK" + uuid.uuid4().hex[:12].upper()
        self.db.execute("insert into mock_orders values(?,?,?)", (oid, "PAYER_ACTION_REQUIRED", str(amount)))
        self.db.commit()
        return {"order_id": oid, "status": "PAYER_ACTION_REQUIRED",
                "approve_url": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}

    def payer_approve(self, oid):
        self._status(oid)
        self._set(oid, "APPROVED")

    def get_order(self, oid):
        return {"order_id": oid, "status": self._status(oid)}

    def capture(self, oid):
        if self._status(oid) != "APPROVED":
            raise PayPalError("UNPROCESSABLE_ENTITY ORDER_NOT_APPROVED: payer has not approved the order")
        self._set(oid, "COMPLETED")
        return {"order_id": oid, "status": "COMPLETED", "capture_id": "CAP" + oid[4:]}


def from_env(mock_db=":memory:"):
    """Real sandbox client when PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are set, otherwise the offline mock."""
    cid, sec = os.environ.get("PAYPAL_CLIENT_ID"), os.environ.get("PAYPAL_CLIENT_SECRET")
    if cid and sec:
        return PayPalClient(cid, sec, os.environ.get("PAYPAL_BASE_URL", SANDBOX))
    return MockPayPal(mock_db)
