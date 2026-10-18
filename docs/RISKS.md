# Risks, surprises, and what we did about them

"Status" says whether each item is fixed, mitigated, or open.

## Submission facts that are still open
* The PayPal client ran against the live sandbox on 2026-10-18 (docs/SANDBOX_RUN.md): no shape mismatches. Still to do: record the sandbox clip for the video (material: docs/sandbox_evidence.png).
* The Devpost page could not be fetched by the tooling used to prepare this repo (HTTP 403): criteria and dates in ROLE_MODELS.md come from secondary sources and must be checked on the official page before submitting.
* No hosted demo: the dashboard binds to 127.0.0.1 and would need real authentication before any hosting.

## Engineering risks
| # | Risk | Status / action |
|---|---|---|
| R8 | **Check-then-act race across processes** (MCP server and dashboard / several agent sessions on one SQLite file). | **Fixed on one machine** (round 2): every state-changing section holds a thread lock plus an OS file lock (`flock` on `<db>.lock`); AUTO_APPROVED rows and the PayPal call happen under it. Test with 8 real processes, which also reproduces the old overspend (6+ orders against a 5-order cap). Open: multi-machine / network file systems need Postgres row locks. |
| R9 | **Pending requests do not reserve budget.** Three 30.00 requests can be pending against a 100.00 cap; the fourth approval is denied at approval time. | By design (tested: `test_human_approval_rechecks_budget`). Surprise for the human, not a safety hole. Reservation would be safer and more confusing; revisit with Postgres. |
| R10 | **Audit log is tamper-evident, not tamper-proof.** Anyone who can write the DB can recompute the whole chain; deleting the newest rows is not detectable by the chain alone. | Open, stated in README ("detects edits"). Fix: periodically publish the head hash (a signed line in a git commit or email), or write to append-only storage. |
| R11 | **Injection scan is a heuristic.** Paraphrase, other languages, or instructions the agent never puts in `context` slip through. | Accepted and stated. The allow-list, caps and human approval are the real defences; the scan only raises attention. Hidden-character and full-width normalisation were added after thinking about how a page would hide text. |
| R12 | **Self-declared `context`.** A compromised agent can leave `context` empty. | Accepted: the tripwire is advisory, and empty context never *lowers* a decision. A stricter mode would require non-empty `context` above the auto-approve limit; not added because it would break legitimate flows. |
| R13 | **Dashboard token is readable by any local process** that can GET `/` with the right Host header. | Accepted for a localhost tool; protects against browser-based attacks (cross-site, DNS rebinding), which is the realistic threat. Do not expose the port. |
| R14 | **Hand-rolled MCP over stdio** only. No Streamable HTTP transport; protocol versions 2025-06-18, 2025-03-26, 2024-11-05 negotiated. | Open. A client that only speaks HTTP cannot connect. Tested with a real subprocess and the JSON-RPC edge cases (parse errors, unknown tool, bad arguments). |
| R15 | **Duplicate guard is exact-match** (payee, amount, category, normalised description, agent). An agent that rewords the description evades it. | Accepted; the hourly/daily velocity and per-payee caps are the backstop. |
| R16 | **Webhooks are not handled**: if the payer approves and PayPal later reverses or the capture goes `PENDING`, the ledger relies on the capture response. | Partly mitigated: a `PENDING` capture is *not* reported as success. Next step: webhook receiver + reconciliation against the ledger. |
| R17 | **Stored `context` (up to 2000 chars) may hold sensitive text** and is shown on the dashboard. | Accepted; DB files are git-ignored. Redaction would be the next step. |

## Surprises found while working
* `Decimal("NaN")` passed amount parsing and raised *inside* the policy engine, so a NaN amount crashed the request instead of being denied. Fixed (finite check + fail-closed wrapper), tests added.
* The sandbox guard was `"sandbox" in base`, which `https://evil.example/sandbox` satisfies. Now an exact-host, https-only check (6 hostile URLs tested).
* The original human-approval re-check evaluated the request with an empty description and context, so a flagged request was re-judged as clean. Now re-evaluated with the stored text.
* A scripted "4 identical requests" test started failing once duplicate prevention existed: correct behaviour, and a reminder that legitimate repeat purchases need a different description or a wait of 10 minutes (configurable).
* The Devpost page returned 403 to the fetch tool, and the only PayPal-hackathon winners post I could find (Dev Days 2025) gives almost no judge commentary, so the "why they won" column in ROLE_MODELS.md is labelled inference.

