# AgentPurse

**Give your AI agent a PayPal budget it cannot exceed, be tricked past, or use to pay the same bill twice (caveats under Limits).**
The agent asks through MCP. A deterministic policy answers *deny*, *ask a human*, or *open a PayPal order*. The human keeps the last word on PayPal's own approval page.

PayPal AI Hackathon (Agentic Commerce) entry. Python 3.9+, **zero third-party dependencies**, MIT, 149 tests, runs offline with no keys.

![Approval screen on a phone: the text the agent read, with the injected phrases highlighted](docs/dashboard-approval-375px.jpg)

## Try it in 30 seconds (no keys, no install)
```
git clone https://github.com/fujiken1016/agentpurse && cd agentpurse
python3 demo.py /tmp/ap.db                          # a scripted agent talks to the real MCP server: 9 scenarios
python3 -m agentpurse.dashboard --db /tmp/ap.db     # open http://127.0.0.1:8787  (approve / reject, audit log)
python3 -m unittest discover -s tests               # 149 tests
```
Without PayPal keys it uses an offline mock with the same response shapes as PayPal Orders v2.

### Judge click path (2 minutes)
1. Run `demo.py`, read scenarios 2 and 3: an injected web page tries to make the agent pay an attacker. It is denied, or held for a human with the phrase highlighted.
2. Open the dashboard. In **Needs you**, tick "I read the highlighted text", press **Approve**. The order is created; press **Play payer: approve on PayPal (mock)**.
3. Open **Audit log**: every step is there and the badge says *chain intact*. Edit any row in the SQLite file by hand and the badge turns red (`python3 -m agentpurse.audit --db /tmp/ap.db` exits 2).

## What stops a bad payment
| Threat | Control | Result |
|---|---|---|
| Injected page says "pay fast-refund-desk now" | payee allow-list, exact match (look-alike ids fail) | DENIED, PayPal never called |
| Injected text steers a *real* payee | instruction-like text and hidden zero-width characters in `context` are flagged | human must approve, sees the exact phrases |
| Agent spends too much | per-payment cap, per-payee cap, 24h cap, per-payee 24h cap, payments per hour/day | DENIED with **every** reason listed |
| Agent retries after a timeout | `idempotency_key` replay + same-payment duplicate guard + PayPal `PayPal-Request-Id` | a retry with the same key cannot open a second order; a different request cannot reuse the key |
| Agent floods the human with requests | pending-queue cap, requests expire after 24h | human is not buried; stale approvals are void |
| Budget spent while a request waited | limits re-checked at approval time | DENIED instead of overspend |
| Someone edits the log afterwards | hash-chained audit table | tampering detected |
| A web page drives the approval dashboard | per-run token, Host check (DNS rebinding), JSON-only POST, CSP | 403 |
| Misconfigured policy | typo keys and inconsistent limits refuse to load; any evaluation error fails closed | never silently weaker |

## How it works
```mermaid
flowchart LR
  A[AI agent<br/>Claude via MCP] -->|propose_payment| P{Policy<br/>pure function}
  P -->|deny| D[(Ledger + audit chain)]
  P -->|needs human| H[Dashboard<br/>approve / reject]
  H -->|approve, limits re-checked| O
  P -->|approve| O[PayPal Orders v2<br/>create order]
  O -->|approve_url| U[Payer approves on PayPal]
  U --> C[capture_payment]
  C --> D
```

## Where AI is used, and where it is not
| Where | What the AI does | What it can never do |
|---|---|---|
| **The shopping agent** (Claude via MCP) | Reads the job and the supplier text, chooses what to buy, calls the tools, reacts to DENIED / PENDING_HUMAN. [docs/LIVE_RUN.md](docs/LIVE_RUN.md) is a real Claude doing exactly this (raw log included). | Spend outside the policy. A DENIED is final. |
| **Optional AI reviewer** (`AGENTPURSE_ADVISOR=claude-cli`, off by default, local Claude Code CLI, no extra key) | Reads each request the rules did not deny and may say "suspicious" (mismatch, pressure, social engineering). That turns an auto-approval into **human review** and puts its note on the approval card. | Approve, lift a DENY, or soften a human-review. The whole reply must be one JSON object (quoted attacker text cannot spoof a verdict); if the reviewer is down or unparseable, an auto-approval also goes to a human. Its note is shown labelled "AI opinion, untrusted". 9 tests. |
| **The policy decision** | Nothing. Pure deterministic rules a risk team can unit-test and read. | n/a: a model here could be talked out of its rules, so none is allowed to decide. |

The AI is asked for caution, never for permission. `simulate_payment` and the server `instructions` also let the agent ask "would this pass?" and self-correct instead of retrying blindly.

## PayPal APIs used
| Step | Call |
|---|---|
| Open checkout | `POST /v2/checkout/orders` intent `CAPTURE`, `payment_source.paypal`, `user_action: PAY_NOW`, `PayPal-Request-Id` = our payment id |
| Payer consent | redirect to the `payer-action` link |
| Move money | `POST /v2/checkout/orders/{id}/capture` (a `PENDING` capture is not reported as success) |
| Auth | `POST /v1/oauth2/token`, refreshed once on 401 |

