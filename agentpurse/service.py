"""Core service used by the MCP server, the dashboard and the demo.

State machine (every arrow writes an audit row that also carries a hash of the payment row):
  propose -> DENIED
          -> PENDING_HUMAN -> (human) REJECTED | EXPIRED | -> ORDER_CREATED | DENIED (limits re-checked)
          -> AUTO_APPROVED -> ORDER_CREATED | FAILED | ORDER_UNKNOWN
  ORDER_UNKNOWN  -> (payment_status retries with the same PayPal-Request-Id) ORDER_CREATED | FAILED
  ORDER_CREATED  -> (payer approves on PayPal, agent calls capture) CAPTURED | CAPTURE_PENDING | CANCELLED | EXPIRED
  CAPTURE_PENDING -> (payment_status asks PayPal) CAPTURED | FAILED
  Payout payees (policy method "payout"): AUTO_APPROVED / approved -> PAID | PAYOUT_PENDING -> PAID | FAILED  (no payer click, no capture)
Concurrency: every state-changing section runs under one lock that is a thread lock AND an OS file lock next to the
SQLite file, so the MCP server and the dashboard (separate processes) cannot both pass the same budget check.
"""
from __future__ import annotations
import hashlib, json, os, threading, time
try:
    import fcntl
except ImportError:  # Windows: thread lock only
    fcntl = None
from decimal import Decimal
from . import policy as P
from .ledger import Ledger
from .paypal import CapturePending, PayPalError, PayPalUnknown

REQ_FIELDS = ("amount", "currency", "payee_id", "category", "description", "context", "agent_id", "idempotency_key")


class _XLock:
    """Re-entrant lock across threads AND processes (flock on <db>.lock). Without a file path it is a plain RLock."""

    def __init__(self, path=None, cross_process=True, timeout=60):
        self.t, self.path, self.fd, self.depth = threading.RLock(), path if cross_process else None, None, 0
        self.timeout = timeout

    def __enter__(self):
        self.t.acquire()
        try:
            if self.depth == 0 and self.path and fcntl:
                fd = open(self.path + ".lock", "a")
                deadline = time.time() + self.timeout
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.time() > deadline:
                            fd.close()
                            raise TimeoutError("AgentPurse is busy (another process holds the budget lock); retry in a moment")
                        time.sleep(0.02)
                self.fd = fd
        except BaseException:
            self.t.release()
            raise
        self.depth += 1

    def __exit__(self, *a):
        self.depth -= 1
        if self.depth == 0 and self.fd:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()
            self.fd = None
        self.t.release()


