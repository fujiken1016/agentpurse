# AgentPurse

**A policy gate between AI agents and PayPal.** The agent proposes a payment through MCP. A deterministic policy decides: deny, ask a human, or open a PayPal order. The human always has the last word on PayPal's approval page.

Built for the PayPal AI Hackathon (Agentic Commerce). Python 3.9+, **zero third-party dependencies**, MIT.

## Why
Agents that can spend money are one injected web page away from paying an attacker. A system prompt saying "never pay strangers" is not a control. AgentPurse moves the control out of the model:

* allow-listed payees and categories (an injected payee is denied before PayPal is ever called)
* per-payment cap, rolling 24h cap, hourly velocity limit
* auto-approve only below a small limit; anything bigger, or any subscription, waits for a human
* the text the agent read (`context`) is scanned for payment-pressure phrases; a hit forces human review
* limits are re-checked when the human approves (budget may have been spent in between)
* refuses to talk to any non-sandbox PayPal host

## Run it (no keys needed)
```
python3 -m unittest discover -s tests      # 20 tests
python3 demo.py /tmp/ap.db                 # scripted agent over real MCP stdio
python3 -m agentpurse.dashboard --db /tmp/ap.db   # http://127.0.0.1:8787
```
Without keys it uses an offline mock with the same response shapes as PayPal Orders v2.

## Use PayPal sandbox
```
export PAYPAL_CLIENT_ID=...   # sandbox REST app
export PAYPAL_CLIENT_SECRET=...
python3 demo.py
```
Flow: `POST /v2/checkout/orders` (intent CAPTURE, `payment_source.paypal` with PAY_NOW) -> payer approves at the returned `payer-action` link -> `POST /v2/checkout/orders/{id}/capture`.

## Use it from Claude (MCP)
```
{"mcpServers": {"agentpurse": {"command": "python3", "args": ["-m", "agentpurse.mcp_server", "--db", "agentpurse.db", "--policy", "policy.json"]}}}
```
Tools: `check_budget`, `propose_payment`, `payment_status`, `capture_payment`, `list_payments`. Copy `policy.example.json` to start.

## Limits (honest)
* The injection scan is a heuristic tripwire; the real defense is the allow-list and caps.
* PayPal Orders needs the payer to approve each order, so "auto-approve" means the agent may open the checkout itself, not that money moves without the payer.
* `demo.py` uses a scripted stand-in for the LLM so that it is reproducible.
