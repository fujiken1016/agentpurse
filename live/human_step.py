#!/usr/bin/env python3
"""Plays the two humans between agent runs, exactly as the dashboard would (same service, same SQLite file):
  approve|reject <payment_id>   the budget owner decides a PENDING_HUMAN request
  payer <payment_id>            the payer approves the order on PayPal (offline mock only)
Env: AP_DB, AP_POLICY, AP_LOG.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentpurse import paypal, policy as P
from agentpurse.ledger import Ledger
from agentpurse.service import AgentPurse

db = os.environ["AP_DB"]
svc = AgentPurse(paypal.MockPayPal(db), Ledger(db), P.load_policy(os.environ.get("AP_POLICY")))
act, pid = sys.argv[1], sys.argv[2]
if act in ("approve", "reject"):
    out = svc.human_decide(pid, act == "approve", actor="human:budget-owner", note="live run")
elif act == "payer":
    p = svc.ledger.get(pid)
    svc.pp.payer_approve(p["order_id"])
    svc.ledger.log("mock_payer_approved", pid, "mock-payer", {})
    out = {"payment_id": pid, "payer_approved": p["order_id"]}
else:
    sys.exit("usage: human_step.py approve|reject|payer <payment_id>")
if os.environ.get("AP_LOG"):
    with open(os.environ["AP_LOG"], "a") as f:
        f.write(json.dumps({"t": time.strftime("%H:%M:%S"), "human": act, "payment_id": pid, "result": out}) + "\n")
print(json.dumps(out))
