# Devpost submission text (English). Paste each section into the matching Devpost field.

**Project name:** AgentPurse

**Tagline:** A PayPal budget your AI agent can't exceed or be tricked past

## Inspiration
An agent that can spend money is one poisoned web page away from paying an attacker. "Never pay strangers" in a system
prompt is a request, not a control. We wanted the control to live outside the model, where it can be tested, audited,
and explained to the person whose money it is.

## What it does
AgentPurse sits between an AI agent and PayPal. The agent calls MCP tools (`propose_payment`, `simulate_payment`,
`capture_payment`, ...). A deterministic policy decides in milliseconds:
* **Deny** (with every reason): payee not on the allow-list, wrong category, over the per-payment / per-payee / daily cap,
  too many payments per hour or day, or a duplicate of a payment already made. PayPal is never called.
* **Ask a human**: amount above the auto-approve limit, a category that always needs review (subscriptions), or text the
  agent read that looks like injected instructions or hides invisible characters. The approval screen shows the exact text the
  agent read with the suspicious phrases highlighted, and re-checks every limit at the moment of approval.
* **Open a PayPal order** (Orders v2, `PAY_NOW`) for small, allow-listed purchases. The payer still approves on PayPal's page;
  the agent can then capture, exactly once.

Retries are safe (`idempotency_key`, duplicate guard, PayPal `PayPal-Request-Id`). Every step is written to a hash-chained
audit log that detects after-the-fact edits.

## How we built it
Python standard library only: a JSON-RPC 2.0 MCP server over stdio, a pure-function policy engine, a SQLite ledger with a
hash-chained audit table, a PayPal Orders v2 client with an exact-host sandbox guard, and a phone-friendly approval dashboard
protected against cross-site requests. The same code runs against an offline PayPal mock, or the sandbox when two
environment variables are set (not yet run against the live sandbox: no keys at the time of writing). 186 automated tests, including the PayPal client against a fake transport and a real MCP subprocess.

## How AI is used
1. **A real Claude is the buyer.** It reads the job and supplier emails, chooses what to buy, calls the MCP tools and reacts to the answers.
   `docs/LIVE_RUN.md` has two real runs with raw logs: it buys inside the policy, waits for a human on an over-limit purchase, accepts a
   forbidden purchase being denied without trying to get around it, and (run B, no hints) ignores an injected "SYSTEM OVERRIDE" paragraph on its own.
2. **An optional Claude reviewer can only add caution.** For requests the rules did not deny, it can ask for human review (and write a note
   labelled "AI opinion, untrusted"). It can never approve, lift a DENY or soften a human review; if it fails, auto-approvals go to a human.
3. **No model decides.** The policy is deterministic so it cannot be talked out of its rules. Server `instructions`, `simulate_payment`
   and model-readable denial reasons help the agent correct itself instead of retrying blindly.

## PayPal technology used
PayPal Payouts API (the agent pays an allow-listed PayPal account with no payer click, small amounts only) and REST Orders v2 (create with `payment_source.paypal`, `PAY_NOW`; capture; for purchases on the owner's own merchant account), OAuth2 client-credentials with refresh on 401,
`PayPal-Request-Id` idempotency, sandbox environment. Designed around PayPal's consent model: money moves only after the payer
approves on PayPal. Not yet exercised against the live sandbox.

## Challenges
* Making "auto-approve" honest: with PayPal Orders the payer approves every order, so auto-approve means the agent may open the checkout itself.
* Approval fatigue: a flooding agent can bury a human, so there is a pending-queue cap and approvals expire.
* A heuristic injection scan will miss things; we made it a tripwire that raises human attention, never the only defence.
* Found and fixed a crash while hardening: `Decimal("NaN")` passed amount parsing and raised inside the policy engine. It now fails closed, with a test.

## Accomplishments
A complete loop (agent -> policy -> human -> PayPal -> capture -> audit) that a judge can run in 30 seconds with no keys, and
a security posture (look-alike payees, hidden characters, DNS-rebinding, tamper-evident log) you rarely see in a demo.

## What we learned
Put the hard rule outside the model; give the model a dry-run tool; show the human *evidence*, not just an amount.

## What's next
Webhook reconciliation (PayPal events vs ledger), Postgres with spend reservations for multi-node, per-agent policies,
Payouts for agent-to-agent settlement, PYUSD.

## Built with
python, mcp, paypal-orders-v2, paypal-payouts, sqlite, claude

## Try it
Repo: https://github.com/fujiken1016/agentpurse (MIT). `python3 demo.py` then `python3 -m agentpurse.dashboard`. No keys needed.
Video: <YouTube URL, public, under 3 minutes>

## Testing instructions for judges
No account, no keys. Steps are in the README under "Judge click path". With sandbox keys: set `PAYPAL_CLIENT_ID` and
`PAYPAL_CLIENT_SECRET`; the dashboard then shows "Approve on PayPal" linking to the sandbox checkout.
