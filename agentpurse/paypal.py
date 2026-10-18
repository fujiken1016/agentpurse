"""PayPal Orders v2 client (sandbox) with an offline mock that mimics the same response shapes."""
from __future__ import annotations
import base64, json, os, uuid, urllib.parse, urllib.request, urllib.error

SANDBOX = "https://api-m.sandbox.paypal.com"


class PayPalError(Exception):
    pass


class PayPalUnknown(PayPalError):
    """PayPal may or may not have acted (timeout, network error, 5xx). Retry with the same PayPal-Request-Id."""


class CapturePending(PayPalError):
    """PayPal accepted the capture but it is not COMPLETED yet (e.g. under review). Carries the capture id."""
    def __init__(self, capture_id, status="PENDING"):
        super().__init__(f"capture {capture_id} is {status}, not COMPLETED yet")
        self.capture_id, self.status = capture_id, status


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
            cls = PayPalUnknown if e.code >= 500 else PayPalError
            raise cls(f"{e.code} {e.read().decode()[:300]}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PayPalUnknown(f"network error talking to PayPal: {e}")

    def _token(self):
        if not self._tok:
            basic = base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()
            r = self._call("POST", "/v1/oauth2/token", form={"grant_type": "client_credentials"},
                           auth=f"Basic {basic}")
            self._tok = r["access_token"]
        return self._tok

    def create_order(self, ref, amount, currency, description, payee_email=None):
        # PayPal-Request-Id = our payment id: a retried HTTP call can never create a second order
        body = {"intent": "CAPTURE",
                "purchase_units": [{"reference_id": ref, "custom_id": ref, "description": description[:127],
                                    "amount": {"currency_code": currency, "value": str(amount)}}],
                "payment_source": {"paypal": {"experience_context": {
                    "return_url": self.return_url, "cancel_url": self.cancel_url,
                    "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING"}}}}
        if payee_email:  # only honoured by PayPal for accounts permitted to take payment for that payee
            body["purchase_units"][0]["payee"] = {"email_address": payee_email}
        r = self._call("POST", "/v2/checkout/orders", body, {"PayPal-Request-Id": ref})
        link = next((l["href"] for l in r.get("links", []) if l["rel"] in ("payer-action", "approve")), None)
        return {"order_id": r["id"], "status": r["status"], "approve_url": link}

    def get_order(self, order_id):
        r = self._call("GET", f"/v2/checkout/orders/{order_id}")
        out = {"order_id": r["id"], "status": r["status"]}
        try:
            out["capture_id"] = r["purchase_units"][0]["payments"]["captures"][0]["id"]
        except (KeyError, IndexError, TypeError):
            pass
        return out

    def capture(self, order_id):
        r = self._call("POST", f"/v2/checkout/orders/{order_id}/capture", {},
                       {"PayPal-Request-Id": "cap-" + order_id})
        try:
            cap = r["purchase_units"][0]["payments"]["captures"][0]
        except (KeyError, IndexError, TypeError):
            raise PayPalError(f"capture response had no capture (order status {r.get('status')})")
        st = cap.get("status", "COMPLETED")
        if st == "PENDING":  # accepted but not final: never report money as moved, but remember the capture id
            raise CapturePending(cap["id"], st)
        if st != "COMPLETED":
            raise PayPalError(f"capture is {st}, not COMPLETED")
        return {"order_id": r["id"], "status": r["status"], "capture_id": cap["id"]}

    def get_capture(self, capture_id):
        r = self._call("GET", f"/v2/payments/captures/{capture_id}")
        return {"capture_id": r["id"], "status": r["status"]}

    # --- Payouts API: the agent pays an allow-listed PayPal account directly, no payer click -------------
    def create_payout(self, ref, amount, currency, note, email):
        """POST /v1/payments/payouts, one item. `ref` is both sender_batch_id and PayPal-Request-Id, so a retry cannot pay twice."""
        body = {"sender_batch_header": {"sender_batch_id": ref, "email_subject": "Payment from AgentPurse"[:120]},
                "items": [{"recipient_type": "EMAIL", "receiver": email, "sender_item_id": ref,
                           "amount": {"value": str(amount), "currency": currency}, "note": (note or "")[:160]}]}
        r = self._call("POST", "/v1/payments/payouts", body, {"PayPal-Request-Id": ref})
        h = r.get("batch_header", {})
        if "payout_batch_id" not in h:
            raise PayPalError(f"payout response had no batch id: {str(r)[:200]}")
        return {"payout_id": h["payout_batch_id"], "status": h.get("batch_status", "PENDING")}

    def get_payout(self, payout_id):
        r = self._call("GET", f"/v1/payments/payouts/{payout_id}")
        items = r.get("items") or []
        item = items[0].get("transaction_status") if items else None
        return {"payout_id": payout_id, "status": r.get("batch_header", {}).get("batch_status"), "item_status": item}


def _locked(f):
    def w(self, *a, **k):
        with self._lk:
            return f(self, *a, **k)
    w.__name__ = f.__name__
    return w


class MockPayPal:
    """Offline stand-in with the same method shapes, persisted in SQLite so the MCP server and the dashboard
    (separate processes) see the same orders. `payer_approve` plays the human payer."""
    mode = "mock"

    def __init__(self, path=":memory:"):
        import sqlite3, threading
        self._lk = threading.RLock()  # one sqlite connection shared by threads: serialise every method
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.capture_status, self.captures, self.payout_status = "COMPLETED", {}, "SUCCESS"
        self.db.execute("create table if not exists mock_orders(id text primary key, status text, amount text)")
        self.db.commit()

    @_locked
    def count(self):
        return self.db.execute("select count(*) from mock_orders").fetchone()[0]

    @_locked
    def _status(self, oid):
        r = self.db.execute("select status from mock_orders where id=?", (oid,)).fetchone()
        if not r:
            raise PayPalError("RESOURCE_NOT_FOUND")
        return r[0]

    @_locked
    def _set(self, oid, st):
        self.db.execute("update mock_orders set status=? where id=?", (st, oid))
        self.db.commit()

    @_locked
    def create_order(self, ref, amount, currency, description, payee_email=None):
        oid = "MOCK" + uuid.uuid4().hex[:12].upper()
        self.db.execute("insert into mock_orders values(?,?,?)", (oid, "PAYER_ACTION_REQUIRED", str(amount)))
        self.db.commit()
        return {"order_id": oid, "status": "PAYER_ACTION_REQUIRED",
                "approve_url": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}

    @_locked
    def payer_approve(self, oid):
        self._status(oid)
        self._set(oid, "APPROVED")

    @_locked
    def get_order(self, oid):
        st = self._status(oid)
        out = {"order_id": oid, "status": st}
        if st == "COMPLETED":
            out["capture_id"] = "CAP" + oid[4:]
        return out

    @_locked
    def capture(self, oid):
        if self._status(oid) != "APPROVED":
            raise PayPalError("UNPROCESSABLE_ENTITY ORDER_NOT_APPROVED: payer has not approved the order")
        cid = "CAP" + oid[4:]
        if self.capture_status != "COMPLETED":  # test hook: make captures come back PENDING
            self._set(oid, "COMPLETED")
            self.captures[cid] = self.capture_status
            raise CapturePending(cid, self.capture_status)
        self._set(oid, "COMPLETED")
        return {"order_id": oid, "status": "COMPLETED", "capture_id": cid}

    @_locked
    def get_capture(self, cid):
        return {"capture_id": cid, "status": self.captures.get(cid, "COMPLETED")}

    @_locked
    def create_payout(self, ref, amount, currency, note, email):
        """Mock of the Payouts API. Idempotent on `ref` like PayPal-Request-Id. `payout_status` is a test hook (SUCCESS / PENDING / DENIED ...)."""
        self.db.execute("create table if not exists mock_payouts(ref text primary key, id text, status text, email text, amount text)")
        r = self.db.execute("select id, status from mock_payouts where ref=?", (ref,)).fetchone()
        if r:
            return {"payout_id": r[0], "status": r[1]}
        pid = "MOCKPAYOUT" + uuid.uuid4().hex[:10].upper()
        self.db.execute("insert into mock_payouts values(?,?,?,?,?)", (ref, pid, self.payout_status, email, str(amount)))
        self.db.commit()
        return {"payout_id": pid, "status": self.payout_status}

    @_locked
    def get_payout(self, pid):
        self.db.execute("create table if not exists mock_payouts(ref text primary key, id text, status text, email text, amount text)")
        r = self.db.execute("select status from mock_payouts where id=?", (pid,)).fetchone()
        if not r:
            raise PayPalError("RESOURCE_NOT_FOUND")
        return {"payout_id": pid, "status": r[0], "item_status": "SUCCESS" if r[0] == "SUCCESS" else r[0]}

    @_locked
    def set_payout_status(self, pid, status):
        self.db.execute("update mock_payouts set status=? where id=?", (status, pid))
        self.db.commit()


def from_env(mock_db=":memory:"):
    """Real sandbox client when PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are set, otherwise the offline mock."""
    cid, sec = os.environ.get("PAYPAL_CLIENT_ID"), os.environ.get("PAYPAL_CLIENT_SECRET")
    if cid and sec:
        return PayPalClient(cid, sec, os.environ.get("PAYPAL_BASE_URL", SANDBOX))
    return MockPayPal(mock_db)
