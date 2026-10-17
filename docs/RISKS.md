# Risks, surprises, and what we did about them

"Status" says whether each item is fixed, mitigated, or open.

## Submission facts that are still open
* The PayPal client has not run against the live sandbox (no keys yet). First task when keys arrive: run `demo.py` with keys, fix shape mismatches, record a sandbox clip.
* The Devpost page could not be fetched by the tooling used to prepare this repo (HTTP 403): criteria and dates in ROLE_MODELS.md come from secondary sources and must be checked on the official page before submitting.
* No hosted demo: the dashboard binds to 127.0.0.1 and would need real authentication before any hosting.

## Engineering risks
| # | Risk | Status / action |
|---|---|---|
| R8 | **Check-then-act race across processes.** The MCP server and the dashboard are separate processes on one SQLite file. The service lock is per process, so two proposals landing in different processes at the same instant can both pass the budget check. | Open and documented. Mitigated by: approval-time re-check, per-hour velocity, the audit chain uses `BEGIN IMMEDIATE`. Next step: wrap evaluate+insert in one `BEGIN IMMEDIATE` transaction, or move to Postgres with row locks and explicit spend reservations (what spendwall does). |
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
No keys, `.env` or `*.db` in the tree (`.gitignore` covers them). No personal name or contact data in files or in commit author fields (commits use the repository's existing noreply identity). No billable action was taken; sandbox keys were never used.

## Added after review round 1 (see REVIEW_R1.md)
| # | Risk | Status |
|---|---|---|
| R18 | **Who is the PayPal payee?** Orders v2 pays the credential owner; `payee_id` was policy-only. | Mitigated: optional per-payee `paypal_email` is sent as `purchase_units[].payee`. Paying arbitrary third parties needs Payouts: open. |
| R19 | **A `PENDING` capture leaves the ledger at ORDER_CREATED**, and a create-order timeout is recorded as FAILED, so a re-proposal gets a new PayPal-Request-Id. | Open. Needs `CAPTURE_PENDING` / `ORDER_UNKNOWN` states and webhook reconciliation. |
| R20 | **The audit chain does not cover the payments table**; the `proposed` and `advisor` rows are not linked to a payment id. | Open. Next: hash each payment row into the chain, create the row before logging. |
| R21 | **The AI reviewer runs inside the service lock** (up to 30 s) and shells out to the local CLI, which loads that user's own Claude settings. | Open. Off by default. Next: run outside the lock, neutral cwd and env, cache by request hash. Its CLI flags have not been run end to end yet (login expired during preparation). |
| R22 | **Capture does no policy re-check** and `simulate_payment` excludes the AI reviewer. | Open / documented in the tool description. |
