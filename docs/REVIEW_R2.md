# Review round 2 (2026-10-18)

A different Claude instance, six **new** viewpoints (not the ones from round 1), given the code and the round-1 file but not our conclusions.
It ran the tests on Python 3.9 and 3.12, ran real multi-process experiments, and hand-mutated a copy of the code (38 mutations; 32 caught, 6 survived).
Round-1 items were verified fixed first: the cross-process budget race held under 10 processes, ~4,500 mixed operations and a `kill -9` of the lock holder.

| Viewpoint | Before | After (our estimate, not re-reviewed) |
|---|---|---|
| Distributed systems / concurrency | 5 | 7: lost-capture hole, window anchor, orphans, lock timeout and approval starvation fixed; lock still held during PayPal HTTP calls |
| QA / test engineer | 7 | 8: every R2 finding has a test; the 6 surviving mutants are now covered |
| Compliance / audit | 4 | 6: whole-row hash, atomic state+audit, wrong-path fix; no retention, no approver identity, head hash not auto-published |
| First-time judge (5-minute cold read) | 6 | 6: demo rerun fixed, evidence caveats written down; the AI reviewer and native-MCP run still have never run for real |
| OSS maintainer / DX | 5 | 6: but the Gmail in git history (F4) is open and needs the owner |
| Skeptical product / investor | 5 | 5: unchanged by design work (payee semantics, per-order payer approval, no sandbox run) |

Tests: 149 -> 167. "After" is our estimate; nobody has re-reviewed.

## FATAL
| # | Finding | Status |
|---|---|---|
| F1 | A capture whose response is lost leaves the ledger on ORDER_CREATED while PayPal holds the money; later the order is expired and the daily cap re-armed (reproduced). | **Fixed.** `CAPTURE_UNKNOWN` counts as spent and never expires; `get_order` reconciles (COMPLETED -> CAPTURED with the capture id, APPROVED -> capturable again). 3 tests |
| F2 | Two concurrent dashboard `/api/state` requests crash CPython 3.9 (shared SQLite connection, exit 139; IndexError on 3.12). | **Fixed.** Connection wrapper serialises calls and materialises results. 4-thread test |
| F3 | README said "edit any row and the badge turns red", but the row hash covered only money columns: editing `context`, `decision`, `agent_id`, `created` or `approve_url` went undetected. | **Fixed.** Hash covers every column except `updated`; 10 columns tested one by one. |
| F4 | A personal Gmail address is the author of one commit (`fa3112b`, the PR #1 merge made in the GitHub web UI) in the public history, contradicting RISKS "Hygiene". | **Open, needs the owner.** Fix = rewrite history and force-push public `main` (not done without approval). RISKS now states the fact instead of denying it. |

## IMPORTANT
| Finding | Status |
|---|---|
| Budget window anchored on `created`: a late human approval fell out of the 24 h window early (about 2x the cap possible) | Fixed: `committed_at` anchors the window |
| Crash between AUTO_APPROVED and order creation = immortal orphan; ORDER_UNKNOWN never expires | Fixed: stale AUTO_APPROVED retried with the same request id; ORDER_UNKNOWN fails after the pending TTL (never capturable) |
| flock without timeout; lock held across PayPal HTTP | Timeout (60 s, then "busy, retry") fixed; thread lock no longer leaks on failure. Lock-during-HTTP: **open**, stated in README Limits |
| Human approval denied by the request's own row / starvation by the rate limit | Fixed: the request-rate rule is input-side only |
| Audit and ledger not atomic (a crash looked like tampering) | Fixed: update + audit row share one transaction (tested by crashing between them) |
| Audit CLI printed green on a wrong path | Fixed: missing file fails with exit 2; empty log warns |
| Head hash never published | Partly: printed by the CLI, by `audit_trail` and at server start. Automatic publication: open |
| PII / retention / approver identity / data sent to Anthropic | **Open**, stated plainly in README Limits |
| `demo.py` crashed on a second run | Fixed: refuses an existing ledger path |
| LIVE_RUN evidence ran on older code, the refusal is only self-reported, the bridge hid tool schemas | Written down in LIVE_RUN.md ("Which code these runs used") |
| `live_claude.py` gave Claude `Read` in the repo directory | Fixed: temp working directory with only the supplier notes |
| Six surviving mutants (row_hash scope, event name, FAILED capture, AI-note label, advisor cache key, mock approval) | Tests added for each except the mock-only one |

## POLISH
Done: `_XLock` leak, dashboard bad `Content-Length` and unknown-order mock approval, tagline hedge, README comparison with PayPal's agent toolkit (sourced).
Not done: Windows is thread-lock only; rate-limit denials still create rows; `verify_audit` runs over the whole table per poll; `-m unittest tests.x` needs `discover -s tests`; SUBMISSION still has the YouTube placeholder; ResourceWarnings in two live-driver tests.

## What a third review should look at
The lock is held while talking to PayPal; the reviewer and the native `claude -p` run have never executed; per-order payer approval and payee semantics limit the product story.
