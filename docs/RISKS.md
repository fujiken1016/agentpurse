# Risks, surprises, and what we did about them

Ordered by how much each could cost us in judging. "Status" says whether it is fixed, mitigated, or open.

## Judging risks
| # | Risk | Why it matters | Status / action |
|---|---|---|---|
| R1 | **"Meaningful AI" is thin.** The policy has no model by design and `demo.py` uses a scripted agent. Rules require meaningful AI use. | Could cost the Technological Implementation score outright. | **Open, highest priority.** Mitigation: record the video with a real Claude client driving the MCP server (shotlist scene 3), keep the "Where AI is used" table in the README, and lean on `simulate_payment` + server `instructions` as AI-facing design. Cheap upgrade if time allows: an optional LLM "explain this request to the human" summary on the approval card (advisory only, never decides). Not built: needs an API key and spend. |
| R2 | **Never run against the real PayPal sandbox** (no keys yet). Real response shapes could differ from the ones we coded to. | The criteria say it must work; "mock only" reads as a mockup. | Mitigated: client tested against a fake transport built from PayPal's documented Orders v2 shapes (create, capture, 401 refresh, 422, PENDING capture). **First task when keys arrive:** run `demo.py` with keys, fix any shape mismatch, add a recorded sandbox clip to the video. |
| R3 | **Crowded idea.** `spendwall` (https://github.com/AndrewDongminYoo/spendwall) is the same pattern with Postgres, reservations and webhooks; `holdfast` has a hosted demo. | Innovation score; judges may see us as a clone. | Mitigated in positioning (ROLE_MODELS.md section 3): buyer-side agent spend, stdlib-only 30-second run, evidence-first approval screen, hash-chained audit, injection hardening. Open: no hosted demo URL (see R4). |
| R5 | **Could not read the official Devpost page** (HTTP 403). Criteria, dates and prizes come from a secondary summary and search snippets. | A wrong deadline or missing requirement is fatal. | Open, 2 minutes of human work: read https://paypalaihackathon.devpost.com/ and `/rules`. Deadline per sources: **2026-11-12 12:00 PM Pacific = 2026-11-13 05:00 JST.** |
| R6 | **Video over 3:00 or not public on YouTube** is disqualifying per the sources. | Binary. | Shotlist targets 2:40. Upload as Public, check from a logged-out window. |
| R7 | **"Autonomous payments" overclaim.** PayPal Orders needs the payer to approve every order. | Judges who know PayPal will notice. | Fixed: README, submission text and tool descriptions all say auto-approve = the agent may open the checkout, the payer still consents. |
| R4 | **No hosted demo.** The dashboard binds to 127.0.0.1 and needs Python. | Judges may not run code. | Open. Options (not done, no billing allowed): static recording in the README (screenshot added), GIF from the video. A hosted deployment needs the dashboard behind real auth first. |

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
