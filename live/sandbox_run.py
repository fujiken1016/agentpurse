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
  4. python3 live/sandbox_run.py            # payout runs by itself; the checkout waits for you to approve in the printed URL
     python3 live/sandbox_run.py --wait-payer 0   # do not wait: print the approve URL and stop after recording

Steps recorded: token check -> demo.py against the sandbox -> a Payouts payment (no human click) -> a Checkout order
(payer approves on PayPal's page, then capture) -> audit verification. Failures are written as "FAILED: reason" with the
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
    db = os.path.join(tmp, "sb.db")
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

    r = svc.propose({"amount": "5.00", "payee_id": "design-co", "category": "design", "description": f"logo draft {stamp}",
                     "context": "Task: pay the designer 5.00 for the logo draft", "idempotency_key": f"sb-payout-{stamp}"})
    rep.add(f"3. Payouts API, agent pays {mask(payee_email)} with no human click: status **{r.get('status')}**"
            + (f", payout batch `{r.get('payout_id')}`" if r.get("payout_id") else "") + (f", error: {r.get('error')}" if r.get("error") else ""))
    if r.get("status") not in ("PAID", "PAYOUT_PENDING"):
        rep.fail(f"payout did not go through: {json.dumps(r)[:300]} (is Payouts enabled for the sandbox app?)")
    else:
        for _ in range(12):
            st = svc.status(r["payment_id"])["status"]
            if st != "PAYOUT_PENDING":
                break
            time.sleep(5)
        rep.add(f"   after polling: **{st}**")

    c = svc.propose({"amount": "12.00", "payee_id": "acme-api", "category": "api", "description": f"500 API credits {stamp}",
                     "context": "Task: top up the Acme quota. Price list: 12.00 per 500 credits.", "idempotency_key": f"sb-order-{stamp}"})
    rep.add(f"4. Checkout order for 12.00: status **{c.get('status')}**, PayPal order `{svc.ledger.get(c['payment_id'])['order_id'] if c.get('payment_id') else '-'}`")
    if c.get("status") != "ORDER_CREATED":
        rep.fail(f"order was not created: {json.dumps(c)[:300]}")
    else:
        url = c["approve_url"]
        rep.add(f"   Payer approval page (open as the sandbox buyer): {url}")
        oid = svc.ledger.get(c["payment_id"])["order_id"]
        deadline = time.time() + a.wait_payer
        if a.mock:
            pp.payer_approve(oid)
        while a.wait_payer and time.time() < deadline:
            if pp.get_order(oid).get("status") == "APPROVED":
                break
            time.sleep(5)
        if pp.get_order(oid).get("status") == "APPROVED":
            cap = svc.capture(c["payment_id"])
            rep.add(f"   capture: **{cap.get('status')}**, capture id `{cap.get('capture_id', '-')}`" + (f", error {cap.get('error')}" if cap.get("error") else ""))
            if cap.get("status") not in ("CAPTURED", "CAPTURE_PENDING"):
                rep.fail(f"capture did not complete: {json.dumps(cap)[:300]}")
        else:
            rep.add("   The payer has not approved yet: open the URL as the sandbox buyer, approve, then run `python3 live/sandbox_run.py --skip-demo` again "
                    "or call capture_payment on this payment id: `" + c["payment_id"] + "`. (Recorded as incomplete, not as a pass.)")
            rep.failed = True
    ok, bad, n = svc.ledger.verify_audit()
    seq, h = svc.ledger.head_hash()
    rep.add(f"5. Audit: hash chain {'OK' if ok else 'BROKEN'} ({n} rows), payments vs chain {'OK' if not svc.ledger.verify_ledger() else 'MISMATCH'}, head `{seq}:{h[:16]}...`")
    return finish(rep, a)


def finish(rep, a):
    rep.add("\n**Result: " + ("INCOMPLETE / FAILED, see lines marked FAILED or 'not approved yet'" if rep.failed else "all steps passed") + "**")
    if not a.mock or a.out != os.path.join(ROOT, "docs", "SANDBOX_RUN.md"):
        open(a.out, "w").write("\n".join(rep.lines) + "\n")
        print("wrote", a.out)
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