class AgentPurse:
    def __init__(self, paypal, ledger=None, policy=None, clock=time.time, advisor=None, cross_process_lock=True):
        self.advisor = advisor  # optional AI reviewer; may only escalate (see advisor.py)
        self.pp, self.ledger, self.clock = paypal, ledger or Ledger(), time.time if clock is None else clock
        self.policy = P.validate_policy(policy or P.DEFAULT_POLICY)
        self._lock = _XLock(self.ledger.path, cross_process_lock)
        self._advice_cache = {}

    # --- helpers ------------------------------------------------------
    def _clean(self, req) -> dict:
        """Keep known fields only; normalise the amount so PayPal never sees '1E+1' or ' 10 '."""
        if not isinstance(req, dict):
            raise ValueError("arguments must be an object")
        r = {k: req[k] for k in REQ_FIELDS if k in req and req[k] is not None}
        for k in ("payee_id", "category", "currency", "description", "context", "agent_id", "idempotency_key"):
            if k in r and not isinstance(r[k], str):
                r[k] = str(r[k])
        if r.get("idempotency_key"):  # scoped per agent so one agent cannot replay another's payment
            r["idempotency_key"] = f"{r.get('agent_id', 'agent')}:{r['idempotency_key'][:100]}"
        try:
            r["amount"] = f"{P.money(r.get('amount')):.2f}"
        except ValueError:
            r["amount"] = str(r.get("amount"))
        return r

    def _usage(self, req, exclude="") -> P.Usage:
        now = self.clock()
        day, payee = now - 86400, req.get("payee_id")
        return P.Usage(
            spent_24h=self.ledger.spent_since(day), count_1h=self.ledger.count_since(now - 3600),
            count_24h=self.ledger.count_since(day),
            payee_spent_24h=self.ledger.spent_since(day, payee), payee_count_24h=self.ledger.count_since(day, payee),
            pending_human=0 if exclude else self.ledger.pending_count(),
            requests_1h=0 if exclude else self.ledger.requests_since(req.get("agent_id", "agent"), now - 3600),  # an input-side control: not applied again when a human approves
            duplicate_of=self.ledger.find_duplicate(req, now - int(self.policy["duplicate_window_seconds"]), exclude))

    def _expire(self):
        now = self.clock()
        with self.ledger.atomic():
            for pid in self.ledger.expire_pending(now - int(self.policy["pending_ttl_seconds"])):
                self.ledger.log("expired", pid, "system", {"ttl_seconds": self.policy["pending_ttl_seconds"]})
        # crashed between "approved" and "order opened": retry with the same PayPal-Request-Id (PayPal dedupes)
        for p in self.ledger.stale("AUTO_APPROVED", now - 120):
            self._open_order(p["id"], json.loads(p["decision"] or "{}"), actor="reconcile")
        # PayPal never answered for a whole TTL: stop reserving budget; the order can no longer be captured by us
        for p in self.ledger.stale("ORDER_UNKNOWN", now - int(self.policy["pending_ttl_seconds"])):
            with self.ledger.atomic():
                if self.ledger.update(p["id"], only_if_status="ORDER_UNKNOWN", status="FAILED"):
                    self.ledger.log("order_unknown_expired", p["id"], "system", {"needs_manual_review": True})

    # --- agent-facing -------------------------------------------------
    def budget(self):
        with self._lock:
            now = self.clock()
            spent = self.ledger.spent_since(now - 86400)
            cap = Decimal(self.policy["daily_cap"])
            return {"currency": self.policy["currency"], "daily_cap": str(cap), "spent_24h": str(spent),
                    "remaining_24h": str(cap - spent), "per_payment_cap": self.policy["per_payment_cap"],
                    "auto_approve_up_to": self.policy["auto_approve_up_to"],
                    "max_payments_per_hour": self.policy["max_payments_per_hour"],
                    "payments_last_hour": self.ledger.count_since(now - 3600),
                    "pending_human": self.ledger.pending_count(),
                    "pending_ttl_seconds": self.policy["pending_ttl_seconds"],
                    "payees": {k: v["categories"] for k, v in self.policy["allowed_payees"].items()},
                    "blocked_categories": self.policy["blocked_categories"],
                    "human_review_categories": self.policy["human_review_categories"],
                    "paypal_mode": self.pp.mode}

    def simulate(self, req) -> dict:
        """Dry run: what would the policy say? Writes nothing, never calls PayPal."""
        with self._lock:
            try:
                r = self._clean(req)
            except ValueError as e:
                return {"verdict": P.DENY, "reasons": [str(e)], "flags": []}
            return {**P.evaluate(self.policy, r, usage=self._usage(r)).to_dict(), "dry_run": True}

    def propose(self, req: dict) -> dict:
        try:
            r = self._clean(req)
        except ValueError as e:
            return {"status": "DENIED", "verdict": P.DENY, "reasons": [str(e)], "flags": []}
        advice = self._advice(r)  # slow (a model call): done BEFORE taking the lock
        with self._lock:
            self._expire()
            key = r.get("idempotency_key")
            if key:
                prior = self.ledger.by_idempotency_key(key)
                if prior:  # the agent retried after a timeout: same answer, no second payment
                    if any(str(prior[k]) != str(r.get(k)) for k in ("payee_id", "amount", "category")):
                        return {"error": "idempotency_key was already used for a different request; use a new key",
                                "payment_id": prior["id"]}
                    return {**self.status(prior["id"]), "idempotent_replay": True}
            pid = self.ledger.new_id()
            d = self._apply_advice(r, P.evaluate(self.policy, r, usage=self._usage(r)), advice, pid)
            self.ledger.log("proposed", pid, r.get("agent_id", "agent"),
                            {k: r.get(k) for k in ("amount", "currency", "payee_id", "category", "description")})
            if d.verdict == P.DENY:
                self._record(r, "DENIED", d, pid)
                return {"payment_id": pid, "status": "DENIED", **d.to_dict()}
            if d.verdict == P.NEEDS_HUMAN:
                self._record(r, "PENDING_HUMAN", d, pid)
                return {"payment_id": pid, "status": "PENDING_HUMAN", **d.to_dict(),
                        "expires_in_seconds": self.policy["pending_ttl_seconds"],
                        "next": "A human must approve this in the AgentPurse dashboard. Poll payment_status; do not re-propose."}
            self._record(r, "AUTO_APPROVED", d, pid)  # counts as spent from here (a reservation): the lock is released before PayPal is called
        return self._open_order(pid, d.to_dict(), actor="policy")

    def _advice(self, r):
        """Ask the optional AI reviewer, outside the lock and cached per request. Returns None (not asked),
        ("ok", {...}) or ("error", message). Only asked when a preliminary rule check does not already deny."""
        if self.advisor is None:
            return None
        if P.evaluate(self.policy, r, usage=self._usage(r)).verdict == P.DENY:
            return None
        k = hashlib.sha256(json.dumps([r.get(x) for x in ("payee_id", "category", "amount", "currency", "description", "context")]).encode()).hexdigest()
        if k not in self._advice_cache:
            try:
                a = self.advisor.review(r, None)
                if not (isinstance(a, dict) and isinstance(a.get("suspicious"), bool)):
                    raise ValueError("bad advisor reply")
                self._advice_cache[k] = ("ok", a)
            except Exception as e:
                return ("error", f"{type(e).__name__}: {str(e)[:120]}")  # errors are not cached: retry next time
        return self._advice_cache[k]

    def _apply_advice(self, r, d: P.Decision, advice, pid=None) -> P.Decision:
        """AI second opinion. Only ever moves APPROVE -> NEEDS_HUMAN; DENY is returned untouched and a NEEDS_HUMAN
        only gains the AI note. A missing or failing advisor never loosens anything."""
        if self.advisor is None or d.verdict == P.DENY:
            return d
        if advice is None or advice[0] == "error":
            self.ledger.log("advisor_error", pid, "advisor", {"error": advice[1] if advice else "not consulted"})
            if d.verdict == P.APPROVE:
                return P.Decision(P.NEEDS_HUMAN, ["AI reviewer was unavailable, so a human decides"], ["advisor_unavailable"])
            return d
        a = advice[1]
        note = str(a.get("note", ""))[:200]
        self.ledger.log("advisor", pid, "advisor", {"suspicious": a["suspicious"], "note": note})
        if not a["suspicious"]:
            return d
        reasons = d.reasons if d.verdict == P.NEEDS_HUMAN else []
        return P.Decision(P.NEEDS_HUMAN, reasons + [f"[AI opinion, untrusted] {note[:120] or 'flagged as suspicious'}"],
                          d.flags + ["ai_advisor_concern"], d.evidence)

    def _record(self, r, status, d: P.Decision, pid=None) -> str:
        with self.ledger.atomic():
            pid = self.ledger.add(r, status, d.to_dict(), pid=pid)
            self.ledger.log("decision:" + d.verdict, pid, "policy", {"status": status, **d.to_dict()})
        return pid

    def _open_order(self, pid, decision, actor):
        """Move money for an approved payment. The PayPal call itself is made WITHOUT the service lock (the row is already
        reserved as AUTO_APPROVED / PENDING_HUMAN-approved); every ledger write takes the lock and is a compare-and-swap."""
        p = self.ledger.get(pid)
        payee = self.policy["allowed_payees"].get(p["payee_id"], {})
        payout = payee.get("method") == "payout"
        try:
            if payout:
                o = self.pp.create_payout(pid, p["amount"], p["currency"], p["description"] or "AgentPurse payment", payee["paypal_email"])
            else:
                o = self.pp.create_order(pid, p["amount"], p["currency"], p["description"] or "AgentPurse payment",
                                         payee_email=payee.get("paypal_email"))
        except PayPalUnknown as e:  # PayPal may have acted: keep the budget reserved, retry with the same request id
            with self._lock, self.ledger.atomic():
                self.ledger.update(pid, only_if_status=p["status"], status="ORDER_UNKNOWN")
                self.ledger.log("order_unknown", pid, actor, {"error": str(e)[:300]})
            return {"payment_id": pid, "status": "ORDER_UNKNOWN",
                    "next": "PayPal did not answer. Call payment_status to retry safely (same PayPal-Request-Id); do not re-propose."}
        except PayPalError as e:
            with self._lock, self.ledger.atomic():
                self.ledger.update(pid, status="FAILED")
                self.ledger.log("order_failed", pid, actor, {"error": str(e)[:300]})
            return {"payment_id": pid, "status": "FAILED", "error": str(e)}
        if payout:
            new = "PAID" if o["status"] == "SUCCESS" else "PAYOUT_PENDING"
            with self._lock, self.ledger.atomic():
                if not self.ledger.update(pid, only_if_status=p["status"], status=new, order_id=o["payout_id"]):
                    self.ledger.log("payout_orphaned", pid, actor, {"payout_id": o["payout_id"], "was": p["status"]})
                    return {"payment_id": pid, "status": "CANCELLED", "error": "payment changed state while the payout was sent; check payment_status and PayPal"}
                self.ledger.log("payout_sent", pid, actor, {"payout_id": o["payout_id"], "batch_status": o["status"], "mode": self.pp.mode})
            return {"payment_id": pid, "status": new, **decision, "payout_id": o["payout_id"],
                    "next": "Paid directly to the allow-listed PayPal account; nothing more to do." if new == "PAID"
                            else "PayPal is still processing the payout; poll payment_status."}
        with self._lock, self.ledger.atomic():
            if not self.ledger.update(pid, only_if_status=p["status"], status="ORDER_CREATED", order_id=o["order_id"],
                                      approve_url=o["approve_url"]):  # cancelled/changed by another process meanwhile
                self.ledger.log("order_orphaned", pid, actor, {"order_id": o["order_id"], "was": p["status"]})
                return {"payment_id": pid, "status": "CANCELLED", "error": "payment changed state while the order was opened; the order will not be captured"}
            self.ledger.log("order_created", pid, actor, {"order_id": o["order_id"], "mode": self.pp.mode})
        return {"payment_id": pid, "status": "ORDER_CREATED", **decision, "approve_url": o["approve_url"],
                "next": "The payer must approve at approve_url (PayPal). Then call capture_payment."}

    def _reconcile(self, p):
        """Resolve the 'PayPal knows, we are not sure' states. Safe to call repeatedly."""
        pid = p["id"]
        if p["status"] == "ORDER_UNKNOWN":
            self._open_order(pid, json.loads(p["decision"] or "{}"), actor="reconcile")
        elif p["status"] == "PAYOUT_PENDING":
            try:
                g = self.pp.get_payout(p["order_id"])
            except PayPalError:
                return
            st = g.get("item_status") or g.get("status")
            if st == "SUCCESS":
                with self.ledger.atomic():
                    if self.ledger.update(pid, only_if_status="PAYOUT_PENDING", status="PAID"):
                        self.ledger.log("payout_paid", pid, "reconcile", {"payout_id": p["order_id"]})
            elif st in ("FAILED", "DENIED", "RETURNED", "BLOCKED", "REFUNDED", "CANCELED", "REVERSED"):
                with self.ledger.atomic():
                    if self.ledger.update(pid, only_if_status="PAYOUT_PENDING", status="FAILED"):
                        self.ledger.log("payout_failed", pid, "reconcile", {"paypal_status": st})
        elif p["status"] == "CAPTURE_UNKNOWN":
            try:
                o = self.pp.get_order(p["order_id"])
            except PayPalError:
                return
            if o.get("status") == "COMPLETED":
                self._mark_captured(p, o.get("capture_id") or "", "CAPTURE_UNKNOWN", "reconcile")
            elif o.get("status") == "APPROVED":  # the capture never happened: safe to try again (PayPal-Request-Id dedupes)
                with self.ledger.atomic():
                    self.ledger.update(pid, only_if_status="CAPTURE_UNKNOWN", status="ORDER_CREATED")
                    self.ledger.log("capture_retry", pid, "reconcile", {})
        elif p["status"] == "CAPTURE_PENDING" and p["capture_id"]:
            try:
                st = self.pp.get_capture(p["capture_id"]).get("status")
            except PayPalError:
                return
            if st == "COMPLETED":
                self._mark_captured(p, p["capture_id"], "CAPTURE_PENDING", "reconcile")
            elif st in ("DECLINED", "FAILED", "DENIED", "REFUNDED"):
                with self.ledger.atomic():
                    if self.ledger.update(pid, only_if_status="CAPTURE_PENDING", status="FAILED"):
                        self.ledger.log("capture_failed", pid, "reconcile", {"paypal_status": st})

    def _mark_captured(self, p, capture_id, was, actor):
        with self.ledger.atomic():
            if self.ledger.update(p["id"], only_if_status=was, status="CAPTURED", capture_id=capture_id or p.get("capture_id") or ""):
                self.ledger.log("captured", p["id"], actor, {"capture_id": capture_id})
                return True
        return False

    def status(self, pid):
        with self._lock:
            self._expire()
            p = self.ledger.get(pid)
            if not p:
                return {"error": "unknown payment_id"}
            if p["status"] in ("ORDER_UNKNOWN", "CAPTURE_PENDING", "CAPTURE_UNKNOWN", "PAYOUT_PENDING"):
                self._reconcile(p)
                p = self.ledger.get(pid)
            out = {k: p[k] for k in ("id", "status", "amount", "currency", "payee_id", "category", "order_id",
                                     "approve_url", "capture_id")}
            out["payment_id"] = out.pop("id")
            if p["status"] == "PENDING_HUMAN":
                out["next"] = "waiting for a human in the dashboard"
            elif p["status"] == "CAPTURE_PENDING":
                out["next"] = "PayPal accepted the capture but it is not final yet; poll payment_status"
            elif p["status"] == "PAYOUT_PENDING":
                out["next"] = "PayPal is still processing the payout; poll payment_status"
            elif p["status"] == "CAPTURE_UNKNOWN":
                out["next"] = "PayPal did not confirm the capture; poll payment_status, do not capture again"
            elif p["status"] == "ORDER_UNKNOWN":
                out["next"] = "PayPal still not answering; poll payment_status again, do not re-propose"
            return out

    def capture(self, pid):
        with self._lock:
            p = self.ledger.get(pid)
            if p and p["status"] in ("CAPTURE_UNKNOWN", "CAPTURE_PENDING"):
                return self.status(pid)
            if not p or p["status"] != "ORDER_CREATED":
                return {"error": f"payment is not capturable (status={p['status'] if p else 'unknown'})"}
            try:
                o = self.pp.get_order(p["order_id"])  # ask first instead of blind-capturing and relying on a 422
            except PayPalError as e:
                return {"payment_id": pid, "status": "ORDER_CREATED", "error": f"could not read the order: {e}"}
            if o.get("status") == "COMPLETED":  # a capture we never heard back about: record it, never expire it
                self._mark_captured(p, o.get("capture_id") or "", "ORDER_CREATED", "agent")
                return self.status(pid)
            why = self._capture_blocker(p)
            if why:
                new = "EXPIRED" if why.startswith("order expired") else "CANCELLED"
                with self.ledger.atomic():
                    self.ledger.update(pid, only_if_status="ORDER_CREATED", status=new)
                    self.ledger.log("capture_blocked", pid, "policy", {"reason": why})
                return {"payment_id": pid, "status": new, "error": f"not captured: {why}"}
            if o.get("status") != "APPROVED":
                return {"payment_id": pid, "status": "ORDER_CREATED", "paypal_order_status": o.get("status"),
                        "error": "the payer has not approved the order yet (ORDER_NOT_APPROVED)"}
            try:
                c = self.pp.capture(p["order_id"])
            except CapturePending as e:
                with self.ledger.atomic():
                    self.ledger.update(pid, only_if_status="ORDER_CREATED", status="CAPTURE_PENDING", capture_id=e.capture_id)
                    self.ledger.log("capture_pending", pid, "agent", {"capture_id": e.capture_id, "paypal_status": e.status})
                return {"payment_id": pid, "status": "CAPTURE_PENDING", "capture_id": e.capture_id,
                        "next": "PayPal accepted the capture but it is not final yet; poll payment_status"}
            except PayPalUnknown as e:  # PayPal may have moved the money: keep it counted, never expire, reconcile via get_order
                with self.ledger.atomic():
                    self.ledger.update(pid, only_if_status="ORDER_CREATED", status="CAPTURE_UNKNOWN")
                    self.ledger.log("capture_unknown", pid, "agent", {"error": str(e)[:300]})
                return {"payment_id": pid, "status": "CAPTURE_UNKNOWN",
                        "next": "PayPal did not confirm the capture. Poll payment_status; do not capture again."}
            except PayPalError as e:
                self.ledger.log("capture_failed", pid, "agent", {"error": str(e)[:300]})
                return {"payment_id": pid, "status": "ORDER_CREATED", "error": str(e)}
            if not self._mark_captured(p, c["capture_id"], "ORDER_CREATED", "agent"):
                return {"error": "payment changed state during capture; check payment_status"}
            return {"payment_id": pid, "status": "CAPTURED", "capture_id": c["capture_id"]}

    def _capture_blocker(self, p) -> str:
        """Static re-check at capture time: the policy or the order may have changed since it was opened."""
        if self.clock() - p["updated"] > int(self.policy["order_ttl_seconds"]):
            return f"order expired (older than {self.policy['order_ttl_seconds']}s)"
        pol = self.policy
        if p["payee_id"] in pol["blocked_payees"] or p["payee_id"] not in pol["allowed_payees"]:
            return f"payee '{p['payee_id']}' is no longer allowed"
        if p["category"] in pol["blocked_categories"] or p["category"] not in pol["allowed_payees"][p["payee_id"]]["categories"]:
            return f"category '{p['category']}' is no longer allowed for this payee"
        return ""

    def cancel(self, pid, actor="agent", reason=""):
        """Withdraw a request that is still waiting for a human, or an order nobody has captured yet."""
        with self._lock:
            p = self.ledger.get(pid)
            if not p or p["status"] not in ("PENDING_HUMAN", "ORDER_CREATED"):
                return {"error": f"cannot cancel (status={p['status'] if p else 'unknown'})"}
            with self.ledger.atomic():
                if not self.ledger.update(pid, only_if_status=p["status"], status="CANCELLED"):
                    return {"error": "payment changed state; check payment_status"}
                self.ledger.log("cancelled", pid, actor, {"reason": reason[:200], "was": p["status"]})
            return {"payment_id": pid, "status": "CANCELLED"}

    # --- human-facing -------------------------------------------------
    def human_decide(self, pid, approve: bool, actor="human", note=""):
        with self._lock:
            self._expire()
            p = self.ledger.get(pid)
            if not p or p["status"] != "PENDING_HUMAN":
                return {"error": f"not pending (status={p['status'] if p else 'unknown'})"}
            note = (note or "")[:300]
            if not approve:
                with self.ledger.atomic():
                    self.ledger.update(pid, status="REJECTED")
                    self.ledger.log("human_rejected", pid, actor, {"note": note})
                return {"payment_id": pid, "status": "REJECTED"}
            # re-check the hard limits at approval time (budget may have been used meanwhile)
            req = {k: p[k] for k in ("amount", "currency", "payee_id", "category", "description", "context", "agent_id")}
            d = P.evaluate(self.policy, req, usage=self._usage(req, exclude=pid))
            if d.verdict == P.DENY:
                with self.ledger.atomic():
                    self.ledger.update(pid, status="DENIED", decision=json.dumps(d.to_dict()))
                    self.ledger.log("human_approval_blocked", pid, actor, d.to_dict())
                return {"payment_id": pid, "status": "DENIED", **d.to_dict()}
            self.ledger.log("human_approved", pid, actor, {"note": note, "flags": json.loads(p["decision"]).get("flags", [])})
            return self._open_order(pid, json.loads(p["decision"]), actor=actor)
