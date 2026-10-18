#!/usr/bin/env python3
"""Run AgentPurse against the real PayPal SANDBOX and write the evidence to docs/SANDBOX_RUN.md.

Owner setup (once, about 10 minutes, no card needed):
  1. developer.paypal.com -> Apps & Credentials -> Sandbox -> Create App (Merchant). Copy Client ID and Secret.
  2. In that app's settings enable "Payouts" (Features). In Testing Tools -> Sandbox Accounts keep one Personal (buyer) account
     for approving checkouts and one Personal account as the payout receiver.
  3. Write ~/.config/fujiken/paypal_sandbox.env  (chmod 600):
         PAYPAL_CLIENT_ID=...
         PAYPAL_CLIENT_SECRET=...
         PAYPAL_SANDBOX_PAYEE_EMAIL=<email of the sandbox personal account that receives payouts>
  4. python3 live/sandbox_run.py            # the checkout waits for you to approve in the printed URL; then it captures and pays out
     python3 live/sandbox_run.py --wait-payer 0   # do not wait: print the approve URL and stop after recording

Steps recorded: token check -> demo.py against the sandbox -> a Checkout order (payer approves on PayPal's page, then capture;
this also funds the sandbox business account) -> a Payouts payment (no human click) -> audit verification. Re-running resumes an unfinished checkout. Failures are written as "FAILED: reason" with the
PayPal error text (no secrets are ever printed or stored). `--mock` rehearses the same script offline.
"""
import argparse, json, os, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agentpurse import paypal, policy as P  # noqa: E402
from agentpurse.ledger import Ledger  # noqa: E402
from agentpurse.service import AgentPurse  # noqa: E402

DEFAULT_ENV = os.path.expanduser("~/.config/fujiken/paypal_sandbox.env")


