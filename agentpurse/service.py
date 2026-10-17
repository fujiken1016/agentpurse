"""Core service used by the MCP server, the dashboard and the demo."""
from __future__ import annotations
import time
from decimal import Decimal
from . import policy as P
from .ledger import Ledger
from .paypal import PayPalError


class AgentPurse:
    def __init__(self, paypal, ledger=None, policy=None, clock=time.time):
        self.pp, self.ledger, self.policy, self.clock = paypal, ledger or Ledger(), policy or P.DEFAULT_POLICY, clock

    # --- agent-facing -------------------------------------------------
    def budget(self):
        now = self.clock()
        spent = self.ledger.spent_since(now - 86400)
        cap = Decimal(self.policy["daily_cap"])
        return {"currency": self.policy["currency"], "daily_cap": str(cap), "spent_24h": str(spent),
                "remaining_24h": str(cap - spent), "per_payment_cap": self.policy["per_payment_cap"],
                "auto_approve_up_to": self.policy["auto_approve_up_to"],
                "payees": {k: v["categories"] for k, v in self.policy["allowed_payees"].items()},
                "paypal_mode": self.pp.mode}

    def propose(self, req: dict) -> dict:
        now = self.clock()
        d = P.evaluate(self.policy, req, self.ledger.spent_since(now - 86400),
                       self.ledger.count_since(now - 3600))
        if d.verdict == P.DENY:
            pid = self.ledger.add(req, "DENIED", d.to_dict())
            return {"payment_id": pid, "status": "DENIED", **d.to_dict()}
        if d.verdict == P.NEEDS_HUMAN:
            pid = self.ledger.add(req, "PENDING_HUMAN", d.to_dict())
            return {"payment_id": pid, "status": "PENDING_HUMAN", **d.to_dict(),
                    "next": "A human must approve this in the AgentPurse dashboard. Poll payment_status."}
        pid = self.ledger.add(req, "PENDING_HUMAN", d.to_dict())
        return self._open_order(pid, d.to_dict())

    def _open_order(self, pid, decision):
        p = self.ledger.get(pid)
        try:
            o = self.pp.create_order(pid, p["amount"], p["currency"], p["description"] or "AgentPurse payment")
        except PayPalError as e:
            self.ledger.update(pid, status="FAILED")
            return {"payment_id": pid, "status": "FAILED", "error": str(e)}
        self.ledger.update(pid, status="ORDER_CREATED", order_id=o["order_id"], approve_url=o["approve_url"])
        return {"payment_id": pid, "status": "ORDER_CREATED", **decision, "approve_url": o["approve_url"],
                "next": "The payer must approve at approve_url (PayPal). Then call capture_payment."}

    def status(self, pid):
        p = self.ledger.get(pid)
        if not p:
            return {"error": "unknown payment_id"}
        return {k: p[k] for k in ("id", "status", "amount", "currency", "payee_id", "order_id", "approve_url", "capture_id")}

    def capture(self, pid):
        p = self.ledger.get(pid)
        if not p or p["status"] != "ORDER_CREATED":
            return {"error": f"payment is not capturable (status={p['status'] if p else 'unknown'})"}
        try:
            c = self.pp.capture(p["order_id"])
        except PayPalError as e:
            return {"payment_id": pid, "status": "ORDER_CREATED", "error": str(e)}
        self.ledger.update(pid, status="CAPTURED", capture_id=c["capture_id"])
        return {"payment_id": pid, "status": "CAPTURED", "capture_id": c["capture_id"]}

    # --- human-facing -------------------------------------------------
    def human_decide(self, pid, approve: bool):
        p = self.ledger.get(pid)
        if not p or p["status"] != "PENDING_HUMAN":
            return {"error": "not pending"}
        if not approve:
            self.ledger.update(pid, status="REJECTED")
            return {"payment_id": pid, "status": "REJECTED"}
        # re-check the hard limits at approval time (budget may have been used meanwhile)
        now = self.clock()
        import json
        req = {"amount": p["amount"], "currency": p["currency"], "payee_id": p["payee_id"],
               "category": p["category"], "description": "", "context": ""}
        d = P.evaluate(self.policy, req, self.ledger.spent_since(now - 86400), self.ledger.count_since(now - 3600))
        if d.verdict == P.DENY:
            self.ledger.update(pid, status="DENIED", decision=json.dumps(d.to_dict()))
            return {"payment_id": pid, "status": "DENIED", **d.to_dict()}
        return self._open_order(pid, json.loads(p["decision"]))
