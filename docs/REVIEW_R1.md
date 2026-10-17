# Review round 1 (2026-10-18)

Six viewpoints, reviewed by a separate Claude instance that was given the code and not our conclusions. It ran 117 tests, then probed the code
(concurrency, Unicode, malformed input, the new advisor) and reported only what it verified. Scores are /10.

| Viewpoint | Before | After (our estimate, not re-reviewed) |
|---|---|---|
| PayPal DevRel judge | 4 | 5: payee handling and honesty fixed; still no live sandbox run, thin PayPal surface |
| Payments-risk judge | 5 | 6: cancel loop, key reuse, capture precheck fixed; race and audit scope still open |
| Agent-design judge | 5 | 6: un-coached run B added; reviewer still not run for real |
| Security engineer | 6 | 7: invisible Unicode, advisor parsing, length limits, state races fixed |
| English technical writer | 6 | 7: stale numbers, overclaims, internal notes fixed |
| Video director | 5 | 6: shotlist rewritten around real Claude, labels, feasibility; not yet filmed |

Test count went from 117 to 128 (11 new tests for the fixes). "After" scores are estimates; a second reviewer has not seen the changes.

## FATAL
| # | Finding | Status |
|---|---|---|
| F1 | The payee never reaches PayPal: orders pay the credential owner, so the allow-list is policy-only (`paypal.py` create_order). | **Mitigated + stated.** Optional per-payee `paypal_email` is sent as `purchase_units[].payee` (tested). README Limits says plainly that otherwise AgentPurse, not PayPal, enforces the allow-list. Payouts for arbitrary payees: open. |
| F2 | Never run against the real sandbox, yet README said it "works the moment keys are set". | **Claim removed** (README, SUBMISSION). The run itself needs keys: **open**, first task when they arrive. |
| F3 | Video scenes 3-5 not producible as written: tool calls take 4-16 s and current Claude usually refuses the injection. | **Fixed in the plan**: 4x speed-up with real clock, a real un-coached run (run B shows Claude refusing on its own), a labelled scripted attacker for the fooled case. Not yet filmed. |

## IMPORTANT (fixed)
| Finding | Fix |
|---|---|
| Hidden-character scan missed Unicode tag characters, soft hyphen, LRM/RLM, variation selectors, invisible math (verified) | Any `Cf`/`Co` character, variation selector or CGJ is flagged; tag-character text is decoded and scanned. 3 tests |
| Advisor parser took the first `{...}` in the reply, so quoted attacker text could spoof "not suspicious" (verified) | Whole reply must be one JSON object (a code fence is tolerated); first-object tests added |
| Advisor prompt breakout via `</untrusted>` | `<` `>` are neutralised in the values |
| Advisor free-text note shown as an ordinary reason | Prefixed `[AI opinion, untrusted]`, capped at 120 characters |
| `maxLength` / `pattern` advertised but not enforced; 3 MB description accepted (verified) | Enforced in the tool-argument check |
| `categories: "api"` (a string) loaded and matched by substring (verified) | Must be a list |
| Cancel loop opened unlimited PayPal orders, bypassing velocity limits (verified) | A cancelled order still counts toward velocity |
| Idempotency key reused for a different purchase silently returned the old payment (verified) | Returns an error naming the mismatch |
| Stale approval could turn a cancelled payment into an open order (verified) | Compare-and-swap status updates; an order that loses the race is logged as orphaned and never captured |
| Capture was blind and relied on a 422 | `get_order` first; capture only when APPROVED |
| `simulate_payment` disagrees with `propose_payment` when the reviewer is on | Documented in the tool description (the reviewer is excluded) |
| "108 tests" stale; AI section out of date; overclaims ("one payment, ever", "tricked out of"); first-person judging notes in a public repo | Numbers, wording and SUBMISSION "How AI is used" rewritten; judging-strategy notes moved out of the repo |
| LIVE_RUN: task text coached the agent; no injection reached Claude | Caveat added; run B (no hints, real injected paragraph) added with raw log. Result: Claude refused on its own, so the deny path was not exercised and the doc says so |
| Shotlist had no reviewer scene, no muted-watch plan, claimed "real PayPal" on a mock | Rewritten (docs/VIDEO_SHOTLIST.md) |

## IMPORTANT (open, in priority order)
1. Cross-process budget race: 6 concurrent 50.00 proposals passed a 100.00 cap (verified). Fix = reserve in one `BEGIN IMMEDIATE` transaction. Now stated in README Limits.
2. AI reviewer never run end to end (the local CLI login had expired); its argv is untested and it runs inside the service lock for up to 30 s. Needs one real run, an argv test, a neutral cwd/env, and to run outside the lock.
3. Audit chain does not cover the payments table; `proposed` and `advisor` rows are not linked to a payment id.
4. `PENDING` capture and create-order timeout need their own states (`CAPTURE_PENDING`, `ORDER_UNKNOWN`); no webhooks.
5. No positioning against PayPal's own agent tooling (needs verified names before citing).
6. No policy re-check at capture; no rate limit on the agent's attempts (the reviewer is a yes/no oracle).
7. Injection regex still misses homoglyphs and paraphrase (stated as a tripwire).

## POLISH not done
Dashboard bad `Content-Length`; render invisible characters visibly on the approval card; `agent_id` is a constant; zero-decimal currencies; README table cells that are too long.