def load_env(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def mask(email):
    local, _, dom = email.partition("@")
    return (local[:2] + "***@" + dom) if dom else "***"


class Report:
    def __init__(self, secrets):
        self.lines, self.secrets, self.failed = [], [s for s in secrets if s], False

    def add(self, s=""):
        for sec in self.secrets:
            s = s.replace(sec, "***")
        self.lines.append(s)
        print(s)

    def fail(self, reason):
        self.failed = True
        self.add(f"**FAILED: {reason}**")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=DEFAULT_ENV)
    ap.add_argument("--mock", action="store_true", help="rehearse offline with the mock (no keys needed)")
    ap.add_argument("--wait-payer", type=int, default=900, help="seconds to wait for the payer to approve the checkout (0 = do not wait)")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "SANDBOX_RUN.md"))
    ap.add_argument("--skip-demo", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="forget the saved sandbox ledger (~/.cache/agentpurse/sandbox.db)")
    a = ap.parse_args(argv)

    env = {}
    if not a.mock:
        if not os.path.exists(a.env):
            print(f"FAILED: no keys yet: {a.env} does not exist. Setup steps are at the top of live/sandbox_run.py")
            return 1
        env = load_env(a.env)
        miss = [k for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_SANDBOX_PAYEE_EMAIL") if not env.get(k)]
        if miss:
            print("FAILED: missing in " + a.env + ": " + ", ".join(miss))
            return 1
    rep = Report([env.get("PAYPAL_CLIENT_ID"), env.get("PAYPAL_CLIENT_SECRET")])
    payee_email = env.get("PAYPAL_SANDBOX_PAYEE_EMAIL", "design-co@example.com")
    rep.add(f"# Sandbox run ({'MOCK rehearsal, not PayPal' if a.mock else 'PayPal sandbox'}), {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
    tmp = tempfile.mkdtemp()
    # the ledger persists between runs (so a half-finished checkout can be resumed); --fresh starts over; the mock always uses a temp file
    state_dir = os.path.expanduser("~/.cache/agentpurse")
    os.makedirs(state_dir, exist_ok=True)
    db = os.path.join(tmp, "sb.db") if a.mock else os.path.join(state_dir, "sandbox.db")
    if a.fresh and not a.mock and os.path.exists(db):
        os.remove(db)
    if a.mock:
        pp = paypal.MockPayPal(db)
    else:
        os.environ.update({k: v for k, v in env.items() if k.startswith("PAYPAL_")})
        pp = paypal.from_env(db)
        if pp.mode != "sandbox":
            rep.fail("PayPal client did not switch to sandbox mode")
            return finish(rep, a)
        try:
            pp._token()
            rep.add("1. OAuth token: OK (client-credentials grant against api-m.sandbox.paypal.com)")
        except Exception as e:
            rep.fail(f"OAuth token request failed: {str(e)[:300]}. Check the client id/secret belong to a SANDBOX app")
            return finish(rep, a)
    if not a.skip_demo:
        ddb = os.path.join(tmp, "demo.db")
        p = subprocess.run([sys.executable, os.path.join(ROOT, "demo.py"), ddb], capture_output=True, text=True, cwd=ROOT,
                           env={**os.environ, **({} if a.mock else {k: v for k, v in env.items() if k.startswith("PAYPAL_")})}, timeout=300)
        tail = "\n".join((p.stdout or p.stderr).splitlines()[-14:])
        rep.add(f"2. demo.py ({'mock' if a.mock else 'sandbox'}) exit code {p.returncode}; last lines:\n```\n{tail}\n```")
        if p.returncode != 0:
            rep.fail("demo.py exited non-zero (shape mismatch against the real API?)")
    pol = dict(P.DEFAULT_POLICY, max_payments_per_hour=50, max_payments_per_day=50, allowed_payees={
        "design-co": {"name": "Design Co (sandbox personal account)", "categories": ["design"], "method": "payout",
                      "paypal_email": payee_email, "max_amount": "30.00"},
        "acme-api": {"name": "Acme Data API", "categories": ["api"]}})
    svc = AgentPurse(pp, Ledger(db), P.validate_policy(pol))
    stamp = time.strftime("%H%M%S")

    # --- Checkout first: capturing it also puts money into the sandbox business account, which the payout then spends ---
    open_orders = [r for r in svc.ledger.list(50) if r["status"] == "ORDER_CREATED" and r["payee_id"] == "acme-api"]
    done = [r for r in svc.ledger.list(50) if r["payee_id"] == "acme-api" and r["status"] in ("CAPTURED", "CAPTURE_PENDING")]
    if done and not open_orders:
        c = {"status": "DONE"}
        rep.add(f"3. Checkout already captured in an earlier run: order `{done[0]['order_id']}`, capture `{done[0]['capture_id']}` ({done[0]['status']})")
    elif open_orders:
        c = {"payment_id": open_orders[0]["id"], "status": "ORDER_CREATED", "approve_url": open_orders[0]["approve_url"]}
        rep.add(f"3. Resuming the open Checkout order from the previous run: `{open_orders[0]['order_id']}`")
    else:
        c = svc.propose({"amount": "12.00", "payee_id": "acme-api", "category": "api", "description": f"500 API credits {stamp}",
                         "context": "Task: top up the Acme quota. Price list: 12.00 per 500 credits.", "idempotency_key": f"sb-order-{stamp}"})
        rep.add(f"3. Checkout order for 12.00: status **{c.get('status')}**, PayPal order `{svc.ledger.get(c['payment_id'])['order_id'] if c.get('payment_id') else '-'}`")
    captured = False
    if c.get("status") == "DONE":
        pass
    elif c.get("status") != "ORDER_CREATED":
        rep.fail(f"order was not created: {json.dumps(c)[:300]}")
    else:
        url = c["approve_url"]
        oid = svc.ledger.get(c["payment_id"])["order_id"]
        rep.add(f"   Payer approval page (open as the sandbox buyer): {url}")
        deadline = time.time() + a.wait_payer
        if a.mock:
            pp.payer_approve(oid)
        while a.wait_payer and time.time() < deadline:
            if pp.get_order(oid).get("status") == "APPROVED":
                break
            time.sleep(5)
        if pp.get_order(oid).get("status") == "APPROVED":
            cap = svc.capture(c["payment_id"])
            captured = cap.get("status") in ("CAPTURED", "CAPTURE_PENDING")
            rep.add(f"   capture: **{cap.get('status')}**, capture id `{cap.get('capture_id', '-')}`" + (f", error {cap.get('error')}" if cap.get("error") else ""))
            if not captured:
                rep.fail(f"capture did not complete: {json.dumps(cap)[:300]}")
        else:
            rep.add("   NOT APPROVED YET (recorded as incomplete, not as a pass). Human step: (1) open the URL above in a browser and log in with the sandbox BUYER "
                    "(personal) account, (2) press Pay/Approve, (3) run `python3 live/sandbox_run.py --skip-demo` again: it resumes this order, captures it, then does the payout.")
            rep.failed = True

    # --- Payout (needs a balance in the sandbox business account: a captured checkout provides it) ---
    paid = [r for r in svc.ledger.list(50) if r["payee_id"] == "design-co" and r["status"] in ("PAID", "PAYOUT_PENDING")]
    if paid:
        rep.add(f"4. Payouts API: already done in an earlier run (payout batch `{paid[0]['order_id']}`, {paid[0]['status']})")
    else:
        r = svc.propose({"amount": "5.00", "payee_id": "design-co", "category": "design", "description": f"logo draft {stamp}",
                         "context": "Task: pay the designer 5.00 for the logo draft", "idempotency_key": f"sb-payout-{stamp}"})
        rep.add(f"4. Payouts API, agent pays {mask(payee_email)} with no human click: status **{r.get('status')}**"
                + (f", payout batch `{r.get('payout_id')}`" if r.get("payout_id") else ""))
        if r.get("status") in ("PAID", "PAYOUT_PENDING"):
            for _ in range(12):
                st = svc.status(r["payment_id"])["status"]
                if st != "PAYOUT_PENDING":
                    break
                time.sleep(5)
            rep.add(f"   after polling: **{st}**")
        else:
            err = str(r.get("error", ""))[:300]
            hint = (" The sandbox business account has no balance yet: capture the checkout first (step 3) or add funds in the sandbox dashboard."
                    if "INSUFFICIENT_FUNDS" in err else " (is Payouts enabled for the sandbox app?)")
            rep.fail(f"payout did not go through: {err}.{hint}")
    ok, bad, n = svc.ledger.verify_audit()
    seq, h = svc.ledger.head_hash()
    rep.add(f"5. Audit: hash chain {'OK' if ok else 'BROKEN'} ({n} rows), payments vs chain {'OK' if not svc.ledger.verify_ledger() else 'MISMATCH'}, head `{seq}:{h[:16]}...`")
    return finish(rep, a)


def finish(rep, a):
    rep.add("\nFindings from the first runs (INSUFFICIENT_FUNDS before the first capture, currency check, return page): see docs/SANDBOX_NOTES.md")
    rep.add("\n**Result: " + ("INCOMPLETE / FAILED, see lines marked FAILED or 'not approved yet'" if rep.failed else "all steps passed") + "**")
    if not a.mock or a.out != os.path.join(ROOT, "docs", "SANDBOX_RUN.md"):
        open(a.out, "w").write("\n".join(rep.lines) + "\n")
        print("wrote", a.out)
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