## Hygiene (checked before the PR)
No keys, `.env` or `*.db` in the tree (`.gitignore` covers them). No personal name or contact data in the files or in the commit author fields: the one commit that carried a personal address (the web-UI merge of PR #1) was removed by a history rewrite on 2026-10-18 and `origin/main` and `refs/pull/1/head` now carry only the noreply identity. GitHub may still resolve the old SHA by direct URL until its caches expire; asking GitHub Support to purge it is optional. No billable action was taken; sandbox keys were never used.

## Added after review round 1 (see REVIEW_R1.md)
| # | Risk | Status |
|---|---|---|
| R18 | **Who is the PayPal payee?** Orders v2 pays the credential owner; `payee_id` was policy-only. | Mitigated: optional per-payee `paypal_email` is sent as `purchase_units[].payee`. Paying arbitrary third parties needs Payouts: open. |
| R19 | **A `PENDING` capture and a create-order timeout** used to be mis-recorded (ORDER_CREATED / FAILED). | **Fixed** (round 2): `CAPTURE_PENDING` and `ORDER_UNKNOWN` states, both count as spent; `payment_status` reconciles (get capture; retry create with the same PayPal-Request-Id). Open: webhooks (PayPal pushing the final state) and a real-sandbox check of the capture-status strings. |
| R20 | **Audit chain vs payments table**, unlinked `proposed` / `advisor` rows. | **Fixed** (round 2): every audit row naming a payment carries a hash of that payment row (`verify_ledger`, shown by `python -m agentpurse.audit`, the dashboard badge and `audit_trail`); `proposed`/`advisor` rows now carry the payment id. Open: the newest rows can still be deleted unless a head hash was published (`--expect-head`). |
| R21 | **AI reviewer in the lock / CLI isolation.** | **Mostly fixed** (round 2): runs before the lock is taken, cached per request, neutral cwd, no tools, no slash commands, own system prompt, argv tested. It has now run against a logged-in CLI (self-test and live scenario b, docs/LIVE_RUN.md); the user-level Claude settings may still be loaded by the CLI. |
| R22 | **Capture without re-check; unlimited probing.** | **Fixed** (round 2): capture re-checks payee/category and refuses orders older than `order_ttl_seconds`; every request (denied too) counts toward `max_requests_per_hour`. `simulate_payment` still excludes the AI reviewer (documented). |

## Added after review round 2 (see REVIEW_R2.md)
| # | Risk | Status |
|---|---|---|
| R23 | **Lost capture response**: PayPal completed the capture, we heard nothing, the ledger expired the order and freed the budget. | **Fixed**: `CAPTURE_UNKNOWN` (counted as spent, never expired); `payment_status` / `capture_payment` reconcile through `get_order`. |
| R24 | **Dashboard threads shared one SQLite connection** (segfault on CPython 3.9). | **Fixed**: the connection is wrapped so every call is serialised and results are materialised. 4-thread test. |
| R25 | **Audit hash covered only money columns.** | **Fixed**: whole row except `updated`; one test per column. State change + audit row now commit in one transaction. |
| R26 | Budget window anchored on request time; human-approved late = counted early; crashed AUTO_APPROVED / ORDER_UNKNOWN rows immortal; human approval blocked by the request-rate rule; lock without timeout. | **Fixed** (see tests/test_round2.py). |
| R27 | PII in `context`, no retention, no approver identity, head hash not published automatically. | Open (stated in README Limits). The audit CLI prints the head hash and the MCP server prints it at start. |
| R28 | Git history contained one web-UI merge commit with a personal Gmail as author. | **Resolved (2026-10-18 history rewrite).** Old SHA may still resolve on GitHub until purged (optional support request). |

## Added after review round 3 (see REVIEW_R3.md)
| # | Risk | Status |
|---|---|---|
| R29 | **Orders v2 pays the credential owner**, so a buyer-side agent could not pay third parties. | **Addressed**: Payouts API path for `method: "payout"` payees (no payer click, lower auto-approve limit 10.00, same policy/human/audit gates), implemented and tested offline; Checkout is documented as "your own merchant account". Both ran on the live sandbox (2026-10-18). Payouts must be enabled for the sandbox app; unclaimed payouts stay pending up to 30 days. |
| R30 | **Lock held across PayPal HTTP.** | **Reduced**: `propose_payment` reserves spend (AUTO_APPROVED counts) and calls PayPal without the lock. Capture and human approval still hold it. |
| R31 | Injection scan misses spaced-out letters and other languages. | Open, now measured: `tests/test_payouts_heuristics.py::test_known_misses_stay_measured`. Added language-neutral signals (amount never mentioned, mixed-script words) and BEC phrasing. |
| R32 | Approver identity was the constant `human:dashboard`. | **Fixed**: `--approver` / `AGENTPURSE_APPROVER` / OS user is recorded. |
| R33 | `context` retention / redaction. | Open (README Limits). |