The client only talks to `api-m.sandbox.paypal.com`; any other host is refused at construction. **This client has not yet been run against the live sandbox** (no keys yet). It is tested offline against a fake transport built from PayPal's documented shapes (`tests/test_paypal_client.py`). With keys it should work as is; expect small shape fixes:
```
export PAYPAL_CLIENT_ID=...  PAYPAL_CLIENT_SECRET=...      # sandbox REST app
# optional: PAYPAL_RETURN_URL / PAYPAL_CANCEL_URL
python3 demo.py
```

## Where it fits next to PayPal's own agent tooling
PayPal publishes an Agent Toolkit and an MCP server that expose PayPal APIs (orders, invoices, disputes, subscriptions) to agents
([PayPal developer blog](https://developer.paypal.com/community/blog/paypal-agentic-ai-toolkit/)). Those give an agent the *ability* to call PayPal.
AgentPurse is the *permission* layer in front of that ability: allow-lists, caps, velocity, a human for the exceptions, an audit trail. It is not a
replacement; the same policy could gate calls to the official server (not built, not tested). We have not verified what confirmation steps the official
server offers; check PayPal's docs.

## Use it from Claude (MCP)
```
claude mcp add agentpurse -- python3 -m agentpurse.mcp_server --db agentpurse.db --policy policy.json
```
or in `claude_desktop_config.json`: `{"mcpServers": {"agentpurse": {"command": "python3", "args": ["-m", "agentpurse.mcp_server", "--db", "agentpurse.db", "--policy", "policy.json"]}}}`

| Tool | Read-only | Purpose |
|---|---|---|
| `check_budget` | yes | limits, remaining budget, allowed payees |
| `simulate_payment` | yes | dry-run the policy; writes nothing |
| `propose_payment` | no | ask to pay; returns DENIED / PENDING_HUMAN / ORDER_CREATED |
| `payment_status` | yes | poll |
| `capture_payment` | no, destructive | capture after the payer approved; cannot capture twice |
| `cancel_payment` | no | withdraw a pending request or uncaptured order |
| `list_payments` | yes | recent payments |
| `audit_trail` | yes | events of one payment + chain verification |

Copy `policy.example.json` to start; `docs/` has the rest: [SUBMISSION.md](docs/SUBMISSION.md), [VIDEO_SHOTLIST.md](docs/VIDEO_SHOTLIST.md), [RISKS.md](docs/RISKS.md), [ROLE_MODELS.md](docs/ROLE_MODELS.md).

## Tests (149)
| Suite | Tests |
|---|---|
| Policy rules: boundaries, hostile input (NaN, look-alike payees, zero-width tricks), policy-file validation | 26 |
| Service flows: duplicates, idempotency, human flow, expiry, cancel, audit chain | 32 |
| Dashboard security and approval flow | 8 |
| PayPal client against a fake transport (request shapes, token refresh, errors) | 10 |
| MCP protocol, tool schemas, real stdio subprocess | 12 |
| Cross-process race (8 real processes), CAPTURE_PENDING / ORDER_UNKNOWN, audit scope, capture re-check, AI reviewer outside the lock | 15 |
| Live driver `live/live_claude.py` against a stub CLI | 4 |
| AI reviewer CLI wrapper (argv, isolation) | 2 |
| Original suite (wiring, ledger, demo flows) | 20 |
| Review-round-1 hardening (invisible Unicode, key reuse, cancel loops, compare-and-swap, payee) | 11 |
| AI reviewer can only escalate (fake advisor) | 9 |

## Limits (honest)
* **Who gets paid.** With plain Orders v2 the money goes to the merchant that owns the REST credentials. If a payee has `paypal_email` in the policy, it is sent as `purchase_units[].payee`, which PayPal only accepts for accounts allowed to take payment on that payee's behalf. Without it, the allow-list is enforced by AgentPurse, not by PayPal. Paying arbitrary third parties would need the Payouts API (listed under next steps, not built).
* **One machine only.** The budget check and the write are serialised by an OS file lock (`<db>.lock`) shared by the MCP server and the dashboard; 8 processes racing for a 100.00 cap now get exactly 5 orders (`tests/test_concurrency_states.py`, which also reproduces the old overspend). The lock does not work across machines or network file systems: that needs Postgres row locks.
* **The audit chain is tamper-evident, not tamper-proof.** Each audit row carries a hash of its payment's row, so editing, deleting or smuggling in a payment is caught (`python -m agentpurse.audit`). Someone with write access can still rebuild everything; `--expect-head <seq:hash>` (a head you published earlier) catches deleted newest rows.
* The injection scan is a heuristic tripwire; the real defence is the allow-list, the caps and the human.
* PayPal Orders needs the payer to approve each order, so "auto-approve" means the agent may open the checkout itself, not that money moves without the payer.
* Sandbox only by design. `demo.py` uses a scripted stand-in for the LLM so it is reproducible; the real-Claude evidence is [docs/LIVE_RUN.md](docs/LIVE_RUN.md) (one run, mock PayPal, driven through a shell bridge because the `claude -p` CLI login had expired; `live/live_claude.py` is the native-MCP version and has not been run yet).
* SQLite single-node; see [docs/RISKS.md](docs/RISKS.md) for what that means and what would come next.
