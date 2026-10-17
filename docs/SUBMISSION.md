# Devpost submission text (English). Paste each section into the matching Devpost field.

**Project name:** AgentPurse

**Tagline (60 chars):** A PayPal budget your AI agent can't overspend or be tricked out of

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
protected against cross-site requests. The same code runs against an offline PayPal mock or the real sandbox (set two
environment variables). 108 automated tests, including the PayPal client against a fake transport and a real MCP subprocess.

## How AI is used
The agent is the AI: Claude (or any MCP client) chooses what to buy and supplies the `context` it read. We deliberately keep
a model *out* of the decision path so the control cannot be argued with. We make the model a better citizen instead: server
`instructions` teach the workflow, `simulate_payment` lets it check a plan before acting, and denial reasons are written for a
model to read and correct from, without rephrasing its way around the rule.

## PayPal technology used
PayPal REST Orders v2 (create with `payment_source.paypal`, `PAY_NOW`; capture), OAuth2 client-credentials with refresh on 401,
`PayPal-Request-Id` idempotency, sandbox environment. Designed around PayPal's consent model: money moves only after the payer
approves on PayPal.

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
python, mcp, paypal-rest-api, sqlite, claude

## Try it
Repo: https://github.com/fujiken1016/agentpurse (MIT). `python3 demo.py` then `python3 -m agentpurse.dashboard`. No keys needed.
Video: <YouTube URL, public, under 3 minutes>

## Testing instructions for judges
No account, no keys. Steps are in the README under "Judge click path". With sandbox keys: set `PAYPAL_CLIENT_ID` and
`PAYPAL_CLIENT_SECRET`; the dashboard then shows "Approve on PayPal" linking to the sandbox checkout.
